"""The `snapjudge` command."""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path


def _guard_hook(args) -> int:
    from snapjudge.guard.hook import run

    return run()


def _guard_install(args) -> int:
    from snapjudge.guard.install import install, settings_path

    path = settings_path(args.scope, Path.cwd())
    added = install(path)
    print(f"{'Installed' if added else 'Already installed'}: {path}")
    print(
        "Engine: set SNAPJUDGE_ENGINE (e.g. 'jev' with TYPESAFE_API_KEY) for judgements;"
    )
    print("without it the guard runs its rules only. Check with: snapjudge guard test")
    return 0


def _guard_uninstall(args) -> int:
    from snapjudge.guard.install import settings_path, uninstall

    path = settings_path(args.scope, Path.cwd())
    print(("Removed from " if uninstall(path) else "Not installed in ") + str(path))
    return 0


def _guard_test(args) -> int:
    from snapjudge.guard.hook import run

    samples = [
        ("git status", "should allow"),
        ("git push --force origin main", "should hold"),
        (args.command, "yours") if args.command else None,
    ]
    for command, expect in filter(None, samples):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(Path.cwd()),
        }
        out = io.StringIO()
        run(io.StringIO(json.dumps(payload)), out)
        result = json.loads(out.getvalue()) if out.getvalue() else {}
        decision = result.get("hookSpecificOutput", {}).get(
            "permissionDecision", "allow"
        )
        print(f"{decision:>5}  {command}  ({expect})")
        if "systemMessage" in result:
            print(f"       note: {result['systemMessage']}")
    return 0


def _guard_stats(args) -> int:
    from snapjudge.guard.hook import home

    path = home() / "guard.jsonl"
    if not path.exists():
        print(f"No guard log yet at {path}")
        return 0
    records = [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
    outcomes = Counter(r["outcome"] for r in records)
    layers = Counter(r["layer"] for r in records)
    judged = [r for r in records if r["layer"] == "judge" and not r.get("error")]
    cost = sum(r["cost_usd"] or 0 for r in records)
    print(f"{len(records)} guarded calls  ({path})")
    print("outcomes: " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()))
    print("layers:   " + ", ".join(f"{k} {v}" for k, v in layers.most_common()))
    if records:
        print(f"holds per 1,000 calls: {1000 * outcomes['hold'] / len(records):.1f}")
    if judged:
        lat = sorted(r["latency_ms"] for r in judged)
        print(
            f"judge latency p50 {lat[len(lat) // 2]:.0f} ms, p95 {lat[int(len(lat) * 0.95)]:.0f} ms"
        )
    print(f"engine cost: ${cost:.4f}")
    errors = sum(1 for r in records if r.get("error"))
    if errors:
        print(f"judge errors: {errors} (ran rules only)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snapjudge")
    sub = parser.add_subparsers(dest="command", required=True)
    guard = sub.add_parser("guard", help="pre-action guard for coding agents")
    gsub = guard.add_subparsers(dest="guard_command", required=True)
    gsub.add_parser("hook", help="run as a Claude Code PreToolUse hook").set_defaults(
        func=_guard_hook
    )
    for name, func in (("install", _guard_install), ("uninstall", _guard_uninstall)):
        p = gsub.add_parser(name, help=f"{name} the Claude Code hook")
        p.add_argument(
            "--for", dest="agent", choices=["claude-code"], default="claude-code"
        )
        p.add_argument("--scope", choices=["user", "project"], default="user")
        p.set_defaults(func=func)
    t = gsub.add_parser("test", help="run sample commands through the guard")
    t.add_argument("command", nargs="?", help="a command of your own to check")
    t.set_defaults(func=_guard_test)
    gsub.add_parser("stats", help="summarise the guard log").set_defaults(
        func=_guard_stats
    )
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
