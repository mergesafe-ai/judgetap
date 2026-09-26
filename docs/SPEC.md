# snapjudge: design spec (v0)

## Problem

Typed-decision models (TypeSafe Jev, released Sept 2026; open-weights Laya, AgentJev-0.6B and a long tail) return a choice, a score or a yes/no probability in one fast pass. They are 40-400x cheaper than a frontier LLM for classification, routing and gating. But:

- every engine has its own API, request shape and confidence format;
- Jev is closed and hosted, and paused signups days after launch, so depending on one engine is a risk;
- the pattern every guide recommends (cheap engine first, escalate on low confidence) is written by hand each time;
- "pick on calibration" is the standard advice, and nothing measures it.

LiteLLM solved the same fragmentation for text generation. snapjudge does it for decisions.

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
| `agentjev` | local, open weights | ~50 ms per pass |
| `llm` | any structured-output LLM via LiteLLM | OpenAI, Gemini, Anthropic, Ollama; probability from logprobs where exposed, else self-reported and flagged `calibrated=False` |

Config: `snapjudge.toml` or env vars; `sj.configure(engines=[...])` in code. Keys never logged.

### 3. Cascade

```toml
[cascade]
order = ["jev", "llm:gemini-flash-lite"]
escalate_below = 0.8      # p under this goes to the next engine
on_exhausted = "raise"    # or "return_last", or a callback (e.g. ask a human)
```

Each hop is recorded on the `Decision`. Engine errors and timeouts fall through the same way.

### 4. Calibration check

`snapjudge eval cases.jsonl --engines jev,laya,llm:gpt-...` runs labelled cases through each engine and reports accuracy, expected calibration error, a reliability table, p50/p95 latency and cost per 1,000 decisions. Output as Markdown and JSON, so results can be published with their data.

### 5. `snapjudge guard`

A pre-action hook for coding agents, built on the core.

- **Agents**: Claude Code (`PreToolUse`), Cursor (`preToolUse`, 1.7+, reads the Claude Code hook format), Codex (experimental hooks). One binary, `snapjudge guard install --for claude-code`; `cursor`, `codex` and `all` arrive with #6.
- **Two layers**:
  1. **Rules** (no model, microseconds): built-in list plus the user's `guard.toml`: recursive delete outside the workspace, force-push or push to protected branches, `DROP`/`DELETE` without `WHERE`, `terraform destroy`, secrets in written files.
  2. **Judgement** (snapjudge, ~250 ms): for everything the rules don't decide, ask: is it irreversible? is it off-task for the stated goal? does it break a rule in `AGENTS.md` / `CLAUDE.md` / `guard.md`?
- **Outcomes**: allow (silent), hold (block with a reason the agent reads and re-plans from), ask (escalate to the user). Holds should be rare; the target is under 5 per 1,000 calls.
- **Fails safe and visibly**: Claude Code treats a crashing hook as non-blocking, so the guard catches its own errors, applies the rules layer alone, and says so.
- **Log**: every decision to a local JSONL, so `snapjudge guard stats` can report holds and cost. Marking a hold as a false alarm comes with the dashboard (#10).

## Non-goals (v0)

- Training or hosting a decision model.
- Text generation, explanations or chat.
- A hosted service; everything runs locally or against the user's own keys.

## Milestones

1. **v0.1 core**: API, `Decision`, `jev` + `llm` adapters, cascade, tests with recorded fixtures.
2. **v0.2 guard**: rules layer, judgement layer, Claude Code install, logging. First public launch (README GIF).
3. **v0.3 breadth**: local `laya`/`agentjev` adapters, Cursor and Codex install, `eval` command.
4. **v0.4**: TypeScript client; published calibration comparison across engines on real agent actions.

## Open questions

- Engine for the guard by default when the user has no Jev key: local model (download size) or their LLM key (latency)?
- Does `eval` ship with a public case set of agent actions, and where do labelled cases come from?
