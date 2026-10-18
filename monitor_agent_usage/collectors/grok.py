"""Grok (xAI) token usage collector (Web, App, CLI, VS Code, and xAI API without proxy)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..db.models import AccountRecord, SessionRecord, SyncSummary, TokenUsagePoint
from .base import (
    BaseCollector,
    extract_clean_session_title,
    make_event_id,
    normalize_interface,
    parse_timestamp,
    safe_is_dir,
    safe_read_text,
    safe_rglob,
    safe_stat_mtime,
)


class GrokCollector(BaseCollector):
    """Collects token usage from Grok CLI/App local logs (~/.grok), xAI API, and grok.com REST API."""

    provider = "grok"

    def collect(self, account: AccountRecord) -> SyncSummary:
        now_iso, _ = parse_timestamp(None)
        summary = SyncSummary(
            provider=self.provider,
            account_id=account.account_id,
            synced_at=now_iso,
        )

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        # 1. Local Grok CLI / VS Code / Desktop App session files (~/.grok, ~/.config/grok)
        root_path = Path(account.config_json.get("root_path", "~/.grok")).expanduser()
        candidate_dirs = [root_path, Path("~/.config/grok").expanduser()]
        seen_files: set[str] = set()

        for base_dir in candidate_dirs:
            if not safe_is_dir(base_dir):
                continue
            for file_path in safe_rglob(base_dir, "*.json*"):
                if file_path.suffix not in (".json", ".jsonl"):
                    continue
                try:
                    resolved = str(file_path.resolve())
                except (OSError, PermissionError):
                    resolved = str(file_path)
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                try:
                    sess, sess_pts = self._parse_local_grok_file(file_path, account)
                    if sess and sess_pts:
                        sessions.append(sess)
                        points.extend(sess_pts)
                except Exception as exc:
                    summary.errors.append(f"Error parsing Grok log {file_path.name}: {exc}")

        # 2. Direct xAI API usage sync when api_key is configured
        api_key = account.config_json.get("api_key", "").strip()
        if api_key and not api_key.startswith("***"):
            try:
                api_sess, api_pts = self._fetch_xai_usage_api(api_key, account)
                sessions.extend(api_sess)
                points.extend(api_pts)
            except Exception as exc:
                summary.errors.append(f"xAI API sync error: {exc}")

        # 3. Direct grok.com Web/App conversation sync when session_cookie (sso=...) is configured
        cookie = account.config_json.get("cookie", "").strip()
        if cookie and not cookie.startswith("***"):
            try:
                web_sess, web_pts = self._fetch_grok_web_conversations(cookie, account)
                sessions.extend(web_sess)
                points.extend(web_pts)
            except Exception as exc:
                summary.errors.append(f"grok.com Web sync error: {exc}")

        if sessions:
            self.db.upsert_sessions(sessions)
        if points:
            self.db.upsert_usage_points(points)

        summary.sessions_scanned = len(sessions)
        summary.events_ingested = len(points)
        self.db.update_account_sync_state(account.account_id, sync_cursor=now_iso, last_sync_at=now_iso)
        return summary

    def _parse_local_grok_file(
        self, file_path: Path, account: AccountRecord
    ) -> Tuple[Optional[SessionRecord], List[TokenUsagePoint]]:
        raw_text = safe_read_text(file_path).strip()
        if not raw_text:
            return None, []

        session_id = file_path.stem
        session_name = ""
        interface = "CLI"
        model_name = "grok-3"
        workspace_path = str(file_path.parent)
        started_at = ""
        last_active_at = ""
        points: List[TokenUsagePoint] = []

        # Support both JSONL and single JSON document formats
        records: List[Dict[str, Any]] = []
        if file_path.suffix == ".jsonl":
            for ln in raw_text.splitlines():
                if ln.strip():
                    try:
                        obj = json.loads(ln)
                        if isinstance(obj, dict):
                            records.append(obj)
                    except Exception:
                        pass
        else:
            try:
                doc = json.loads(raw_text)
                if isinstance(doc, dict):
                    if doc.get("id") or doc.get("conversationId"):
                        session_id = str(doc.get("id") or doc.get("conversationId"))
                    if doc.get("title") or doc.get("name"):
                        session_name = extract_clean_session_title(str(doc.get("title") or doc.get("name")))
                    if doc.get("interface") or doc.get("client"):
                        interface = normalize_interface(str(doc.get("interface") or doc.get("client")), default="CLI")
                    if isinstance(doc.get("messages"), list):
                        records = [m for m in doc["messages"] if isinstance(m, dict)]
                    else:
                        records = [doc]
                elif isinstance(doc, list):
                    records = [m for m in doc if isinstance(m, dict)]
            except Exception:
                return None, []

        for idx, obj in enumerate(records):
            if obj.get("session_id"):
                session_id = str(obj["session_id"])
            if obj.get("title") and not session_name:
                session_name = extract_clean_session_title(str(obj["title"]))
            if obj.get("interface") or obj.get("client") or obj.get("source"):
                interface = normalize_interface(
                    str(obj.get("interface") or obj.get("client") or obj.get("source")),
                    default=interface,
                )
            if obj.get("model"):
                model_name = str(obj["model"])

            if not session_name and obj.get("role") == "user" and obj.get("content"):
                session_name = extract_clean_session_title(str(obj["content"]), fallback="")

            ts_raw = obj.get("timestamp") or obj.get("created_at") or obj.get("createTime")
            ts_iso, ts_epoch = parse_timestamp(ts_raw or safe_stat_mtime(file_path))
            if not started_at or ts_iso < started_at:
                started_at = ts_iso
            if not last_active_at or ts_iso > last_active_at:
                last_active_at = ts_iso

            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else obj
            t_in = int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
                or usage.get("tokens_in")
                or 0
            )
            t_out = int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
                or usage.get("tokens_out")
                or 0
            )
            t_cached = int(
                usage.get("cached_tokens")
                or usage.get("cache_read_input_tokens")
                or usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                if isinstance(usage.get("prompt_tokens_details"), dict)
                else (usage.get("cached_tokens") or usage.get("tokens_cached") or 0)
            )

            if t_in == 0 and t_out == 0 and t_cached == 0:
                continue

            event_id = make_event_id(
                provider=self.provider,
                account_id=account.account_id,
                session_id=session_id,
                turn_ref=f"turn_{idx}_{ts_iso}",
            )
            points.append(
                TokenUsagePoint(
                    event_id=event_id,
                    timestamp=ts_iso,
                    timestamp_epoch=ts_epoch,
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface=interface,
                    session_id=session_id,
                    session_name=session_name or f"Grok {session_id[:8]}",
                    model=model_name,
                    tokens_in=t_in,
                    tokens_out=t_out,
                    tokens_cached=t_cached,
                    turn_index=idx + 1,
                    source_ref=str(file_path),
                )
            )

        if not points:
            return None, []

        final_title = session_name or f"Grok Session {session_id[:8]}"
        for pt in points:
            pt.session_name = final_title

        session_rec = SessionRecord(
            session_id=session_id,
            user_id=account.user_id,
            account_id=account.account_id,
            provider=self.provider,
            interface=interface,
            session_name=final_title,
            workspace_path=workspace_path,
            model=model_name,
            started_at=started_at,
            last_active_at=last_active_at,
            metadata_json={"source_file": str(file_path)},
        )
        return session_rec, points

    def _fetch_xai_usage_api(
        self, api_key: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Query xAI API key / usage endpoint directly."""
        url = "https://api.x.ai/v1/api-key"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        # If usage metrics are included in payload, ingest them
        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            ts_iso, ts_epoch = parse_timestamp(payload.get("modify_time") or None)
            t_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            t_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            t_cached = int(usage.get("cached_prompt_text_tokens") or usage.get("cached_tokens") or 0)
            if t_in > 0 or t_out > 0 or t_cached > 0:
                sess_id = f"grok_api_{account.account_id}"
                sess_name = f"xAI API ({payload.get('name', 'Grok')})"
                sessions.append(
                    SessionRecord(
                        session_id=sess_id,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface="CLI",
                        session_name=sess_name,
                        model="grok-3",
                        started_at=ts_iso,
                        last_active_at=ts_iso,
                    )
                )
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(self.provider, account.account_id, sess_id, ts_iso),
                        timestamp=ts_iso,
                        timestamp_epoch=ts_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface="CLI",
                        session_id=sess_id,
                        session_name=sess_name,
                        model="grok-3",
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        turn_index=1,
                        source_ref="xai_api",
                    )
                )
        return sessions, points

    def _fetch_grok_web_conversations(
        self, cookie: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Query grok.com REST API directly for Web/App conversations without a proxy."""
        cookie_hdr = cookie if "=" in cookie else f"sso={cookie}"
        url = "https://grok.com/rest/app-chat/conversations?pageSize=50"
        req = urllib.request.Request(
            url,
            headers={
                "Cookie": cookie_hdr,
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MonitorAgentUsage/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        convs = payload.get("conversations", []) if isinstance(payload, dict) else []
        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []
        for conv in convs:
            cid = str(conv.get("conversationId") or conv.get("id") or "")
            if not cid:
                continue
            title = extract_clean_session_title(str(conv.get("title") or ""), fallback=f"Grok Chat {cid[:8]}")
            c_iso, _ = parse_timestamp(conv.get("createTime"))
            u_iso, u_epoch = parse_timestamp(conv.get("modifyTime") or conv.get("createTime"))
            model = str(conv.get("modelName") or "grok-3")
            interface = normalize_interface(str(conv.get("client") or "Web"), default="Web")

            sessions.append(
                SessionRecord(
                    session_id=cid,
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface=interface,
                    session_name=title,
                    model=model,
                    started_at=c_iso,
                    last_active_at=u_iso,
                )
            )
            t_in = int(conv.get("inputTokens") or conv.get("promptTokens") or 0)
            t_out = int(conv.get("outputTokens") or conv.get("completionTokens") or 0)
            t_cached = int(conv.get("cachedTokens") or 0)
            if t_in > 0 or t_out > 0 or t_cached > 0:
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(self.provider, account.account_id, cid, u_iso),
                        timestamp=u_iso,
                        timestamp_epoch=u_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface=interface,
                        session_id=cid,
                        session_name=title,
                        model=model,
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        turn_index=1,
                        source_ref="grok_web_api",
                    )
                )
        return sessions, points
