# daq-mcp

An MCP server that lets an AI coding client talk to NI DAQ hardware.

Most MCP demos wrap APIs or filesystems. This one wraps a data-acquisition
device: list channels, read voltages, write digital lines, acquire a short
waveform. That is interesting because the failure modes are physical — a
hallucinated channel name or a stray analog output can damage equipment or
hurt someone. The protocol plumbing is the easy part; the safety model is
the point of the project.

## Safety model

Writes are off by default. Only channels on an explicit allowlist can be
touched. Analog output is clamped to a configured voltage range, and every
write returns what actually happened (including clamp flags and digital
read-back) so the model can verify its own actions.

| Control | Default | Env / constant |
| --- | --- | --- |
| Digital / analog writes | disabled | `DAQ_MCP_ALLOW_WRITE=1` |
| Channel allowlist | `Dev1/ai0`, `Dev1/ai1`, `Dev1/port0/line0`, `Dev1/port0/line1` | `CHANNEL_ALLOWLIST` in `server.py` |
| AO clamp range | ±5 V | `AO_VOLTAGE_LIMITS` in `server.py` |

The MCP tools never import `nidaqmx` directly. They call a backend interface.
That boundary is what makes the server testable and portable.

## Simulated by default (no NI drivers required)

Clone this on a machine without NI-DAQmx — including macOS — and it still
runs. Set `DAQ_MCP_SIMULATE=1` to force the pure-Python backend, or leave it
unset: the server tries the real driver and falls back to simulation with a
clear log line if NI is missing.

The simulator exposes two fake devices (`Dev1` / `Dev2`) with realistic
channel inventories. `Dev1/ai0` is a slow sine with noise; `Dev1/ai1` is a
noisy DC level. Digital lines keep state across calls.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd MCP-NIDAQMX
uv sync
```

Optional real-hardware extra (needs NI-DAQmx drivers on the machine):

```bash
uv sync --extra hardware
```

Run the server:

```bash
uv run server.py
```

Useful environment variables:

```bash
DAQ_MCP_SIMULATE=1      # force simulated backend
DAQ_MCP_ALLOW_WRITE=1   # enable digital / analog output
```

## Verify with MCP Inspector

```bash
cd MCP-NIDAQMX
DAQ_MCP_SIMULATE=1 npx -y @modelcontextprotocol/inspector uv run server.py
```

On Windows PowerShell, pass the variable with Inspector's `-e` flag rather than
setting it in the shell — Inspector spawns the server with a sanitized
environment and does not forward arbitrary shell variables:

```powershell
cd path\to\MCP-NIDAQMX
npx -y @modelcontextprotocol/inspector -e DAQ_MCP_SIMULATE=1 uv run server.py
```

If tools return "No results yet" for `list_devices`, the server is talking to
real hardware and correctly reporting zero devices — the simulate flag did not
reach it. The startup log line on stderr reports which backend was selected.

## Cursor configuration

Add to your Cursor MCP settings (user-level `mcp.json`). Prefer the full path
to `uv` so Cursor does not depend on PATH:

```json
{
  "mcpServers": {
    "daq-mcp": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\MCP-NIDAQMX",
        "server.py"
      ],
      "env": {
        "DAQ_MCP_SIMULATE": "1"
      }
    }
  }
}
```

Add `"DAQ_MCP_ALLOW_WRITE": "1"` only when you intentionally enable outputs.

A copy-paste template lives at `.cursor/mcp.json.example`. Put your real
machine config in `.cursor/mcp.json` (gitignored) or in Cursor's user MCP
settings — do not commit local paths or write-enable flags.

## Tests

```bash
uv run pytest
```

All tests run against the simulated backend.

## Tool design decisions

**One tool per complete operation.** Splitting "create task / add channel /
start / read / close" into separate tools would force the model into multiple
round-trips and make it easy to leave a hardware task open. Each tool opens
what it needs, does one job, and closes everything before returning.

**Summary over raw data for waveforms.** `monitor_analog` returns mean, RMS,
peak-to-peak, standard deviation, and a downsampled preview of at most 50
points. Dumping thousands of floats into the model context is expensive and
rarely what you need for "is my sensor behaving?"

## Project layout

```
server.py                 # MCP tools + safety layer
src/daq_mcp/backend/      # DAQBackend ABC, simulated + nidaqmx backends
tests/                    # pytest against the simulator
```
