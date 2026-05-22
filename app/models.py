from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UsageRecord:
    trace_id: str | None
    request_id: str | None
    biz_key: str | None
    provider: str
    model: str
    endpoint: str | None
    request_type: str | None
    user_id: str | None
    tenant_id: str | None
    latency_ms: int
    status: str
    error_code: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    total_tokens: int
    raw_usage: str | None
    created_at: datetime
    input_tokens_saved: int = 0
    output_tokens_saved: int = 0
