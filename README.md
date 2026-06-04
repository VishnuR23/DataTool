# DataTool

An open-source autonomous experimentation controller.

DataTool sits on top of whatever feature-flagging, metrics, and variant-generation
tools a team already uses, and drives experiments through their full lifecycle —
ramp, evaluate, promote, or revert — without a human in the loop, while honoring a
declarative **trust contract** that bounds and audits its authority.

In one sentence: a daemon that takes "here's a variant, here are the guardrails,
here's the trust budget" and handles ramp, evaluate, promote, or revert — with
statistically valid sequential inference and a provable safety contract.

> **Status:** early development. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
> full build brief. This is the source of truth for the project.

## What it is not

- Not a feature-flag system (uses yours)
- Not a metrics warehouse (queries yours)
- Not a variant generator (accepts variants from humans, LLMs, or external tools)
- Not a multi-armed bandit — DataTool does progressive delivery, not adaptive allocation

## Development

```bash
uv sync                          # install deps (Python 3.11+)
uv run pytest tests/unit         # run the unit suite
uv run pytest tests/stats        # the calibration gate (must pass before merge)
uv run ruff check .              # lint
```

## License

Apache-2.0. See [`LICENSE`](LICENSE).
