# REST API Reference

The local telemetry HTTP server provides a JSON REST API for querying time-series data, triggering synchronizations, and managing accounts.

All endpoints return JSON with `Cache-Control: no-store` and standard HTTP status codes.

---

## 1. Dashboard & Telemetry Endpoints

### `GET /api/dashboard`
The primary endpoint powering the web portal and visualizations.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `range_hours` | float | No | Rolling time window in hours (default: `24`). Set to `0` for all historical data. |
| `bucket_epoch` | int | No | Filter data strictly to a single 15-minute bucket epoch. |
| `user_id` | string | No | Filter by specific user ID, or `"all"`. |
| `provider` | string | No | Filter by provider (`"antigravity"`, `"claude"`, `"codex"`, `"grok"`, or `"all"`). |
| `interface` | string | No | Filter by surface (`"App"`, `"CLI"`, `"Web"`, `"VS Code"`, or `"all"`). |
| `search` | string | No | Free-text search query across session names, paths, or models. |

#### Response Schema Example
```json
{
  "filters": {
    "user_id": "all",
    "provider": "all",
    "interface": "all",
    "range_hours": 24.0,
    "selected_bucket_epoch": null,
    "start_epoch": 1729210000,
    "end_epoch": 1729296400
  },
  "kpis": {
    "tokens_in": 145200,
    "tokens_out": 38400,
    "tokens_cached": 1240000,
    "tokens_cache_write": 12000,
    "total_tokens": 1435600,
    "cache_hit_pct": 88.7,
    "session_count": 8,
    "user_count": 2,
    "turn_count": 64,
    "by_provider": [...],
    "by_interface": [...]
  },
  "timeseries_15m": [
    {
      "bucket_epoch": 1729256400,
      "bucket_end_epoch": 1729257300,
      "bucket_start_iso": "2026-10-18T10:00:00Z",
      "bucket_end_iso": "2026-10-18T10:15:00Z",
      "tokens_in": 7100,
      "tokens_out": 2400,
      "tokens_cached": 31200,
      "tokens_cache_write": 0,
      "total_tokens": 40700,
      "cache_hit_pct": 81.5,
      "session_count": 1,
      "turn_count": 4,
      "by_provider": {
        "antigravity": {
          "tokens_in": 7100,
          "tokens_out": 2400,
          "tokens_cached": 31200,
          "total_tokens": 40700
        }
      }
    }
  ],
  "sessions": [
    {
      "session_id": "conv-agy-123",
      "session_name": "Build 15m token telemetry graph",
      "user_id": "user_cstephan",
      "user_display_name": "cstephan",
      "provider": "antigravity",
      "interface": "App",
      "model": "Gemini 3.8 Flash (Medium)",
      "workspace_path": "/home/cstephan/src/monitor-agent-usage",
      "tokens_in": 7100,
      "tokens_out": 2400,
      "tokens_cached": 31200,
      "tokens_cache_write": 0,
      "total_tokens": 40700,
      "cache_hit_pct": 81.5,
      "turn_count": 4,
      "first_event_at": "2026-10-18T10:02:10Z",
      "last_event_at": "2026-10-18T10:14:05Z",
      "last_event_epoch": 1729257245
    }
  ],
  "users": [...],
  "accounts": [...],
  "last_sync_at": "2026-10-18T10:15:30Z",
  "bounds": {
    "min_epoch": 1729100000,
    "max_epoch": 1729296400,
    "event_count": 482
  }
}
```

---

### `GET /api/sessions/{session_id}/turns`
Returns chronological turn-by-turn token events for an individual session.

#### Query Parameters
- `bucket_epoch` (optional int): Only return turns within a specific 15-minute block.

#### Response
```json
{
  "session_id": "conv-agy-123",
  "turns": [
    {
      "event_id": "antigravity_ab82c9182371",
      "timestamp": "2026-10-18T10:02:10Z",
      "timestamp_epoch": 1729256530,
      "bucket_15m_epoch": 1729256400,
      "provider": "antigravity",
      "interface": "App",
      "model": "Gemini 3.8 Flash (Medium)",
      "tokens_in": 4000,
      "tokens_out": 500,
      "tokens_cached": 12000,
      "tokens_cache_write": 0,
      "total_tokens": 16500,
      "turn_index": 0
    }
  ]
}
```

---

## 2. Synchronization Endpoints

### `POST /api/sync`
Triggers an immediate historical and incremental sweep across all enabled accounts.

#### Request Body (Optional)
```json
{
  "user_id": "user_cstephan",
  "provider": "claude"
}
```

#### Response
```json
{
  "synced_at": "2026-10-18T15:35:00Z",
  "accounts_synced": 4,
  "sessions_scanned": 12,
  "events_ingested": 48,
  "details": [...]
}
```

---

## 3. User & Account Management Endpoints

### `GET /api/accounts`
Lists all registered accounts and users along with cumulative token totals.

### `POST /api/users`
Creates a new user profile and optionally registers local filesystem accounts.

#### Request Body
```json
{
  "username": "alex",
  "display_name": "Alex Rivera",
  "email": "alex@example.com",
  "home_dir": "/home/alex"
}
```

### `POST /api/accounts`
Registers an account for an existing user.

#### Request Body
```json
{
  "user_id": "user_alex",
  "provider": "claude",
  "account_label": "Alex Claude API",
  "auth_type": "api_key",
  "config_json": {
    "api_key": "sk-ant-admin-..."
  }
}
```

### `DELETE /api/accounts/{account_id}`
Deletes an account, removing all associated sessions and usage points.

---

## 4. Demonstration Endpoints

### `POST /api/demo/seed`
Seeds sample multi-user sessions across Grok, Codex, Antigravity, and Claude.

### `POST /api/demo/clear`
Removes all sample demo records from the database.

