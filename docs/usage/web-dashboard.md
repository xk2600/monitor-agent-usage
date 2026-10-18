# Interactive Web Dashboard

The web dashboard is a fast, responsive single-page application served directly from the Python backend on port `8420`. It gives engineering teams full visibility into token consumption across all active coding assistants.

[Open Web Dashboard](http://127.0.0.1:8420){ .md-button .md-button--primary }

---

## Dashboard Anatomy

```
+--------------------------------------------------------------------------------------------------+
|  Monitor Agent Usage  [Last sync: 2m ago]  [Docs]  [Load Sample Data]  [Accounts]  [Sync Now]    |
+--------------------------------------------------------------------------------------------------+
|  Filters: User [All Users v]  Provider [All] [Antigravity] [Claude] [Codex] [Grok]  Window [24h v]|
+--------------------------------------------------------------------------------------------------+
|  [Filtered to 15m Block: 17:15 - 17:30 UTC  (9.7k In / 3.1k Out / 31.5k Cached)]   [x Clear]     |
+--------------------------------------------------------------------------------------------------+
|  KPIs: [Tokens In: 145.2k]  [Tokens Out: 38.4k]  [Tokens Cached: 1.2M]  [Cache Hit: 89.2%]       |
+--------------------------------------------------------------------------------------------------+
|  15-Minute Bucketized Time-Series Chart (Interactive Canvas)                                     |
|  [|||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||] |
|   ^ Click any 15-minute bar to filter chats table below                                          |
+--------------------------------------------------------------------------------------------------+
|  Chats & Token Consumption Table                                                                 |
|  Session Name            | User   | Provider    | Interface | In     | Out   | Cached | Turns    |
|  > Build Telemetry UI    | csteph | Antigravity | App       | 7,100  | 2,400 | 31,200 | 4 turns  |
|  > Kubernetes Latency    | alex   | Grok        | CLI       | 3,100  |   980 |  8,400 | 2 turns  |
+--------------------------------------------------------------------------------------------------+
```

---

## 1. Top Controls & Sync Status
- **Sync Status**: Displays the timestamp of the last successful synchronization cycle.
- **Documentation Link**: Opens this complete documentation site in a new browser tab.
- **Load Sample Data**: Seeds realistic multi-user demo conversations across all 4 providers for demonstrations.
- **Accounts & Users**: Opens the modal to manage users, register custom file paths, or add provider API keys.
- **Sync Historical & Live**: Triggers an immediate full sync across all enabled accounts.

---

## 2. Multi-Dimensional Filter Bar
- **User Account**: Filter activity to a single developer account or view all users combined.
- **Provider Pills**: Toggle between **All**, **Antigravity**, **Claude**, **Codex**, and **Grok**.
- **Interface Pills**: Filter by execution surface (**App**, **CLI**, **Web**, or **VS Code**).
- **Time Window**: Select rolling periods (**Last 6 Hours**, **Last 24 Hours**, **Last 3 Days**, **Last 7 Days**, **Last 30 Days**, or **All Historical Data**).

---

## 3. 15-Minute Interactive Bar Chart

The chart renders 15-minute interval blocks on an HTML5 Canvas:
- **Navy / Blue Segment**: Uncached **Tokens In**.
- **Emerald Green Segment**: Generated **Tokens Out**.
- **Purple / Indigo Segment**: Prompt **Tokens Cached**.

### Hover Tooltip
Hovering over any bar reveals exact token metrics for that 15-minute interval, the prompt cache hit percentage, and a breakdown per provider.

### Clicking a 15-Minute Bar (Block Drill-Down)
When you click any 15-minute bar:
1. The bar is highlighted with a cyan outline.
2. The **Active 15-Minute Block Banner** appears with navigation buttons (`Prev 15m`, `Next 15m`, `Clear 15m Selection`).
3. The **Summary KPI Cards** recalculate to show the exact metrics incurred during that single 15-minute block.
4. The **Chats & Token Consumption Table** filters strictly to sessions that had activity during that 15-minute window, with token numbers showing only what was consumed within that block.

---

## 4. Chats Table & Turn-Level Inspector

The table provides complete visibility into individual conversations:
- **Session Title**: Extracted from prompt content or session metadata.
- **Interface & Model**: Displays the interface tag (**VS Code**, **CLI**, **App**, or **Web**) and LLM model version.
- **Workspace Path**: The local repository or working directory where the agent was invoked.
- **Expandable Turn Inspector**: Clicking the expand arrow (`>`) queries `/api/sessions/{id}/turns` to show every turn in chronological order with exact timestamps and token breakdowns.

