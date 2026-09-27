# judgetap

**A pre-action guard for coding agents, built on one API for fast, typed AI decisions.**

<!-- Demo GIF: rendered by the "demo" workflow from docs/demo.tape; enable once docs/demo.gif is merged.
![judgetap guard holding a force-push](docs/demo.gif)
-->

```text
$ snapjudge guard test "git push --force origin main"
allow  git status  (should allow)
 deny  git push --force origin main  (should hold)
 deny  git push --force origin main  (yours)
```

## Quickstart

```bash
pip install "git+https://github.com/mergesafe-ai/judgetap"
snapjudge guard install --for claude-code        # or cursor, codex, all
snapjudge guard test "git push --force origin main"
```

> The code and CLI are still named `snapjudge`; they become `judgetap` with [#38](https://github.com/mergesafe-ai/judgetap/issues/38). The `judgetap` package on PyPI is a placeholder until then.

## What it does

- **Guard.** A hook that checks every shell command (and, in Claude Code, every file write and edit) before it runs. Rules catch common destructive forms: recursive deletes outside the workspace, force-pushes and pushes to protected branches, `DROP`/`DELETE` without `WHERE`, `terraform destroy`, and secrets written to files. With an engine configured, a model judges the rest: is it irreversible? off-task? against a rule in `AGENTS.md`? Without an engine, the rules fail closed: anything they can't vouch for asks you.
- **Library.** One API (`choice`, `score`, `yesno`, `batch`) over every Jev-style decision engine, with a cascade that escalates low-confidence answers to a stronger engine.
- **Eval.** `snapjudge eval cases.jsonl --engines jev,laya` compares engines on your labelled cases: accuracy, calibration (ECE), latency and cost.
- **Dashboard.** `snapjudge dashboard` is a local page with recent decisions, holds, asks, latency and cost per engine. You can mark a hold as a false alarm.

## Library

```python
import snapjudge as sj

sj.configure(sj.engines.load("jev"))  # needs TYPESAFE_API_KEY

verdict = sj.yesno(
    "Is this shell command hard to undo?",
    context={"command": "git push --force origin main", "task": "fix typo in README"},
)
verdict.value, verdict.p, verdict.engine  # ("yes", 0.97, "jev")

# Ask the cheap engine first, escalate low-confidence answers
sj.configure(sj.Cascade([sj.engines.load("jev"), sj.engines.load("llm:gemini/gemini-2.0-flash-lite")]))
```

## Engines

| Spec | Engine | Key |
|---|---|---|
| `jev` | TypeSafe Jev (hosted) | `TYPESAFE_API_KEY` |
| `jev@<url>` / `typesafe:<url>` | Any TypeSafe-compatible server | none on localhost; remote needs the key and https |
| `laya` | Laya, open weights, runs in process (`pip install "snapjudge[laya]"`) | none |
| `agentjev` / `agentjev:<url>` | A local AgentJev server | none |
| `llm:<model>` | Any LiteLLM model (`pip install "snapjudge[llm]"`); probabilities self-reported | the provider's |

On a CPU-only Linux box, install the CPU PyTorch wheel before `snapjudge[laya]` (`pip install torch --index-url https://download.pytorch.org/whl/cpu`). [snapjudge by Micha0827](https://github.com/Micha0827/snapjudge) is a TypeSafe-compatible local server for Apple Silicon (MLX), an independent project that works as a `jev@http://127.0.0.1:<port>` engine.

## Guard details

- **Agents:** Claude Code (shell, writes and edits), Cursor and Codex (shell only; their hooks don't expose writes and edits).
- **Engine:** `snapjudge guard install` uses one you already have (`$SNAPJUDGE_ENGINE`, a `TYPESAFE_API_KEY`, or a local AgentJev) and saves it in `~/.snapjudge/guard.toml`, because agents often run hooks without your shell's environment.
- **Keys:** read from the environment, then the OS keychain (`pip install "snapjudge[keychain]"`, then `snapjudge keys set TYPESAFE_API_KEY`). Keys are never written to config or logs.
- **Log:** every decision goes to `~/.snapjudge/guard.jsonl` (owner-only, with credentials redacted). See it with `snapjudge guard stats` or `snapjudge dashboard`.
- **Limits:** the rules are a best-effort denylist, not a sandbox. Shell is a full language; see [SPEC §5](docs/SPEC.md).

## Reviewed by MergeSafe

Every PR here is reviewed by [MergeSafe](https://mergesafe.ai) before merge. A few of the catches:

- [#17](https://github.com/mergesafe-ai/judgetap/pull/17): **P0**, commands with secrets in them were written to the guard log in plain text; also `sudo rm -rf /` and `$(rm -rf /)` slipping past the rules.
- [#16](https://github.com/mergesafe-ai/judgetap/pull/16): the cascade under-reported cost by dropping the engines it consulted before the winner.
- [#37](https://github.com/mergesafe-ai/judgetap/pull/37): quoted, attached and tab-separated `curl -u` credentials leaking past redaction.

## Status

Early development. Design in [docs/SPEC.md](docs/SPEC.md); roadmap in the issues.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0
