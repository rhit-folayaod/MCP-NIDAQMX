# Architecture

daq-mcp is a single Python process that exposes NI DAQ hardware (or a
simulator) to MCP clients and optionally a browser dashboard.

```
AI client (Cursor, Inspector, cloud agent, …)
        │  MCP tools (stdio or HTTP /mcp/)
        ▼
   server.py  ── safety: allowlist, direction, write gate, AO clamp
        │
        ├── LiveMonitor   continuous AI stream + optional DI poll
        ├── Dashboard     Starlette + SSE plot / DO toggles / wiring picker
        └── DAQBackend
              ├── SimulatedBackend   pure Python (default when NI missing)
              └── NIDAQmxBackend     optional `nidaqmx` extra
```

## Layers

| Piece | Role |
| --- | --- |
| `server.py` | FastMCP tools, safety gates, wiring apply, process entry |
| `src/daq_mcp/backend/` | Hardware abstraction; tools never import `nidaqmx` |
| `src/daq_mcp/measurements.py` | Voltage / strain / thermocouple / accelerometer options |
| `src/daq_mcp/live.py` | Background thread + rolling sample window |
| `src/daq_mcp/dashboard.py` | Browser UI served from the same process |
| `src/daq_mcp/wiring.py` | Local named profiles (gitignored on disk) |
| `src/daq_mcp/captures.py` | Snapshot live window to JSON |
| `src/daq_mcp/auth.py` | Shared-secret gate for non-loopback HTTP |

## Snapshot vs live

Most tools open one DAQmx task, do one job, and close the task before
returning. That avoids leaving hardware reserved across agent turn-taking.

Continuous acquisition is the deliberate exception: `LiveMonitor` owns one
long-lived stream. While that channel is streaming, `read_analog` /
`monitor_analog` on the same channel serve from the ring buffer
(`"source": "live_buffer"`) instead of opening a competing task.

## Why the dashboard shares the process

NI-DAQmx reserves a channel for the lifetime of a task. A separate dashboard
process could not read the same AI channel the MCP server was streaming.
One process means the agent and the human see and drive the same hardware,
through the same safety checks.

## Transports

| Mode | Command idea | Use when |
| --- | --- | --- |
| stdio MCP | `uv run server.py` | Editor launches the server |
| Dashboard only | `uv run server.py --dashboard-only` | Browser UI, no MCP client |
| Unified HTTP | `uv run server.py --http` | Dashboard + `/mcp/` on one port |

Only one process should own a given device and the dashboard port at a time.

## CompactDAQ inventory

On CompactDAQ, physical channels live on modules (`…ModN/…`), not on the
chassis name alone. The server can merge module inventories when describing a
chassis so the channel picker shows pins across installed modules.
