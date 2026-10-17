"""Configuration and path discovery for monitor-agent-usage."""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_PORT = 8420
DEFAULT_HOST = "127.0.0.1"


def get_data_dir() -> Path:
    """Return the persistent data directory for TSDB storage and configuration."""
    env_dir = os.environ.get("MONITOR_AGENT_USAGE_DATA_DIR")
    if env_dir:
        path = Path(env_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    candidates = []
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        candidates.append(Path(xdg_data).expanduser() / "monitor-agent-usage")
    candidates.append(Path.home() / ".local" / "share" / "monitor-agent-usage")
    candidates.append(Path(__file__).resolve().parent.parent / ".data")
    candidates.append(Path("/tmp/monitor-agent-usage"))

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path("/tmp")


def discover_user_roots(extra_roots: List[str] | None = None) -> List[Path]:
    """Discover local user home directories including WSL Windows host roots if present."""
    roots: List[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            resolved = str(p.resolve())
            if resolved not in seen and p.exists() and p.is_dir():
                seen.add(resolved)
                roots.append(p)
        except OSError:
            pass

    _add(Path.home())

    # Check /home/* for multi-user Linux setups
    home_parent = Path("/home")
    if home_parent.exists():
        try:
            for child in sorted(home_parent.iterdir()):
                _add(child)
        except OSError:
            pass

    # Check WSL Windows host user directories (/mnt/c/Users/*)
    wsl_users = Path("/mnt/c/Users")
    if wsl_users.exists():
        skip_names = {"All Users", "Default", "Default User", "Public", "desktop.ini"}
        try:
            for child in sorted(wsl_users.iterdir()):
                if child.name not in skip_names:
                    _add(child)
        except OSError:
            pass

    if extra_roots:
        for raw in extra_roots:
            _add(Path(raw).expanduser())

    return roots


@dataclass
class AppConfig:
    """Application runtime configuration."""

    db_path: Path = field(default_factory=lambda: get_data_dir() / "usage_tsdb.sqlite")
    config_file: Path = field(default_factory=lambda: get_data_dir() / "config.json")
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    extra_scan_roots: List[str] = field(default_factory=list)
    auto_sync_interval_seconds: int = 60
    default_username: str = field(default_factory=lambda: getpass.getuser() or "default")

    def load_saved_settings(self) -> Dict[str, Any]:
        if self.config_file.exists():
            try:
                return json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save_settings(self, data: Dict[str, Any]) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
