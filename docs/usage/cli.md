# CLI Commands & Automation

Monitor Agent Usage provides a CLI executable named `monitor_agent_usage` (also available via `make` targets) for launching the web dashboard, executing batch historical syncs, or querying terminal status.

---

## Command Overview

```bash
monitor_agent_usage [--db-path PATH] [--scan-root PATH] {serve,sync,status} [options]
```

### Global Options

| Flag | Description | Default |
|---|---|---|
| `--db-path DB_PATH` | Path to the SQLite time-series database file. | `~/.local/share/monitor-agent-usage/usage_tsdb.sqlite` |
| `--scan-root SCAN_ROOT` | Additional user root directory to scan (can be passed multiple times). | `[]` |

---

## 1. `serve`: Web Dashboard & Live Sync

Runs an initial historical sync, starts the periodic background sync daemon, and binds the HTTP server:

```bash
# Using CLI directly
monitor_agent_usage serve --port 8420

# Using Makefile
make serve
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--host HOST` | Network interface to bind. | `127.0.0.1` |
| `--port PORT` | HTTP port to listen on. | `8420` |
| `--sync-interval SEC` | Background collector sweep interval in seconds. | `60` |
| `--seed-demo` | Automatically seeds demo sessions upon server startup. | `False` |

---

## 2. `sync`: One-Shot Historical & Incremental Ingestion

Executes a synchronous sweep across all enabled accounts, ingests any newly appended turns, updates checkpoints, and exits with a JSON summary. Ideal for running from cron or CI.

```bash
# Using CLI directly
monitor_agent_usage sync

# Filter sync to a specific provider
monitor_agent_usage sync --provider claude

# Using Makefile
make sync
```

### Output Example
```json
{
  "synced_at": "2026-10-18T15:30:00Z",
  "accounts_synced": 4,
  "sessions_scanned": 12,
  "events_ingested": 45,
  "details": [
    {
      "provider": "antigravity",
      "account_id": "acct_cstephan_antigravity_local",
      "sessions_scanned": 4,
      "events_ingested": 18,
      "errors": []
    }
  ]
}
```

---

## 3. `status`: Terminal Summary Metrics

Outputs a concise JSON summary of total tokens, cache hit percentage, session count, and breakdown by provider and interface over a rolling window.

```bash
# Using CLI directly (e.g. last 48 hours)
monitor_agent_usage status --hours 48

# Using Makefile (default 24h)
make status
```

---

## Automation: Scheduled Background Ingestion

To ensure your local database continuously collects token usage even when the web UI is closed:

### 1. Crontab (Every 15 Minutes)
Edit your crontab with `crontab -e`:

```cron
*/15 * * * * /home/cstephan/src/monitor-agent-usage/.venv/bin/monitor_agent_usage sync >/dev/null 2>&1
```

### 2. Systemd User Timer
Create `~/.config/systemd/user/monitor-agent-sync.service`:

```ini
[Unit]
Description=Monitor Agent Usage Background Sync

[Service]
Type=oneshot
ExecStart=/home/cstephan/src/monitor-agent-usage/.venv/bin/monitor_agent_usage sync
```

And timer `~/.config/systemd/user/monitor-agent-sync.timer`:

```ini
[Unit]
Description=Run Monitor Agent Usage Sync Every 10 Minutes

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the timer:
```bash
systemctl --user daemon-reload
systemctl --user enable --now monitor-agent-sync.timer
```

