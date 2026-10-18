# Testing & Quality Assurance

The test suite is built on standard Python `unittest` to ensure zero dependencies and complete reproducibility in CI/CD pipelines.

---

## Running the Test Suite

```bash
# Using Makefile
make test

# Or using .venv directly with verbose output
.venv/bin/python3 -m unittest discover -s tests -v
```

---

## Test Architecture & Isolation

Every test case runs inside an isolated, temporary SQLite database and scratch directory:

```python
class MonitorAgentUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.db_path = self.root / "test_usage.sqlite"
        self.db = TimeSeriesDB(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
```

This guarantees:
1. Tests never touch your real developer session transcripts in `~/.gemini`, `~/.claude`, etc.
2. Every test starts with an empty, pristine schema and deterministic state.
3. Tests can run in parallel without file contention.

---

## Key Test Scenarios

### 1. Collector Ingestion & 15m Drilldown (`test_collectors_and_15m_bucket_drilldown`)
- Generates synthetic JSONL transcripts for Antigravity, Claude Code (VS Code), Codex CLI, and Grok Web across two distinct 15-minute intervals.
- Verifies that:
  - Antigravity, Claude, Codex, and Grok collectors successfully parse and ingest turns.
  - Bucket 1 (`17:00-17:15 UTC`) combines Antigravity turn 1 and Claude turn 1.
  - Bucket 2 (`17:15-17:30 UTC`) combines Antigravity turn 2, Codex, and Grok.
  - Filtering to Bucket 1 filters the sessions table strictly to the 2 chats active in that interval and reports their exact Bucket 1 token metrics.
  - Ingestion is idempotent: running sync again produces identical bucket totals.

### 2. Multi-User & Inaccessible Directory Protection (`test_permission_denied_user_root_and_collectors_graceful_handling`)
- Creates directories with restricted permissions (`chmod 0`) simulating another user's private home directory.
- Verifies:
  - `is_dir_accessible()` returns `False`.
  - `discover_user_roots()` skips the restricted directory without raising `PermissionError`.
  - All collectors handle inaccessible paths gracefully, returning 0 scanned sessions without throwing exceptions.
  - `SyncManager.sync_all()` completes successfully without emitting tracebacks.
  - In a directory with both accessible files and restricted subfolders, valid turns are ingested while restricted subfolders are safely bypassed.

### 3. Demo Lifecycle (`test_sync_manager_demo_lifecycle`)
- Tests seeding multi-user demo sessions and verifies KPI counts.
- Tests clearing demo data and ensures real data is unharmed.

### 4. HTTP Dashboard & REST Endpoints (`test_http_dashboard_and_15m_filter`)
- Spins up a background `ThreadingHTTPServer` on an ephemeral port.
- Queries `/api/dashboard?range_hours=24` and validates the JSON payload.
- Queries `/api/dashboard?bucket_epoch={epoch}` and confirms filtering consistency.

