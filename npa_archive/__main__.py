"""Command line for sync, replay, and the round dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from npa_archive.client import NpaClient
from npa_archive.replay import render_replay
from npa_archive.sync import sync_archive


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="npa_archive",
        description="Archive public Never Play Alone agents and browse rounds locally.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    current = sub.add_parser("current", help="Print the live round from the public API")
    current.add_argument("--api", default=None, help="Override the API base URL")

    sync = sub.add_parser("sync", help="Save round standings and non-crown agent artifacts")
    sync.add_argument("--round", dest="round_id", default=None, help="Archive one round id, for example 2026-11-15")
    sync.add_argument(
        "--previous",
        type=int,
        default=1,
        help="How many latest completed rounds to download in full (default: 1)",
    )
    sync.add_argument("--all", action="store_true", help="Download every completed round")
    sync.add_argument("--skip-mcpr", action="store_true", help="Save code and detail without .mcpr replays")
    sync.add_argument("--workers", type=int, default=4, help="Parallel agent downloads")
    sync.add_argument("--api", default=None, help="Override the API base URL")

    replay = sub.add_parser("replay", help="Print every saved fact for one agent directory")
    replay.add_argument("agent_dir", type=Path)

    dashboard = sub.add_parser("dashboard", help="Serve the per-round web dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8780)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "current":
        client = NpaClient(args.api) if args.api else NpaClient()
        current = client.current_round()
        mission = current.get("mission") or {}
        mission_name = mission.get("name") if isinstance(mission, dict) else mission
        print(f"round {current.get('round_id')}  status {current.get('status')}  mission {mission_name}")
        print(f"block {current.get('current_block')}  window {current.get('start_block')} -> {current.get('end_block')}")
        return 0
    if args.command == "sync":
        client = NpaClient(args.api) if args.api else NpaClient()
        sync_archive(
            round_id=args.round_id,
            previous=None if args.all or args.round_id else args.previous,
            include_all=args.all,
            with_mcpr=not args.skip_mcpr,
            workers=args.workers,
            client=client,
        )
        return 0
    if args.command == "replay":
        sys.stdout.write(render_replay(args.agent_dir))
        return 0
    if args.command == "dashboard":
        import importlib.util

        from npa_archive.paths import DASHBOARD_STATIC

        server_path = DASHBOARD_STATIC.parent / "server.py"
        spec = importlib.util.spec_from_file_location("npa_dashboard_server", server_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"dashboard server is missing: {server_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.serve(host=args.host, port=args.port)
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
