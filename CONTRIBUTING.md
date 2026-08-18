# Contributing to daq-mcp

Thanks for taking a look. This is a personal learning / portfolio project;
small, well-scoped contributions are welcome.

## Documentation

User-facing docs live under [`docs/`](docs/README.md). Prefer updating those
when behavior changes, and keep the root README as a short overview.

## Before you change hardware paths

- Prefer the simulated backend (`DAQ_MCP_SIMULATE=1`) for development.
- Do not enable `DAQ_MCP_ALLOW_WRITE` against real hardware unless you own the
  bench and the wiring profile matches it.
- Do not commit `.daq_mcp_profiles/`, `.daq_mcp_captures/`, `.cursor/mcp.json`,
  tokens, or absolute machine paths.

## Dev setup

```bash
uv sync
uv run pytest
```

Optional lint:

```bash
uv sync --group dev
uv run ruff check .
```

## Pull requests

- Keep changes focused. Prefer hardening, docs, tests, or small features over
  broad rewrites of the safety layer.
- Match existing style and comments that explain *why*.
- Add or update tests when behavior changes.
- Apache 2.0 applies to contributions (see `LICENSE` and `NOTICE`).

## Reporting bugs

Use the GitHub bug report template. Include:

- OS and Python version
- Simulated vs real hardware (and device model if real)
- Exact command / env vars (redact tokens)
- Unexpected output or driver error codes
