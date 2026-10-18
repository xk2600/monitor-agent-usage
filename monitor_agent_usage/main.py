"""Main HTTP server and CLI entrypoint for monitor-agent-usage."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from .collectors.base import parse_timestamp
from .config import DEFAULT_HOST, DEFAULT_PORT, AppConfig
from .db import AccountRecord, TimeSeriesDB, UserRecord
from .services import SyncManager


WEB_DIR = Path(__file__).parent / "web"
DOCS_DIR = WEB_DIR / "docs"
FALLBACK_DOCS_DIR = Path(__file__).resolve().parent.parent / "site"


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Thread-safe HTTP handler serving the local web dashboard and JSON telemetry API."""

    db: TimeSeriesDB
    sync_manager: SyncManager
    app_config: AppConfig

    def log_message(self, format: str, *args: Any) -> None:
        # Keep console output clean unless debugging
        pass

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        mime, _ = mimetypes.guess_type(str(file_path))
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html")
            return

        if path == "/docs":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/docs/")
            self.end_headers()
            return

        if path.startswith("/docs/"):
            rel = path[len("/docs/") :].strip("/")
            docs_root = DOCS_DIR if DOCS_DIR.exists() else FALLBACK_DOCS_DIR
            if not docs_root.exists():
                self._send_json(
                    {
                        "error": "Documentation site not built yet. Run 'make docs-build' to generate docs.",
                        "portal": "http://127.0.0.1:8420/",
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            safe_target = (docs_root / (rel or "index.html")).resolve()
            if safe_target.is_dir():
                safe_target = safe_target / "index.html"
            if str(safe_target).startswith(str(docs_root.resolve())) and safe_target.exists():
                self._send_file(safe_target)
                return
            self._send_json({"error": "Documentation page not found"}, status=HTTPStatus.NOT_FOUND)
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            safe_path = (WEB_DIR / "static" / rel).resolve()
            if str(safe_path).startswith(str((WEB_DIR / "static").resolve())):
                self._send_file(safe_path)
                return
            self._send_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
            return

        if path == "/api/health":
            self._send_json({"status": "ok", "last_sync_at": self.sync_manager.last_sync_at})
            return

        if path == "/api/dashboard":
            self._handle_dashboard_query(qs)
            return

        if path.startswith("/api/sessions/") and path.endswith("/turns"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                session_id = urllib.parse.unquote(parts[2])
                bucket_raw = qs.get("bucket_epoch", [None])[0]
                bucket_epoch = int(bucket_raw) if bucket_raw and str(bucket_raw).isdigit() else None
                turns = self.db.query_session_turns(session_id=session_id, bucket_15m_epoch=bucket_epoch)
                self._send_json({"session_id": session_id, "turns": turns})
                return

        if path == "/api/accounts":
            accounts = [a.to_dict() for a in self.db.get_accounts()]
            users = self.db.list_users()
            self._send_json({"accounts": accounts, "users": users})
            return

        self._send_json({"error": f"Unknown endpoint: {path}"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/sync":
            user_id = body.get("user_id")
            provider = body.get("provider")
            result = self.sync_manager.sync_all(user_id=user_id, provider=provider)
            self._send_json(result)
            return

        if path == "/api/users":
            username = str(body.get("username") or "").strip()
            display_name = str(body.get("display_name") or username).strip()
            email = str(body.get("email") or "").strip()
            home_dir = str(body.get("home_dir") or "").strip()
            if not username:
                self._send_json({"error": "username is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            user_id = str(body.get("user_id") or f"user_{username.lower()}")
            now_iso, _ = parse_timestamp(None)
            user = self.db.upsert_user(
                UserRecord(
                    user_id=user_id,
                    username=username,
                    display_name=display_name,
                    email=email,
                    created_at=now_iso,
                )
            )
            if home_dir:
                root = Path(home_dir).expanduser()
                for prov, subdir in (
                    ("antigravity", ".gemini/antigravity"),
                    ("claude", ".claude"),
                    ("codex", ".codex"),
                    ("grok", ".grok"),
                ):
                    self.db.upsert_account(
                        AccountRecord(
                            account_id=f"acct_{username.lower()}_{prov}_custom",
                            user_id=user_id,
                            provider=prov,
                            account_label=f"{prov.title()} ({display_name})",
                            auth_type="local_fs",
                            config_json={"root_path": str(root / subdir)},
                            enabled=True,
                        )
                    )
                self.sync_manager.sync_all(user_id=user_id)
            self._send_json({"user": user.to_dict()}, status=HTTPStatus.CREATED)
            return

        if path == "/api/accounts":
            user_id = str(body.get("user_id") or "").strip()
            provider = str(body.get("provider") or "").strip().lower()
            account_label = str(body.get("account_label") or f"{provider.title()} Account").strip()
            auth_type = str(body.get("auth_type") or "api_key").strip()
            config_json = body.get("config_json") if isinstance(body.get("config_json"), dict) else {}

            if not user_id or provider not in ("grok", "codex", "antigravity", "claude"):
                self._send_json(
                    {"error": "Valid user_id and provider (grok, codex, antigravity, claude) required"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            account_id = str(
                body.get("account_id")
                or f"acct_{user_id}_{provider}_{int(dt.datetime.now(dt.timezone.utc).timestamp())}"
            )
            acct = self.db.upsert_account(
                AccountRecord(
                    account_id=account_id,
                    user_id=user_id,
                    provider=provider,
                    account_label=account_label,
                    auth_type=auth_type,
                    config_json=config_json,
                    enabled=True,
                )
            )
            sync_res = self.sync_manager.sync_all(user_id=user_id, provider=provider)
            self._send_json({"account": acct.to_dict(), "sync": sync_res}, status=HTTPStatus.CREATED)
            return

        if path == "/api/demo/seed":
            res = self.sync_manager.seed_demo_sessions()
            self._send_json(res)
            return

        if path == "/api/demo/clear":
            res = self.sync_manager.clear_demo_sessions()
            self._send_json(res)
            return

        self._send_json({"error": f"Unknown POST endpoint: {path}"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/accounts/"):
            account_id = urllib.parse.unquote(path[len("/api/accounts/") :].strip("/"))
            if account_id:
                self.db.delete_account(account_id)
                self._send_json({"deleted": account_id})
                return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_dashboard_query(self, qs: Dict[str, list[str]]) -> None:
        def _first(key: str, default: Optional[str] = None) -> Optional[str]:
            vals = qs.get(key)
            return vals[0] if vals else default

        user_id = _first("user_id", "all")
        provider = _first("provider", "all")
        interface = _first("interface", "all")
        search = _first("search", "")
        range_hours_str = _first("range_hours", "24")
        bucket_str = _first("bucket_epoch")

        _, now_epoch = parse_timestamp(None)
        bounds = self.db.get_time_bounds()

        try:
            range_hours = float(range_hours_str) if range_hours_str is not None else 24.0
        except ValueError:
            range_hours = 24.0

        start_epoch: Optional[int] = None
        end_epoch: Optional[int] = now_epoch

        if range_hours > 0:
            start_epoch = int(now_epoch - (range_hours * 3600))
            # If all recorded events are older than the selected window and user hasn't explicitly filtered,
            # still anchor end_epoch to now so real-time buckets appear accurately.
        else:
            start_epoch = bounds["min_epoch"]
            end_epoch = bounds["max_epoch"] or now_epoch

        selected_bucket: Optional[int] = None
        if bucket_str and str(bucket_str).lstrip("-").isdigit():
            selected_bucket = int(bucket_str)

        timeseries = self.db.query_timeseries_15m(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            user_id=user_id,
            provider=provider,
            interface=interface,
            fill_empty_buckets=(range_hours > 0 and range_hours <= 72),
        )

        window_kpis = self.db.query_summary_kpis(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            user_id=user_id,
            provider=provider,
            interface=interface,
        )

        active_kpis = (
            self.db.query_summary_kpis(
                bucket_15m_epoch=selected_bucket,
                user_id=user_id,
                provider=provider,
                interface=interface,
            )
            if selected_bucket is not None
            else window_kpis
        )

        sessions = self.db.query_sessions(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            bucket_15m_epoch=selected_bucket,
            user_id=user_id,
            provider=provider,
            interface=interface,
            search=search,
        )

        self._send_json(
            {
                "filters": {
                    "user_id": user_id,
                    "provider": provider,
                    "interface": interface,
                    "range_hours": range_hours,
                    "selected_bucket_epoch": selected_bucket,
                    "start_epoch": start_epoch,
                    "end_epoch": end_epoch,
                },
                "kpis": active_kpis,
                "window_kpis": window_kpis,
                "timeseries_15m": timeseries,
                "sessions": sessions,
                "users": self.db.list_users(),
                "accounts": [a.to_dict() for a in self.db.get_accounts()],
                "last_sync_at": self.sync_manager.last_sync_at,
                "bounds": bounds,
            }
        )


def create_http_server(
    host: str, port: int, db: TimeSeriesDB, sync_manager: SyncManager, app_config: AppConfig
) -> ThreadingHTTPServer:
    handler_cls = type(
        "BoundDashboardHandler",
        (DashboardRequestHandler,),
        {
            "db": db,
            "sync_manager": sync_manager,
            "app_config": app_config,
        },
    )
    return ThreadingHTTPServer((host, port), handler_cls)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor-agent-usage",
        description="Proxy-free multi-user token usage time-series database & web monitor for Grok, Codex, Antigravity, and Claude.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to the SQLite time-series database file.",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        default=[],
        help="Additional user home root directory to scan for local logs (can be passed multiple times).",
    )

    subparsers = parser.add_subparsers(dest="command")

    serve_p = subparsers.add_parser("serve", help="Run historical sync and launch the local web dashboard.")
    serve_p.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"Bind host (default: {DEFAULT_HOST})")
    serve_p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    serve_p.add_argument(
        "--sync-interval",
        type=int,
        default=60,
        help="Background sync interval in seconds (default: 60).",
    )
    serve_p.add_argument(
        "--seed-demo",
        action="store_true",
        help="Seed sample multi-user sessions across Grok, Codex, Antigravity, and Claude.",
    )

    sync_p = subparsers.add_parser(
        "sync",
        help="Run a one-shot historical & incremental sync into the time-series database (can be run via cron/systemd while web UI is closed).",
    )
    sync_p.add_argument("--provider", type=str, default=None, help="Filter sync to a single provider.")
    sync_p.add_argument("--seed-demo", action="store_true", help="Also seed demo multi-user data.")

    status_p = subparsers.add_parser("status", help="Print a summary of tracked users, sessions, and token usage.")
    status_p.add_argument("--hours", type=float, default=24.0, help="Time window in hours (default: 24).")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    cfg = AppConfig()
    if args.db_path:
        cfg.db_path = Path(args.db_path).expanduser().resolve()
    if args.scan_root:
        cfg.extra_scan_roots.extend(args.scan_root)

    db = TimeSeriesDB(cfg.db_path)
    sync_mgr = SyncManager(db, cfg)

    cmd = args.command or "serve"

    if cmd == "sync":
        if getattr(args, "seed_demo", False):
            sync_mgr.seed_demo_sessions()
        res = sync_mgr.sync_all(provider=getattr(args, "provider", None))
        print(json.dumps(res, indent=2))
        return 0

    if cmd == "status":
        sync_mgr.sync_all()
        _, now_epoch = parse_timestamp(None)
        hours = getattr(args, "hours", 24.0)
        start_ep = int(now_epoch - hours * 3600) if hours > 0 else None
        kpis = db.query_summary_kpis(start_epoch=start_ep, end_epoch=now_epoch)
        print(json.dumps(kpis, indent=2))
        return 0

    # Default: serve
    host = getattr(args, "host", DEFAULT_HOST)
    port = getattr(args, "port", DEFAULT_PORT)
    sync_interval = getattr(args, "sync_interval", 60)

    if getattr(args, "seed_demo", False):
        sync_mgr.seed_demo_sessions()

    sync_res = sync_mgr.sync_all()
    sync_mgr.start_background_sync(interval_seconds=sync_interval)

    server = create_http_server(host, port, db, sync_mgr, cfg)
    print(
        f"monitor-agent-usage running at http://{host}:{port} "
        f"(DB: {cfg.db_path}, sessions synced: {sync_res['sessions_scanned']}, events: {sync_res['events_ingested']})"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down monitor-agent-usage...")
    finally:
        sync_mgr.stop_background_sync()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
