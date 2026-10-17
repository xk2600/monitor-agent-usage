"""Base collector utilities for proxy-free token usage ingestion."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple

from ..db.models import AccountRecord, SessionRecord, SyncSummary, TokenUsagePoint
from ..db.tsdb import TimeSeriesDB


def parse_timestamp(raw: Any) -> Tuple[str, int]:
    """Parse an ISO-8601 string or epoch number into (UTC ISO-8601 string, epoch seconds)."""
    if raw is None or raw == "":
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        return dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), now

    if isinstance(raw, (int, float)):
        val = float(raw)
        # Convert milliseconds to seconds if needed
        if val > 1e11:
            val = val / 1000.0
        epoch = int(val)
        iso = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return iso, epoch

    s = str(raw).strip()
    if s.isdigit():
        return parse_timestamp(int(s))

    try:
        if s.endswith("Z"):
            s_norm = s[:-1] + "+00:00"
        else:
            s_norm = s
        parsed = dt.datetime.fromisoformat(s_norm)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        else:
            parsed = parsed.astimezone(dt.timezone.utc)
        epoch = int(parsed.timestamp())
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ"), epoch
    except Exception:
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        return dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), now


def make_event_id(provider: str, account_id: str, session_id: str, turn_ref: str) -> str:
    """Create a deterministic SHA-256 event ID for idempotent time-series deduplication."""
    payload = f"{provider}:{account_id}:{session_id}:{turn_ref}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{provider}_{digest}"


def normalize_interface(raw: Optional[str], default: str = "CLI") -> str:
    """Normalize client surface strings to one of: App, CLI, Web, VS Code."""
    if not raw:
        return default
    low = raw.strip().lower()
    if any(k in low for k in ("vscode", "vs code", "vs_code", "ide", "cursor", "windsurf", "extension")):
        return "VS Code"
    if any(k in low for k in ("web", "browser", "claude.ai", "chatgpt.com", "grok.com", "http")):
        return "Web"
    if any(k in low for k in ("app", "desktop", "electron", "gui", "agy 2.0", "macos", "windows_app")):
        return "App"
    if any(k in low for k in ("cli", "terminal", "shell", "sdk", "agy", "codex-cli", "claude-code")):
        return "CLI"
    return default


_TAG_BLOCK_RE = re.compile(
    r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|ide_opened_file|ide_selection|system-reminder|context)[^>]*>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)
_USER_REQ_RE = re.compile(
    r"<USER_REQUEST>(.*?)</USER_REQUEST>",
    flags=re.DOTALL | re.IGNORECASE,
)
_XML_TAG_RE = re.compile(r"<[^>]+>")


def extract_clean_session_title(raw_text: str, fallback: str = "Untitled Session", max_len: int = 76) -> str:
    """Extract a concise, human-readable session title from a prompt or markdown text."""
    if not raw_text:
        return fallback

    text = str(raw_text)
    m = _USER_REQ_RE.search(text)
    if m:
        text = m.group(1)

    text = _TAG_BLOCK_RE.sub(" ", text)
    text = _XML_TAG_RE.sub(" ", text)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return fallback

    first_line = lines[0].lstrip("#*- >").strip()
    first_line = re.sub(r"\s+", " ", first_line)
    if not first_line:
        return fallback

    if len(first_line) > max_len:
        return first_line[: max_len - 1].rstrip() + "…"
    return first_line


class BaseCollector(ABC):
    """Abstract base class for provider token usage collectors."""

    provider: str = ""

    def __init__(self, db: TimeSeriesDB) -> None:
        self.db = db

    @abstractmethod
    def collect(self, account: AccountRecord) -> SyncSummary:
        """Collect historical and incremental usage for the given account."""
        raise NotImplementedError
