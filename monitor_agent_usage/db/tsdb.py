"""Time-Series Database engine supporting 15-minute interval bucketing and multi-user tracking."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import AccountRecord, SessionRecord, TokenUsagePoint, UserRecord


BUCKET_SECONDS = 900  # 15 minutes


def epoch_to_iso(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TimeSeriesDB:
    """Thread-safe Time-Series Database backed by SQLite (with WAL & 15m epoch indexing)."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    account_label TEXT NOT NULL,
                    auth_type TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    sync_cursor TEXT NOT NULL DEFAULT '',
                    last_sync_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    workspace_path TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    last_active_at TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS token_usage_ts (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    timestamp_epoch INTEGER NOT NULL,
                    bucket_15m_epoch INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    tokens_cached INTEGER NOT NULL DEFAULT 0,
                    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
                    turn_index INTEGER NOT NULL DEFAULT 0,
                    source_ref TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_ts_bucket_15m
                    ON token_usage_ts(bucket_15m_epoch, provider, interface, user_id);

                CREATE INDEX IF NOT EXISTS idx_ts_timestamp_epoch
                    ON token_usage_ts(timestamp_epoch);

                CREATE INDEX IF NOT EXISTS idx_ts_session_id
                    ON token_usage_ts(session_id, timestamp_epoch);

                CREATE INDEX IF NOT EXISTS idx_sessions_lookup
                    ON sessions(provider, interface, user_id, last_active_at);
                """
            )

    # ------------------------------------------------------------------
    # Users & Accounts Management
    # ------------------------------------------------------------------

    def upsert_user(self, user: UserRecord) -> UserRecord:
        now = user.created_at or epoch_to_iso(int(dt.datetime.now(dt.timezone.utc).timestamp()))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, display_name, email, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    email = CASE WHEN excluded.email != '' THEN excluded.email ELSE users.email END
                """,
                (user.user_id, user.username, user.display_name, user.email, now),
            )
        return user

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.*,
                       COUNT(DISTINCT a.account_id) AS account_count,
                       COALESCE(SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write), 0) AS total_tokens
                FROM users u
                LEFT JOIN accounts a ON a.user_id = u.user_id
                LEFT JOIN token_usage_ts t ON t.user_id = u.user_id
                GROUP BY u.user_id
                ORDER BY u.display_name ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_account(self, account: AccountRecord) -> AccountRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    account_id, user_id, provider, account_label, auth_type,
                    config_json, sync_cursor, last_sync_at, enabled
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    provider = excluded.provider,
                    account_label = excluded.account_label,
                    auth_type = excluded.auth_type,
                    config_json = CASE
                        WHEN excluded.config_json != '{}' THEN excluded.config_json
                        ELSE accounts.config_json
                    END,
                    enabled = excluded.enabled
                """,
                (
                    account.account_id,
                    account.user_id,
                    account.provider,
                    account.account_label,
                    account.auth_type,
                    json.dumps(account.config_json),
                    account.sync_cursor,
                    account.last_sync_at,
                    1 if account.enabled else 0,
                ),
            )
        return account

    def update_account_sync_state(
        self, account_id: str, sync_cursor: str, last_sync_at: Optional[str] = None
    ) -> None:
        ts = last_sync_at or epoch_to_iso(int(dt.datetime.now(dt.timezone.utc).timestamp()))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET sync_cursor = ?, last_sync_at = ?
                WHERE account_id = ?
                """,
                (sync_cursor, ts, account_id),
            )

    def get_accounts(
        self, user_id: Optional[str] = None, provider: Optional[str] = None, only_enabled: bool = False
    ) -> List[AccountRecord]:
        clauses: List[str] = []
        params: List[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if only_enabled:
            clauses.append("enabled = 1")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM accounts {where_sql} ORDER BY provider, account_label", params
            ).fetchall()
        result: List[AccountRecord] = []
        for r in rows:
            try:
                cfg = json.loads(r["config_json"] or "{}")
            except Exception:
                cfg = {}
            result.append(
                AccountRecord(
                    account_id=r["account_id"],
                    user_id=r["user_id"],
                    provider=r["provider"],
                    account_label=r["account_label"],
                    auth_type=r["auth_type"],
                    config_json=cfg,
                    sync_cursor=r["sync_cursor"] or "",
                    last_sync_at=r["last_sync_at"],
                    enabled=bool(r["enabled"]),
                )
            )
        return result

    def delete_account(self, account_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM token_usage_ts WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))

    # ------------------------------------------------------------------
    # Sessions & Time-Series Point Ingestion
    # ------------------------------------------------------------------

    def upsert_sessions(self, sessions: Iterable[SessionRecord]) -> int:
        batch = list(sessions)
        if not batch:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO sessions (
                    session_id, user_id, account_id, provider, interface,
                    session_name, workspace_path, model, started_at, last_active_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    interface = excluded.interface,
                    session_name = CASE
                        WHEN excluded.session_name != '' AND excluded.session_name != 'Untitled Session'
                        THEN excluded.session_name
                        ELSE sessions.session_name
                    END,
                    workspace_path = CASE
                        WHEN excluded.workspace_path != '' THEN excluded.workspace_path
                        ELSE sessions.workspace_path
                    END,
                    model = CASE
                        WHEN excluded.model != '' THEN excluded.model
                        ELSE sessions.model
                    END,
                    started_at = CASE
                        WHEN sessions.started_at = '' OR (excluded.started_at != '' AND excluded.started_at < sessions.started_at)
                        THEN excluded.started_at
                        ELSE sessions.started_at
                    END,
                    last_active_at = CASE
                        WHEN excluded.last_active_at > sessions.last_active_at
                        THEN excluded.last_active_at
                        ELSE sessions.last_active_at
                    END,
                    metadata_json = excluded.metadata_json
                """,
                [
                    (
                        s.session_id,
                        s.user_id,
                        s.account_id,
                        s.provider,
                        s.interface,
                        s.session_name,
                        s.workspace_path,
                        s.model,
                        s.started_at,
                        s.last_active_at,
                        json.dumps(s.metadata_json),
                    )
                    for s in batch
                ],
            )
        return len(batch)

    def upsert_usage_points(self, points: Iterable[TokenUsagePoint]) -> int:
        batch = list(points)
        if not batch:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO token_usage_ts (
                    event_id, timestamp, timestamp_epoch, bucket_15m_epoch,
                    user_id, account_id, provider, interface, session_id, session_name,
                    model, tokens_in, tokens_out, tokens_cached, tokens_cache_write,
                    turn_index, source_ref
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    timestamp_epoch = excluded.timestamp_epoch,
                    bucket_15m_epoch = excluded.bucket_15m_epoch,
                    interface = excluded.interface,
                    session_name = excluded.session_name,
                    model = excluded.model,
                    tokens_in = excluded.tokens_in,
                    tokens_out = excluded.tokens_out,
                    tokens_cached = excluded.tokens_cached,
                    tokens_cache_write = excluded.tokens_cache_write
                """,
                [
                    (
                        p.event_id,
                        p.timestamp,
                        p.timestamp_epoch,
                        p.bucket_15m_epoch,
                        p.user_id,
                        p.account_id,
                        p.provider,
                        p.interface,
                        p.session_id,
                        p.session_name,
                        p.model,
                        int(p.tokens_in),
                        int(p.tokens_out),
                        int(p.tokens_cached),
                        int(p.tokens_cache_write),
                        int(p.turn_index),
                        p.source_ref,
                    )
                    for p in batch
                ],
            )
        return len(batch)

    # ------------------------------------------------------------------
    # Time-Series Queries (15-Minute Buckets & Session Filtering)
    # ------------------------------------------------------------------

    def _build_filter_clause(
        self,
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
        bucket_15m_epoch: Optional[int] = None,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        interface: Optional[str] = None,
        table_alias: str = "t",
    ) -> tuple[str, List[Any]]:
        prefix = f"{table_alias}." if table_alias else ""
        clauses: List[str] = []
        params: List[Any] = []

        if bucket_15m_epoch is not None:
            clauses.append(f"{prefix}bucket_15m_epoch = ?")
            params.append(int(bucket_15m_epoch))
        else:
            if start_epoch is not None:
                clauses.append(f"{prefix}timestamp_epoch >= ?")
                params.append(int(start_epoch))
            if end_epoch is not None:
                clauses.append(f"{prefix}timestamp_epoch <= ?")
                params.append(int(end_epoch))

        if user_id and user_id.lower() != "all":
            clauses.append(f"{prefix}user_id = ?")
            params.append(user_id)
        if provider and provider.lower() != "all":
            clauses.append(f"LOWER({prefix}provider) = LOWER(?)")
            params.append(provider)
        if interface and interface.lower() != "all":
            clauses.append(f"LOWER({prefix}interface) = LOWER(?)")
            params.append(interface)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def get_time_bounds(self) -> Dict[str, Optional[int]]:
        """Return earliest and latest timestamp_epoch in the database."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(timestamp_epoch) AS min_epoch,
                       MAX(timestamp_epoch) AS max_epoch,
                       COUNT(*) AS event_count
                FROM token_usage_ts
                """
            ).fetchone()
        return {
            "min_epoch": row["min_epoch"] if row else None,
            "max_epoch": row["max_epoch"] if row else None,
            "event_count": row["event_count"] if row else 0,
        }

    def query_timeseries_15m(
        self,
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        interface: Optional[str] = None,
        fill_empty_buckets: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return 15-minute bucketized token usage time-series."""
        where_sql, params = self._build_filter_clause(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            user_id=user_id,
            provider=provider,
            interface=interface,
            table_alias="t",
        )

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.bucket_15m_epoch,
                    SUM(t.tokens_in) AS tokens_in,
                    SUM(t.tokens_out) AS tokens_out,
                    SUM(t.tokens_cached) AS tokens_cached,
                    SUM(t.tokens_cache_write) AS tokens_cache_write,
                    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
                    COUNT(DISTINCT t.session_id) AS session_count,
                    COUNT(*) AS turn_count
                FROM token_usage_ts t
                {where_sql}
                GROUP BY t.bucket_15m_epoch
                ORDER BY t.bucket_15m_epoch ASC
                """,
                params,
            ).fetchall()

            # Also fetch provider breakdown per bucket for stacked tooltip insights
            provider_rows = conn.execute(
                f"""
                SELECT
                    t.bucket_15m_epoch,
                    t.provider,
                    SUM(t.tokens_in) AS tokens_in,
                    SUM(t.tokens_out) AS tokens_out,
                    SUM(t.tokens_cached) AS tokens_cached,
                    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens
                FROM token_usage_ts t
                {where_sql}
                GROUP BY t.bucket_15m_epoch, t.provider
                ORDER BY t.bucket_15m_epoch ASC
                """,
                params,
            ).fetchall()

        by_bucket_providers: Dict[int, Dict[str, Any]] = {}
        for pr in provider_rows:
            b_epoch = int(pr["bucket_15m_epoch"])
            by_bucket_providers.setdefault(b_epoch, {})[pr["provider"]] = {
                "tokens_in": int(pr["tokens_in"]),
                "tokens_out": int(pr["tokens_out"]),
                "tokens_cached": int(pr["tokens_cached"]),
                "total_tokens": int(pr["total_tokens"]),
            }

        bucket_map: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            b_epoch = int(r["bucket_15m_epoch"])
            t_in = int(r["tokens_in"] or 0)
            t_out = int(r["tokens_out"] or 0)
            t_cached = int(r["tokens_cached"] or 0)
            t_cwrite = int(r["tokens_cache_write"] or 0)
            total = int(r["total_tokens"] or 0)
            denom = t_in + t_cached + t_cwrite
            cache_pct = round((t_cached / denom) * 100.0, 1) if denom > 0 else 0.0
            bucket_map[b_epoch] = {
                "bucket_epoch": b_epoch,
                "bucket_end_epoch": b_epoch + BUCKET_SECONDS,
                "bucket_start_iso": epoch_to_iso(b_epoch),
                "bucket_end_iso": epoch_to_iso(b_epoch + BUCKET_SECONDS),
                "tokens_in": t_in,
                "tokens_out": t_out,
                "tokens_cached": t_cached,
                "tokens_cache_write": t_cwrite,
                "total_tokens": total,
                "cache_hit_pct": cache_pct,
                "session_count": int(r["session_count"] or 0),
                "turn_count": int(r["turn_count"] or 0),
                "by_provider": by_bucket_providers.get(b_epoch, {}),
            }

        if not fill_empty_buckets or not bucket_map:
            return list(bucket_map.values())

        # Determine bucket range to fill continuous 15m grid (capped to max 672 buckets = 7 days)
        min_b = (
            (start_epoch // BUCKET_SECONDS) * BUCKET_SECONDS
            if start_epoch is not None
            else min(bucket_map.keys())
        )
        max_b = (
            (end_epoch // BUCKET_SECONDS) * BUCKET_SECONDS
            if end_epoch is not None
            else max(bucket_map.keys())
        )
        total_slots = (max_b - min_b) // BUCKET_SECONDS + 1
        if total_slots > 384:
            # For windows > 4 days, only fill between min and max active data or return sparse if very wide
            min_b = min(bucket_map.keys())
            max_b = max(bucket_map.keys())
            total_slots = (max_b - min_b) // BUCKET_SECONDS + 1
            if total_slots > 672:
                return list(bucket_map.values())

        filled: List[Dict[str, Any]] = []
        curr = min_b
        while curr <= max_b:
            if curr in bucket_map:
                filled.append(bucket_map[curr])
            else:
                filled.append(
                    {
                        "bucket_epoch": curr,
                        "bucket_end_epoch": curr + BUCKET_SECONDS,
                        "bucket_start_iso": epoch_to_iso(curr),
                        "bucket_end_iso": epoch_to_iso(curr + BUCKET_SECONDS),
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "tokens_cached": 0,
                        "tokens_cache_write": 0,
                        "total_tokens": 0,
                        "cache_hit_pct": 0.0,
                        "session_count": 0,
                        "turn_count": 0,
                        "by_provider": {},
                    }
                )
            curr += BUCKET_SECONDS
        return filled

    def query_sessions(
        self,
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
        bucket_15m_epoch: Optional[int] = None,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        interface: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query chat sessions and their token consumption for the selected time window or 15m block."""
        where_sql, params = self._build_filter_clause(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            bucket_15m_epoch=bucket_15m_epoch,
            user_id=user_id,
            provider=provider,
            interface=interface,
            table_alias="t",
        )
        if search and search.strip():
            q = f"%{search.strip()}%"
            if where_sql:
                where_sql += " AND (t.session_name LIKE ? OR t.session_id LIKE ? OR t.model LIKE ? OR s.workspace_path LIKE ?)"
            else:
                where_sql = "WHERE (t.session_name LIKE ? OR t.session_id LIKE ? OR t.model LIKE ? OR s.workspace_path LIKE ?)"
            params.extend([q, q, q, q])

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.session_id,
                    COALESCE(NULLIF(s.session_name, ''), t.session_name, t.session_id) AS session_name,
                    t.user_id,
                    COALESCE(u.display_name, u.username, t.user_id) AS user_display_name,
                    t.account_id,
                    COALESCE(a.account_label, t.account_id) AS account_label,
                    t.provider,
                    t.interface,
                    COALESCE(NULLIF(MAX(t.model), ''), s.model, 'default') AS model,
                    COALESCE(s.workspace_path, '') AS workspace_path,
                    SUM(t.tokens_in) AS tokens_in,
                    SUM(t.tokens_out) AS tokens_out,
                    SUM(t.tokens_cached) AS tokens_cached,
                    SUM(t.tokens_cache_write) AS tokens_cache_write,
                    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
                    COUNT(*) AS turn_count,
                    MIN(t.timestamp) AS first_event_at,
                    MAX(t.timestamp) AS last_event_at,
                    MAX(t.timestamp_epoch) AS last_event_epoch
                FROM token_usage_ts t
                LEFT JOIN sessions s ON s.session_id = t.session_id
                LEFT JOIN users u ON u.user_id = t.user_id
                LEFT JOIN accounts a ON a.account_id = t.account_id
                {where_sql}
                GROUP BY t.session_id
                ORDER BY last_event_epoch DESC, total_tokens DESC
                """,
                params,
            ).fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            t_in = int(r["tokens_in"] or 0)
            t_out = int(r["tokens_out"] or 0)
            t_cached = int(r["tokens_cached"] or 0)
            t_cwrite = int(r["tokens_cache_write"] or 0)
            total = int(r["total_tokens"] or 0)
            denom = t_in + t_cached + t_cwrite
            cache_pct = round((t_cached / denom) * 100.0, 1) if denom > 0 else 0.0
            results.append(
                {
                    "session_id": r["session_id"],
                    "session_name": r["session_name"],
                    "user_id": r["user_id"],
                    "user_display_name": r["user_display_name"],
                    "account_id": r["account_id"],
                    "account_label": r["account_label"],
                    "provider": r["provider"],
                    "interface": r["interface"],
                    "model": r["model"],
                    "workspace_path": r["workspace_path"],
                    "tokens_in": t_in,
                    "tokens_out": t_out,
                    "tokens_cached": t_cached,
                    "tokens_cache_write": t_cwrite,
                    "total_tokens": total,
                    "cache_hit_pct": cache_pct,
                    "turn_count": int(r["turn_count"] or 0),
                    "first_event_at": r["first_event_at"],
                    "last_event_at": r["last_event_at"],
                    "last_event_epoch": int(r["last_event_epoch"] or 0),
                }
            )
        return results

    def query_session_turns(
        self,
        session_id: str,
        bucket_15m_epoch: Optional[int] = None,
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return individual turn token events for a specific chat session."""
        where_sql, params = self._build_filter_clause(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            bucket_15m_epoch=bucket_15m_epoch,
            table_alias="t",
        )
        if where_sql:
            where_sql += " AND t.session_id = ?"
        else:
            where_sql = "WHERE t.session_id = ?"
        params.append(session_id)

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.event_id,
                    t.timestamp,
                    t.timestamp_epoch,
                    t.bucket_15m_epoch,
                    t.provider,
                    t.interface,
                    t.model,
                    t.tokens_in,
                    t.tokens_out,
                    t.tokens_cached,
                    t.tokens_cache_write,
                    (t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
                    t.turn_index
                FROM token_usage_ts t
                {where_sql}
                ORDER BY t.timestamp_epoch ASC, t.turn_index ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def query_summary_kpis(
        self,
        start_epoch: Optional[int] = None,
        end_epoch: Optional[int] = None,
        bucket_15m_epoch: Optional[int] = None,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        interface: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute aggregate KPI metrics across the filtered time range or 15m bucket."""
        where_sql, params = self._build_filter_clause(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            bucket_15m_epoch=bucket_15m_epoch,
            user_id=user_id,
            provider=provider,
            interface=interface,
            table_alias="t",
        )
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(t.tokens_in), 0) AS tokens_in,
                    COALESCE(SUM(t.tokens_out), 0) AS tokens_out,
                    COALESCE(SUM(t.tokens_cached), 0) AS tokens_cached,
                    COALESCE(SUM(t.tokens_cache_write), 0) AS tokens_cache_write,
                    COALESCE(SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write), 0) AS total_tokens,
                    COUNT(DISTINCT t.session_id) AS session_count,
                    COUNT(DISTINCT t.user_id) AS user_count,
                    COUNT(*) AS turn_count
                FROM token_usage_ts t
                {where_sql}
                """,
                params,
            ).fetchone()

            by_provider_rows = conn.execute(
                f"""
                SELECT
                    t.provider,
                    SUM(t.tokens_in) AS tokens_in,
                    SUM(t.tokens_out) AS tokens_out,
                    SUM(t.tokens_cached) AS tokens_cached,
                    SUM(t.tokens_cache_write) AS tokens_cache_write,
                    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
                    COUNT(DISTINCT t.session_id) AS session_count
                FROM token_usage_ts t
                {where_sql}
                GROUP BY t.provider
                ORDER BY total_tokens DESC
                """,
                params,
            ).fetchall()

            by_interface_rows = conn.execute(
                f"""
                SELECT
                    t.interface,
                    SUM(t.tokens_in) AS tokens_in,
                    SUM(t.tokens_out) AS tokens_out,
                    SUM(t.tokens_cached) AS tokens_cached,
                    SUM(t.tokens_cache_write) AS tokens_cache_write,
                    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
                    COUNT(DISTINCT t.session_id) AS session_count
                FROM token_usage_ts t
                {where_sql}
                GROUP BY t.interface
                ORDER BY total_tokens DESC
                """,
                params,
            ).fetchall()

        t_in = int(row["tokens_in"] or 0)
        t_out = int(row["tokens_out"] or 0)
        t_cached = int(row["tokens_cached"] or 0)
        t_cwrite = int(row["tokens_cache_write"] or 0)
        total = int(row["total_tokens"] or 0)
        denom = t_in + t_cached + t_cwrite
        cache_pct = round((t_cached / denom) * 100.0, 1) if denom > 0 else 0.0

        return {
            "tokens_in": t_in,
            "tokens_out": t_out,
            "tokens_cached": t_cached,
            "tokens_cache_write": t_cwrite,
            "total_tokens": total,
            "cache_hit_pct": cache_pct,
            "session_count": int(row["session_count"] or 0),
            "user_count": int(row["user_count"] or 0),
            "turn_count": int(row["turn_count"] or 0),
            "by_provider": [dict(r) for r in by_provider_rows],
            "by_interface": [dict(r) for r in by_interface_rows],
        }
