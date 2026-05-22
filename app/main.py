from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.proxy import proxy_request
from app.repository import UsageRepository
from app.schemas import (
    DailyStatsItem,
    ProxyConfigResponse,
    ProxyConfigUpdateRequest,
    SummaryResponse,
    UsageListItem,
    UsageRecordRequest,
    UsageRecordResponse,
)
from app.service import ProxyCatalogService, UsageService


app = FastAPI(
    title="LLM Usage Service",
    version="0.1.0",
    description="OpenAI / Anthropic token usage statistics service",
)

repository = UsageRepository()
usage_service = UsageService(repository=repository)
proxy_service = ProxyCatalogService()
BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
WEB_PAGES_DIR = WEB_DIR / "pages"
WEB_STATIC_DIR = WEB_DIR / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app.mount("/static", StaticFiles(directory=WEB_STATIC_DIR), name="static")


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(WEB_PAGES_DIR / "dashboard.html")


@app.get("/proxy-config")
def proxy_config_page() -> FileResponse:
    return FileResponse(WEB_PAGES_DIR / "proxy-config.html")


@app.post("/api/v1/usage/record", response_model=UsageRecordResponse)
def create_usage_record(payload: UsageRecordRequest) -> UsageRecordResponse:
    record_id, record = usage_service.create_record(payload)
    return UsageRecordResponse(
        id=record_id,
        provider=payload.provider,
        model=payload.model,
        total_tokens=record.total_tokens,
        status=payload.status,
        created_at=record.created_at,
    )


@app.get("/api/v1/usage/list", response_model=list[UsageListItem])
def list_usage(
    provider: str | None = None,
    model: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[UsageListItem]:
    rows = repository.list_usage(
        provider=provider,
        model=model,
        tenant_id=tenant_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return [UsageListItem.model_validate(row) for row in rows]


@app.get("/api/v1/stats/summary", response_model=SummaryResponse)
def stats_summary(
    provider: str | None = None,
    model: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> SummaryResponse:
    row = repository.summary(
        provider=provider,
        model=model,
        tenant_id=tenant_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
    )
    return SummaryResponse(
        request_count=row.get("request_count", 0),
        success_count=row.get("success_count", 0),
        failed_count=row.get("failed_count", 0),
        total_input_tokens=row.get("total_input_tokens", 0),
        total_output_tokens=row.get("total_output_tokens", 0),
        total_tokens=row.get("total_tokens", 0),
        total_input_tokens_saved=row.get("total_input_tokens_saved", 0),
        total_output_tokens_saved=row.get("total_output_tokens_saved", 0),
    )


@app.get("/api/v1/stats/daily", response_model=list[DailyStatsItem])
def stats_daily(
    provider: str | None = None,
    model: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[DailyStatsItem]:
    rows = repository.daily_stats(
        provider=provider,
        model=model,
        tenant_id=tenant_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
    )
    return [
        DailyStatsItem(
            stat_date=row["stat_date"],
            request_count=row["request_count"],
            total_tokens=row["total_tokens"],
        )
        for row in rows
    ]


@app.get("/api/v1/proxy/configs", response_model=list[ProxyConfigResponse])
def list_proxy_configs(request: Request) -> list[ProxyConfigResponse]:
    proxy_base_root = str(request.base_url).rstrip("/")
    return [
        ProxyConfigResponse.model_validate(item)
        for item in proxy_service.list_configs(proxy_base_root)
    ]


@app.get("/api/v1/proxy/configs/{provider}/{proxy_key}", response_model=ProxyConfigResponse)
def get_proxy_config(provider: str, proxy_key: str, request: Request) -> ProxyConfigResponse:
    proxy_base_root = str(request.base_url).rstrip("/")
    return ProxyConfigResponse.model_validate(
        proxy_service.get_config(provider, proxy_key, proxy_base_root)
    )


@app.post("/api/v1/proxy/configs/reload", response_model=list[ProxyConfigResponse])
def reload_proxy_configs(request: Request) -> list[ProxyConfigResponse]:
    proxy_base_root = str(request.base_url).rstrip("/")
    return [
        ProxyConfigResponse.model_validate(item)
        for item in proxy_service.reload(proxy_base_root)
    ]


@app.put("/api/v1/proxy/configs/{provider}/{proxy_key}", response_model=ProxyConfigResponse)
def update_proxy_config(
    provider: str,
    proxy_key: str,
    payload: ProxyConfigUpdateRequest,
    request: Request,
) -> ProxyConfigResponse:
    proxy_base_root = str(request.base_url).rstrip("/")
    return ProxyConfigResponse.model_validate(
        proxy_service.upsert_config(provider, proxy_key, payload.model_dump(), proxy_base_root)
    )


@app.post("/api/v1/proxy/configs", response_model=ProxyConfigResponse)
def create_proxy_config(
    payload: ProxyConfigUpdateRequest,
    request: Request,
) -> ProxyConfigResponse:
    proxy_base_root = str(request.base_url).rstrip("/")
    return ProxyConfigResponse.model_validate(
        proxy_service.upsert_config(
            payload.provider,
            payload.proxy_key,
            payload.model_dump(),
            proxy_base_root,
        )
    )


@app.delete("/api/v1/proxy/configs/{provider}/{proxy_key}")
def delete_proxy_config(provider: str, proxy_key: str) -> dict:
    proxy_service.delete_config(provider, proxy_key)
    return {"status": "deleted", "provider": provider, "proxy_key": proxy_key}


@app.api_route(
    "/proxy/{provider}/{proxy_key}/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def provider_proxy(provider: str, proxy_key: str, subpath: str, request: Request):
    return await proxy_request(provider, proxy_key, subpath, request, usage_service)
