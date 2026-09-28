# judgetap: design spec (v0)

## Problem

Typed-decision models (TypeSafe Jev, released Sept 2026; open-weights Laya, AgentJev-0.6B and a long tail) return a choice, a score or a yes/no probability in one fast pass. They are 40-400x cheaper than a frontier LLM for classification, routing and gating. But:

- every engine has its own API, request shape and confidence format;
- Jev is closed and hosted, and paused signups days after launch, so depending on one engine is a risk;
- the pattern every guide recommends (cheap engine first, escalate on low confidence) is written by hand each time;
- "pick on calibration" is the standard advice, and nothing measures it.

LiteLLM solved the same fragmentation for text generation. judgetap does it for decisions.

## Scope

### 1. Core API

Three question types, one result type.

```python
sj.choice(question, options: list[str], context=...) -> Decision   # one of up to 255 labels
sj.score(question, levels: list[str], context=...)   -> Decision   # ordered scale, 2-10 levels
sj.yesno(question, context=...)                      -> Decision   # probability 0-1
sj.batch([...questions], context=...)                -> list[Decision]  # one pass where the engine supports it
```

`Decision`: `value`, `p` (probability of `value`), `distribution` (all options), `engine`, `latency_ms`, `cost_usd`, `escalated: bool`.

- Context is a string or JSON-serialisable object; no images or audio (none of the engines take them).
- Sync and async. Python first; a TypeScript client after v0.1, same shapes.
- The answer is always valid: an engine that returns an out-of-set value is a failure, not a result.

### 2. Engine adapters

| Engine | Kind | Notes |
|---|---|---|
| `jev` | hosted (TypeSafe console, Vercel AI Gateway) | native batch; the reference shape |
| `laya` | local, open weights (Apache-2.0) | via transformers; GPU optional |
| `julia` | local, open weights (Apache-2.0), 144M | Julia-1 runtime from its model repo; CPU by default; Jev-shaped API; 2-20 options per question |
| `gliner` | local, open weights (Apache-2.0), Fastino GLiNER2.5-Decide via `gliner2` | all questions as heads in one pass; full probabilities when gliner2 returns them, else the winner's probability with the rest split evenly, else the label alone (`calibrated=False`) |
| `agentjev` | local, open weights | ~50 ms per pass |
| `llm` | any structured-output LLM via LiteLLM | OpenAI, Gemini, Anthropic, Ollama; probabilities are the model's own JSON estimate, flagged `calibrated=False` (see `?logprobs` below) |
| `llm:<model>?logprobs` | same, OpenAI-compatible servers exposing `logprobs` | options listed as letters, one single-token call per question; the distribution is the letters' `top_logprobs` renormalised (missing letters get 0, none present is an error); token probabilities, not calibrated (`calibrated=False`; check with `judgetap eval`); per-question calls run concurrently (max 8) and each is reported as a Call, failed ones included (the first failure cancels requests not yet sent); more than 26 options is a local error raised before any request; falls back to JSON mode for good only if the provider's error names logprobs (other errors are raised), and the rejected requests are reported alongside the JSON call; an empty model (`llm:?logprobs`) is an error |

Config: `judgetap.toml` or env vars; `sj.configure(engines=[...])` in code. Keys never logged.

### 3. Cascade

```toml
[cascade]
order = ["jev", "llm:gemini-flash-lite"]
escalate_below = 0.8      # p under this goes to the next engine
on_exhausted = "raise"    # or "return_last", or a callback (e.g. ask a human)
```

Each hop is recorded on the `Decision`. Engine errors and timeouts fall through the same way, and so does an uncalibrated answer (`calibrated=False`) whatever its p -- including a bare label (p=1.0, uncalibrated), even when it is the only signal --  while an engine is left to ask; the last engine's answer is judged on p alone.

### 4. Calibration check

`judgetap eval cases.jsonl --engines jev,laya,llm:openai/gpt-4o-mini` (or `python -m judgetap.evaluate`) runs labelled cases through each engine and reports accuracy, expected calibration error, a reliability table, p50/p95 latency and cost per 1,000 decisions. Output as Markdown and JSON, so results can be published with their data.

### 5. `judgetap guard`

A pre-action hook for coding agents, built on the core.

- **Agents**: Claude Code (`PreToolUse`), Cursor (`beforeShellExecution`), Codex (`PreToolUse`, hooks enabled by default since 0.145). One binary, `judgetap guard install --for claude-code|cursor|codex|all`. Cursor uses `beforeShellExecution` (shell only; allow defers to Cursor's settings). Codex's `PreToolUse` fires for shell only today, has no *ask*, so ask becomes a deny that tells the agent to get the user's go-ahead, and new hooks must be trusted with `/hooks`.
- **Two layers**:
  1. **Rules** (no model, microseconds): built-in list plus the user's `guard.toml`: recursive delete outside the workspace, force-push or push to protected branches, `DROP`/`DELETE` without `WHERE`, `terraform destroy`, secrets in written files. This is a **denylist of common destructive forms, not a sandbox**: shell is a full language, and a determined or obfuscated command can always be written that no pattern set recognises. The rules fail closed where they can tell they can't see (unresolvable paths or command names, unknown push destinations) and otherwise catch what agents actually type. The judgement layer covers the rest; for isolation, use the agent's own sandbox. **With no engine configured**, the guard fails closed instead: anything a rule already holds still holds (e.g. `terraform destroy`), and otherwise programs whose effect the rules can't bound (terraform, kubectl, cloud CLIs, database shells, dd, xargs, `sh -c`, `sudo`, ...), git subcommands outside a known set or with destructive flags, and opaque destructive shell all *ask*.
  2. **Judgement** (judgetap, ~250 ms): for everything the rules don't decide, ask: is it irreversible? is it off-task for the stated goal? does it break a rule in `AGENTS.md` / `CLAUDE.md` / `guard.md`? That repo text is untrusted and reaches the judge's prompt, so it may only tighten: when it was in the judge's context, a judge *allow* never lifts what rules-only mode would ask about (the judge can still hold or ask).
- **Outcomes**: allow (silent), hold (block with a reason the agent reads and re-plans from), ask (escalate to the user). Holds should be rare; the target is under 5 per 1,000 calls.
- **Fails safe and visibly**: Claude Code treats a crashing hook as non-blocking, so the guard catches its own errors and says so. An engine that fails leaves the rules layer alone to decide a command (rules-only mode), and asks about a file write, which has no rules-only check to fall back on; input the guard can't read (no tool name, an unreadable command or destination) and any other unexpected failure before the check finishes *ask*, since the guard can't vouch for an action it didn't finish checking. Tool names are matched after trimming whitespace.
- **Log**: every decision to a local JSONL, so `judgetap guard stats` can report holds and cost. Marking a hold as a false alarm comes with the dashboard (#10).
- **Loop detection** (Claude Code `PostToolUseFailure` for failures, `PostToolUse` for successes that reset a streak; no model): the same action failing with the same error (numbers ignored) 3 times in the last 8 actions adds `additionalContext` telling the agent to re-plan; a success of that action resets the count. Never blocks. Per-session ring buffer of 20 redacted actions and error hashes in `~/.judgetap/sessions/`, 0600. Logged as layer `loop`, outcome `note`.
- **Output pruning, shadow mode only** (v0.5, #27/#29; Claude Code `PostToolUse`; no model): a tool output longer than `prune_threshold` characters (default 2000, `~/.judgetap/guard.toml`) is labelled keep, summarize or drop by rules: errors, tracebacks and short outputs are keep; mostly progress-bar lines are drop; long repetitive logs and very long search listings are summarize; everything else is keep. The output is **never modified, suppressed or replaced**. Each considered output is logged as layer `prune`, outcome `note`, with `label`, `output_chars` and `est_tokens_saved` only: none of the output itself is logged. Only the first 200,000 characters are labelled. Error-looking outputs (errors, failures, `npm ERR!`, tracebacks) are always keep, and a bare percentage (a coverage report) isn't a progress line. Tokens are **estimated as characters / 4**: a drop saves all of them, a summarize all but an assumed 500-character summary. `guard stats` and the dashboard report the total as "est. tokens pruneable (shadow)": what pruning *would* have saved, not what was saved. Errors are swallowed and noted; the hook never blocks. No engine is used (an engine labeller stays off for cost and latency). Real pruning is gated on the labelled set in #89, which these records seed.

## Decided: the guard's default engine (#8)

No engine by default, and nothing downloaded or stored. Rules-only mode fails closed for shell commands (it asks when unsure); without an engine, file writes and edits are checked for secrets, user rules and writes to the guard's own configuration (guard.toml, agent hook settings), and any shell command that mentions one of those paths (or a word in it that resolves to one through a symlink) asks, since listing every way the shell can write a file isn't possible. `judgetap guard install` uses an engine the user already has, in this order: `$JUDGETAP_ENGINE`, a `TYPESAFE_API_KEY` (Jev), a local AgentJev server on 127.0.0.1:8149. It records the choice as `engine = "..."` in `~/.judgetap/guard.toml`, because agents often run hooks without the user's shell environment. Install never stores a key by itself; when it detects `TYPESAFE_API_KEY` in the environment and runs in a terminal, it offers (opt-in) to copy it into the OS keychain, and engines read the environment first, then the keychain. A local model isn't the default because of the download (Laya pulls PyTorch; AgentJev needs its own server), and the user's LLM key isn't auto-picked because it adds seconds per guarded call. Either is one line in guard.toml.

**Experimental, opt-in: task-done check.** `judgetap guard install --with stop` adds a Claude Code `Stop` hook that asks the engine whether the user's task is actually finished. Only a confident "not done" (p(done) <= `stop_threshold`, default 0.15, set in `~/.judgetap/guard.toml`) blocks the stop, at most twice per session, and never while a previous block is being handled. It needs an engine and is off by default until an eval set shows it's reliable.

## Engine calls

Every `Decision` carries `calls`: one `Call(engine, latency_ms, ok, questions)` per engine invocation behind its batch, measured where the call was made. The core times plain engines. A cascade times each engine it asks and passes a nested cascade's own calls through, so the list names providers, never cascades; its `on_exhausted` callback isn't an engine and makes no call. Failures carry the calls made so far (`err.calls`, including `CascadeExhaustedError`). Logs write a batch's calls once, and the dashboard's engine table counts and times each call; records written before calls existed fall back to one call per successful guard judgement.

## Non-goals (v0)

- Training or hosting a decision model.
- Text generation, explanations or chat.
- A hosted service; everything runs locally or against the user's own keys.

## Milestones

1. **v0.1 core**: API, `Decision`, `jev` + `llm` adapters, cascade, tests with recorded fixtures.
2. **v0.2 guard**: rules layer, judgement layer, Claude Code install, logging. First public launch (README GIF).
3. **v0.3 breadth**: local `laya`/`agentjev` adapters, Cursor and Codex install, `eval` command.
4. **v0.4**: TypeScript client; published calibration comparison across engines on real agent actions.
5. **v0.5**: context hygiene. Output pruning ships first in shadow mode (measure est. tokens pruneable, #29); pruning for real only after the #89 labelled set shows drops are safe.

## Open questions

- Does `eval` ship with a public case set of agent actions, and where do labelled cases come from?
