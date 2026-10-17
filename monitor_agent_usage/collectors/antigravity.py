"""Antigravity token usage collector (App, VS Code, CLI) via local transcript logs."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..db.models import AccountRecord, SessionRecord, SyncSummary, TokenUsagePoint
from .base import (
    BaseCollector,
    extract_clean_session_title,
    make_event_id,
    normalize_interface,
    parse_timestamp,
)


_MODEL_CHANGE_RE = re.compile(r"Model Selection`\s+from\s+[^\s]+\s+to\s+([^.\n<]+)", re.IGNORECASE)


class AntigravityCollector(BaseCollector):
    """Collects token usage from Google Antigravity (~/.gemini/antigravity & CLI)."""

    provider = "antigravity"

    def collect(self, account: AccountRecord) -> SyncSummary:
        now_iso, _ = parse_timestamp(None)
        summary = SyncSummary(
            provider=self.provider,
            account_id=account.account_id,
            synced_at=now_iso,
        )

        root_dir = Path(account.config_json.get("root_path", "~/.gemini/antigravity")).expanduser()
        cli_dir = Path(account.config_json.get("cli_path", "~/.gemini/antigravity-cli")).expanduser()

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        # 1. Scan brain/<conversation-id>/.system_generated/logs/transcript.jsonl
        brain_dir = root_dir / "brain"
        if brain_dir.exists() and brain_dir.is_dir():
            try:
                conv_dirs = sorted([d for d in brain_dir.iterdir() if d.is_dir()])
            except OSError as exc:
                summary.errors.append(f"Cannot list {brain_dir}: {exc}")
                conv_dirs = []

            for conv_dir in conv_dirs:
                conv_id = conv_dir.name
                if conv_id.startswith("."):
                    continue
                transcript_path = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
                if not transcript_path.exists():
                    transcript_path = conv_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
                if not transcript_path.exists():
                    continue

                try:
                    sess, sess_points = self._parse_brain_transcript(
                        conv_dir=conv_dir,
                        conv_id=conv_id,
                        transcript_path=transcript_path,
                        account=account,
                    )
                    if sess:
                        sessions.append(sess)
                        points.extend(sess_points)
                except Exception as exc:
                    summary.errors.append(f"Error parsing Antigravity session {conv_id}: {exc}")

        # 2. Scan ~/.gemini/antigravity-cli/ if present
        if cli_dir.exists() and cli_dir.is_dir():
            for jsonl_file in sorted(cli_dir.rglob("*.jsonl")):
                try:
                    conv_id = f"agy-cli-{jsonl_file.stem}"
                    sess, sess_points = self._parse_brain_transcript(
                        conv_dir=jsonl_file.parent,
                        conv_id=conv_id,
                        transcript_path=jsonl_file,
                        account=account,
                        forced_interface="CLI",
                    )
                    if sess:
                        sessions.append(sess)
                        points.extend(sess_points)
                except Exception as exc:
                    summary.errors.append(f"Error parsing Antigravity CLI log {jsonl_file}: {exc}")

        if sessions:
            self.db.upsert_sessions(sessions)
        if points:
            self.db.upsert_usage_points(points)

        summary.sessions_scanned = len(sessions)
        summary.events_ingested = len(points)
        self.db.update_account_sync_state(account.account_id, sync_cursor=now_iso, last_sync_at=now_iso)
        return summary

    def _detect_interface(self, conv_dir: Path, raw_lines: List[str], forced: Optional[str] = None) -> str:
        if forced:
            return normalize_interface(forced, default="App")
        joined_sample = "\n".join(raw_lines[:10])
        if ".vscode-server" in joined_sample or "vscode" in joined_sample.lower():
            return "VS Code"
        if "antigravity-cli" in str(conv_dir).lower():
            return "CLI"
        return "App"

    def _extract_artifact_title(self, conv_dir: Path) -> Optional[str]:
        for md_name in ("implementation_plan.md", "task.md", "walkthrough.md"):
            candidate = conv_dir / md_name
            if candidate.exists():
                try:
                    for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line_s = line.strip()
                        if line_s.startswith("# "):
                            title = line_s.lstrip("# ").strip()
                            if title:
                                return title[:76]
                except OSError:
                    pass
        return None

    def _parse_brain_transcript(
        self,
        conv_dir: Path,
        conv_id: str,
        transcript_path: Path,
        account: AccountRecord,
        forced_interface: Optional[str] = None,
    ) -> Tuple[Optional[SessionRecord], List[TokenUsagePoint]]:
        raw_text = transcript_path.read_text(encoding="utf-8", errors="ignore")
        raw_lines = [ln for ln in raw_text.splitlines() if ln.strip()]
        if not raw_lines:
            return None, []

        interface = self._detect_interface(conv_dir, raw_lines, forced=forced_interface)
        session_name = self._extract_artifact_title(conv_dir) or ""
        current_model = "Gemini (Antigravity)"
        workspace_path = str(conv_dir)
        started_at = ""
        last_active_at = ""
        points: List[TokenUsagePoint] = []

        for idx, line in enumerate(raw_lines):
            try:
                obj = json.loads(line)
            except Exception:
                continue

            step_index = obj.get("step_index", idx)
            step_type = str(obj.get("type", ""))
            created_at_raw = obj.get("created_at")
            ts_iso, ts_epoch = parse_timestamp(created_at_raw)

            if not started_at or ts_iso < started_at:
                started_at = ts_iso
            if not last_active_at or ts_iso > last_active_at:
                last_active_at = ts_iso

            content = str(obj.get("content", "") or "")
            if content:
                m_model = _MODEL_CHANGE_RE.search(content)
                if m_model:
                    current_model = m_model.group(1).strip()

            if step_type == "USER_INPUT" and not session_name:
                session_name = extract_clean_session_title(content, fallback=f"Antigravity {conv_id[:8]}")

            if obj.get("model"):
                current_model = str(obj["model"])

            # Check for token counts on PLANNER_RESPONSE or any step carrying token metrics
            has_tokens = any(
                k in obj for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cached_tokens")
            )
            if step_type == "PLANNER_RESPONSE" or has_tokens:
                tokens_in = int(obj.get("input_tokens") or 0)
                tokens_out = int(obj.get("output_tokens") or 0)
                tokens_cached = int(obj.get("cache_read_tokens") or obj.get("cached_tokens") or 0)
                tokens_cache_write = int(obj.get("cache_creation_tokens") or 0)

                if tokens_in == 0 and tokens_out == 0 and tokens_cached == 0 and tokens_cache_write == 0:
                    continue

                event_id = make_event_id(
                    provider=self.provider,
                    account_id=account.account_id,
                    session_id=conv_id,
                    turn_ref=f"step_{step_index}_{ts_iso}",
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
                        session_id=conv_id,
                        session_name=session_name or f"Antigravity {conv_id[:8]}",
                        model=current_model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        tokens_cached=tokens_cached,
                        tokens_cache_write=tokens_cache_write,
                        turn_index=int(step_index),
                        source_ref=str(transcript_path),
                    )
                )

        if not session_name:
            session_name = f"Antigravity Session {conv_id[:8]}"

        # Ensure all points have the finalized session_name
        for pt in points:
            pt.session_name = session_name

        session_rec = SessionRecord(
            session_id=conv_id,
            user_id=account.user_id,
            account_id=account.account_id,
            provider=self.provider,
            interface=interface,
            session_name=session_name,
            workspace_path=workspace_path,
            model=current_model,
            started_at=started_at,
            last_active_at=last_active_at,
            metadata_json={"transcript_path": str(transcript_path)},
        )
        return session_rec, points
