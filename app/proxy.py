from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from time import perf_counter
from typing import Any

import certifi

EXTRA_CA_PATH = Path(__file__).resolve().parent.parent / "configs" / "digicert-ca.pem"

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.config import load_proxy_catalog
from app.schemas import UsageRecordRequest
from app.service import UsageService
from app.token_saver import TokenSaverConfig, TokenSaverService


logger = logging.getLogger("llm_proxy")

HOP_BY_HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _copy_headers(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HOP_BY_HOP_HEADERS:
            continue
        result[key] = value
    return result


def _join_url(base_url: str, subpath: str, query_string: str) -> str:
    path = subpath.lstrip("/")
    url = f"{base_url.rstrip('/')}/{path}"
    if query_string:
        url = f"{url}?{query_string}"
    return url


async def _read_request_json(request: Request, body: bytes) -> dict[str, Any] | None:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return None
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _prepare_request_body(
    provider: str,
    request_json: dict[str, Any] | None,
    body: bytes,
) -> tuple[bytes, dict[str, Any] | None]:
    if not request_json:
        return body, request_json

    mutated = False
    payload = dict(request_json)

    if provider == "openai" and payload.get("stream") is True:
        stream_options = dict(payload.get("stream_options") or {})
        if stream_options.get("include_usage") is not True:
            stream_options["include_usage"] = True
            payload["stream_options"] = stream_options
            mutated = True

    if not mutated:
        return body, request_json

    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), payload


def _read_response_json(headers: dict[str, str], body: bytes) -> dict[str, Any] | None:
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    if "application/json" not in content_type.lower():
        return None
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _extract_usage_payload(
    provider: str,
    upstream_path: str,
    request_json: dict[str, Any] | None,
    response_json: dict[str, Any] | None,
    status_code: int,
    latency_ms: int,
    input_tokens_saved: int = 0,
    output_tokens_saved: int = 0,
) -> UsageRecordRequest | None:
    if not request_json:
        return None

    status = "success" if 200 <= status_code < 400 else "failed"
    trace_id = request_json.get("trace_id") or request_json.get("metadata", {}).get("trace_id")
    biz_key = request_json.get("biz_key") or request_json.get("metadata", {}).get("biz_key")
    user_id = request_json.get("user") or request_json.get("metadata", {}).get("user_id")
    tenant_id = request_json.get("metadata", {}).get("tenant_id")
    request_id = response_json.get("id") if isinstance(response_json, dict) else None

    model = ""
    input_tokens = 0
    output_tokens = 0
    cache_creation_tokens = 0
    cache_read_tokens = 0
    reasoning_tokens = 0
    total_tokens = None

    if provider == "openai":
        model = (response_json or {}).get("model") or request_json.get("model", "")
        usage = (response_json or {}).get("usage", {})
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        reasoning_tokens = int(
            ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
        )
        total_tokens = usage.get("total_tokens")
    elif provider == "anthropic":
        model = (response_json or {}).get("model") or request_json.get("model", "")
        usage = (response_json or {}).get("usage", {})
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        total_tokens = (
            input_tokens
            + output_tokens
            + cache_creation_tokens
            + cache_read_tokens
            + reasoning_tokens
        )

    if not model:
        return None

    return UsageRecordRequest(
        trace_id=trace_id,
        request_id=request_id,
        biz_key=biz_key,
        provider=provider,
        model=model,
        endpoint=upstream_path,
        request_type=request_json.get("stream") and "stream" or "sync",
        user_id=user_id,
        tenant_id=tenant_id,
        latency_ms=latency_ms,
        status=status,
        error_code=None if status == "success" else f"http_{status_code}",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
        raw_usage=response_json.get("usage") if isinstance(response_json, dict) else None,
        input_tokens_saved=input_tokens_saved,
        output_tokens_saved=output_tokens_saved,
    )


class StreamUsageState:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.request_id: str | None = None
        self.model: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0
        self.reasoning_tokens = 0
        self.total_tokens: int | None = None
        self.raw_usage: dict[str, Any] | None = None

    def consume_event(self, event_payload: dict[str, Any]) -> None:
        if self.provider == "openai":
            self._consume_openai_event(event_payload)
        elif self.provider == "anthropic":
            self._consume_anthropic_event(event_payload)

    def _consume_openai_event(self, event_payload: dict[str, Any]) -> None:
        if event_payload.get("id"):
            self.request_id = event_payload["id"]
        if event_payload.get("model"):
            self.model = event_payload["model"]

        usage = event_payload.get("usage") or {}
        if usage:
            self.input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            self.output_tokens = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
            self.reasoning_tokens = int(
                ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
            )
            total_tokens = usage.get("total_tokens")
            self.total_tokens = int(total_tokens) if total_tokens is not None else None
            self.raw_usage = usage

    def _consume_anthropic_event(self, event_payload: dict[str, Any]) -> None:
        event_type = event_payload.get("type")
        message = event_payload.get("message") or {}
        delta = event_payload.get("delta") or {}
        usage = {}

        if event_payload.get("message", {}).get("id"):
            self.request_id = event_payload["message"]["id"]
        elif event_payload.get("id"):
            self.request_id = event_payload["id"]

        if message.get("model"):
            self.model = message["model"]
        elif event_payload.get("model"):
            self.model = event_payload["model"]

        if event_type == "message_start":
            usage = message.get("usage") or {}
            self.input_tokens = int(usage.get("input_tokens") or 0)
            self.cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
            self.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        elif event_type == "message_delta":
            usage = delta.get("usage") or event_payload.get("usage") or {}
            if "output_tokens" in usage:
                self.output_tokens = max(self.output_tokens, int(usage.get("output_tokens") or 0))
        elif event_type == "message_stop":
            usage = event_payload.get("usage") or {}

        if usage:
            self.raw_usage = {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_creation_input_tokens": self.cache_creation_tokens,
                "cache_read_input_tokens": self.cache_read_tokens,
            }

        self.total_tokens = (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.reasoning_tokens
        )


def _build_stream_usage_payload(
    provider: str,
    upstream_path: str,
    request_json: dict[str, Any] | None,
    state: StreamUsageState,
    status: str,
    latency_ms: int,
    error_code: str | None = None,
    input_tokens_saved: int = 0,
    output_tokens_saved: int = 0,
) -> UsageRecordRequest | None:
    if not request_json:
        return None

    model = state.model or request_json.get("model")
    if not model:
        return None

    trace_id = request_json.get("trace_id") or request_json.get("metadata", {}).get("trace_id")
    biz_key = request_json.get("biz_key") or request_json.get("metadata", {}).get("biz_key")
    user_id = request_json.get("user") or request_json.get("metadata", {}).get("user_id")
    tenant_id = request_json.get("metadata", {}).get("tenant_id")

    return UsageRecordRequest(
        trace_id=trace_id,
        request_id=state.request_id,
        biz_key=biz_key,
        provider=provider,
        model=model,
        endpoint=upstream_path,
        request_type="stream",
        user_id=user_id,
        tenant_id=tenant_id,
        latency_ms=latency_ms,
        status=status,
        error_code=error_code,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        cache_creation_tokens=state.cache_creation_tokens,
        cache_read_tokens=state.cache_read_tokens,
        reasoning_tokens=state.reasoning_tokens,
        total_tokens=state.total_tokens,
        raw_usage=state.raw_usage,
        input_tokens_saved=input_tokens_saved,
        output_tokens_saved=output_tokens_saved,
    )


def _usage_log_fields(payload: UsageRecordRequest | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "model": payload.model,
        "input_tokens": payload.input_tokens,
        "output_tokens": payload.output_tokens,
        "cache_creation_tokens": payload.cache_creation_tokens,
        "cache_read_tokens": payload.cache_read_tokens,
        "reasoning_tokens": payload.reasoning_tokens,
        "total_tokens": payload.total_tokens,
        "input_tokens_saved": payload.input_tokens_saved,
        "output_tokens_saved": payload.output_tokens_saved,
        "status": payload.status,
    }


def _record_error(
    provider: str,
    proxy_key: str,
    subpath: str,
    request_json: dict[str, Any] | None,
    status_code: int,
    response_body: bytes | None,
    response_headers: dict[str, str] | None,
    usage_service: UsageService,
    error_detail: str | None = None,
) -> None:
    try:
        response_json = None
        if response_headers and response_body:
            response_json = _read_response_json(response_headers, response_body)
        usage_payload = _extract_usage_payload(
            provider=provider,
            upstream_path=f"/{subpath.lstrip('/')}",
            request_json=request_json,
            response_json=response_json,
            status_code=status_code,
            latency_ms=0,
        )
        if usage_payload:
            usage_service.create_record(usage_payload)
        logger.warning(
            "proxy_stream_error provider=%s proxy_key=%s status=%s detail=%s",
            provider,
            proxy_key,
            status_code,
            error_detail or "",
        )
    except Exception:
        logger.exception(
            "proxy_stream_error_record_failed provider=%s proxy_key=%s",
            provider,
            proxy_key,
        )


def _build_ssl_context(ssl_verify: bool) -> ssl.SSLContext | None:
    if not ssl_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context(cafile=certifi.where())
    if EXTRA_CA_PATH.exists():
        ctx.load_verify_locations(str(EXTRA_CA_PATH))
    return ctx


async def _send_upstream_request(
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: int,
    ssl_verify: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    ssl_ctx = _build_ssl_context(ssl_verify)

    def do_request() -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(url=url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds, context=ssl_ctx) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"upstream request failed: {exc.reason}")

    return await asyncio.to_thread(do_request)


async def _stream_upstream_request(
    provider: str,
    proxy_key: str,
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: int,
    request_json: dict[str, Any] | None,
    subpath: str,
    usage_service: UsageService,
    ssl_verify: bool = True,
    input_tokens_saved: int = 0,
    output_tokens_saved: int = 0,
    output_level: str = "off",
) -> Response:
    logger.info(
        "proxy_stream_start provider=%s proxy_key=%s method=%s upstream=%s model=%s",
        provider,
        proxy_key,
        method,
        url,
        (request_json or {}).get("model"),
    )
    ssl_ctx = _build_ssl_context(ssl_verify)
    req = urllib.request.Request(url=url, data=body or None, headers=headers, method=method)

    def do_connect():
        logger.info(
            "proxy_stream_req method=%s url=%s headers=%s body=%s",
            method,
            url,
            json.dumps(headers, ensure_ascii=False)
        )
        return urllib.request.urlopen(req, timeout=timeout_seconds, context=ssl_ctx)

    try:
        upstream_resp = await asyncio.to_thread(do_connect)
    except urllib.error.HTTPError as exc:
        logger.warning(
            "proxy_stream_http_error provider=%s proxy_key=%s status=%s upstream=%s",
            provider,
            proxy_key,
            exc.code,
            url,
        )
        error_body = exc.read()
        error_headers = dict(exc.headers.items())
        _record_error(
            provider=provider,
            proxy_key=proxy_key,
            subpath=subpath,
            request_json=request_json,
            status_code=exc.code,
            response_body=error_body,
            response_headers=error_headers,
            usage_service=usage_service,
        )
        return Response(
            content=error_body,
            status_code=exc.code,
            headers={
                key: value
                for key, value in error_headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            },
            media_type=exc.headers.get("Content-Type"),
        )
    except urllib.error.URLError as exc:
        logger.exception(
            "proxy_stream_upstream_error provider=%s proxy_key=%s upstream=%s reason=%s",
            provider,
            proxy_key,
            url,
            exc.reason,
        )
        _record_error(
            provider=provider,
            proxy_key=proxy_key,
            subpath=subpath,
            request_json=request_json,
            status_code=502,
            response_body=None,
            response_headers=None,
            usage_service=usage_service,
            error_detail=str(exc.reason),
        )
        raise HTTPException(status_code=502, detail=f"upstream request failed: {exc.reason}")

    filtered_headers = {
        key: value
        for key, value in dict(upstream_resp.headers.items()).items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    media_type = filtered_headers.get("Content-Type", filtered_headers.get("content-type"))
    state = StreamUsageState(provider=provider)
    started = perf_counter()

    async def async_iterator():
        nonlocal state
        data_lines: list[str] = []
        stream_status = "success"
        stream_error_code: str | None = None
        try:
            while True:
                raw_line = await asyncio.to_thread(upstream_resp.readline)
                if not raw_line:
                    break
                yield raw_line

                line = raw_line.decode("utf-8", errors="ignore").rstrip("\r\n")
                if not line:
                    if data_lines:
                        data = "\n".join(data_lines)
                        data_lines.clear()
                        if data != "[DONE]":
                            try:
                                state.consume_event(json.loads(data))
                            except json.JSONDecodeError:
                                pass
                    continue

                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        except Exception:
            stream_status = "interrupted"
            stream_error_code = "stream_interrupted"
            logger.exception(
                "proxy_stream_interrupted provider=%s proxy_key=%s upstream=%s",
                provider,
                proxy_key,
                url,
            )
            raise
        finally:
            if data_lines:
                data = "\n".join(data_lines)
                if data != "[DONE]":
                    try:
                        state.consume_event(json.loads(data))
                    except json.JSONDecodeError:
                        pass
            latency_ms = int((perf_counter() - started) * 1000)
            # Estimate output tokens saved from token saver output prompt
            final_output_saved = output_tokens_saved
            if output_level != "off" and state.output_tokens > 0:
                final_output_saved = TokenSaverService.estimate_output_savings(
                    state.output_tokens, output_level
                )
            usage_payload = _build_stream_usage_payload(
                provider=provider,
                upstream_path=f"/{subpath.lstrip('/')}",
                request_json=request_json,
                state=state,
                status=stream_status,
                latency_ms=latency_ms,
                error_code=stream_error_code,
                input_tokens_saved=input_tokens_saved,
                output_tokens_saved=final_output_saved,
            )
            if usage_payload:
                try:
                    usage_service.create_record(usage_payload)
                except Exception:
                    logger.exception(
                        "proxy_stream_usage_record_failed provider=%s proxy_key=%s model=%s",
                        provider,
                        proxy_key,
                        usage_payload.model,
                    )
            logger.info(
                "proxy_stream_finish provider=%s proxy_key=%s status_code=%s latency_ms=%s %s",
                provider,
                proxy_key,
                getattr(upstream_resp, "status", 200),
                latency_ms,
                " ".join(f"{key}={value}" for key, value in _usage_log_fields(usage_payload).items()),
            )
            upstream_resp.close()

    return StreamingResponse(
        async_iterator(),
        status_code=getattr(upstream_resp, "status", 200),
        headers=filtered_headers,
        media_type=media_type,
    )


async def proxy_request(
    provider: str,
    proxy_key: str,
    subpath: str,
    request: Request,
    usage_service: UsageService,
) -> Response:
    catalog = load_proxy_catalog()
    config = catalog.get((provider.lower(), proxy_key))
    if not config or not config.enabled:
        logger.warning(
            "proxy_config_missing_or_disabled provider=%s proxy_key=%s path=%s",
            provider,
            proxy_key,
            subpath,
        )
        raise HTTPException(status_code=404, detail="proxy provider config not found or disabled")

    body = await request.body()
    request_json = await _read_request_json(request, body)
    body, request_json = _prepare_request_body(provider.lower(), request_json, body)

    # Token saver processing
    saver_service = TokenSaverService()
    saver_config = TokenSaverConfig(
        enabled=config.token_saver_enabled,
        input_level=config.token_saver_input_level,
        output_level=config.token_saver_output_level,
    )
    saved_body, saved_request_json, saver_stats = saver_service.process_request(
        request_json, saver_config, provider=provider.lower()
    )
    if saved_body and saved_request_json:
        body = saved_body
        request_json = saved_request_json
        logger.info(
            "token_saver_applied provider=%s proxy_key=%s input_saved=%d output_level=%s",
            provider,
            proxy_key,
            saver_stats.input_tokens_saved,
            saver_stats.output_level,
        )

    upstream_url = _join_url(config.base_url, subpath, request.url.query)
    headers = _copy_headers(request.headers)
    headers.pop("Content-Length", None)
    headers.pop("content-length", None)

    if config.forward_user_auth:
        user_auth = headers.pop("Authorization", None) or headers.pop("authorization", None)
        if user_auth:
            headers[config.auth_header] = user_auth.removeprefix("Bearer ")
        else:
            user_auth = headers.pop("x-api-key", None) or headers.pop("X-Api-Key", None)
            if user_auth:
                headers[config.auth_header] = user_auth
    else:
        headers.pop("Authorization", None)
        headers.pop("authorization", None)
        headers.pop("x-api-key", None)
        headers.pop("X-Api-Key", None)

        if config.api_key_env:
            api_key = os.getenv(config.api_key_env)
            if not api_key:
                raise HTTPException(
                    status_code=500,
                    detail=f"missing upstream api key env: {config.api_key_env}",
                )
            if api_key:
                prefix = f"{config.api_key_prefix} " if config.api_key_prefix else ""
                headers[config.auth_header] = f"{prefix}{api_key}"

    for key, value in config.static_headers.items():
        headers[key] = value

    if request_json and request_json.get("stream") is True:
        return await _stream_upstream_request(
            provider=provider.lower(),
            proxy_key=proxy_key,
            method=request.method,
            url=upstream_url,
            body=body,
            headers=headers,
            timeout_seconds=config.timeout_seconds,
            request_json=request_json,
            subpath=subpath,
            usage_service=usage_service,
            ssl_verify=config.ssl_verify,
            input_tokens_saved=saver_stats.input_tokens_saved,
            output_tokens_saved=0,
            output_level=saver_stats.output_level,
        )

    logger.info(
        "proxy_request_start provider=%s proxy_key=%s method=%s upstream=%s model=%s stream=%s",
        provider,
        proxy_key,
        request.method,
        upstream_url,
        (request_json or {}).get("model"),
        bool((request_json or {}).get("stream")),
    )
    started = perf_counter()
    status_code, upstream_headers, upstream_body = await _send_upstream_request(
        method=request.method,
        url=upstream_url,
        body=body,
        headers=headers,
        timeout_seconds=config.timeout_seconds,
        ssl_verify=config.ssl_verify,
    )
    latency_ms = int((perf_counter() - started) * 1000)
    response_json = _read_response_json(upstream_headers, upstream_body)

    # Estimate output tokens saved based on token saver output level
    output_tokens_saved_est = 0
    if saver_stats.output_level != "off" and response_json:
        raw_output = 0
        if provider.lower() == "openai":
            raw_output = int(
                ((response_json or {}).get("usage") or {}).get("completion_tokens")
                or ((response_json or {}).get("usage") or {}).get("output_tokens")
                or 0
            )
        elif provider.lower() == "anthropic":
            raw_output = int(((response_json or {}).get("usage") or {}).get("output_tokens") or 0)
        if raw_output > 0:
            output_tokens_saved_est = TokenSaverService.estimate_output_savings(
                raw_output, saver_stats.output_level
            )

    usage_payload = _extract_usage_payload(
        provider=provider.lower(),
        upstream_path=f"/{subpath.lstrip('/')}",
        request_json=request_json,
        response_json=response_json,
        status_code=status_code,
        latency_ms=latency_ms,
        input_tokens_saved=saver_stats.input_tokens_saved,
        output_tokens_saved=output_tokens_saved_est,
    )
    if usage_payload:
        try:
            usage_service.create_record(usage_payload)
        except Exception:
            logger.exception(
                "proxy_usage_record_failed provider=%s proxy_key=%s model=%s",
                provider,
                proxy_key,
                usage_payload.model,
            )

    logger.info(
        "proxy_request_finish provider=%s proxy_key=%s status_code=%s latency_ms=%s %s",
        provider,
        proxy_key,
        status_code,
        latency_ms,
        " ".join(f"{key}={value}" for key, value in _usage_log_fields(usage_payload).items()),
    )

    filtered_headers = {
        key: value
        for key, value in upstream_headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    media_type = filtered_headers.get("Content-Type", filtered_headers.get("content-type"))
    return Response(
        content=upstream_body,
        status_code=status_code,
        headers=filtered_headers,
        media_type=media_type,
    )
