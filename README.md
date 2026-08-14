# daq-mcp

An MCP server that lets an AI coding client talk to NI DAQ hardware.

For a portfolio-oriented stack summary (resume bullets, keywords, architecture),
see [TECH_STACK.md](TECH_STACK.md).

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

The defaults in `server.py` describe one particular bench (a USB-6421 with
two buttons and two LEDs). Prefer the **dashboard channel picker**: it loads
the connected device's full inventory and lets you mark which lines are
inputs vs outputs. Choices are saved to `.daq_mcp_wiring.json` (gitignored)
and applied to the same allowlist the agent uses — so a phone-prompted agent
and the laptop dashboard stay in sync.

An allowlist that does not describe your hardware protects nothing. Never
enable writes until the wiring matches the bench.

The MCP tools never import `nidaqmx` directly. They call a backend interface.
That boundary is what makes the server testable and portable.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_devices` | enumerate connected devices |
| `describe_device` | model, serial, channel inventory, limits |
| `read_analog` | sample an analog input |
| `write_analog` | drive an analog output (clamped) |
| `read_digital` | read one digital line |
| `write_digital` | drive one digital line, with read-back |
| `monitor_analog` | finite waveform reduced to statistics |
| `self_test` | device built-in self-test |
| `start_live` / `live_status` / `stop_live` | continuous background acquisition |
| `get_wiring` / `set_wiring` | read or update channel roles (same as the picker) |
| `list_wiring_profiles` / `load_wiring_profile` / `delete_wiring_profile` | named local wiring layouts |
| `save_capture` / `list_captures` | snapshot live metrics + samples to disk |

<!-- Demo GIF placeholder: drop a short clip of the dashboard + LED blink here. -->

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
and skips the MCP loop entirely.

### Unified HTTP mode (dashboard + MCP on one port)

Inspector normally *spawns* its own server process, which steals the device from
a running dashboard. `--http` serves both on one process instead:

```powershell
$env:DAQ_MCP_ALLOW_WRITE="1"
uv run server.py --http
```

- Dashboard: <http://127.0.0.1:8765/>
- MCP (Streamable HTTP): <http://127.0.0.1:8765/mcp/>

Point MCP Inspector at the `/mcp/` URL rather than launching `uv run server.py`
again.

### Remote / phone / cloud agents

A cloud agent cannot reach `127.0.0.1` on your laptop. To demo from a phone:

1. Run with a shared secret and (only then) a non-loopback bind, **or** keep
   loopback and put a tunnel in front:

```powershell
$env:DAQ_MCP_ALLOW_WRITE="1"
$env:DAQ_MCP_TOKEN="pick-a-long-random-secret"
$env:DAQ_MCP_DASHBOARD_HOST="0.0.0.0"   # refused without DAQ_MCP_TOKEN
uv run server.py --http
```

2. Open the dashboard as `http://<lan-ip>:8765/?token=pick-a-long-random-secret`
   (EventSource cannot set Authorization headers, so the token is also accepted
   as a query param).
3. For agents off your LAN, put a tunnel in front (Cloudflare Tunnel, ngrok,
   etc.) and configure the remote MCP client with the public `/mcp/` URL plus
   `Authorization: Bearer <token>`.

Never bind `0.0.0.0` without `DAQ_MCP_TOKEN`. The server exits if you try.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DAQ_MCP_DASHBOARD` | off | serve the dashboard beside stdio MCP |
| `DAQ_MCP_DASHBOARD_PORT` | `8765` | listen port |
| `DAQ_MCP_DASHBOARD_HOST` | `127.0.0.1` | loopback only by default |
| `DAQ_MCP_TOKEN` | unset | shared secret; required for non-loopback |
| `DAQ_MCP_LIVE_CHANNEL` | `Dev1/ai0` | channel to stream |
| `DAQ_MCP_LIVE_RATE` | `1000` | sample rate in Hz |

Standalone `--dashboard-only` exists because an MCP server speaking stdio with
no client attached reads EOF immediately and exits, taking the dashboard with
it. Drop `--dashboard-only` and set `DAQ_MCP_DASHBOARD=1` when an editor
launches the server over stdio and you still want the browser UI.

The dashboard runs **inside the MCP server process** rather than beside it.
NI-DAQmx reserves a channel for the lifetime of a task, so a separate process
could not read `ai0` while the server held it. Sharing a process means the
model and the human see and drive exactly the same hardware, and browser writes
go through the same allowlist and write-enable checks as model writes.

While a stream is running, `read_analog` and `monitor_analog` on that channel
serve from the rolling buffer instead of opening a competing task; the response
carries `"source": "live_buffer"` so the caller knows. The agent can also drive
the stream itself with `start_live`, `live_status`, and `stop_live`.

### Channel picker

The **Channel wiring** panel on the dashboard lists every AI / AO / DIO pin the
driver reports for the selected device. Check the ones you want on the
allowlist, mark digital lines as input or output (not both), choose which
analog input to stream, give the layout a **name**, and Save. That updates:

- a named profile under `.daq_mcp_profiles/` on this PC (gitignored)
- the MCP allowlist and writable set (what a cloud agent may touch)
- which lamps/toggles the dashboard shows
- the live stream target (restarted on save)

Load and Delete switch or remove profiles without touching the repo. The
driver still cannot detect that a pin is an LED vs a button — you are
asserting that. MCP tools: `list_wiring_profiles`, `load_wiring_profile`,
`delete_wiring_profile`, `get_wiring`, `set_wiring`.

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

All tests run against the simulated backend. CI runs the same command on every
push to `main`.

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
src/daq_mcp/wiring.py     # local channel-role config for the picker
src/daq_mcp/auth.py      # optional shared-secret gate for HTTP mode
tests/                    # pytest against the simulator
.github/workflows/ci.yml  # simulator pytest on push
```
