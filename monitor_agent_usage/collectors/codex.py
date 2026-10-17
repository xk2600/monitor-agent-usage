"""OpenAI Codex token usage collector (CLI, VS Code, Desktop App, and Web/API without proxy)."""

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
)


class CodexCollector(BaseCollector):
    """Collects token usage from OpenAI Codex (~/.codex/sessions, OpenAI Usage API, and Codex Web)."""

    provider = "codex"

    def collect(self, account: AccountRecord) -> SyncSummary:
        now_iso, _ = parse_timestamp(None)
        summary = SyncSummary(
            provider=self.provider,
            account_id=account.account_id,
            synced_at=now_iso,
        )

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        # 1. Local ~/.codex/sessions & ~/.codex/archived_sessions rollout JSONL logs
        root_path = Path(account.config_json.get("root_path", "~/.codex")).expanduser()
        for sub in ("sessions", "archived_sessions"):
            scan_dir = root_path / sub
            if not scan_dir.exists() or not scan_dir.is_dir():
                continue
            for file_path in sorted(scan_dir.rglob("*.json*")):
                if file_path.suffix not in (".jsonl", ".json"):
                    continue
                try:
                    sess, sess_pts = self._parse_codex_rollout_file(file_path, account)
                    if sess and sess_pts:
                        sessions.append(sess)
                        points.extend(sess_pts)
                except Exception as exc:
                    summary.errors.append(f"Error parsing Codex file {file_path.name}: {exc}")

        # 2. Direct OpenAI Organization Usage API (/v1/organization/usage/completions)
        api_key = account.config_json.get("api_key", "").strip()
        if api_key and not api_key.startswith("***"):
            try:
                api_sess, api_pts = self._fetch_openai_usage_api(api_key, account)
                sessions.extend(api_sess)
                points.extend(api_pts)
            except Exception as exc:
                summary.errors.append(f"OpenAI Usage API sync error: {exc}")

        # 3. Direct ChatGPT / Codex Web backend API when bearer_token is configured
        bearer_token = account.config_json.get("bearer_token", "").strip()
        if bearer_token and not bearer_token.startswith("***"):
            try:
                web_sess, web_pts = self._fetch_codex_web_sessions(bearer_token, account)
                sessions.extend(web_sess)
                points.extend(web_pts)
            except Exception as exc:
                summary.errors.append(f"Codex Web API sync error: {exc}")

        if sessions:
            self.db.upsert_sessions(sessions)
        if points:
            self.db.upsert_usage_points(points)

        summary.sessions_scanned = len(sessions)
        summary.events_ingested = len(points)
        self.db.update_account_sync_state(account.account_id, sync_cursor=now_iso, last_sync_at=now_iso)
        return summary

    def _parse_codex_rollout_file(
        self, file_path: Path, account: AccountRecord
    ) -> Tuple[Optional[SessionRecord], List[TokenUsagePoint]]:
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw_text:
            return None, []

        lines = [ln for ln in raw_text.splitlines() if ln.strip()]
        session_id = file_path.stem
        session_name = ""
        interface = "CLI"
        workspace_path = ""
        current_model = "codex-mini-latest"
        started_at = ""
        last_active_at = ""

        points: List[TokenUsagePoint] = []
        prev_total_in = 0
        prev_total_cached = 0
        prev_total_out = 0
        seen_totals: set[Tuple[int, int, int]] = set()
        turn_counter = 0

        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if not isinstance(obj, dict):
                continue

            ts_raw = obj.get("timestamp") or obj.get("created_at")
            ts_iso, ts_epoch = parse_timestamp(ts_raw or file_path.stat().st_mtime)
            if not started_at or ts_iso < started_at:
                started_at = ts_iso
            if not last_active_at or ts_iso > last_active_at:
                last_active_at = ts_iso

            entry_type = str(obj.get("type") or "")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj

            if entry_type == "session_meta" or "originator" in payload:
                if payload.get("id"):
                    session_id = str(payload["id"])
                if payload.get("cwd"):
                    workspace_path = str(payload["cwd"])
                origin = str(payload.get("originator") or payload.get("source") or "")
                if origin:
                    interface = normalize_interface(origin, default="CLI")

            if entry_type == "turn_context" and payload.get("model"):
                current_model = str(payload["model"])
            elif obj.get("model"):
                current_model = str(obj["model"])

            # Extract user prompt for session title
            if not session_name:
                if entry_type == "response_item" and payload.get("role") == "user":
                    content_list = payload.get("content", [])
                    if isinstance(content_list, list):
                        for item in content_list:
                            if isinstance(item, dict) and item.get("text"):
                                candidate = extract_clean_session_title(str(item["text"]), fallback="")
                                if candidate and not candidate.startswith("<environment_context"):
                                    session_name = candidate
                                    break
                elif entry_type == "event_msg" and payload.get("type") == "user_message":
                    msg_txt = str(payload.get("message") or "")
                    if msg_txt:
                        session_name = extract_clean_session_title(msg_txt, fallback="")

            # Extract token_count events
            is_token_event = (
                (entry_type == "event_msg" and payload.get("type") == "token_count")
                or entry_type == "token_count"
                or "usage" in obj
            )
            if not is_token_event:
                continue

            info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
            if not isinstance(info, dict):
                continue

            last_usage = info.get("last_token_usage")
            total_usage = info.get("total_token_usage")
            direct_usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None

            if isinstance(total_usage, dict):
                tot_in = int(total_usage.get("input_tokens") or 0)
                tot_cached = int(total_usage.get("cached_input_tokens") or total_usage.get("cached_tokens") or 0)
                tot_out = int(total_usage.get("output_tokens") or 0)
                sig = (tot_in, tot_cached, tot_out)
                if sig in seen_totals:
                    continue
                seen_totals.add(sig)

            if isinstance(last_usage, dict):
                raw_in = int(last_usage.get("input_tokens") or 0)
                cached_in = int(last_usage.get("cached_input_tokens") or last_usage.get("cached_tokens") or 0)
                out_tok = int(last_usage.get("output_tokens") or 0)
                # In Codex rollout logs, input_tokens includes cached_input_tokens
                uncached_in = max(0, raw_in - cached_in) if raw_in >= cached_in else raw_in
            elif isinstance(total_usage, dict):
                tot_in = int(total_usage.get("input_tokens") or 0)
                tot_cached = int(total_usage.get("cached_input_tokens") or 0)
                tot_out = int(total_usage.get("output_tokens") or 0)
                delta_in = max(0, tot_in - prev_total_in)
                cached_in = max(0, tot_cached - prev_total_cached)
                out_tok = max(0, tot_out - prev_total_out)
                uncached_in = max(0, delta_in - cached_in) if delta_in >= cached_in else delta_in
                prev_total_in, prev_total_cached, prev_total_out = tot_in, tot_cached, tot_out
            elif isinstance(direct_usage, dict):
                uncached_in = int(direct_usage.get("input_tokens") or direct_usage.get("prompt_tokens") or 0)
                cached_in = int(direct_usage.get("cached_tokens") or direct_usage.get("cached_input_tokens") or 0)
                out_tok = int(direct_usage.get("output_tokens") or direct_usage.get("completion_tokens") or 0)
            else:
                continue

            if uncached_in == 0 and cached_in == 0 and out_tok == 0:
                continue

            turn_counter += 1
            event_id = make_event_id(
                provider=self.provider,
                account_id=account.account_id,
                session_id=session_id,
                turn_ref=f"turn_{turn_counter}_{ts_iso}_{uncached_in}_{out_tok}",
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
                    session_name=session_name or f"Codex {session_id[:8]}",
                    model=current_model,
                    tokens_in=uncached_in,
                    tokens_out=out_tok,
                    tokens_cached=cached_in,
                    turn_index=turn_counter,
                    source_ref=str(file_path),
                )
            )

        if not points:
            return None, []

        final_title = (
            session_name
            or (f"Codex ({Path(workspace_path).name})" if workspace_path else f"Codex Session {session_id[:8]}")
        )
        for pt in points:
            pt.session_name = final_title

        session_rec = SessionRecord(
            session_id=session_id,
            user_id=account.user_id,
            account_id=account.account_id,
            provider=self.provider,
            interface=interface,
            session_name=final_title,
            workspace_path=workspace_path or str(file_path.parent),
            model=current_model,
            started_at=started_at,
            last_active_at=last_active_at,
            metadata_json={"source_file": str(file_path)},
        )
        return session_rec, points

    def _fetch_openai_usage_api(
        self, api_key: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Query OpenAI Organization Usage API (/v1/organization/usage/completions)."""
        _, now_epoch = parse_timestamp(None)
        start_time = now_epoch - (7 * 86400)
        url = f"https://api.openai.com/v1/organization/usage/completions?start_time={start_time}&bucket_width=1h"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        sessions: Dict[str, SessionRecord] = {}
        points: List[TokenUsagePoint] = []
        for b_idx, bucket in enumerate(payload.get("data", [])):
            ts_iso, ts_epoch = parse_timestamp(bucket.get("start_time"))
            for r_idx, res in enumerate(bucket.get("results", [])):
                model = str(res.get("model") or "codex-api")
                t_in = int(res.get("input_tokens") or 0)
                t_cached = int(res.get("input_cached_tokens") or 0)
                t_out = int(res.get("output_tokens") or 0)
                if t_in == 0 and t_cached == 0 and t_out == 0:
                    continue
                uncached_in = max(0, t_in - t_cached) if t_in >= t_cached else t_in
                sess_id = f"codex_api_{account.account_id}_{model}"
                sess_name = f"OpenAI Codex API ({model})"
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
                        event_id=make_event_id(
                            self.provider, account.account_id, sess_id, f"{ts_iso}_{b_idx}_{r_idx}"
                        ),
                        timestamp=ts_iso,
                        timestamp_epoch=ts_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface="CLI",
                        session_id=sess_id,
                        session_name=sess_name,
                        model=model,
                        tokens_in=uncached_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        turn_index=b_idx + 1,
                        source_ref="openai_usage_api",
                    )
                )
        return list(sessions.values()), points

    def _fetch_codex_web_sessions(
        self, bearer_token: str, account: AccountRecord
    ) -> Tuple[List[SessionRecord], List[TokenUsagePoint]]:
        """Fetch Codex / ChatGPT web conversations directly via backend-api."""
        url = "https://chatgpt.com/backend-api/conversations?offset=0&limit=50&order=updated"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MonitorAgentUsage/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []
        for item in payload.get("items", []):
            cid = str(item.get("id") or "")
            if not cid:
                continue
            title = extract_clean_session_title(str(item.get("title") or ""), fallback=f"Codex Web {cid[:8]}")
            c_iso, _ = parse_timestamp(item.get("create_time"))
            u_iso, u_epoch = parse_timestamp(item.get("update_time") or item.get("create_time"))
            sessions.append(
                SessionRecord(
                    session_id=cid,
                    user_id=account.user_id,
                    account_id=account.account_id,
                    provider=self.provider,
                    interface="Web",
                    session_name=title,
                    model="codex-web",
                    started_at=c_iso,
                    last_active_at=u_iso,
                )
            )
            t_in = int(item.get("input_tokens") or 0)
            t_out = int(item.get("output_tokens") or 0)
            t_cached = int(item.get("cached_tokens") or 0)
            if t_in > 0 or t_out > 0 or t_cached > 0:
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(self.provider, account.account_id, cid, u_iso),
                        timestamp=u_iso,
                        timestamp_epoch=u_epoch,
                        user_id=account.user_id,
                        account_id=account.account_id,
                        provider=self.provider,
                        interface="Web",
                        session_id=cid,
                        session_name=title,
                        model="codex-web",
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        turn_index=1,
                        source_ref="codex_web_api",
                    )
                )
        return sessions, points
