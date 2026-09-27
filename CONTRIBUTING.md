# Contributing

Thanks for helping. judgetap is small, so the process is too.

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                          # install
uv run pytest                                    # tests
uv run ruff check . && uv run ruff format --check .   # lint and format
```

CI runs the same three on Python 3.12 and 3.13 for every PR.

## Pull requests

- **One change per PR.** Small PRs review faster and converge sooner.
- **Conventional commit titles** (`feat: …`, `fix: …`, `docs: …`, `ci: …`).
  release-please builds the CHANGELOG and the next version from them:
  while below 1.0, `feat` bumps the minor version and `fix` the patch.
- **Tests at the boundary of each fix.** A bug fix comes with a test that
  fails without it and exercises the exact input that broke.
- Say in the description what the PR does *and* what it deliberately doesn't.

## Review

Every PR is reviewed by [MergeSafe](https://mergesafe.ai) (`mergesafeai[bot]`):

- A sticky summary gives **Merge Readiness 1–5** and findings by severity:
  **P0** (must fix), **P1** (should fix before merge), **P2/P3** (worth fixing).
- **Answer every thread**, with the commit that fixes it or the reason it
  won't be changed. A reply doesn't clear a finding; changing the code does.
- A PR merges at **5/5, or 4/5 with nothing blocking**, with green CI.
- Push each round's fixes as one commit, after the review of the previous
  push has landed.

## Security

Don't open public issues for vulnerabilities. Report them privately through
[GitHub security advisories](https://github.com/mergesafe-ai/judgetap/security/advisories/new).

## License

By contributing you agree your work is licensed under Apache-2.0.

## Releases

- Every merge to `main` publishes a dev build to PyPI (`X.Y.Z.devN`), except merging the release PR itself, which publishes the stable version instead. `pip install judgetap` ignores it; `pip install --pre judgetap` gets it.
- A stable version is published only when a GitHub Release is created: release-please keeps a release PR open with the next version and the changelog, built from conventional commit titles, and merging it tags and publishes. A release published by hand in GitHub is published the same way, after checking the tag matches the package version. If a stable publish fails, re-run it from Actions → release → "Run workflow" with the release tag; it only publishes a tag that has a GitHub Release.
- Publishing uses PyPI Trusted Publishing (OIDC): no tokens are stored anywhere.
