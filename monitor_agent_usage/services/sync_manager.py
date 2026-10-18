"""SyncManager: orchestrates historical backfill and live provider token collection."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..collectors import (
    AntigravityCollector,
    BaseCollector,
    ClaudeCollector,
    CodexCollector,
    GrokCollector,
    make_event_id,
    parse_timestamp,
)
from ..config import AppConfig, discover_user_roots
from ..db import AccountRecord, SessionRecord, SyncSummary, TimeSeriesDB, TokenUsagePoint, UserRecord


logger = logging.getLogger(__name__)


class SyncManager:
    """Manages multi-user account discovery, historical backfill, and periodic collector runs."""

    def __init__(self, db: TimeSeriesDB, config: Optional[AppConfig] = None) -> None:
        self.db = db
        self.config = config or AppConfig()
        self.collectors: Dict[str, BaseCollector] = {
            "antigravity": AntigravityCollector(db),
            "claude": ClaudeCollector(db),
            "codex": CodexCollector(db),
            "grok": GrokCollector(db),
        }
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_sync_results: List[Dict[str, Any]] = []
        self.last_sync_at: Optional[str] = None

    def ensure_default_accounts(self) -> List[AccountRecord]:
        """Auto-discover local and WSL user directories and register default local_fs accounts."""
        roots = discover_user_roots(self.config.extra_scan_roots)
        now_iso, _ = parse_timestamp(None)

        for root in roots:
            username = root.name
            is_wsl_win = "/mnt/c/Users/" in str(root)
            user_id = f"user_{username.lower()}"
            display_name = f"{username} (Windows Host)" if is_wsl_win else username

            self.db.upsert_user(
                UserRecord(
                    user_id=user_id,
                    username=username if not is_wsl_win else f"{username}_win",
                    display_name=display_name,
                    created_at=now_iso,
                )
            )

            # Register local filesystem collectors for each provider under this user root
            provider_paths = [
                (
                    "antigravity",
                    "Antigravity Local",
                    {
                        "root_path": str(root / ".gemini" / "antigravity"),
                        "cli_path": str(root / ".gemini" / "antigravity-cli"),
                    },
                ),
                (
                    "claude",
                    "Claude Local & Code",
                    {
                        "root_path": str(root / ".claude"),
                        "desktop_path": str(
                            root / "AppData" / "Roaming" / "Claude"
                            if is_wsl_win
                            else root / ".config" / "Claude"
                        ),
                    },
                ),
                (
                    "codex",
                    "Codex Local",
                    {
                        "root_path": str(root / ".codex"),
                    },
                ),
                (
                    "grok",
                    "Grok Local",
                    {
                        "root_path": str(root / ".grok"),
                    },
                ),
            ]

            for provider, label_prefix, cfg in provider_paths:
                suffix = "win" if is_wsl_win else "local"
                account_id = f"acct_{username.lower()}_{provider}_{suffix}"
                label = f"{label_prefix} ({username})"
                self.db.upsert_account(
                    AccountRecord(
                        account_id=account_id,
                        user_id=user_id,
                        provider=provider,
                        account_label=label,
                        auth_type="local_fs",
                        config_json=cfg,
                        enabled=True,
                    )
                )

        return self.db.get_accounts(only_enabled=True)

    def sync_all(self, user_id: Optional[str] = None, provider: Optional[str] = None) -> Dict[str, Any]:
        """Run historical and incremental sync across all enabled accounts."""
        self.ensure_default_accounts()
        accounts = self.db.get_accounts(user_id=user_id, provider=provider, only_enabled=True)

        summaries: List[Dict[str, Any]] = []
        total_sessions = 0
        total_events = 0

        for account in accounts:
            collector = self.collectors.get(account.provider.lower())
            if not collector:
                continue
            try:
                res: SyncSummary = collector.collect(account)
                summaries.append(res.to_dict())
                total_sessions += res.sessions_scanned
                total_events += res.events_ingested
            except PermissionError as exc:
                logger.debug("Permission denied syncing account %s: %s", account.account_id, exc)
                now_iso, _ = parse_timestamp(None)
                summaries.append(
                    SyncSummary(
                        provider=account.provider,
                        account_id=account.account_id,
                        errors=[f"Permission denied: {exc}"],
                        synced_at=now_iso,
                    ).to_dict()
                )
            except Exception as exc:
                logger.exception("Error syncing account %s", account.account_id)
                now_iso, _ = parse_timestamp(None)
                summaries.append(
                    SyncSummary(
                        provider=account.provider,
                        account_id=account.account_id,
                        errors=[str(exc)],
                        synced_at=now_iso,
                    ).to_dict()
                )

        now_iso, _ = parse_timestamp(None)
        self.last_sync_at = now_iso
        self.last_sync_results = summaries
        return {
            "synced_at": now_iso,
            "accounts_synced": len(accounts),
            "sessions_scanned": total_sessions,
            "events_ingested": total_events,
            "details": summaries,
        }

    def start_background_sync(self, interval_seconds: int = 60) -> None:
        """Start a daemon thread that periodically syncs all accounts."""
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.sync_all()
                except PermissionError as exc:
                    logger.debug("Background sync permission error: %s", exc)
                except Exception as exc:
                    logger.warning("Background sync error: %s", exc)
                self._stop_event.wait(interval_seconds)

        self._bg_thread = threading.Thread(target=_loop, name="monitor-usage-sync", daemon=True)
        self._bg_thread.start()

    def stop_background_sync(self) -> None:
        self._stop_event.set()

    def seed_demo_sessions(self) -> Dict[str, Any]:
        """Seed realistic multi-user, multi-provider, multi-interface time-series sessions for demonstration."""
        _, now_epoch = parse_timestamp(None)
        base_bucket = (now_epoch // 900) * 900

        demo_users = [
            UserRecord(
                user_id="user_demo_alex",
                username="alex",
                display_name="Alex Rivera (Demo)",
                email="alex@example.com",
            ),
            UserRecord(
                user_id="user_demo_sam",
                username="sam",
                display_name="Sam Chen (Demo)",
                email="sam@example.com",
            ),
        ]
        for u in demo_users:
            self.db.upsert_user(u)

        demo_specs = [
            # (user_id, provider, interface, model, session_name, workspace, bucket_offsets, in_base, out_base, cache_base)
            (
                "user_demo_alex",
                "grok",
                "Web",
                "grok-3",
                "Rust Async Runtime Lock Contention Analysis",
                "https://grok.com/chat/rust-async",
                [-12, -11, -10, -4, -3],
                4200,
                1650,
                14800,
            ),
            (
                "user_demo_alex",
                "grok",
                "CLI",
                "grok-3-mini",
                "Kubernetes Ingress Latency Triage",
                "~/src/infra-k8s",
                [-8, -7, -2, -1],
                3100,
                980,
                8400,
            ),
            (
                "user_demo_alex",
                "codex",
                "VS Code",
                "codex-mini-latest",
                "Refactor Payment Webhook Retry Queue",
                "~/src/payments-service",
                [-14, -13, -9, -6, -5, -1, 0],
                5400,
                2100,
                28500,
            ),
            (
                "user_demo_sam",
                "codex",
                "CLI",
                "o4-mini",
                "Generate SQLite to DuckDB Migration Script",
                "~/src/analytics-pipeline",
                [-10, -9, -5, -4, -2],
                3800,
                1450,
                19200,
            ),
            (
                "user_demo_sam",
                "claude",
                "App",
                "claude-opus-4",
                "Architecture Review: Multi-Region Failover",
                "Claude Desktop App",
                [-15, -14, -8, -7, -3, -2],
                6200,
                2850,
                34000,
            ),
            (
                "user_demo_alex",
                "claude",
                "VS Code",
                "claude-sonnet-4",
                "Implement WebSocket Reconnection Backoff",
                "~/src/realtime-gateway",
                [-11, -6, -5, -2, -1, 0],
                4900,
                1920,
                26400,
            ),
            (
                "user_demo_sam",
                "antigravity",
                "App",
                "Gemini 4 Argon (Medium)",
                "Build Token Telemetry Dashboard UI",
                "~/src/monitor-agent-usage",
                [-9, -8, -4, -3, -1, 0],
                7100,
                2400,
                31200,
            ),
        ]

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        for s_idx, (uid, prov, iface, model, s_name, ws, offsets, b_in, b_out, b_cache) in enumerate(demo_specs):
            acct_id = f"acct_demo_{uid}_{prov}"
            self.db.upsert_account(
                AccountRecord(
                    account_id=acct_id,
                    user_id=uid,
                    provider=prov,
                    account_label=f"{prov.title()} Demo ({iface})",
                    auth_type="demo",
                    config_json={"demo": True},
                    enabled=True,
                )
            )
            sess_id = f"demo_sess_{prov}_{s_idx + 1}"
            first_ep = base_bucket + (min(offsets) * 900) + 120
            last_ep = base_bucket + (max(offsets) * 900) + 480
            first_iso, _ = parse_timestamp(first_ep)
            last_iso, _ = parse_timestamp(last_ep)

            sessions.append(
                SessionRecord(
                    session_id=sess_id,
                    user_id=uid,
                    account_id=acct_id,
                    provider=prov,
                    interface=iface,
                    session_name=s_name,
                    workspace_path=ws,
                    model=model,
                    started_at=first_iso,
                    last_active_at=last_iso,
                    metadata_json={"demo": True},
                )
            )

            for t_idx, off in enumerate(offsets):
                ev_epoch = base_bucket + (off * 900) + ((t_idx * 137) % 750)
                ev_iso, _ = parse_timestamp(ev_epoch)
                factor = 0.85 + ((t_idx % 4) * 0.15)
                t_in = int(b_in * factor)
                t_out = int(b_out * factor)
                t_cached = int(b_cache * (0.6 + (t_idx * 0.12)))
                points.append(
                    TokenUsagePoint(
                        event_id=make_event_id(prov, acct_id, sess_id, f"demo_{off}_{t_idx}"),
                        timestamp=ev_iso,
                        timestamp_epoch=ev_epoch,
                        user_id=uid,
                        account_id=acct_id,
                        provider=prov,
                        interface=iface,
                        session_id=sess_id,
                        session_name=s_name,
                        model=model,
                        tokens_in=t_in,
                        tokens_out=t_out,
                        tokens_cached=t_cached,
                        tokens_cache_write=int(t_in * 0.2) if t_idx == 0 else 0,
                        turn_index=t_idx + 1,
                        source_ref="demo_seed",
                    )
                )

        self.db.upsert_sessions(sessions)
        self.db.upsert_usage_points(points)
        return {"seeded_sessions": len(sessions), "seeded_events": len(points)}

    def clear_demo_sessions(self) -> Dict[str, Any]:
        """Remove demo users, accounts, sessions, and usage points."""
        accounts = [a for a in self.db.get_accounts() if a.auth_type == "demo" or a.account_id.startswith("acct_demo_")]
        for acct in accounts:
            self.db.delete_account(acct.account_id)
        return {"removed_demo_accounts": len(accounts)}
