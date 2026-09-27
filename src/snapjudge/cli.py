"""The `snapjudge` command."""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path


def _guard_hook(args) -> int:
    from snapjudge.guard.hook import run

    return run(agent=args.agent)


def _agents(args) -> list[str]:
    from snapjudge.guard.install import detected_agents

    if args.agent != "all":
        return [args.agent]
    found = detected_agents()
    if not found:
        print("No agent config directory found (~/.claude, ~/.cursor, ~/.codex).")
    return found


def _guard_install(args) -> int:
    from snapjudge.guard.hook import engine_spec, home
    from snapjudge.guard.install import (
        detect_engine,
        install,
        settings_path,
        write_engine,
    )

    for agent in _agents(args):
        path = settings_path(args.scope, Path.cwd(), agent)
        added = install(path, agent)
        print(f"{agent}: {'installed' if added else 'already installed'} in {path}")
        if agent == "codex":
            print("  codex: review and trust the new hook with /hooks before it runs.")
    existing = engine_spec()
    if existing and not args.detect:
        # A chosen engine is kept; re-detection only when asked for.
        print(f"Engine: keeping {existing} (re-detect with --detect).")
    else:
        spec, why = detect_engine()
        if spec:
            try:
                saved = write_engine(home(), spec)
            except ValueError as err:
                print(f"Engine: not saved ({err}).")
            else:
                print(f"Engine: {spec} ({why}); saved to {saved}")
                if spec.startswith("jev"):
                    print(
                        "  Jev reads TYPESAFE_API_KEY at hook time: make sure the agent's"
                    )
                    print(
                        "  environment has it, or the guard falls back to rules only."
                    )
        else:
            print(f"Engine: none ({why}).")
            print(
                "  For judgements: export TYPESAFE_API_KEY, or start an AgentJev server,"
            )
            print('  then run this install again; or set engine = "llm:<model>" in')
            print("  ~/.snapjudge/guard.toml.")
    print("Check with: snapjudge guard test")
    return 0


def _guard_uninstall(args) -> int:
    from snapjudge.guard.install import settings_path, uninstall

    for agent in _agents(args):
        path = settings_path(args.scope, Path.cwd(), agent)
        print(
            f"{agent}: "
            + ("removed from " if uninstall(path, agent) else "not installed in ")
            + str(path)
        )
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


def _rank(histogram: Counter[int], q: float) -> int:
    """Nearest-rank percentile: smallest value with at least q of samples at or below it."""
    target = max(1, math.ceil(q * sum(histogram.values())))
    seen = 0
    for value in sorted(histogram):
        seen += histogram[value]
        if seen >= target:
            return value
    return max(histogram)


def _guard_stats(args) -> int:
    from snapjudge.guard.hook import home

    path = home() / "guard.jsonl"
    if not path.exists():
        print(f"No guard log yet at {path}")
        return 0
    outcomes: Counter[str] = Counter()
    layers: Counter[str] = Counter()
    latency_ms: Counter[int] = Counter()  # 1 ms histogram: bounded memory
    total = errors = 0
    cost = 0.0
    with path.open() as fh:  # streamed: the log grows without bound
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a line cut off by a concurrent write
            total += 1
            outcomes[r["outcome"]] += 1
            layers[r["layer"]] += 1
            cost += r.get("cost_usd") or 0
            errors += bool(r.get("error"))
            if r["layer"] == "judge" and not r.get("error"):
                latency_ms[min(round(r["latency_ms"]), 60_000)] += 1  # <= 60k buckets
    print(f"{total} guarded calls  ({path})")
    print("outcomes: " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()))
    print("layers:   " + ", ".join(f"{k} {v}" for k, v in layers.most_common()))
    if total:
        print(f"holds per 1,000 calls: {1000 * outcomes['hold'] / total:.1f}")
    if latency_ms:
        p50, p95 = _rank(latency_ms, 0.5), _rank(latency_ms, 0.95)
        print(f"judge latency p50 {p50} ms, p95 {p95} ms")
    print(f"engine cost: ${cost:.4f}")
    if errors:
        print(f"calls with errors (engine or config; rules still ran): {errors}")
    return 0


def _dashboard(args) -> int:
    import webbrowser

    from snapjudge.dashboard.server import serve
    from snapjudge.guard.hook import home

    server = serve(home(), args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"snapjudge dashboard on {url} (Ctrl+C to stop); reading {home()}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snapjudge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "eval", help="compare engines on labelled cases (snapjudge eval --help)"
    )
    dash = sub.add_parser("dashboard", help="local page over the guard log")
    dash.add_argument("--port", type=int, default=8765)
    dash.add_argument("--no-browser", action="store_true", help="don't open a browser")
    dash.set_defaults(func=_dashboard)
    guard = sub.add_parser("guard", help="pre-action guard for coding agents")
    gsub = guard.add_subparsers(dest="guard_command", required=True)
    hook = gsub.add_parser("hook", help="run as an agent's pre-action hook")
    hook.add_argument(
        "--agent", choices=["claude-code", "cursor", "codex"], default="claude-code"
    )
    hook.set_defaults(func=_guard_hook)
    for name, func in (("install", _guard_install), ("uninstall", _guard_uninstall)):
        p = gsub.add_parser(name, help=f"{name} the Claude Code hook")
        p.add_argument(
            "--for",
            dest="agent",
            choices=["claude-code", "cursor", "codex", "all"],
            default="claude-code",
        )
        p.add_argument("--scope", choices=["user", "project"], default="user")
        if name == "install":
            p.add_argument(
                "--detect",
                action="store_true",
                help="re-detect the engine even if one is saved",
            )
        p.set_defaults(func=func)
    t = gsub.add_parser("test", help="run sample commands through the guard")
    t.add_argument("command", nargs="?", help="a command of your own to check")
    t.set_defaults(func=_guard_test)
    gsub.add_parser("stats", help="summarise the guard log").set_defaults(
        func=_guard_stats
    )
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["eval"]:  # its own argparse, so its --help and flags pass through
        from snapjudge.evaluate import main as eval_main

        return eval_main(argv[1:])
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
