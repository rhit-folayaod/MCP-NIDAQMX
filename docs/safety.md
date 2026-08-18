# Safety model

Physical I/O is the point of this project. A wrong channel name or a write to
an input line is not a soft HTTP error — it can damage equipment or create a
short. The server encodes that in software.

## Controls

| Control | Default | How to change |
| --- | --- | --- |
| Digital / analog writes | **disabled** | `DAQ_MCP_ALLOW_WRITE=1` |
| Readable channels | wiring / defaults | dashboard picker or `set_wiring` |
| Writable channels | output groups only | same — derived from AO + DO lists |
| AO voltage clamp | ±5 V | `AO_VOLTAGE_LIMITS` in `server.py` |

## Rules

1. **Writes off until enabled.** Tools return a clear error until
   `DAQ_MCP_ALLOW_WRITE=1`.
2. **Allowlist every channel.** Arguments not on the allowlist are rejected
   before the backend runs.
3. **Direction is enforced.** Digital/analog *inputs* are readable but not
   writable. Driving a line that something else also drives puts two drivers
   in opposition.
4. **AO is clamped.** Responses include requested voltage, applied voltage,
   and a `clamped` flag — never a silent rewrite.
5. **Digital write read-back.** `write_digital` returns the line state from
   the DO task after the write, not an echo of the request.
6. **Same gates for the browser.** Dashboard toggles call the same write path
   as MCP tools.

## Wiring profiles

NI-DAQmx lists every channel a device *supports*. It cannot tell you which pin
is an LED vs a button. You assert direction and membership via the dashboard
picker or MCP wiring tools. Profiles are stored under `.daq_mcp_profiles/` on
the local machine and are **gitignored** — do not commit bench layouts.

An allowlist that does not match your hardware protects nothing. Enable writes
only after the profile matches the physical bench.

## Network exposure

Loopback HTTP needs no token. Binding beyond localhost without `DAQ_MCP_TOKEN`
is refused at startup. Prefer a tunnel plus Bearer auth for remote agents
rather than an open LAN bind.
