# snapjudge

**One API for fast, typed decisions across every Jev-style engine, and a guard for coding agents built on it.**

A new class of models answers questions with a *decision* instead of text: pick one of these labels, place this on a scale, yes or no, each with a probability, in tens to hundreds of milliseconds for a fraction of a cent. TypeSafe's Jev started it; Laya, AgentJev and others followed, and structured-output LLMs can do the same job more slowly.

Each has its own API, answer shape and way of reporting confidence. snapjudge puts one interface over them:

```python
import snapjudge as sj

sj.configure(sj.engines.load("jev"))  # needs TYPESAFE_API_KEY; see "Engines" below

verdict = sj.yesno(
    "Is this shell command hard to undo?",
    context={"command": "git push --force origin main", "task": "fix typo in README"},
)
verdict.value  # "yes"
verdict.p  # 0.97
verdict.engine  # "jev"
```

## Engines

```python
# TypeSafe Jev (TYPESAFE_API_KEY)
jev = sj.engines.load("jev")
# Any LiteLLM model: pip install "snapjudge[llm]"
flash = sj.engines.load("llm:gemini/gemini-2.0-flash-lite")
# Ask Jev first, escalate low-confidence answers to the LLM
sj.configure(sj.Cascade([jev, flash]))
```

Local engines: `sj.engines.load("laya")` runs Laya in process (`pip install "snapjudge[laya]"`; on a CPU-only Linux box install the CPU PyTorch wheel first with `pip install torch --index-url https://download.pytorch.org/whl/cpu`, or pip pulls the multi-GB CUDA build). `sj.engines.load("agentjev")` talks to a local AgentJev server.

- **Adapters**: Jev, local open-weights models (Laya, AgentJev), and any structured-output LLM (OpenAI, Gemini, Anthropic, Ollama).
- **Cascade**: ask the cheap, fast engine first; send low-confidence answers to a stronger one, or to a human.
- **Calibration check**: run your labelled examples through every engine and compare accuracy, calibration, speed and cost.

## snapjudge guard

A pre-action hook for Claude Code (shell commands, file writes and edits), Cursor and Codex (shell commands only; their hooks don't expose writes and edits). Every command, file write and edit is checked before it runs: a denylist of common destructive forms (`rm -rf /`, force-push to `main`, `DROP TABLE`; best-effort, not a sandbox, see SPEC §5), and, once you configure an engine (`SNAPJUDGE_ENGINE`), a snapjudge decision for the rest (is this irreversible? off-task? against a rule in `AGENTS.md`?). Without an engine the guard runs its rules only. Most actions pass in about a quarter-second; the rare risky one is held, and the agent is told why.

## Status

Early development. See [docs/SPEC.md](docs/SPEC.md) for the design and the issues for the roadmap.

Built in the open, with every pull request reviewed by [MergeSafe](https://mergesafe.ai).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every PR is reviewed by MergeSafe.

## License

Apache-2.0
