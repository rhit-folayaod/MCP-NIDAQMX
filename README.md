# daq-mcp

An MCP server that lets an AI coding client talk to National Instruments DAQ
hardware — with a safety model aimed at physical I/O, not just HTTP errors.

Not affiliated with, endorsed by, or supported by NI / Emerson. Licensed under
the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

**Documentation:** [docs/](docs/README.md) — architecture, safety, configuration,
tool reference, devices, examples, limitations, troubleshooting, development
history.

Portfolio-oriented stack notes: [TECH_STACK.md](TECH_STACK.md).

## Why this exists

Most MCP servers wrap APIs or filesystems. This one wraps data acquisition:
list devices, read sensors, write digital/analog lines, stream a live signal.
The protocol plumbing is straightforward; the interesting part is refusing
unsafe writes, clamping analog output, and keeping agent and browser on the
same allowlist.

## Quick start (no hardware)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/rhit-folayaod/daq-mcp.git
cd daq-mcp
uv sync
DAQ_MCP_SIMULATE=1 uv run server.py
```

```powershell
$env:DAQ_MCP_SIMULATE="1"; uv run server.py
```

Optional NI binding: `uv sync --extra hardware` (needs NI-DAQmx on the machine).
If the driver is missing, the server falls back to simulation automatically.

## Safety in one paragraph

Writes are **off** unless `DAQ_MCP_ALLOW_WRITE=1`. Only allowlisted channels are
reachable; only channels marked as outputs are writable; analog output is
clamped (±5 V by default). Configure channel roles with the dashboard picker or
wiring tools — profiles stay local (gitignored). Details: [docs/safety.md](docs/safety.md).

## Tools (summary)

| Area | Tools |
| --- | --- |
| Discovery | `list_devices`, `describe_device`, `self_test` |
| Analog in | `read_analog`, `monitor_analog` (`measurement`: voltage / strain / thermocouple / accelerometer) |
| Digital / AO | `read_digital`, `write_digital`, `write_analog`, `animate_digital` |
| Live | `start_live`, `live_status`, `stop_live`, `save_capture`, `list_captures` |
| Wiring | `get_wiring`, `set_wiring`, profile list / load / delete |

Full parameter notes: [docs/tools.md](docs/tools.md).

## Live dashboard

```bash
DAQ_MCP_ALLOW_WRITE=1 uv run server.py --dashboard-only
```

Open http://127.0.0.1:8765/. For dashboard + MCP on one port (avoids two
processes fighting over the device): `uv run server.py --http` → MCP at
`/mcp/`. Remote access needs a token and/or a tunnel —
[docs/configuration.md](docs/configuration.md).

## Cursor

See `.cursor/mcp.json.example` and [docs/configuration.md](docs/configuration.md).
Do not commit machine-local `.cursor/mcp.json`, tokens, or wiring profiles.

## Tests

```bash
uv run pytest
```

Simulator-only; CI runs the same on `main`.

## Layout

```
server.py              MCP tools + safety + entrypoint
src/daq_mcp/           backends, live, dashboard, wiring, captures, auth
docs/                  architecture, safety, config, tools, …
tests/                 pytest against the simulator
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
