"""CLI for running saved interaction testcases.

Usage:
    python -m dapi_emu.workbench list
    python -m dapi_emu.workbench run [path/to/case.json ...]
    python -m dapi_emu.workbench run --all
    python -m dapi_emu.workbench show <name>

The runner spins up the FastAPI app **in-process** so dispatch and capture
share the same WORLD. To test against an external long-running server, use
the /workbench/run-all HTTP endpoint instead.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import runner


def _bootstrap_app():
    """Import the FastAPI app and load persistence if configured.

    For pure replay, we don't need uvicorn — we just need WORLD populated.
    Replay testcases reference bot/user/guild/channel IDs, so persistence
    must already contain them.
    """
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from dapi_emu import persistence
    from dapi_emu.state import WORLD
    db_path = os.environ.get("DAPI_DB_PATH")
    if db_path and persistence.is_enabled():
        persistence.init_db(db_path)
        persistence.load_world(WORLD, db_path)
    return WORLD


def cmd_list(_args) -> int:
    cases = runner.list_testcases()
    if not cases:
        print("(no testcases under {})".format(runner.testcase_dir()))
        return 0
    width = max(len(c["name"]) for c in cases)
    for c in cases:
        cmd = c.get("command") or "-"
        print(f"{c['name']:{width}}  {c['kind']:20}  {cmd}")
    return 0


def cmd_show(args) -> int:
    try:
        body = runner.load_testcase(args.name)
    except FileNotFoundError:
        print(f"not found: {args.name}", file=sys.stderr)
        return 2
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0


def _resolve_targets(args) -> list[dict]:
    out: list[dict] = []
    if args.all or not args.paths:
        for c in runner.list_testcases():
            out.append(runner.load_testcase(c["name"]))
        return out
    for p in args.paths:
        out.append(runner.load_testcase(p))
    return out


def cmd_run(args) -> int:
    _bootstrap_app()
    cases = _resolve_targets(args)
    if not cases:
        print("no testcases to run", file=sys.stderr)
        return 1
    passed = failed = 0
    for tc in cases:
        name = tc.get("name", "<unnamed>")
        report = runner.execute_testcase(tc, timeout=args.timeout)
        if report["passed"]:
            passed += 1
            print(f"PASS  {name}  ({report['elapsed_ms']}ms)")
        else:
            failed += 1
            print(f"FAIL  {name}  ({report['elapsed_ms']}ms)")
            for r in report["reasons"]:
                print(f"        - {r}")
            if args.verbose:
                msgs = report.get("all_new_messages") or []
                print(f"        captured {len(msgs)} messages:")
                for m in msgs:
                    print(f"          [{m['type']}] {m['author']['username']}: {m['content']!r}")
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dapi_emu.workbench",
                                     description="Discord Interaction Workbench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list saved testcases")

    show = sub.add_parser("show", help="print a testcase JSON")
    show.add_argument("name")

    run = sub.add_parser("run", help="execute one or more testcases")
    run.add_argument("paths", nargs="*",
                     help="testcase paths or names (defaults: --all)")
    run.add_argument("--all", action="store_true",
                     help="run every saved testcase")
    run.add_argument("--timeout", type=float, default=2.5)
    run.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
