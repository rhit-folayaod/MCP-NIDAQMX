# Examples

Examples use **simulator** channel names so anyone can run them after
`uv sync` without hardware. On real devices, substitute names from
`list_devices` / `describe_device` and configure wiring first.

## 1. Simulated smoke test

```bash
DAQ_MCP_SIMULATE=1 uv run server.py
```

In an MCP client: `list_devices` → `read_analog` on `Dev1/ai0` with
`samples=10`.

Inspector (Windows PowerShell — pass env via `-e`):

```powershell
npx -y @modelcontextprotocol/inspector -e DAQ_MCP_SIMULATE=1 uv run server.py
```

## 2. Finite waveform summary

Call `monitor_analog` with `channel="Dev1/ai0"`, `duration_s=0.5`,
`rate_hz=1000`. Expect mean / RMS / peak-to-peak / preview — not thousands of
raw floats.

## 3. Specialty measurement (simulator)

Same channel names work with `measurement="strain"`, `"thermocouple"`, or
`"accelerometer"` so you can exercise tool parameters without hardware. On a
real module, use the matching measurement and a channel on that module.

## 4. Live chart + capture

```bash
DAQ_MCP_SIMULATE=1 uv run server.py --dashboard-only
```

Open http://127.0.0.1:8765/. Or from MCP: `start_live` → wait →
`save_capture` → `stop_live`.

## 5. Writes (still simulated)

```bash
DAQ_MCP_SIMULATE=1 DAQ_MCP_ALLOW_WRITE=1 uv run server.py --dashboard-only
```

Use the wiring picker (or `set_wiring`) so a DO line is listed as an output,
then `write_digital`. Without `DAQ_MCP_ALLOW_WRITE=1`, writes error by design.

## 6. Real hardware checklist

1. Install NI-DAQmx; `uv sync --extra hardware`
2. Leave `DAQ_MCP_SIMULATE` unset; confirm stderr says nidaqmx backend
3. `list_devices` / `describe_device`
4. Build a **local** wiring profile (gitignored) that matches your bench
5. Enable writes only after direction and allowlist look correct
6. Prefer `--http` if you need both the dashboard and an MCP client at once

## 7. Remote MCP over a tunnel

Keep the server on loopback or require `DAQ_MCP_TOKEN`, put a tunnel in front,
point the remote client at `/mcp/` with `Authorization: Bearer <token>`. Never
commit tokens or tunnel URLs.
