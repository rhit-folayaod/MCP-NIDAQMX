# Cursor prompt — build the DAQ MCP server
---

## Context for the agent

I'm building an MCP server that exposes NI DAQ hardware to AI coding clients. There's a
scaffold at `server.py` with the tool surface, safety configuration, and docstrings
already written. The function bodies are stubs (`...`) — your job is to implement them.

Stack decisions, already made. Don't relitigate them:

- **`fastmcp` standalone package**, not the in-SDK version. Bare `@mcp.tool` decorator.
- **stdio transport.** That's what Cursor and Claude Code launch locally. Do not use
  `transport="sse"` — that transport is deprecated.
- **`uv`** for dependency management.
- Python 3.11+.

Critical constraint on the simulated backend: **it must be pure Python and must not
require NI-DAQmx drivers to be installed at all.** Someone cloning this on a Mac, where
NI-DAQmx doesn't exist, has to be able to run the server, connect it to Cursor, and see
tools working. `nidaqmx` gets imported lazily, only when the real backend is selected.

**Verify the `nidaqmx` API against its actual documentation before you write against it.**
Do not write `nidaqmx` calls from memory — the library has specific patterns for task
creation, channel naming, and terminal configuration, and guessing produces code that
looks right and fails on hardware. If you're unsure of a call, say so instead of guessing.

Check in with me at the end of each phase.

## Phase 1 — Project setup and backend abstraction

Set up the project with `uv`, then define the backend layer:

- A `DAQBackend` abstract base class (or Protocol) with methods matching the operations
  the tools need: enumerate devices, describe a device, read analog, read digital, write
  digital, write analog, acquire a finite waveform, self test.
- The MCP tools should never touch `nidaqmx` directly. They call the backend. This is
  the boundary that makes the whole thing testable and portable.
- Backend selection: `DAQ_MCP_SIMULATE=1` forces simulation; otherwise try to import and
  initialize the real backend and fall back to simulation with a clear log line if NI
  drivers aren't present.

## Phase 2 — Simulated backend

Pure Python, no NI dependency. Make it behave plausibly rather than returning zeros:

- Two fake devices with realistic-looking names, product types, and channel inventories.
- Analog inputs generate a signal with configurable noise — one channel a slow sine, one
  a noisy DC level — so `monitor_analog` returns statistics that are actually interesting
  to reason about.
- Digital lines hold state across calls, so writing a line high and then reading it back
  actually reflects the write.
- Make the sine frequency and noise level module-level constants so they're easy to find.

## Phase 3 — Real nidaqmx backend

Implement against the real library. Requirements:

- One task per operation, created and closed inside the call. No long-lived tasks.
- Correct handling of the `terminal_config` parameter for analog input.
- Finite acquisition for `monitor_analog` with proper sample-clock timing, not a loop of
  single reads.
- Real error handling: catch `nidaqmx.DaqError` and return a structured error dict with
  the NI error code and message rather than letting the exception escape as a traceback.

## Phase 4 — Implement the tools

Fill in the stubs in `server.py` against the backend interface. Preserve every existing
docstring — they're the tool descriptions the model reads, and I wrote them deliberately.

Honor the safety layer exactly as scaffolded: allowlist checks on every channel argument,
writes gated behind `DAQ_MCP_ALLOW_WRITE`, analog output clamped with both requested and
applied voltage returned, and digital writes reading back actual line state rather than
echoing the requested value.

For `monitor_analog`, return summary statistics plus a downsampled preview of at most 50
points. Never return the full sample array — it would flood the model's context.

## Phase 5 — Tests

`pytest` against the simulated backend. Cover:

- Every tool returns a well-formed response.
- Channel allowlist rejects channels not on it.
- Writes are refused when `DAQ_MCP_ALLOW_WRITE` is unset.
- Analog output clamping works at both ends of the range and reports that it clamped.
- `monitor_analog` never returns more than 50 preview points regardless of duration.
- Digital write-then-read round-trips correctly in simulation.

## Phase 6 — Connect it

- Verify the server with the MCP Inspector first, and tell me the exact command to run.
- Then give me the exact JSON to add to Cursor's MCP configuration, with the correct
  path and `uv` invocation for this project.
- Tell me three specific things to type into Cursor to prove the tools work end to end.

## Phase 7 — Documentation

- `README.md`: what it is, why exposing test hardware to an AI client is interesting, the
  safety model and why it exists, install and configuration steps, a note that it runs
  fully simulated without NI drivers, and a placeholder for a demo GIF.
- A section on tool design decisions: one tool per complete operation rather than
  exposing task lifecycle, and summary-over-raw-data for waveforms. Explain the reasoning.
- `.cursor/rules/` with rules specific to this repo: the backend abstraction boundary,
  the requirement that new tools go through allowlist checks, and the no-guessing rule
  for `nidaqmx` API calls.
- Do not claim the project does anything it doesn't. No badge walls, no emoji headers.
