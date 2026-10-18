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
- **Multi-User & WSL Host Discovery**: Tracks multiple user accounts and automatically discovers both Linux (`/home/*`) and WSL Windows host (`/mnt/c/Users/*`) session directories without permission errors.
- **Integrated MkDocs Documentation Portal**: Comprehensive technical documentation, architecture and entity-relationship diagrams, database schema, queries, and statistical formulae integrated directly into the web portal at `/docs/`.

---

## Quick Start (with Makefile & .venv)

### 1. View Makefile Help & Available Targets

```bash
make help
```

Output:
```text
========================================================================
 monitor-agent-usage Build & Execution Management
========================================================================
  help             Show this help message and target descriptions
  venv             Create local python virtual environment in .venv
  install          Install package in editable mode with CLI into .venv
  install-docs     Install documentation dependencies (mkdocs, mkdocs-material) into .venv
  run              Alias for serve
  serve            Run historical sync and launch local web dashboard (port 8420)
  sync             Run one-shot historical & incremental sync
  status           Print 24-hour summary status of tracked users and token usage
  test             Run unit tests with unittest discover
  docs-build       Build static MkDocs site and bundle into web portal
  docs-serve       Launch live MkDocs preview server (port 8000)
  clean            Clean build, egg-info, test caches, and site artifacts
```

### 2. Install Package into Local `.venv`

```bash
make install
```

This creates the isolated `.venv/` virtual environment and installs the CLI executable `monitor_agent_usage`.

### 3. Launch the Local Web Dashboard

```bash
make serve
# or
.venv/bin/monitor_agent_usage serve --port 8420
```

Then open **http://127.0.0.1:8420** in your browser.

To launch with realistic multi-user demo sessions pre-loaded:

```bash
.venv/bin/monitor_agent_usage serve --port 8420 --seed-demo
```

### 4. Build & View MkDocs Documentation

```bash
# Build documentation and integrate into web portal at http://127.0.0.1:8420/docs/
make docs-build

# Or run live preview server on http://127.0.0.1:8000
make docs-serve
```

### 5. Run One-Shot Historical / Background Sync (While UI Is Closed)

You can run `sync` from a cron job or `systemd --user` timer to continuously ingest usage while the web page is not open:

```bash
make sync
# or
.venv/bin/monitor_agent_usage sync
```

### 6. Check CLI Summary Status

```bash
make status
# or
.venv/bin/monitor_agent_usage status --hours 24
```

### 7. Run Unit Tests

```bash
make test
```
