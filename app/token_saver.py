"""
Token Saver Module — Caveman-style token optimization for LLM proxy.

Two strategies:
1. Input-side: NLP rule-based compression of prompt text (remove filler words,
   articles, pleasantries, redundant connectors; simplify passive voice).
2. Output-side: System prompt injection to constrain model output style
   (fragment expressions, drop filler, keep technical accuracy).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("llm_proxy.token_saver")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TokenSaverStats:
    """Statistics returned after processing a request."""
    input_tokens_original: int = 0
    input_tokens_compressed: int = 0
    input_tokens_saved: int = 0
    output_prompt_tokens_added: int = 0
    output_level: str = "off"


@dataclass
class TokenSaverConfig:
    enabled: bool = False
    input_level: str = "full"   # off / lite / full / ultra
    output_level: str = "full"  # off / lite / full / ultra / wenyan


# ---------------------------------------------------------------------------
# Rough token estimator (whitespace + CJK split)
# ---------------------------------------------------------------------------

_CJK_RANGES = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate: ~1 token per 4 chars for English,
    ~1 token per 1.5 chars for CJK, punctuation counted separately."""
    if not text:
        return 0
    cjk_count = len(_CJK_RANGES.findall(text))
    non_cjk_text = _CJK_RANGES.sub('', text)
    words = non_cjk_text.split()
    return cjk_count + len(words)


# ---------------------------------------------------------------------------
# Input Compressor — rule-based text compression
# ---------------------------------------------------------------------------

class InputCompressor:
    """Compress prompt text using caveman-style NLP rules."""

    # --- Shared patterns (all levels) ---

    # Pleasantries / filler at the start of assistant messages
    _PLEASANTRIES = re.compile(
        r'(?i)^(?:sure[!,]?\s*|certainly[!,]?\s*|of\s*course[!,]?\s*|'
        r'I\'d\s+be\s+happy\s+to\s+(?:help\s+)?(?:you\s+)?(?:with\s+that\.?\s*)?|'
        r'let\s+me\s+help\s+you\s+with\s+that\.?\s*|'
        r'great\s+question[!,]?\s*|'
        r'absolutely[!,]?\s*|'
        r'definitely[!,]?\s*|'
        r'certainly[!,]?\s*)+',
    )

    # Filler words (can appear mid-sentence)
    _FILLER_WORDS = re.compile(
        r'(?i)\b(?:just|really|basically|actually|simply|literally|honestly|'
        r'please|to be honest|in fact|as a matter of fact|needless to say|'
        r'it goes without saying|it is worth noting that|'
        r'it should be noted that|importantly|notably)\b[,\s]*',
    )

    # --- Full-level patterns (lite + these) ---

    # Articles
    _ARTICLES = re.compile(r'(?i)\b(?:a|an|the)\b')

    # Redundant connectors → shorter equivalents
    _CONNECTOR_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r'(?i)\bin\s+order\s+to\b'), 'to'),
        (re.compile(r'(?i)\bdue\s+to\s+the\s+fact\s+that\b'), 'because'),
        (re.compile(r'(?i)\bfor\s+the\s+purpose\s+of\b'), 'to'),
        (re.compile(r'(?i)\bin\s+the\s+event\s+that\b'), 'if'),
        (re.compile(r'(?i)\bat\s+this\s+point\s+in\s+time\b'), 'now'),
        (re.compile(r'(?i)\bin\s+spite\s+of\s+the\s+fact\s+that\b'), 'although'),
        (re.compile(r'(?i)\bwith\s+regard\s+to\b'), 'about'),
        (re.compile(r'(?i)\bwith\s+respect\s+to\b'), 'about'),
        (re.compile(r'(?i)\bin\s+terms\s+of\b'), 'in'),
        (re.compile(r'(?i)\bon\s+the\s+other\s+hand\b'), 'alternatively'),
        (re.compile(r'(?i)\bfor\s+the\s+most\s+part\b'), 'mostly'),
        (re.compile(r'(?i)\bin\s+a\s+lot\s+of\s+cases\b'), 'often'),
        (re.compile(r'(?i)\bmore\s+often\s+than\s+not\b'), 'usually'),
        (re.compile(r'(?i)\bthe\s+majority\s+of\b'), 'most'),
        (re.compile(r'(?i)\ba\s+large\s+number\s+of\b'), 'many'),
        (re.compile(r'(?i)\bthe\s+reason\s+why\b'), 'why'),
        (re.compile(r'(?i)\bthe\s+fact\s+that\b'), ''),
        (re.compile(r'(?i)\bat\s+the\s+present\s+time\b'), 'now'),
        (re.compile(r'(?i)\bby\s+means\s+of\b'), 'by'),
        (re.compile(r'(?i)\bin\s+the\s+vicinity\s+of\b'), 'near'),
        (re.compile(r'(?i)\buntil\s+such\s+time\s+as\b'), 'until'),
    ]

    # Emphatic adverbs to remove
    _EMPHATIC_ADVERBS = re.compile(
        r'(?i)\b(?:very|extremely|quite|rather|somewhat|really|terribly|awfully|'
        r'incredibly|remarkably|particularly|exceptionally|highly)\b[,\s]*',
    )

    # --- Ultra-level patterns (full + these) ---

    # Common verb phrase simplifications
    _VERB_SIMPLIFICATIONS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r'(?i)\bis\s+able\s+to\b'), 'can'),
        (re.compile(r'(?i)\bare\s+able\s+to\b'), 'can'),
        (re.compile(r'(?i)\bis\s+going\s+to\b'), 'will'),
        (re.compile(r'(?i)\bare\s+going\s+to\b'), 'will'),
        (re.compile(r'(?i)\bhas\s+the\s+ability\s+to\b'), 'can'),
        (re.compile(r'(?i)\bhave\s+the\s+ability\s+to\b'), 'can'),
        (re.compile(r'(?i)\bis\s+capable\s+of\b'), 'can'),
        (re.compile(r'(?i)\bare\s+capable\s+of\b'), 'can'),
        (re.compile(r'(?i)\bmake\s+a\s+decision\b'), 'decide'),
        (re.compile(r'(?i)\bgive\s+an\s+explanation\s+of\b'), 'explain'),
        (re.compile(r'(?i)\bcome\s+to\s+a\s+conclusion\b'), 'conclude'),
        (re.compile(r'(?i)\bput\s+an\s+end\s+to\b'), 'end'),
        (re.compile(r'(?i)\btake\s+into\s+consideration\b'), 'consider'),
        (re.compile(r'(?i)\bhave\s+an\s+effect\s+on\b'), 'affect'),
        (re.compile(r'(?i)\bmake\s+use\s+of\b'), 'use'),
        (re.compile(r'(?i)\bI\s+would\s+like\s+(?:to\s+)?\b'), ''),
        (re.compile(r'(?i)\bI\s+want\s+(?:to\s+)?\b'), ''),
        (re.compile(r'(?i)\bI\s+am\s+(?:trying\s+to|looking\s+to)\b'), 'need to'),
        (re.compile(r'(?i)\b(?:could|could\s+you\s+please)\s+you\s+(?:please\s+)?explain\b'), 'explain'),
        (re.compile(r'(?i)\b(?:make\s+sure|be\s+sure)\s+to\b'), 'ensure'),
        (re.compile(r'(?i)\bwould\s+you\s+(?:be\s+able\s+to|mind)\b'), 'please'),
        (re.compile(r'(?i)\bI\s+would\s+(?:really\s+)?appreciate\s+(?:it\s+)?if\b'), 'if'),
        (re.compile(r'(?i)\b(?:let\s+me\s+know|get\s+back\s+to\s+me)\b'), 'respond'),
        (re.compile(r'(?i)\bas\s+soon\s+as\s+possible\b'), 'ASAP'),
        (re.compile(r'(?i)\ba\s+lot\s+of\b'), 'many'),
        (re.compile(r'(?i)\bplenty\s+of\b'), 'many'),
    ]

    # Code block pattern — content between ``` should never be modified
    _CODE_BLOCK = re.compile(r'(```[\s\S]*?```)')

    # Minimum savings threshold (fraction) below which we revert
    _MIN_SAVINGS_RATIO = 0.05

    # ---- Public API ----

    def compress_messages(
        self,
        messages: list[dict[str, Any]],
        level: str = "full",
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Compress messages, return (compressed_messages, original_tokens, compressed_tokens)."""
        if level == "off" or not messages:
            total = sum(_estimate_tokens(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))
            return messages, total, total

        compressed = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                compressed.append(msg)
                continue

            role = msg.get("role", "")
            # Skip system messages by default — they contain instructions
            if role == "system":
                compressed.append(msg)
                continue

            new_content = self._compress_text(content, level)
            compressed.append({**msg, "content": new_content})

        orig_tokens = sum(_estimate_tokens(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))
        comp_tokens = sum(_estimate_tokens(m.get("content", "")) for m in compressed if isinstance(m.get("content"), str))

        # Revert if savings too small
        if orig_tokens > 0 and (orig_tokens - comp_tokens) / orig_tokens < self._MIN_SAVINGS_RATIO:
            return messages, orig_tokens, orig_tokens

        return compressed, orig_tokens, comp_tokens

    # ---- Internal ----

    def _compress_text(self, text: str, level: str) -> str:
        """Apply compression rules to a single text string, preserving code blocks."""
        # Split out code blocks
        parts = self._CODE_BLOCK.split(text)
        result_parts = []
        for i, part in enumerate(parts):
            if part.startswith("```"):
                result_parts.append(part)  # Preserve code block verbatim
            else:
                result_parts.append(self._apply_rules(part, level))
        return "".join(result_parts)

    def _apply_rules(self, text: str, level: str) -> str:
        """Apply compression rules based on level."""
        # --- lite level ---
        text = self._PLEASANTRIES.sub('', text)
        text = self._FILLER_WORDS.sub('', text)

        if level == "lite":
            return self._cleanup_whitespace(text)

        # --- full level ---
        text = self._ARTICLES.sub('', text)
        for pattern, replacement in self._CONNECTOR_REPLACEMENTS:
            text = pattern.sub(replacement, text)
        text = self._EMPHATIC_ADVERBS.sub('', text)

        if level == "full":
            return self._cleanup_whitespace(text)

        # --- ultra level ---
        for pattern, replacement in self._VERB_SIMPLIFICATIONS:
            text = pattern.sub(replacement, text)

        return self._cleanup_whitespace(text)

    @staticmethod
    def _cleanup_whitespace(text: str) -> str:
        """Clean up extra whitespace left after deletions."""
        text = re.sub(r'  +', ' ', text)   # Multiple spaces → single
        text = re.sub(r'\n{3,}', '\n\n', text)  # Multiple newlines → double
        text = re.sub(r' ([,.:;!?)])', r'\1', text)  # Space before punctuation
        text = re.sub(r'\( ', '(', text)    # Space after opening paren
        text = text.strip()
        return text


# ---------------------------------------------------------------------------
# Output Prompt Injector — system prompt injection for output compression
# ---------------------------------------------------------------------------

_OUTPUT_PROMPTS: dict[str, str] = {
    "lite": (
        "Be concise. Drop filler words and pleasantries. "
        "Use direct statements. Keep technical accuracy."
    ),
    "full": (
        "Reply in caveman style: drop articles, filler words, pleasantries. "
        "Use fragments, not full sentences. Keep technical terms exact. "
        "Keep code blocks unchanged. Quote errors exactly. "
        "Pattern: [thing] [action] [reason]. [next step]. "
        "If user asks to delete/destroy/drop/overwrite data, switch to normal clear language for safety warnings."
    ),
    "ultra": (
        "Ultra-concise mode: telegraphic style only. No articles, no filler, no complete sentences. "
        "Noun-verb-noun only. Omit 'is/are/was/were'. "
        "Technical terms exact. Code unchanged. Errors quoted exact. "
        "Safety warnings: switch to normal language."
    ),
    "wenyan": (
        "以文言文风格回复。省去虚词、语气词、冗余连接词。"
        "保留技术术语原样。代码块不变。错误信息原样引用。"
        "涉及删除/破坏性操作时，切换为现代白话文给出安全警告。"
    ),
}

# High-risk keywords that should prevent output prompt injection
_RISK_KEYWORDS = re.compile(
    r'(?i)\b(?:delete|drop|remove|destroy|overwrite|truncate|erase|'
    r'rm|format|reset|wipe)\b',
)


class OutputPromptInjector:
    """Inject a system prompt to constrain model output style."""

    def inject_prompt(
        self,
        messages: list[dict[str, Any]],
        level: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Inject output compression prompt. Returns (modified_messages, tokens_added)."""
        if level == "off" or level not in _OUTPUT_PROMPTS:
            return messages, 0

        if self.should_skip(messages):
            logger.info("token_saver_output_skip: high-risk keywords detected")
            return messages, 0

        prompt_text = _OUTPUT_PROMPTS[level]
        injected_msg = {"role": "system", "content": prompt_text}

        # Append at end so it has strong influence
        modified = list(messages)
        modified.append(injected_msg)

        tokens_added = _estimate_tokens(prompt_text)
        return modified, tokens_added

    @staticmethod
    def should_skip(messages: list[dict[str, Any]]) -> bool:
        """Check if the user message contains high-risk operation keywords."""
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and _RISK_KEYWORDS.search(content):
                return True
            # Handle list content (multimodal messages)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text", "")
                        if isinstance(text, str) and _RISK_KEYWORDS.search(text):
                            return True
        return False


# ---------------------------------------------------------------------------
# Token Saver Service — orchestrator
# ---------------------------------------------------------------------------

class TokenSaverService:
    """Orchestrate input compression + output injection for a proxy request."""

    def __init__(self) -> None:
        self._input_compressor = InputCompressor()
        self._output_injector = OutputPromptInjector()

    def process_request(
        self,
        request_json: dict[str, Any] | None,
        config: TokenSaverConfig,
        provider: str | None = None,
    ) -> tuple[bytes, dict[str, Any] | None, TokenSaverStats]:
        """Process request JSON through token saver pipeline.

        Args:
            request_json: Parsed request body (may be None for non-JSON).
            config: Token saver configuration for this proxy.
            provider: Provider name ('openai' or 'anthropic'). Used to handle
                      system prompt format differences.

        Returns:
            (modified_body_bytes, modified_request_json, stats)
        """
        stats = TokenSaverStats()

        if not config.enabled or not request_json:
            return b'', None, stats

        messages = request_json.get("messages")
        if not messages or not isinstance(messages, list):
            return b'', None, stats

        original_tokens = sum(
            _estimate_tokens(m.get("content", ""))
            for m in messages
            if isinstance(m.get("content"), str)
        )
        stats.input_tokens_original = original_tokens

        # Step 1: Input compression
        if config.input_level != "off":
            messages, orig, comp = self._input_compressor.compress_messages(
                messages, config.input_level
            )
            request_json = {**request_json, "messages": messages}
            stats.input_tokens_original = orig
            stats.input_tokens_compressed = comp
            stats.input_tokens_saved = max(0, orig - comp)

        # Step 2: Output prompt injection
        if config.output_level != "off":
            messages, tokens_added = self._output_injector.inject_prompt(
                messages, config.output_level
            )
            request_json = {**request_json, "messages": messages}
            stats.output_prompt_tokens_added = tokens_added
            stats.output_level = config.output_level
            # NOTE: input_tokens_saved already reflects pure input compression savings.
            # The output prompt token cost is tracked separately as output_prompt_tokens_added.

        # Step 3: Handle provider-specific system prompt format
        # Anthropic requires system prompt as top-level "system" field, not in messages[]
        if provider and provider.lower() == "anthropic":
            messages = request_json.get("messages", [])
            system_contents = []
            filtered_messages = []
            for msg in messages:
                if msg.get("role") == "system":
                    content = msg.get("content", "")
                    if content:
                        system_contents.append(content)
                else:
                    filtered_messages.append(msg)
            if system_contents:
                request_json = {**request_json, "system": "\n\n".join(system_contents), "messages": filtered_messages}

        modified_body = json.dumps(request_json, ensure_ascii=False).encode("utf-8")
        return modified_body, request_json, stats

    @staticmethod
    def estimate_output_savings(output_tokens: int, level: str) -> int:
        """Estimate output tokens saved based on compression level.

        These are conservative estimates based on caveman benchmarks:
        - lite: ~20% savings
        - full: ~50% savings
        - ultra: ~65% savings
        - wenyan: ~50% savings (Chinese specific)
        """
        ratios = {
            "off": 0.0,
            "lite": 0.20,
            "full": 0.50,
            "ultra": 0.65,
            "wenyan": 0.50,
        }
        ratio = ratios.get(level, 0.0)
        return int(output_tokens * ratio)
