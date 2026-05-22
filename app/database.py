from __future__ import annotations

import sqlite3

from app.config import DB_PATH, DATA_DIR


LLM_USAGE_LOG_COLUMNS = (
    "id",
    "trace_id",
    "request_id",
    "biz_key",
    "provider",
    "model",
    "endpoint",
    "request_type",
    "user_id",
    "tenant_id",
    "latency_ms",
    "status",
    "error_code",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
    "total_tokens",
    "raw_usage",
    "input_tokens_saved",
    "output_tokens_saved",
    "created_at",
)


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _create_llm_usage_log_table(connection: sqlite3.Connection, table_name: str) -> None:
    connection.execute(
        f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT,
            request_id TEXT,
            biz_key TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            endpoint TEXT,
            request_type TEXT,
            user_id TEXT,
            tenant_id TEXT,
            latency_ms INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            error_code TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            raw_usage TEXT,
            input_tokens_saved INTEGER DEFAULT 0,
            output_tokens_saved INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def _current_columns(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("PRAGMA table_info(llm_usage_log)").fetchall()
    return [row["name"] for row in rows]


def _migrate_llm_usage_log_table(connection: sqlite3.Connection) -> None:
    current_columns = _current_columns(connection)
    if not current_columns:
        _create_llm_usage_log_table(connection, "llm_usage_log")
        return
    if tuple(current_columns) == LLM_USAGE_LOG_COLUMNS:
        return

    current_set = set(current_columns)
    migrated_table = "llm_usage_log_migrated"
    _create_llm_usage_log_table(connection, migrated_table)

    # Build column list: use existing columns from old table, default 0 for new ones
    select_exprs = []
    for col in LLM_USAGE_LOG_COLUMNS:
        if col == "id":
            select_exprs.append(col)
        elif col in current_set:
            select_exprs.append(col)
        else:
            select_exprs.append("0")

    column_list = ", ".join(LLM_USAGE_LOG_COLUMNS)
    select_list = ", ".join(select_exprs)
    connection.execute(
        f"""
        INSERT INTO {migrated_table} ({column_list})
        SELECT {select_list}
        FROM llm_usage_log
        """
    )
    connection.execute("DROP TABLE llm_usage_log")
    connection.execute(f"ALTER TABLE {migrated_table} RENAME TO llm_usage_log")


def init_db() -> None:
    with get_connection() as connection:
        _migrate_llm_usage_log_table(connection)
        connection.commit()
