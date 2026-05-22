from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "configs"
DB_PATH = DATA_DIR / "llm_usage.db"
PROXY_CATALOG_PATH = CONFIG_DIR / "proxy_catalog.json"


class ProxyProviderConfig(BaseModel):
    provider: str
    proxy_key: str = "default"
    display_name: str | None = None
    base_url: str
    auth_header: str = "Authorization"
    api_key_env: str | None = None
    api_key_prefix: str = "Bearer"
    forward_user_auth: bool = False
    timeout_seconds: int = 60
    enabled: bool = True
    ssl_verify: bool = True
    static_headers: dict[str, str] = Field(default_factory=dict)
    token_saver_enabled: bool = False
    token_saver_input_level: str = "full"
    token_saver_output_level: str = "full"


class ProxyCatalog(BaseModel):
    providers: list[ProxyProviderConfig]


def _normalize_proxy_item(raw: dict) -> dict:
    provider = raw.get("provider", "")
    proxy_key = raw.get("proxy_key") or raw.get("name") or "default"
    display_name = raw.get("display_name") or proxy_key
    return {
        **raw,
        "proxy_key": proxy_key,
        "display_name": display_name,
        "provider": provider,
    }


@lru_cache(maxsize=1)
def load_proxy_catalog() -> Dict[Tuple[str, str], ProxyProviderConfig]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw = json.loads(PROXY_CATALOG_PATH.read_text(encoding="utf-8"))
    normalized = {
        "providers": [_normalize_proxy_item(item) for item in raw.get("providers", [])]
    }
    catalog = ProxyCatalog.model_validate(normalized)
    return {(item.provider.lower(), item.proxy_key): item for item in catalog.providers}


def reload_proxy_catalog() -> Dict[Tuple[str, str], ProxyProviderConfig]:
    load_proxy_catalog.cache_clear()
    return load_proxy_catalog()


def save_proxy_catalog(
    configs: Dict[Tuple[str, str], ProxyProviderConfig],
) -> Dict[Tuple[str, str], ProxyProviderConfig]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": [
            item.model_dump()
            for item in sorted(
                configs.values(),
                key=lambda item: (item.provider.lower(), item.proxy_key.lower()),
            )
        ]
    }
    PROXY_CATALOG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return reload_proxy_catalog()
