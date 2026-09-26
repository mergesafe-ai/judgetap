# snapjudge

**One API for fast, typed decisions across every Jev-style engine, and a guard for coding agents built on it.**

A new class of models answers questions with a *decision* instead of text: pick one of these labels, place this on a scale, yes or no, each with a probability, in tens to hundreds of milliseconds for a fraction of a cent. TypeSafe's Jev started it; Laya, AgentJev and others followed, and structured-output LLMs can do the same job more slowly.

Each has its own API, answer shape and way of reporting confidence. snapjudge puts one interface over them:

```python
import snapjudge as sj

sj.configure(
    engine
)  # any snapjudge.Engine; bundled adapters for Jev and LLMs are coming (#2)

verdict = sj.yesno(
    "Is this shell command hard to undo?",
    context={"command": "git push --force origin main", "task": "fix typo in README"},
)
verdict.p  # 0.97
verdict.engine  # "jev"
```

- **Adapters**: Jev, local open-weights models (Laya, AgentJev), and any structured-output LLM (OpenAI, Gemini, Anthropic, Ollama).
- **Cascade**: ask the cheap, fast engine first; send low-confidence answers to a stronger one, or to a human.
- **Calibration check**: run your labelled examples through every engine and compare accuracy, calibration, speed and cost.

## snapjudge guard

A pre-action hook for Claude Code (Cursor and Codex next, #6). Every command, file write and edit is checked before it runs: hard rules for the obvious (`rm -rf /`, force-push to `main`), a snapjudge decision for the rest (is this irreversible? off-task? against a rule in `AGENTS.md`?). Most actions pass in about a quarter-second; the rare risky one is held, and the agent is told why.

## Status

Early development. See [docs/SPEC.md](docs/SPEC.md) for the design and the issues for the roadmap.

Built in the open, with every pull request reviewed by [MergeSafe](https://mergesafe.ai).

## License

Apache-2.0
