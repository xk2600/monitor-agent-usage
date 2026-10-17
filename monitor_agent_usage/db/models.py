"""Data models for users, accounts, sessions, and time-series token events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


PROVIDERS = ("antigravity", "claude", "codex", "grok")
INTERFACES = ("App", "CLI", "Web", "VS Code")


@dataclass
class UserRecord:
    user_id: str
    username: str
    display_name: str
    email: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AccountRecord:
    account_id: str
    user_id: str
    provider: str  # grok | codex | antigravity | claude
    account_label: str
    auth_type: str  # local_fs | api_key | session_cookie
    config_json: Dict[str, Any] = field(default_factory=dict)
    sync_cursor: str = ""
    last_sync_at: Optional[str] = None
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Mask sensitive keys when returning to UI
        masked_cfg = dict(self.config_json)
        for secret_key in ("api_key", "session_key", "cookie", "bearer_token"):
            if secret_key in masked_cfg and masked_cfg[secret_key]:
                val = str(masked_cfg[secret_key])
                masked_cfg[secret_key] = (
                    f"{val[:6]}...{val[-4:]}" if len(val) > 12 else "***configured***"
                )
        data["config_json"] = masked_cfg
        return data


@dataclass
class SessionRecord:
    session_id: str
    user_id: str
    account_id: str
    provider: str
    interface: str  # App | CLI | Web | VS Code
    session_name: str
    workspace_path: str = ""
    model: str = ""
    started_at: str = ""
    last_active_at: str = ""
    metadata_json: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TokenUsagePoint:
    event_id: str
    timestamp: str  # ISO-8601 UTC string e.g. 2026-10-17T22:14:16Z
    timestamp_epoch: int  # Unix epoch seconds
    user_id: str
    account_id: str
    provider: str  # grok | codex | antigravity | claude
    interface: str  # App | CLI | Web | VS Code
    session_id: str
    session_name: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_cache_write: int = 0
    turn_index: int = 0
    source_ref: str = ""

    @property
    def bucket_15m_epoch(self) -> int:
        """Floor timestamp_epoch to 15-minute (900s) boundary."""
        return (self.timestamp_epoch // 900) * 900

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out + self.tokens_cached + self.tokens_cache_write

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["bucket_15m_epoch"] = self.bucket_15m_epoch
        d["total_tokens"] = self.total_tokens
        return d


@dataclass
class SyncSummary:
    provider: str
    account_id: str
    sessions_scanned: int = 0
    events_ingested: int = 0
    errors: List[str] = field(default_factory=list)
    synced_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
