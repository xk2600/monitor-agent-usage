# monitor-agent-usage

A proxy-free, multi-user **Time-Series Token Usage Database & Interactive Web Dashboard** that tracks token consumption (**Tokens In**, **Tokens Out**, and **Cached Tokens**) across:

- **Grok** (`xAI API`, `grok.com` Web/App, Local Grok CLI)
- **Codex** (`~/.codex/sessions` CLI & VS Code Extension, OpenAI Organization Usage API, ChatGPT/Codex Web)
- **Antigravity** (`~/.gemini/antigravity/brain` App & IDE transcripts, `~/.gemini/antigravity-cli`)
- **Claude** (`~/.claude/projects` Claude Code CLI & VS Code Extension, Claude Desktop App, Anthropic Usage API, `claude.ai` Web)

---

## Key Features

- **100% Proxy-Free Operation**: Never intercepts or MITMs your traffic. Reads local append-only session transcripts (`~/.gemini/antigravity`, `~/.claude`, `~/.codex`, `~/.grok`) and talks directly to provider usage/conversation APIs.
- **15-Minute Bucketized Time-Series Graph**: Visualizes `Tokens In`, `Tokens Out`, and `Cached Tokens` in 15-minute intervals.
- **Interactive 15-Minute Block Drill-Down**: Clicking any **15-minute bar** on the time-series graph highlights that interval and filters the **Chats & Token Consumption Table** below strictly to chats active during that 15-minute block.
- **Interface & Session Name Detection**: Distinguishes between **App**, **CLI**, **Web**, and **VS Code** interfaces and extracts human-readable **Session Names** alongside expandable per-turn token breakdowns.
- **Historical Sync While Closed**: Automatically backfills all historical sessions that occurred while the web dashboard was closed, using deterministic SHA-256 event deduplication and per-account sync checkpoints.
- **Multi-User & WSL Host Discovery**: Tracks multiple user accounts and automatically discovers both Linux (`/home/*`) and WSL Windows host (`/mnt/c/Users/*`) session directories.

---

## Quick Start

### 1. Launch the Local Web Dashboard

```bash
python3 -m monitor_agent_usage serve --port 8420
```

Then open **http://127.0.0.1:8420** in your browser.

To launch with sample multi-user sessions pre-loaded alongside your real local sessions:

```bash
python3 -m monitor_agent_usage serve --port 8420 --seed-demo
```

### 2. Run One-Shot Historical / Background Sync (While UI Is Closed)

You can run `sync` from a cron job or `systemd --user` timer to continuously ingest usage while the web page is not open:

```bash
python3 -m monitor_agent_usage sync
```

### 3. Check CLI Summary Status

```bash
python3 -m monitor_agent_usage status --hours 24
```

### 4. Run Tests

```bash
python3 -m unittest discover -s tests -v
```
