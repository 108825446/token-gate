from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ProviderType = Literal["openai", "anthropic"]
StatusType = Literal["success", "failed", "interrupted"]


class UsageRecordRequest(BaseModel):
    trace_id: str | None = None
    request_id: str | None = None
    biz_key: str | None = None
    provider: ProviderType
    model: str
    endpoint: str | None = None
    request_type: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    latency_ms: int = 0
    status: StatusType = "success"
    error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int | None = None
    raw_usage: dict[str, Any] | None = None
    input_tokens_saved: int = 0
    output_tokens_saved: int = 0
    created_at: datetime | None = None


class UsageRecordResponse(BaseModel):
    id: int
    provider: str
    model: str
    total_tokens: int
    status: str
    created_at: datetime


class UsageListItem(BaseModel):
    id: int
    provider: str
    model: str
    user_id: str | None
    tenant_id: str | None
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int
    input_tokens_saved: int = 0
    output_tokens_saved: int = 0
    latency_ms: int
    created_at: datetime


class SummaryResponse(BaseModel):
    request_count: int
    success_count: int
    failed_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_input_tokens_saved: int = 0
    total_output_tokens_saved: int = 0


class DailyStatsItem(BaseModel):
    stat_date: str
    request_count: int
    total_tokens: int


class ProxyConfigResponse(BaseModel):
    provider: str
    proxy_key: str
    display_name: str | None = None
    base_url: str
    auth_header: str
    api_key_env: str | None = None
    api_key_prefix: str
    forward_user_auth: bool
    timeout_seconds: int
    enabled: bool
    ssl_verify: bool = True
    static_headers: dict[str, str] = Field(default_factory=dict)
    token_saver_enabled: bool = False
    token_saver_input_level: str = "full"
    token_saver_output_level: str = "full"
    proxy_base_url: str


class ProxyConfigUpdateRequest(BaseModel):
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
