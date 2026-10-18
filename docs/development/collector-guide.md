# Building Custom Collectors

Monitor Agent Usage makes it straightforward to add support for new AI coding tools, copilots, or provider APIs by writing a custom collector.

---

## The Collector Interface (`BaseCollector`)

Every collector inherits from [`BaseCollector`](file:///home/cstephan/src/monitor-agent-usage/monitor_agent_usage/collectors/base.py) and implements the `collect()` method:

```python
from monitor_agent_usage.collectors.base import BaseCollector
from monitor_agent_usage.db import AccountRecord, SyncSummary

class MyAgentCollector(BaseCollector):
    provider: str = "myagent"

    def collect(self, account: AccountRecord) -> SyncSummary:
        """Collect historical and incremental usage for the given account."""
        ...
```

### Return Contract
The `collect` method must return a [`SyncSummary`](file:///home/cstephan/src/monitor-agent-usage/monitor_agent_usage/db/models.py) record:
- `provider`: String matching provider identifier.
- `account_id`: String ID of the account being synced.
- `sessions_scanned`: Integer count of sessions parsed.
- `events_ingested`: Integer count of token usage points inserted/updated.
- `errors`: List of non-fatal string error messages encountered during sync.
- `synced_at`: ISO-8601 UTC timestamp.

---

## Safe Filesystem Access Rules

When reading local logs from disk, **never use raw `Path.exists()`, `Path.rglob()`, or `Path.read_text()` directly**. Always use the safe helpers imported from `.base`:

```python
from .base import (
    safe_exists,
    safe_is_dir,
    safe_is_file,
    safe_iterdir,
    safe_read_text,
    safe_rglob,
    safe_stat_mtime,
)

# 1. Check directory status safely
if not safe_is_dir(log_dir):
    return summary

# 2. Enumerate matching files recursively without crashing on restricted subfolders
for file_path in safe_rglob(log_dir, "*.jsonl"):
    # 3. Read text safely
    raw = safe_read_text(file_path)
    if not raw:
        continue
    ...
```

---

## Step-by-Step Implementation Example

Here is a minimal collector template:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import List

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
)

class MyAgentCollector(BaseCollector):
    provider = "myagent"

    def collect(self, account: AccountRecord) -> SyncSummary:
        now_iso, _ = parse_timestamp(None)
        summary = SyncSummary(
            provider=self.provider,
            account_id=account.account_id,
            synced_at=now_iso,
        )

        root_dir = Path(account.config_json.get("root_path", "~/.myagent")).expanduser()
        if not safe_is_dir(root_dir):
            return summary

        sessions: List[SessionRecord] = []
        points: List[TokenUsagePoint] = []

        for log_file in safe_rglob(root_dir, "*.jsonl"):
            try:
                sess, pts = self._parse_file(log_file, account)
                if sess and pts:
                    sessions.append(sess)
                    points.extend(pts)
            except Exception as exc:
                summary.errors.append(f"Error parsing {log_file.name}: {exc}")

        if sessions:
            self.db.upsert_sessions(sessions)
        if points:
            self.db.upsert_usage_points(points)

        summary.sessions_scanned = len(sessions)
        summary.events_ingested = len(points)
        self.db.update_account_sync_state(account.account_id, sync_cursor=now_iso, last_sync_at=now_iso)
        return summary
```

---

## Registering the Collector in SyncManager

Add the new collector to the dictionary in [`SyncManager.__init__`](file:///home/cstephan/src/monitor-agent-usage/monitor_agent_usage/services/sync_manager.py):

```python
self.collectors: Dict[str, BaseCollector] = {
    "antigravity": AntigravityCollector(db),
    "claude": ClaudeCollector(db),
    "codex": CodexCollector(db),
    "grok": GrokCollector(db),
    "myagent": MyAgentCollector(db),  # <-- Added
}
```

And in `ensure_default_accounts()` to register discovery paths under user home roots.

