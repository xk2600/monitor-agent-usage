"""Unit and integration tests for monitor-agent-usage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from monitor_agent_usage.collectors import (
    AntigravityCollector,
    ClaudeCollector,
    CodexCollector,
    GrokCollector,
)
from monitor_agent_usage.config import AppConfig
from monitor_agent_usage.db import AccountRecord, TimeSeriesDB, UserRecord
from monitor_agent_usage.services import SyncManager


class MonitorAgentUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.db_path = self.root / "test_usage.sqlite"
        self.db = TimeSeriesDB(self.db_path)

        self.user1 = UserRecord(
            user_id="user_alice",
            username="alice",
            display_name="Alice Dev",
            email="alice@example.com",
        )
        self.user2 = UserRecord(
            user_id="user_bob",
            username="bob",
            display_name="Bob Ops",
            email="bob@example.com",
        )
        self.db.upsert_user(self.user1)
        self.db.upsert_user(self.user2)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_collectors_and_15m_bucket_drilldown(self) -> None:
        # 1. Prepare mock Antigravity brain directory (Bucket 1: 2026-10-17T17:00:00Z -> epoch 1792256400)
        agy_root = self.root / ".gemini" / "antigravity"
        conv_dir = agy_root / "brain" / "conv-agy-123" / ".system_generated" / "logs"
        conv_dir.mkdir(parents=True)
        transcript = conv_dir / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "step_index": 0,
                            "type": "USER_INPUT",
                            "created_at": "2026-10-17T17:02:10Z",
                            "content": "<USER_REQUEST>\nBuild 15m token telemetry graph\n</USER_REQUEST>",
                        }
                    ),
                    json.dumps(
                        {
                            "step_index": 1,
                            "type": "PLANNER_RESPONSE",
                            "created_at": "2026-10-17T17:03:00Z",
                            "input_tokens": 4000,
                            "output_tokens": 500,
                            "cache_read_tokens": 12000,
                        }
                    ),
                    # Second turn in NEXT 15-minute bucket (17:18:00Z)
                    json.dumps(
                        {
                            "step_index": 2,
                            "type": "PLANNER_RESPONSE",
                            "created_at": "2026-10-17T17:18:00Z",
                            "input_tokens": 2500,
                            "output_tokens": 800,
                            "cache_read_tokens": 15500,
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        acct_agy = AccountRecord(
            account_id="acct_alice_agy",
            user_id="user_alice",
            provider="antigravity",
            account_label="Alice AGY",
            auth_type="local_fs",
            config_json={"root_path": str(agy_root)},
        )
        self.db.upsert_account(acct_agy)
        res_agy = AntigravityCollector(self.db).collect(acct_agy)
        self.assertEqual(res_agy.sessions_scanned, 1)
        self.assertEqual(res_agy.events_ingested, 2)

        # 2. Prepare mock Claude Code VS Code session in 17:00-17:15 bucket
        claude_root = self.root / ".claude"
        claude_proj = claude_root / "projects" / "my-repo"
        claude_proj.mkdir(parents=True)
        claude_log = claude_proj / "sess-claude-vscode.jsonl"
        claude_log.write_text(
            "\n".join(
                [
                    json.dumps({"type": "summary", "summary": "Optimize PostgreSQL Indexes"}),
                    json.dumps(
                        {
                            "type": "user",
                            "entrypoint": "claude-vscode",
                            "timestamp": "2026-10-17T17:05:00Z",
                            "message": {"content": "<ide_opened_file>schema.sql</ide_opened_file> Optimize indexes"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": "2026-10-17T17:05:30Z",
                            "message": {
                                "id": "msg_01",
                                "model": "claude-sonnet-4",
                                "usage": {
                                    "input_tokens": 3000,
                                    "output_tokens": 900,
                                    "cache_read_input_tokens": 18000,
                                    "cache_creation_input_tokens": 1000,
                                },
                            },
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        acct_claude = AccountRecord(
            account_id="acct_alice_claude",
            user_id="user_alice",
            provider="claude",
            account_label="Alice Claude",
            auth_type="local_fs",
            config_json={"root_path": str(claude_root)},
        )
        self.db.upsert_account(acct_claude)
        res_claude = ClaudeCollector(self.db).collect(acct_claude)
        self.assertEqual(res_claude.sessions_scanned, 1)
        self.assertEqual(res_claude.events_ingested, 1)

        # 3. Prepare mock Codex CLI session for User 2 (Bob) in 17:15-17:30 bucket
        codex_root = self.root / ".codex"
        codex_sess_dir = codex_root / "sessions" / "2026" / "10" / "17"
        codex_sess_dir.mkdir(parents=True)
        codex_log = codex_sess_dir / "rollout-codex-1.jsonl"
        codex_log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": "2026-10-17T17:20:00Z",
                            "type": "session_meta",
                            "payload": {
                                "id": "codex-sess-99",
                                "originator": "codex_cli_rs",
                                "cwd": "/home/bob/api",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-10-17T17:20:05Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "Add rate limiting middleware",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-10-17T17:21:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 15000,
                                        "cached_input_tokens": 10000,
                                        "output_tokens": 1200,
                                    }
                                },
                            },
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        acct_codex = AccountRecord(
            account_id="acct_bob_codex",
            user_id="user_bob",
            provider="codex",
            account_label="Bob Codex",
            auth_type="local_fs",
            config_json={"root_path": str(codex_root)},
        )
        self.db.upsert_account(acct_codex)
        res_codex = CodexCollector(self.db).collect(acct_codex)
        self.assertEqual(res_codex.sessions_scanned, 1)
        self.assertEqual(res_codex.events_ingested, 1)

        # 4. Prepare mock Grok Web session in 17:15-17:30 bucket
        grok_root = self.root / ".grok"
        grok_root.mkdir(parents=True)
        grok_log = grok_root / "grok-web-chat.json"
        grok_log.write_text(
            json.dumps(
                {
                    "conversationId": "grok-conv-77",
                    "title": "Analyze CUDA Kernel Occupancy",
                    "interface": "Web",
                    "messages": [
                        {
                            "timestamp": "2026-10-17T17:22:00Z",
                            "model": "grok-3",
                            "usage": {
                                "input_tokens": 2200,
                                "output_tokens": 1100,
                                "cached_tokens": 6000,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        acct_grok = AccountRecord(
            account_id="acct_bob_grok",
            user_id="user_bob",
            provider="grok",
            account_label="Bob Grok",
            auth_type="local_fs",
            config_json={"root_path": str(grok_root)},
        )
        self.db.upsert_account(acct_grok)
        res_grok = GrokCollector(self.db).collect(acct_grok)
        self.assertEqual(res_grok.sessions_scanned, 1)
        self.assertEqual(res_grok.events_ingested, 1)

        # 5. Verify 15-minute bucketing
        buckets = self.db.query_timeseries_15m(fill_empty_buckets=False)
        self.assertEqual(len(buckets), 2)

        b1 = buckets[0]  # 17:00 - 17:15 UTC
        b2 = buckets[1]  # 17:15 - 17:30 UTC
        self.assertEqual(b1["bucket_start_iso"], "2026-10-17T17:00:00Z")
        self.assertEqual(b2["bucket_start_iso"], "2026-10-17T17:15:00Z")

        # Bucket 1 has Antigravity turn 1 (in=4000, out=500, cached=12000) + Claude turn 1 (in=3000, out=900, cached=18000)
        self.assertEqual(b1["tokens_in"], 7000)
        self.assertEqual(b1["tokens_out"], 1400)
        self.assertEqual(b1["tokens_cached"], 30000)
        self.assertEqual(b1["session_count"], 2)

        # Bucket 2 has Antigravity turn 2 (in=2500, out=800, cached=15500) + Codex (in=5000, out=1200, cached=10000) + Grok (in=2200, out=1100, cached=6000)
        self.assertEqual(b2["tokens_in"], 9700)
        self.assertEqual(b2["tokens_out"], 3100)
        self.assertEqual(b2["tokens_cached"], 31500)
        self.assertEqual(b2["session_count"], 3)

        # 6. Verify clicking Bucket 1 filters the sessions table to ONLY chats in Bucket 1 and their Bucket 1 tokens
        b1_sessions = self.db.query_sessions(bucket_15m_epoch=b1["bucket_epoch"])
        self.assertEqual(len(b1_sessions), 2)
        by_id_b1 = {s["session_id"]: s for s in b1_sessions}
        self.assertEqual(by_id_b1["conv-agy-123"]["tokens_in"], 4000)
        self.assertEqual(by_id_b1["conv-agy-123"]["session_name"], "Build 15m token telemetry graph")
        self.assertEqual(by_id_b1["sess-claude-vscode"]["interface"], "VS Code")
        self.assertEqual(by_id_b1["sess-claude-vscode"]["session_name"], "Optimize PostgreSQL Indexes")

        # 7. Verify idempotency when re-running historical sync
        res_agy_repeat = AntigravityCollector(self.db).collect(acct_agy)
        self.assertEqual(res_agy_repeat.events_ingested, 2)
        buckets_after = self.db.query_timeseries_15m(fill_empty_buckets=False)
        self.assertEqual(buckets_after[0]["tokens_in"], 7000)

    def test_sync_manager_demo_lifecycle(self) -> None:
        cfg = AppConfig(db_path=self.db_path)
        mgr = SyncManager(self.db, cfg)
        seeded = mgr.seed_demo_sessions()
        self.assertGreater(seeded["seeded_sessions"], 0)
        kpis = self.db.query_summary_kpis()
        self.assertGreater(kpis["total_tokens"], 0)

        mgr.clear_demo_sessions()
        kpis_after = self.db.query_summary_kpis()
        self.assertEqual(kpis_after["total_tokens"], 0)

    def test_http_dashboard_and_15m_filter(self) -> None:
        import threading
        import urllib.request
        from monitor_agent_usage.main import create_http_server

        cfg = AppConfig(db_path=self.db_path)
        mgr = SyncManager(self.db, cfg)
        mgr.seed_demo_sessions()

        server = create_http_server("127.0.0.1", 0, self.db, mgr, cfg)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard?range_hours=24") as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("timeseries_15m", data)
            self.assertIn("sessions", data)
            self.assertGreater(len(data["sessions"]), 0)

            active_buckets = [b for b in data["timeseries_15m"] if b["total_tokens"] > 0]
            self.assertGreater(len(active_buckets), 0)
            chosen_bucket = active_buckets[0]["bucket_epoch"]

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/dashboard?range_hours=24&bucket_epoch={chosen_bucket}"
            ) as resp2:
                filtered = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(filtered["filters"]["selected_bucket_epoch"], chosen_bucket)
            self.assertEqual(len(filtered["sessions"]), active_buckets[0]["session_count"])

            # Verify MkDocs portal integration endpoint (/docs/)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/docs/") as docs_resp:
                self.assertEqual(docs_resp.status, 200)
                docs_html = docs_resp.read().decode("utf-8")
                self.assertIn("Monitor Agent Usage", docs_html)
        finally:
            server.shutdown()
            server.server_close()

    def test_permission_denied_user_root_and_collectors_graceful_handling(self) -> None:
        import os
        from monitor_agent_usage.config import discover_user_roots, is_dir_accessible

        forbidden_dir = self.root / "forbidden_user"
        forbidden_dir.mkdir()
        os.chmod(forbidden_dir, 0)

        # A readable directory with a forbidden subdirectory
        partially_forbidden_dir = self.root / "partial_user"
        partially_forbidden_dir.mkdir()
        secret_sub = partially_forbidden_dir / "secret"
        secret_sub.mkdir()
        os.chmod(secret_sub, 0)

        try:
            # 1. is_dir_accessible must return False on inaccessible directory
            self.assertFalse(is_dir_accessible(forbidden_dir))
            self.assertTrue(is_dir_accessible(partially_forbidden_dir))

            # 2. discover_user_roots must not include inaccessible directory
            discovered = discover_user_roots(extra_roots=[str(forbidden_dir)])
            self.assertNotIn(forbidden_dir.resolve(), [d.resolve() for d in discovered])

            # Upsert test users for foreign key integrity
            self.db.upsert_user(UserRecord(user_id="user_forb", username="forb", display_name="Forbidden User"))
            self.db.upsert_user(UserRecord(user_id="user_partial", username="partial", display_name="Partial User"))

            # 3. Test each collector directly on forbidden paths
            for provider_cls, prov_name, root_cfg in [
                (AntigravityCollector, "antigravity", {"root_path": str(forbidden_dir / ".gemini" / "antigravity")}),
                (ClaudeCollector, "claude", {"root_path": str(forbidden_dir / ".claude"), "desktop_path": str(forbidden_dir / "app")}),
                (CodexCollector, "codex", {"root_path": str(forbidden_dir / ".codex")}),
                (GrokCollector, "grok", {"root_path": str(forbidden_dir / ".grok")}),
            ]:
                acct = AccountRecord(
                    account_id=f"acct_forb_{prov_name}",
                    user_id="user_forb",
                    provider=prov_name,
                    account_label=f"Forbidden {prov_name}",
                    auth_type="local_fs",
                    config_json=root_cfg,
                )
                self.db.upsert_account(acct)
                collector = provider_cls(self.db)
                summary = collector.collect(acct)
                self.assertEqual(summary.sessions_scanned, 0)
                self.assertEqual(summary.events_ingested, 0)

            # 4. SyncManager.sync_all must succeed without raising PermissionError
            cfg = AppConfig(db_path=self.db_path, extra_scan_roots=[str(forbidden_dir)])
            mgr = SyncManager(self.db, cfg)
            sync_res = mgr.sync_all()
            self.assertIsInstance(sync_res, dict)
            self.assertIn("accounts_synced", sync_res)

            # 5. Collectors must safely ingest valid files in a directory that has a forbidden subfolder
            claude_partial_root = partially_forbidden_dir / ".claude"
            claude_projects = claude_partial_root / "projects"
            claude_projects.mkdir(parents=True)
            sample_jsonl = claude_projects = claude_partial_root / "projects" / "test.jsonl"
            sample_jsonl.write_text(
                json.dumps(
                    {
                        "sessionId": "partial-sess-1",
                        "type": "summary",
                        "summary": "Partial dir test",
                        "message": {
                            "usage": {"input_tokens": 100, "output_tokens": 50},
                            "model": "claude-sonnet-4",
                        },
                    }
                )
            )

            acct_claude_partial = AccountRecord(
                account_id="acct_partial_claude",
                user_id="user_partial",
                provider="claude",
                account_label="Partial Claude",
                auth_type="local_fs",
                config_json={"root_path": str(claude_partial_root)},
            )
            self.db.upsert_account(acct_claude_partial)
            summary_partial = ClaudeCollector(self.db).collect(acct_claude_partial)
            self.assertEqual(summary_partial.sessions_scanned, 1)
            self.assertEqual(summary_partial.events_ingested, 1)

        finally:
            os.chmod(forbidden_dir, 0o777)
            os.chmod(secret_sub, 0o777)


if __name__ == "__main__":
    unittest.main()
