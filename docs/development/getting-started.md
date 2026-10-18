# Developer Getting Started

This guide explains how to set up a local development environment, build the CLI and documentation, and run the test suite.

---

## Prerequisites

- **Linux** (e.g. Fedora, Ubuntu, Debian, RHEL) or **WSL2** on Windows
- **Python >= 3.10** (with `python3-venv` available)
- **Make** (`make`)

---

## Local Environment Setup (.venv)

The project uses a local virtual environment in `.venv` to isolate all dependencies:

### 1. Clone the Repository
```bash
git clone https://github.com/xk2600/monitor-agent-usage.git
cd monitor-agent-usage
```

### 2. View Available Makefile Targets
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

### 3. Install Package & CLI
```bash
make install
```
This builds and installs `monitor-agent-usage` in editable mode inside `.venv`. The `monitor_agent_usage` and `monitor-agent-usage` executables are placed in `.venv/bin/`.

### 4. Install Documentation Tools (MkDocs)
```bash
make install-docs
```
Installs `mkdocs`, `mkdocs-material`, and `pymdown-extensions` inside `.venv`.

---

## Running the Application Locally

```bash
# Launch the local dashboard on http://127.0.0.1:8420
make serve

# Or launch with sample multi-user demo sessions
.venv/bin/monitor_agent_usage serve --seed-demo
```

---

## Running the Documentation Site

```bash
# Build documentation and copy into web portal (monitor_agent_usage/web/docs/)
make docs-build

# Or run live auto-reloading documentation preview server on http://127.0.0.1:8000
make docs-serve
```

---

## Running the Automated Test Suite

```bash
make test
```
Executes all unit and integration tests across collectors, 15m bucketing, permissions handling, and HTTP endpoints.

