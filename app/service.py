from __future__ import annotations

import json
import os
from datetime import datetime

from fastapi import HTTPException

from app.config import (
    ProxyProviderConfig,
    load_proxy_catalog,
    reload_proxy_catalog,
    save_proxy_catalog,
)
from app.models import UsageRecord
from app.repository import UsageRepository
from app.schemas import UsageRecordRequest


class UsageService:
    def __init__(self, repository: UsageRepository | None = None) -> None:
        self.repository = repository or UsageRepository()

    def create_record(
        self,
        payload: UsageRecordRequest,
    ) -> tuple[int, UsageRecord]:
        record = self.prepare_record(payload)
        return self.repository.insert(record), record

    def prepare_record(self, payload: UsageRecordRequest) -> UsageRecord:
        total_tokens = payload.total_tokens
        if total_tokens is None:
            total_tokens = (
                payload.input_tokens
                + payload.output_tokens
                + payload.cache_creation_tokens
                + payload.cache_read_tokens
                + payload.reasoning_tokens
            )

        return UsageRecord(
            trace_id=payload.trace_id,
            request_id=payload.request_id,
            biz_key=payload.biz_key,
            provider=payload.provider,
            model=payload.model,
            endpoint=payload.endpoint,
            request_type=payload.request_type,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
            latency_ms=payload.latency_ms,
            status=payload.status,
            error_code=payload.error_code,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            cache_creation_tokens=payload.cache_creation_tokens,
            cache_read_tokens=payload.cache_read_tokens,
            reasoning_tokens=payload.reasoning_tokens,
            total_tokens=total_tokens,
            raw_usage=json.dumps(payload.raw_usage, ensure_ascii=False)
            if payload.raw_usage
            else None,
            input_tokens_saved=payload.input_tokens_saved,
            output_tokens_saved=payload.output_tokens_saved,
            created_at=payload.created_at or datetime.utcnow(),
        )


class ProxyCatalogService:
    @staticmethod
    def list_configs(proxy_base_root: str) -> list[dict]:
        catalog = load_proxy_catalog()
        result: list[dict] = []
        for item in catalog.values():
            result.append(
                {
                    **item.model_dump(),
                    "proxy_base_url": f"{proxy_base_root}/proxy/{item.provider}/{item.proxy_key}",
                }
            )
        return sorted(result, key=lambda item: (item["provider"], item["proxy_key"]))

    @staticmethod
    def get_config(provider: str, proxy_key: str, proxy_base_root: str) -> dict:
        catalog = load_proxy_catalog()
        item = catalog.get((provider.lower(), proxy_key))
        if not item:
            raise HTTPException(status_code=404, detail="proxy config not found")
        return {
            **item.model_dump(),
            "proxy_base_url": f"{proxy_base_root}/proxy/{item.provider}/{item.proxy_key}",
        }

    @staticmethod
    def reload(proxy_base_root: str) -> list[dict]:
        catalog = reload_proxy_catalog()
        return [
            {
                **item.model_dump(),
                "proxy_base_url": f"{proxy_base_root}/proxy/{item.provider}/{item.proxy_key}",
            }
            for item in catalog.values()
        ]

    @staticmethod
    def upsert_config(
        provider: str,
        proxy_key: str,
        payload: dict,
        proxy_base_root: str,
    ) -> dict:
        catalog = load_proxy_catalog()
        normalized = ProxyProviderConfig.model_validate(
            {
                **payload,
                "provider": provider,
                "proxy_key": proxy_key,
                "display_name": payload.get("display_name") or proxy_key,
            }
        )
        catalog[(provider.lower(), proxy_key)] = normalized
        latest = save_proxy_catalog(catalog)
        item = latest[(provider.lower(), proxy_key)]
        return {
            **item.model_dump(),
            "proxy_base_url": f"{proxy_base_root}/proxy/{item.provider}/{item.proxy_key}",
        }

    @staticmethod
    def delete_config(provider: str, proxy_key: str) -> None:
        catalog = load_proxy_catalog()
        key = (provider.lower(), proxy_key)
        if key not in catalog:
            raise HTTPException(status_code=404, detail="proxy config not found")
        del catalog[key]
        save_proxy_catalog(catalog)

    @staticmethod
    def resolve_api_key(provider: str, proxy_key: str) -> str | None:
        catalog = load_proxy_catalog()
        item = catalog.get((provider.lower(), proxy_key))
        if not item:
            raise HTTPException(status_code=404, detail="proxy config not found")
        if not item.api_key_env:
            return None
        return os.getenv(item.api_key_env)
