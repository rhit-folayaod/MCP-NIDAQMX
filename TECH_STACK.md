# Tech stack — daq-mcp

Portfolio / resume notes for **daq-mcp**: an MCP server that lets AI coding
clients safely control National Instruments DAQ hardware.

---

## One-line summary

Built an MCP server that bridges AI agents to NI-DAQmx hardware with a
direction-aware safety model, continuous live acquisition, and a browser
dashboard — runnable fully simulated without NI drivers.

---

## Resume bullets (pick 2–3)

- Designed an MCP (Model Context Protocol) server in Python so AI agents can
  discover, read, and write NI DAQ channels through structured tools rather
  than ad-hoc scripts.
- Implemented a hardware safety layer: writes off by default, channel
  allowlists, input/output direction enforcement, analog output clamping, and
  digital write read-back verification.
- Added specialty measurements (strain, thermocouple, IEPE accelerometer)
  alongside voltage AI behind one tool surface.
- Built a backend abstraction with a pure-Python simulator and a real NI-DAQmx
  driver path so the same tools work without NI hardware.
- Added continuous acquisition, a live Starlette/SSE dashboard, named local
  wiring profiles, and token-gated HTTP for remote MCP clients.

---

## Core stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.11+ |
| Package / env | `uv`, `pyproject.toml` |
| MCP framework | FastMCP (stdio + Streamable HTTP) |
| Hardware API | NI-DAQmx via `nidaqmx` (optional extra) |
| Simulation | Pure-Python backend (no NI drivers required) |
| Live UI | Starlette, uvicorn, Server-Sent Events, vanilla JS canvas |
| Concurrency | Background acquisition thread, ring buffer, asyncio for HTTP |
| Auth (HTTP) | Shared-secret Bearer / query token (`DAQ_MCP_TOKEN`) |
| Tests | pytest against the simulated backend |
| CI | GitHub Actions (`uv sync` + pytest) |

---

## Architecture (short)

See [docs/architecture.md](docs/architecture.md).

```
AI client
   │  MCP (stdio or HTTP /mcp/)
   ▼
server.py  ── safety gates
   ├── LiveMonitor
   ├── Dashboard
   └── DAQBackend → SimulatedBackend | NIDAQmxBackend
```

---

## Keywords

`Python` · `MCP` · `FastMCP` · `NI-DAQmx` · `nidaqmx` · `Starlette` ·
`uvicorn` · `SSE` · `asyncio` · `pytest` · `uv` · `GitHub Actions` ·
`hardware safety` · `continuous acquisition`

---

## Repo

https://github.com/rhit-folayaod/daq-mcp

License: Apache 2.0 (`LICENSE` / `NOTICE`). Personal project; not affiliated
with NI / Emerson.
