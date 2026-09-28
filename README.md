# judgetap

**A pre-action guard for coding agents, built on one API for fast, typed AI decisions.**

<!-- Demo GIF: rendered by the "demo" workflow from docs/demo.tape; enable once docs/demo.gif is merged.
![judgetap guard holding a force-push](docs/demo.gif)
-->

```text
$ judgetap guard test "git push --force origin main"
allow  git status  (should allow)
 deny  git push --force origin main  (should hold)
 deny  git push --force origin main  (yours)
```

## Quickstart

```bash
pip install judgetap          # latest release
# pip install --pre judgetap  # newest build from main (X.Y.Z.devN)
judgetap guard install --for claude-code        # or cursor, codex, all
judgetap guard test "git push --force origin main"
```


## What it does

- **Guard.** A hook that checks every shell command (and, in Claude Code, every file write and edit) before it runs. Rules catch common destructive forms: recursive deletes outside the workspace, force-pushes and pushes to protected branches, `DROP`/`DELETE` without `WHERE`, `terraform destroy`, and secrets written to files. With an engine configured, a model judges the rest: is it irreversible? off-task? against a rule in `AGENTS.md`? Without an engine, the rules fail closed for shell commands (anything they can't vouch for asks you); file writes and edits are checked for secrets, your own rules, and writes to the guard's own configuration (which ask).
- **Library.** One API (`choice`, `score`, `yesno`, `batch`) over every Jev-style decision engine, with a cascade that escalates low-confidence answers to a stronger engine.
- **Eval.** `judgetap eval cases.jsonl --engines jev,laya` compares engines on your labelled cases: accuracy, calibration (ECE), latency and cost. `judgetap eval --suite banking77 --limit 200 --engines jev` runs a public suite instead (`ag_news`, `banking77`): it is downloaded from Hugging Face on first use, converted to judgetap's case format and cached in `~/.judgetap/suites`; nothing is bundled. `--limit N` takes the same fixed-seed sample every run. Each suite's licence and source URL are listed in `src/judgetap/suites.py`.
- **Dashboard.** `judgetap dashboard` is a local page with recent decisions, holds, asks, latency and cost per engine. You can mark a hold as a false alarm.

## Library

```python
import judgetap as sj

sj.configure(sj.engines.load("jev"))  # needs TYPESAFE_API_KEY

verdict = sj.yesno(
    "Is this shell command hard to undo?",
    context={"command": "git push --force origin main", "task": "fix typo in README"},
)
verdict.value, verdict.p, verdict.engine  # ("yes", 0.97, "jev")

# Ask the cheap engine first, escalate low-confidence answers
jev = sj.engines.load("jev")
flash = sj.engines.load("llm:gemini/gemini-2.0-flash-lite")
sj.configure(sj.Cascade([jev, flash]))
```

## Engines

| Spec | Engine | Key |
|---|---|---|
| `jev` | TypeSafe Jev (hosted) | `TYPESAFE_API_KEY` |
| `jev@<url>` / `typesafe:<url>` | Any TypeSafe-compatible server | none on localhost; remote needs the key and https |
| `laya` | Laya, open weights, runs in process (`pip install "judgetap[laya]"`) | none |
| `julia` / `julia:<path>` | [Julia-1](https://huggingface.co/SupersonicLabs/Julia-1), 144M open weights, runs in process on CPU (download into `~/.judgetap/models/Julia-1` and `pip install -e` it; relative paths never come from the working directory). Loads per process, so for the guard prefer a server engine | none |
| `gliner[:<hf model>]` | Fastino GLiNER2.5-Decide, 340M encoder, CPU or GPU (`pip install "judgetap[gliner]"`); only a full probability map over every option counts as calibrated; a winner-only score (the others share the rest evenly) or a bare label is uncalibrated. Loads in-process, so the guard refuses it: use a server engine there | none |
| `agentjev` / `agentjev:<url>` | A local AgentJev server | none |
| `llm:<model>` | Any LiteLLM model (`pip install "judgetap[llm]"`); probabilities self-reported | the provider's |

On a CPU-only Linux box, install the CPU PyTorch wheel before `judgetap[laya]` (`pip install torch --index-url https://download.pytorch.org/whl/cpu`). [snapjudge by Micha0827](https://github.com/Micha0827/snapjudge) is a TypeSafe-compatible local server for Apple Silicon (MLX), an independent project that works as a `jev@http://127.0.0.1:<port>` engine.

## Guard details

**Loop detection (Claude Code).** `PostToolUse` and `PostToolUseFailure` hooks (`judgetap guard post`, added by `guard install --for claude-code`) notices when the same command or edit fails the same way three times within eight actions and adds a note asking the agent to re-plan. It never blocks, uses no model, and stores only a redacted action and an error hash per session. Cursor and Codex: not yet.

**Output pruning, shadow mode (Claude Code).** The same `PostToolUse` hook labels large tool outputs (over 2000 characters, `prune_threshold` in `~/.judgetap/guard.toml`) keep, summarize or drop by rules and logs the tokens that would have been saved, estimated as characters / 4. It is observe-only: outputs are never changed. `judgetap guard stats` and the dashboard show the total as "est. tokens pruneable (shadow)". Real pruning is gated on a labelled set (#89).

- **Agents:** Claude Code (shell, writes and edits), Cursor and Codex (shell only; their hooks don't expose writes and edits).
- **Engine:** `judgetap guard install` uses one you already have (`$JUDGETAP_ENGINE`, a `TYPESAFE_API_KEY`, or a local AgentJev) and saves it in `~/.judgetap/guard.toml`, because agents often run hooks without your shell's environment.
- **Keys:** read from the environment, then the OS keychain (`pip install "judgetap[keychain]"`, then `judgetap keys set TYPESAFE_API_KEY`). Keys are never written to config or logs.
- **Log:** every decision goes to `~/.judgetap/guard.jsonl` (owner-only, with credentials redacted). See it with `judgetap guard stats` or `judgetap dashboard`.
- **Limits:** the rules are a best-effort denylist, not a sandbox. Shell is a full language; see [SPEC §5](docs/SPEC.md).

## Reviewed by MergeSafe

Every PR here is reviewed by [MergeSafe](https://mergesafe.ai) before merge. A few of the catches:

- [#17](https://github.com/mergesafe-ai/judgetap/pull/17): **P0**, commands with secrets in them were written to the guard log in plain text; also `sudo rm -rf /` and `$(rm -rf /)` slipping past the rules.
- [#16](https://github.com/mergesafe-ai/judgetap/pull/16): the cascade under-reported cost by dropping the engines it consulted before the winner.
- [#37](https://github.com/mergesafe-ai/judgetap/pull/37): quoted, attached and tab-separated `curl -u` credentials leaking past redaction.

## Status

Early development. Design in [docs/SPEC.md](docs/SPEC.md); roadmap in the issues.

## Open judge models

Open models now match Jev on most decision tasks. LangWatch's comparison reports, over the tasks each model could run: Jev 81.9%, Eikos-27B 80.0%, Shisa DE-1 79.7%, AutoJev-27B 78.9% ([langwatch.ai/compare/jev-vs-all](https://langwatch.ai/compare/jev-vs-all); their numbers, not ours). Run one behind any OpenAI-compatible server and use it through the `llm` engine in **logprobs** mode, which reads each option's probability from the model's token logprobs instead of asking for JSON:

```bash
# vLLM (GPU; a 27B model needs roughly an H100):
vllm serve <hf-repo-of-the-model> --served-model-name judge
# or Ollama:
ollama serve   # after `ollama pull <model>`
```

```python
import os

import judgetap as jt

os.environ["OPENAI_API_BASE"] = (
    "http://127.0.0.1:8000/v1"  # vLLM; Ollama: http://127.0.0.1:11434/v1
)
os.environ["OPENAI_API_KEY"] = "local"  # any value for a local server
jt.configure(jt.engines.load("llm:openai/judge?logprobs"))
```

Logprobs mode makes one short call per question (one generated token), run concurrently (up to 8 at a time), and each call is counted in the metrics. The probabilities come from the model's token distribution; they are not calibrated (answers stay `calibrated=False`), so use `judgetap eval` to check how well they track accuracy on your cases. If the server rejects the logprobs parameters with an error that names logprobs, the engine falls back to JSON mode on its own; any other error (including a generic "unsupported parameter" that doesn't mention logprobs) is raised rather than treated as a fallback.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0


**Experimental, opt-in: task-done check.** `judgetap guard install --with stop` adds a Claude Code `Stop` hook that asks the engine whether the user's task is actually finished. Only a confident "not done" (p(done) <= `stop_threshold`, default 0.15, set in `~/.judgetap/guard.toml`) blocks the stop, at most twice per session, and never while a previous block is being handled. It needs an engine and is off by default until an eval set shows it's reliable.
