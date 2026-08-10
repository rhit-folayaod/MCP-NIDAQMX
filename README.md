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
touched, and only channels wired as *outputs* can be written. Analog output is
clamped to a configured voltage range, and every write returns what actually
happened (including clamp flags and digital read-back) so the model can verify
its own actions.

| Control | Default | Env / constant |
| --- | --- | --- |
| Digital / analog writes | disabled | `DAQ_MCP_ALLOW_WRITE=1` |
| Readable channels | `ANALOG_INPUTS` + `DIGITAL_INPUTS` + `DIGITAL_OUTPUTS` | `server.py` |
| Writable channels | `DIGITAL_OUTPUTS` + `ANALOG_OUTPUTS` | `WRITABLE_CHANNELS` in `server.py` |
| AO clamp range | ±5 V | `AO_VOLTAGE_LIMITS` in `server.py` |

Direction is enforced, not just documented. Channels are declared by direction
and the writable set is derived from the output groups, so a line wired to a
button is readable but cannot be driven. That is not pedantry: driving a line
that a button also drives puts two drivers in opposition, which is a short.

The channel lists in `server.py` describe one particular bench (a USB-6421 with
two buttons and two LEDs). Edit them to match your wiring before enabling
writes — an allowlist that does not describe your hardware protects nothing.

The MCP tools never import `nidaqmx` directly. They call a backend interface.
That boundary is what makes the server testable and portable.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_devices` | enumerate connected devices |
| `list_channels` | channel inventory for a device |
| `device_info` | model, serial, limits for one device |
| `read_analog` | sample an analog input |
| `write_analog` | drive an analog output (clamped) |
| `read_digital` | read one digital line |
| `write_digital` | drive one digital line, with read-back |
| `monitor_analog` | finite waveform reduced to statistics |
| `self_test` | device built-in self-test |
| `start_live` / `live_status` / `stop_live` | continuous background acquisition |

## Simulated by default (no NI drivers required)

Clone this on a machine without NI-DAQmx — including macOS — and it still
runs. Set `DAQ_MCP_SIMULATE=1` to force the pure-Python backend, or leave it
unset: the server tries the real driver and falls back to simulation with a
clear log line if NI is missing.

The simulator exposes two fake devices (`Dev1` / `Dev2`) with realistic
channel inventories. `Dev1/ai0` is a slow sine with noise; `Dev1/ai1` is a
noisy DC level. Digital lines keep state across calls.

## Live dashboard

The MCP tools are snapshots by design, and MCP is request/response, so neither
can express "show me the signal as it happens". For that there is a browser
dashboard: a scrolling plot of one analog channel, live digital input states,
and toggles for the digital outputs.

```bash
DAQ_MCP_ALLOW_WRITE=1 uv run server.py --dashboard-only
```

```powershell
$env:DAQ_MCP_ALLOW_WRITE="1"; uv run server.py --dashboard-only
```

Then open <http://127.0.0.1:8765/>. `--dashboard-only` implies the dashboard
and skips the MCP loop entirely; to get the dashboard alongside a normal MCP
server (the mode an editor launches), drop the flag and set
`DAQ_MCP_DASHBOARD=1` instead.

Standalone mode exists because an MCP server speaking stdio with no client
attached reads EOF immediately and exits, taking the dashboard with it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DAQ_MCP_DASHBOARD` | off | serve the dashboard |
| `DAQ_MCP_DASHBOARD_PORT` | `8765` | listen port |
| `DAQ_MCP_DASHBOARD_HOST` | `127.0.0.1` | loopback only by default |
| `DAQ_MCP_LIVE_CHANNEL` | `Dev1/ai0` | channel to stream |
| `DAQ_MCP_LIVE_RATE` | `1000` | sample rate in Hz |

The dashboard runs **inside the MCP server process** rather than beside it.
NI-DAQmx reserves a channel for the lifetime of a task, so a separate process
could not read `ai0` while the server held it. Sharing a process means the
model and the human see and drive exactly the same hardware, and browser writes
go through the same allowlist and write-enable checks as model writes.

While a stream is running, `read_analog` and `monitor_analog` on that channel
serve from the rolling buffer instead of opening a competing task; the response
carries `"source": "live_buffer"` so the caller knows. The agent can also drive
the stream itself with `start_live`, `live_status`, and `stop_live`.

### Only one server at a time

A DAQmx channel belongs to exactly one task, and the dashboard binds a fixed
port, so two copies of this server cannot share a device. Run the standalone
dashboard **or** let your editor launch the server — not both. MCP Inspector
spawns its own copy and collides the same way.

| Symptom | Meaning |
| --- | --- |
| `-50103` resource reserved | another process already holds the channel |
| `-200279` buffer overflow | the consumer stalled; the task is dead and must restart |
| `-201003` device cannot be accessed | the device is not plugged in or lost power |
| port already in use | another dashboard is running |

On Windows, killing `uv run ...` leaves the child Python process alive still
holding the device and the port. Kill the `python.exe` running `server.py`:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*server.py*' }
```

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

**Streaming errors are fatal, not swallowed.** Continuous acquisition is the
one place holding a task open, and a dead task returns no samples in exactly
the same way an idle one does. Only "samples not ready yet" is ignored; every
other driver error stops the monitor, releases the channel, and surfaces the
message in `live_status` and the dashboard. A stall that reports itself is
worth far more than one that keeps the UI looking healthy.

## Project layout

```
server.py                 # MCP tools + safety layer + dashboard wiring
src/daq_mcp/backend/      # DAQBackend ABC, simulated + nidaqmx backends
src/daq_mcp/live.py       # continuous acquisition thread + rolling window
src/daq_mcp/dashboard.py  # Starlette app, SSE stream, single-page UI
tests/                    # pytest against the simulator
```
