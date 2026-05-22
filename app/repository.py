from __future__ import annotations

from typing import Any

from app.database import get_connection
from app.models import UsageRecord


class UsageRepository:
    def insert(self, record: UsageRecord) -> int:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO llm_usage_log (
                    trace_id, request_id, biz_key, provider, model, endpoint, request_type,
                    user_id, tenant_id, latency_ms, status, error_code,
                    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                    reasoning_tokens, total_tokens, raw_usage,
                    input_tokens_saved, output_tokens_saved, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trace_id,
                    record.request_id,
                    record.biz_key,
                    record.provider,
                    record.model,
                    record.endpoint,
                    record.request_type,
                    record.user_id,
                    record.tenant_id,
                    record.latency_ms,
                    record.status,
                    record.error_code,
                    record.input_tokens,
                    record.output_tokens,
                    record.cache_creation_tokens,
                    record.cache_read_tokens,
                    record.reasoning_tokens,
                    record.total_tokens,
                    record.raw_usage,
                    record.input_tokens_saved,
                    record.output_tokens_saved,
                    record.created_at.isoformat(),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_usage(
        self,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT id, provider, model, user_id, tenant_id, status,
                   input_tokens, output_tokens,
                   cache_creation_tokens, cache_read_tokens,
                   total_tokens, input_tokens_saved, output_tokens_saved,
                   latency_ms, created_at
            FROM llm_usage_log
            WHERE 1 = 1
        """
        params: list[Any] = []
        sql, params = self._append_filters(
            sql, params, provider, model, tenant_id, user_id, start_date, end_date
        )
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        with get_connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def summary(
        self,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        sql = """
            SELECT COUNT(*) AS request_count,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS failed_count,
                   COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(input_tokens_saved), 0) AS total_input_tokens_saved,
                   COALESCE(SUM(output_tokens_saved), 0) AS total_output_tokens_saved
            FROM llm_usage_log
            WHERE 1 = 1
        """
        params: list[Any] = []
        sql, params = self._append_filters(
            sql, params, provider, model, tenant_id, user_id, start_date, end_date
        )

        with get_connection() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row else {}

    def daily_stats(
        self,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT substr(created_at, 1, 10) AS stat_date,
                   COUNT(*) AS request_count,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM llm_usage_log
            WHERE 1 = 1
        """
        params: list[Any] = []
        sql, params = self._append_filters(
            sql, params, provider, model, tenant_id, user_id, start_date, end_date
        )
        sql += " GROUP BY substr(created_at, 1, 10) ORDER BY stat_date ASC"

        with get_connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _append_filters(
        self,
        sql: str,
        params: list[Any],
        provider: str | None,
        model: str | None,
        tenant_id: str | None,
        user_id: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[str, list[Any]]:
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        if model:
            sql += " AND model = ?"
            params.append(model)
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if start_date:
            sql += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            # When a bare date (no time) is passed, treat it as end-of-day
            # by comparing with the next day so records with timestamps match
            if "T" not in end_date and " " not in end_date:
                from datetime import datetime, timedelta
                dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                sql += " AND created_at < ?"
                params.append(dt.isoformat())
            else:
                sql += " AND created_at <= ?"
                params.append(end_date)
        return sql, params
