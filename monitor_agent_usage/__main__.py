"""Allow executing monitor_agent_usage via `python3 -m monitor_agent_usage`."""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
