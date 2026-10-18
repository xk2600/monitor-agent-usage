# Queries & Data Retrieval

This document outlines the core SQL queries executed by [`TimeSeriesDB`](../development/api-reference.md) to generate the metrics, charts, and tables for the web portal.

---

## 1. 15-Minute Bucket Aggregation Query

Invoked by `query_timeseries_15m()`. It groups token metrics strictly into 15-minute time slots (`bucket_15m_epoch`).

```sql
SELECT
    t.bucket_15m_epoch,
    SUM(t.tokens_in) AS tokens_in,
    SUM(t.tokens_out) AS tokens_out,
    SUM(t.tokens_cached) AS tokens_cached,
    SUM(t.tokens_cache_write) AS tokens_cache_write,
    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
    COUNT(DISTINCT t.session_id) AS session_count,
    COUNT(*) AS turn_count
FROM token_usage_ts t
WHERE t.timestamp_epoch >= ? AND t.timestamp_epoch <= ?
  AND LOWER(t.provider) = LOWER(?) -- Optional filter
  AND LOWER(t.interface) = LOWER(?) -- Optional filter
  AND t.user_id = ?                -- Optional filter
GROUP BY t.bucket_15m_epoch
ORDER BY t.bucket_15m_epoch ASC;
```

### Provider Breakdown per 15-Minute Bucket
To render provider-segmented hover tooltips on each 15-minute bar, a secondary grouped query runs:

```sql
SELECT
    t.bucket_15m_epoch,
    t.provider,
    SUM(t.tokens_in) AS tokens_in,
    SUM(t.tokens_out) AS tokens_out,
    SUM(t.tokens_cached) AS tokens_cached,
    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens
FROM token_usage_ts t
WHERE t.timestamp_epoch >= ? AND t.timestamp_epoch <= ?
GROUP BY t.bucket_15m_epoch, t.provider
ORDER BY t.bucket_15m_epoch ASC;
```

---

## 2. Summary KPI Metric Query

Invoked by `query_summary_kpis()`. Computes top-level cards for the selected time window (or selected 15-minute block).

```sql
SELECT
    COALESCE(SUM(t.tokens_in), 0) AS tokens_in,
    COALESCE(SUM(t.tokens_out), 0) AS tokens_out,
    COALESCE(SUM(t.tokens_cached), 0) AS tokens_cached,
    COALESCE(SUM(t.tokens_cache_write), 0) AS tokens_cache_write,
    COALESCE(SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write), 0) AS total_tokens,
    COUNT(DISTINCT t.session_id) AS session_count,
    COUNT(DISTINCT t.user_id) AS user_count,
    COUNT(*) AS turn_count
FROM token_usage_ts t
WHERE t.timestamp_epoch >= ? AND t.timestamp_epoch <= ?;
```

---

## 3. Session Aggregation & Search Query

Invoked by `query_sessions()`. Generates the **Chats & Token Consumption Table**. When a user clicks a 15-minute bar on the graph, `bucket_15m_epoch = ?` is appended to restrict the table strictly to active chats in that 15-minute block.

```sql
SELECT
    t.session_id,
    COALESCE(NULLIF(s.session_name, ''), t.session_name, t.session_id) AS session_name,
    t.user_id,
    COALESCE(u.display_name, u.username, t.user_id) AS user_display_name,
    t.account_id,
    COALESCE(a.account_label, t.account_id) AS account_label,
    t.provider,
    t.interface,
    COALESCE(NULLIF(MAX(t.model), ''), s.model, 'default') AS model,
    COALESCE(s.workspace_path, '') AS workspace_path,
    SUM(t.tokens_in) AS tokens_in,
    SUM(t.tokens_out) AS tokens_out,
    SUM(t.tokens_cached) AS tokens_cached,
    SUM(t.tokens_cache_write) AS tokens_cache_write,
    SUM(t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
    COUNT(*) AS turn_count,
    MIN(t.timestamp) AS first_event_at,
    MAX(t.timestamp) AS last_event_at,
    MAX(t.timestamp_epoch) AS last_event_epoch
FROM token_usage_ts t
LEFT JOIN sessions s ON s.session_id = t.session_id
LEFT JOIN users u ON u.user_id = t.user_id
LEFT JOIN accounts a ON a.account_id = t.account_id
WHERE t.bucket_15m_epoch = ? -- When filtering to specific 15-minute bar
  AND (t.session_name LIKE ? OR t.session_id LIKE ? OR t.model LIKE ? OR s.workspace_path LIKE ?)
GROUP BY t.session_id
ORDER BY last_event_epoch DESC, total_tokens DESC;
```

---

## 4. Turn-Level Drilldown Query

Invoked by `query_session_turns()` when an expandable row in the chat table is opened.

```sql
SELECT
    t.event_id,
    t.timestamp,
    t.timestamp_epoch,
    t.bucket_15m_epoch,
    t.provider,
    t.interface,
    t.model,
    t.tokens_in,
    t.tokens_out,
    t.tokens_cached,
    t.tokens_cache_write,
    (t.tokens_in + t.tokens_out + t.tokens_cached + t.tokens_cache_write) AS total_tokens,
    t.turn_index
FROM token_usage_ts t
WHERE t.session_id = ?
  AND t.bucket_15m_epoch = ? -- Optional: only turns within active 15m block
ORDER BY t.timestamp_epoch ASC, t.turn_index ASC;
```

---

## 5. Global Time Bounds Query

Invoked by `get_time_bounds()` to determine the absolute date boundary when selecting "All Historical Data":

```sql
SELECT
    MIN(timestamp_epoch) AS min_epoch,
    MAX(timestamp_epoch) AS max_epoch,
    COUNT(*) AS event_count
FROM token_usage_ts;
```
