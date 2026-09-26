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
        run(io.StringIO(json.dumps(payload)), out, record=False)
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
    outcomes: Counter[str] = Counter()
    layers: Counter[str] = Counter()
    latencies: list[float] = []
    total = errors = 0
    cost = 0.0
    with path.open() as fh:  # streamed: the log grows without bound
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            total += 1
            outcomes[r["outcome"]] += 1
            layers[r["layer"]] += 1
            cost += r.get("cost_usd") or 0
            errors += bool(r.get("error"))
            if r["layer"] == "judge" and not r.get("error"):
                latencies.append(r["latency_ms"])
    print(f"{total} guarded calls  ({path})")
    print("outcomes: " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()))
    print("layers:   " + ", ".join(f"{k} {v}" for k, v in layers.most_common()))
    if total:
        print(f"holds per 1,000 calls: {1000 * outcomes['hold'] / total:.1f}")
    if latencies:
        latencies.sort()
        p50, p95 = latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95)]
        print(f"judge latency p50 {p50:.0f} ms, p95 {p95:.0f} ms")
    print(f"engine cost: ${cost:.4f}")
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
