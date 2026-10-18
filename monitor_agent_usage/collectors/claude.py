"""Claude token usage collector (CLI, VS Code, Desktop App, and Web/API without proxy)."""

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


class ClaudeCollector(BaseCollector):
    """Collects token usage from Claude Code (~/.claude), Claude Desktop App, claude.ai Web, and Anthropic Usage API."""

    provider = "claude"

    def collect(self, account: AccountRecord) -> SyncSummary:
        now_iso, _ = parse_timestamp(None)
        summary = SyncSummary(
            provider=self.provider,
            account_id=account.account_id,
            synced_at=now_iso,
        )

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        # 1. Local filesystem (~/.claude/projects/**/*.jsonl)
        root_path = Path(account.config_json.get("root_path", "~/.claude")).expanduser()
        projects_dir = root_path / "projects"
        if safe_is_dir(projects_dir):
            for jsonl_path in safe_rglob(projects_dir, "*.jsonl"):
                try:
                    sess, sess_pts = self._parse_claude_code_jsonl(jsonl_path, account)
                    if sess and sess_pts:
                        sessions.append(sess)
                        points.extend(sess_pts)
                except Exception as exc:
                    summary.errors.append(f"Error parsing Claude log {jsonl_path.name}: {exc}")

        # 2. Claude Desktop App local storage if configured/present
        desktop_path_raw = account.config_json.get("desktop_path")
        if desktop_path_raw:
            desktop_dir = Path(desktop_path_raw).expanduser()
            if safe_is_dir(desktop_dir):
                for json_file in safe_rglob(desktop_dir, "*.json*"):
                    if json_file.suffix in (".json", ".jsonl"):
                        try:
                            sess, sess_pts = self._parse_claude_code_jsonl(
                                json_file, account, default_interface="App"
                            )
                            if sess and sess_pts:
                                sessions.append(sess)
                                points.extend(sess_pts)
                        except Exception:
                            pass

        # 3. Direct Anthropic Organization Usage API (when Admin API key is provided)
        api_key = account.config_json.get("api_key", "").strip()
        if api_key and not api_key.startswith("***"):
            try:
                api_sess, api_pts = self._fetch_anthropic_usage_api(api_key, account)
                sessions.extend(api_sess)
                points.extend(api_pts)
            except Exception as exc:
                summary.errors.append(f"Anthropic Usage API sync error: {exc}")

        # 4. Direct claude.ai Web / Desktop App Session Sync (when session_key + org_id provided)
        session_key = account.config_json.get("session_key", "").strip()
        org_id = account.config_json.get("org_id", "").strip()
        if session_key and not session_key.startswith("***"):
            try:
                web_sess, web_pts = self._fetch_claude_web_sessions(session_key, org_id, account)
                sessions.extend(web_sess)
                points.extend(web_pts)
            except Exception as exc:
                summary.errors.append(f"claude.ai Web sync error: {exc}")

        if sessions:
            self.db.upsert_sessions(sessions)
        if points:
            self.db.upsert_usage_points(points)

        summary.sessions_scanned = len(sessions)
        summary.events_ingested = len(points)
        self.db.update_account_sync_state(account.account_id, sync_cursor=now_iso, last_sync_at=now_iso)
        return summary

    def _extract_user_content_text(self, message_obj: Any) -> str:
        if not isinstance(message_obj, dict):
            return ""
        content = message_obj.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return ""

    def _parse_claude_code_jsonl(
        self,
        jsonl_path: Path,
        account: AccountRecord,
        default_interface: str = "CLI",
    ) -> Tuple[Optional[SessionRecord], List[TokenUsagePoint]]:
        raw_lines = [
            ln
            for ln in safe_read_text(jsonl_path).splitlines()
            if ln.strip()
        ]
        if not raw_lines:
            return None, []

        session_id = jsonl_path.stem
        session_name = ""
        first_user_prompt = ""
        slug_name = ""
        interface = default_interface
        workspace_path = ""
        model_name = "claude-sonnet"
        started_at = ""
        last_active_at = ""

        # Deduplicate assistant turns by message.id (Claude Code logs intermediate streaming chunks)
        msg_records: Dict[str, Dict[str, Any]] = {}
        turn_order: List[str] = []

        for idx, line in enumerate(raw_lines):
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if not isinstance(obj, dict):
                continue

            if obj.get("sessionId") and not session_id:
                session_id = str(obj["sessionId"])
            if obj.get("cwd") and not workspace_path:
                workspace_path = str(obj["cwd"])
            if obj.get("slug") and not slug_name:
                slug_name = str(obj["slug"]).replace("-", " ").title()

            # Check interface clues
            entrypoint = str(obj.get("entrypoint", "") or "")
            if entrypoint:
                interface = normalize_interface(entrypoint, default=interface)
            if "<ide_opened_file>" in line or "<ide_selection>" in line or "vscode" in entrypoint.lower():
                interface = "VS Code"

            entry_type = str(obj.get("type", ""))
            if entry_type == "summary" and obj.get("summary"):
                session_name = extract_clean_session_title(str(obj["summary"]), fallback=session_name)

            ts_raw = obj.get("timestamp") or obj.get("created_at")
            if ts_raw:
                ts_iso, ts_epoch = parse_timestamp(ts_raw)
                if not started_at or ts_iso < started_at:
                    started_at = ts_iso
                if not last_active_at or ts_iso > last_active_at:
                    last_active_at = ts_iso
            else:
                ts_iso, ts_epoch = parse_timestamp(safe_stat_mtime(jsonl_path))

            if entry_type == "user" and not first_user_prompt:
                txt = self._extract_user_content_text(obj.get("message"))
                if txt:
                    cleaned = extract_clean_session_title(txt, fallback="")
                    if cleaned and not cleaned.startswith("Caveat:"):
                        first_user_prompt = cleaned

            # Extract token usage from assistant messages or direct usage fields
            msg = obj.get("message")
            usage = None
            msg_id = ""
            if isinstance(msg, dict):
                usage = msg.get("usage")
                msg_id = str(msg.get("id") or "")
                if msg.get("model"):
                    model_name = str(msg["model"])
            if not usage and isinstance(obj.get("usage"), dict):
                usage = obj["usage"]

            if isinstance(usage, dict):
                t_in = int(usage.get("input_tokens") or 0)
                t_out = int(usage.get("output_tokens") or 0)
                t_cache_read = int(
                    usage.get("cache_read_input_tokens")
                    or usage.get("cached_tokens")
                    or usage.get("cache_read_tokens")
                    or 0
                )
                t_cache_create = int(usage.get("cache_creation_input_tokens") or 0)

                if t_in == 0 and t_out == 0 and t_cache_read == 0 and t_cache_create == 0:
                    continue

                dedup_key = msg_id or f"line_{idx}_{ts_iso}"
                if dedup_key not in msg_records:
                    turn_order.append(dedup_key)
                    msg_records[dedup_key] = {
                        "ts_iso": ts_iso,
                        "ts_epoch": ts_epoch,
                        "model": model_name,
                        "tokens_in": t_in,
                        "tokens_out": t_out,
                        "tokens_cached": t_cache_read,
                        "tokens_cache_write": t_cache_create,
                        "turn_index": len(turn_order),
                    }
                else:
                    # Keep latest/highest token metrics for streaming message updates
                    prev = msg_records[dedup_key]
                    prev["ts_iso"] = ts_iso
                    prev["ts_epoch"] = ts_epoch
                    prev["tokens_in"] = max(prev["tokens_in"], t_in)
                    prev["tokens_out"] = max(prev["tokens_out"], t_out)
                    prev["tokens_cached"] = max(prev["tokens_cached"], t_cache_read)
                    prev["tokens_cache_write"] = max(prev["tokens_cache_write"], t_cache_create)

        if not msg_records:
            return None, []

        final_title = (
            session_name
            or first_user_prompt
            or slug_name
            or f"Claude {Path(workspace_path).name if workspace_path else session_id[:8]}"
        )

        points: List[TokenUsagePoint] = []
        for key in turn_order:
            rec = msg_records[key]
            event_id = make_event_id(
                provider=self.provider,
                account_id=account.account_id,
                session_id=session_id,
                turn_ref=key,
            )
            points.append(
                TokenUsagePoint(
                    event_id=event_id,
                    timestamp=rec["ts_iso"],
                    timestamp_epoch=rec["ts_epoch"],
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface=interface,
                    session_id=session_id,
                    session_name=final_title,
                    model=rec["model"],
                    tokens_in=rec["tokens_in"],
                    tokens_out=rec["tokens_out"],
                    tokens_cached=rec["tokens_cached"],
                    tokens_cache_write=rec["tokens_cache_write"],
                    turn_index=rec["turn_index"],
                    source_ref=str(jsonl_path),
                )
            )

        session_rec = SessionRecord(
            session_id=session_id,
            user_id=account.user_id,
            account_id=account.account_id,
            provider=self.provider,
            interface=interface,
            session_name=final_title,
            workspace_path=workspace_path or str(jsonl_path.parent),
            model=model_name,
            started_at=started_at,
            last_active_at=last_active_at,
            metadata_json={"source_file": str(jsonl_path)},
        )
        return session_rec, points

    def _fetch_anthropic_usage_api(
        self, api_key: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Fetch bucketed token usage from Anthropic Admin Usage API (/v1/organizations/usage_report/messages)."""
        url = "https://api.anthropic.com/v1/organizations/usage_report/messages?bucket_width=1h&limit=168"
        req = urllib.request.Request(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        sessions: Dict[str, SessionRecord] = {}
        points: List[TokenUsagePoint] = []
        for idx, bucket in enumerate(payload.get("data", [])):
            ts_iso, ts_epoch = parse_timestamp(bucket.get("starting_at") or bucket.get("bucket_start_time"))
            for item in bucket.get("results", [bucket]):
                model = str(item.get("model") or "claude-api")
                t_in = int(item.get("uncached_input_tokens") or item.get("input_tokens") or 0)
                t_out = int(item.get("output_tokens") or 0)
                t_cached = int(item.get("cache_read_input_tokens") or 0)
                t_cwrite = int(item.get("cache_creation_input_tokens") or 0)
                if t_in == 0 and t_out == 0 and t_cached == 0:
                    continue
                sess_id = f"claude_api_{account.account_id}_{model}"
                sess_name = f"Anthropic API ({model})"
                sessions[sess_id] = SessionRecord(
                    session_id=sess_id,
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface="CLI",
                    session_name=sess_name,
                    model=model,
                    started_at=ts_iso,
                    last_active_at=ts_iso,
                )
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(self.provider, account.account_id, sess_id, f"{ts_iso}_{idx}"),
                        timestamp=ts_iso,
                        timestamp_epoch=ts_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface="CLI",
                        session_id=sess_id,
                        session_name=sess_name,
                        model=model,
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        tokens_cache_write=t_cwrite,
                        turn_index=idx,
                        source_ref="anthropic_usage_api",
                    )
                )
        return list(sessions.values()), points

    def _fetch_claude_web_sessions(
        self, session_key: str, org_id: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Sync Web & Desktop App conversations directly from claude.ai REST API using sessionKey cookie."""
        headers = {
            "Cookie": f"sessionKey={session_key}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MonitorAgentUsage/1.0",
            "Accept": "application/json",
        }
        if not org_id:
            org_req = urllib.request.Request("https://claude.ai/api/organizations", headers=headers)
            with urllib.request.urlopen(org_req, timeout=15) as resp:
                orgs = json.loads(resp.read().decode("utf-8"))
                if isinstance(orgs, list) and orgs:
                    org_id = str(orgs[0].get("uuid", ""))
        if not org_id:
            return [], []

        conv_url = f"https://claude.ai/api/organizations/{org_id}/chat_conversations"
        req = urllib.request.Request(conv_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            convs = json.loads(resp.read().decode("utf-8"))

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []
        if not isinstance(convs, list):
            return [], []

        for conv in convs[:50]:
            cid = str(conv.get("uuid") or "")
            if not cid:
                continue
            name = extract_clean_session_title(str(conv.get("name") or ""), fallback=f"Claude Web {cid[:8]}")
            created_iso, _ = parse_timestamp(conv.get("created_at"))
            updated_iso, updated_epoch = parse_timestamp(conv.get("updated_at") or conv.get("created_at"))
            model = str(conv.get("model") or "claude-sonnet-4")
            interface = normalize_interface(str(conv.get("platform") or "Web"), default="Web")

            sessions.append(
                SessionRecord(
                    session_id=cid,
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface=interface,
                    session_name=name,
                    model=model,
                    started_at=created_iso,
                    last_active_at=updated_iso,
                )
            )
            # If summary token metrics are present on conversation object, record point
            t_in = int(conv.get("input_tokens") or conv.get("prompt_tokens") or 0)
            t_out = int(conv.get("output_tokens") or conv.get("completion_tokens") or 0)
            t_cached = int(conv.get("cache_read_input_tokens") or 0)
            if t_in > 0 or t_out > 0 or t_cached > 0:
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(self.provider, account.account_id, cid, updated_iso),
                        timestamp=updated_iso,
                        timestamp_epoch=updated_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface=interface,
                        session_id=cid,
                        session_name=name,
                        model=model,
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        turn_index=1,
                        source_ref="claude_web_api",
                    )
                )

        return sessions, points
