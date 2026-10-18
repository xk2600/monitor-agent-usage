# Historical Usage Portal Integration

The documentation site is integrated directly into the **Monitor Agent Usage** web server. This ensures that engineers, managers, and developers can seamlessly transition between reading architectural and schema documentation and viewing live or historical usage metrics.

---

## Unified Server Hosting

When running the application with `monitor_agent_usage serve --port 8420` (or `make serve`), the server routes both the application and the documentation from a single endpoint:

| Endpoint | Subsystem | Description |
|---|---|---|
| `http://127.0.0.1:8420/` | **Interactive Telemetry Portal** | Main dashboard, 15m time-series chart, and session table. |
| `http://127.0.0.1:8420/docs/` | **MkDocs Technical Site** | This complete technical documentation, architecture diagrams, and schema guide. |
| `http://127.0.0.1:8420/api/dashboard` | **REST Telemetry API** | JSON endpoint providing 15m bucket data, KPIs, and sessions. |
| `http://127.0.0.1:8420/api/health` | **Health & Sync Status** | Service health and timestamp of last synchronization. |

---

## Navigating Between Docs and the Portal

### From the Dashboard to Documentation
In the top header bar of the web dashboard, clicking the **Documentation** button opens the MkDocs documentation site in a new browser tab.

### From Documentation to the Portal
Every page in this documentation contains direct shortcut buttons to open the live portal:

[Launch Portal: All Historical Usage](http://127.0.0.1:8420/?range_hours=0){ .md-button .md-button--primary }
[Launch Portal: Last 24 Hours](http://127.0.0.1:8420/?range_hours=24){ .md-button }
[Launch Portal: Last 7 Days](http://127.0.0.1:8420/?range_hours=168){ .md-button }

---

## URL Query Parameters for Deep Linking

The web portal supports URL query parameters so you can bookmark, share, or link directly into specific filtered views from external tools, scripts, or documentation:

```
http://127.0.0.1:8420/?range_hours={hours}&provider={provider}&interface={interface}&user_id={user_id}&bucket_epoch={epoch}
```

### Parameter Reference

| Parameter | Type | Allowed Values | Example |
|---|---|---|---|
| `range_hours` | Number | `6`, `24`, `72`, `168`, `720`, or `0` (All Historical) | `?range_hours=168` |
| `provider` | String | `all`, `antigravity`, `claude`, `codex`, `grok` | `?provider=claude` |
| `interface` | String | `all`, `App`, `CLI`, `Web`, `VS Code` | `?interface=VS+Code` |
| `user_id` | String | `all` or specific user ID (e.g. `user_cstephan`) | `?user_id=user_cstephan` |
| `bucket_epoch`| Integer| 15-minute epoch timestamp | `?bucket_epoch=1729270800` |
| `search` | String | Text query matching session name, path, or model | `?search=postgres` |

---

## Embedding the Portal

If your team runs internal developer wiki platforms or dashboards, the historical usage portal can be embedded cleanly in an `<iframe>`:

```html
<iframe 
  src="http://127.0.0.1:8420/?range_hours=72&provider=all" 
  width="100%" 
  height="720px" 
  frameborder="0">
</iframe>
```

