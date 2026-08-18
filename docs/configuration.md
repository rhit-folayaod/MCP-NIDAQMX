# Configuration

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DAQ_MCP_SIMULATE` | unset | `1` forces the pure-Python backend |
| `DAQ_MCP_ALLOW_WRITE` | unset | `1` enables digital / analog output |
| `DAQ_MCP_DASHBOARD` | unset | `1` serves the dashboard beside stdio MCP |
| `DAQ_MCP_DASHBOARD_HOST` | `127.0.0.1` | bind address (non-loopback needs token) |
| `DAQ_MCP_DASHBOARD_PORT` | `8765` | listen port |
| `DAQ_MCP_TOKEN` | unset | shared secret for HTTP / non-loopback |
| `DAQ_MCP_LIVE_CHANNEL` | from wiring / defaults | channel to stream on startup |
| `DAQ_MCP_LIVE_RATE` | from wiring / defaults | sample rate in Hz |

## CLI flags

| Flag | Effect |
| --- | --- |
| *(none)* | stdio MCP (typical editor launch) |
| `--dashboard-only` | browser UI only; skips the MCP stdio loop |
| `--http` | dashboard + Streamable HTTP MCP on one port (`/mcp/`) |

`--dashboard-only` exists because a stdio MCP server with no client exits on
EOF and would take the dashboard down with it.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/rhit-folayaod/daq-mcp.git
cd daq-mcp
uv sync
```

Optional NI driver binding:

```bash
uv sync --extra hardware
```

## Cursor / editor MCP

Prefer an absolute path to `uv` and the repo. Example (paths are placeholders):

```json
{
  "mcpServers": {
    "daq-mcp": {
      "command": "/absolute/path/to/uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/daq-mcp",
        "server.py"
      ],
      "env": {
        "DAQ_MCP_SIMULATE": "1"
      }
    }
  }
}
```

Add `"DAQ_MCP_ALLOW_WRITE": "1"` only when you intend to drive outputs.

A template lives at `.cursor/mcp.json.example`. Keep real machine config in a
gitignored `.cursor/mcp.json` or in the editor’s user MCP settings.

## HTTP MCP clients

With `--http`:

- Dashboard: `http://127.0.0.1:8765/`
- MCP: `http://127.0.0.1:8765/mcp/`

For remote clients, put a tunnel in front and send
`Authorization: Bearer <token>`. The dashboard also accepts `?token=` because
EventSource cannot set Authorization headers.

## Local data (not in git)

| Path | Purpose |
| --- | --- |
| `.daq_mcp_profiles/` | named wiring layouts |
| `.daq_mcp_captures/` | saved live snapshots |
| `.cursor/mcp.json` | machine-local MCP launch config |
