# Tech stack — daq-mcp

Portfolio / resume notes for **daq-mcp**: an MCP server that lets AI coding
clients (Cursor, cloud agents) safely control National Instruments DAQ hardware.

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
- Built a backend abstraction with a pure-Python simulator and a real NI-DAQmx
  driver path so the same tool surface works on machines without NI hardware.
- Added continuous acquisition, a live Starlette/SSE dashboard, named wiring
  profiles, and token-gated HTTP access for remote / phone demos (Cloudflare
  Tunnel).

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
| Remote demo | Cloudflare Tunnel (free quick tunnel) to local HTTP MCP |

---

## Architecture (short)

```
AI client (Cursor / cloud agent)
        │  MCP tools (stdio or HTTP /mcp/)
        ▼
   server.py  ── safety: allowlist, direction, write gate, AO clamp
        │
        ├── LiveMonitor (continuous AI stream + DI poll)
        ├── Dashboard (SSE plot, LED toggles, profile picker)
        └── DAQBackend
              ├── SimulatedBackend
              └── NIDAQmxBackend  →  USB-6421 / cDAQ modules
```

**Design rules that matter for the story**

- Tools never import `nidaqmx` directly — only the backend does.
- Snapshot tools open one task per call; live streaming is the deliberate
  long-lived exception, with reads served from a shared buffer.
- Browser writes and agent writes share the same safety gates.
- Wiring profiles are local JSON (gitignored), not hardcoded bench secrets.

---

## Hardware exercised

- NI USB-6421 (mioDAQ-class): analog input, digital I/O, live dashboard demos
- NI cDAQ-9178 chassis with modules (e.g. NI 9215 voltage AI, NI 9472 DO,
  NI 9263 AO) via merged multi-module inventory

---

## Keywords for a skills / tech section

`Python` · `MCP` · `FastMCP` · `NI-DAQmx` · `nidaqmx` · `Starlette` ·
`uvicorn` · `SSE` · `asyncio` · `pytest` · `uv` · `GitHub Actions` ·
`hardware safety` · `continuous acquisition` · `Cloudflare Tunnel`

---

## Repo

https://github.com/rhit-folayaod/MCP-NIDAQMX
