# Troubleshooting

## Backend / simulation

| Symptom | Likely cause |
| --- | --- |
| Expected sim but got real (or empty) devices | `DAQ_MCP_SIMULATE` not set in the process that actually launched (Inspector sanitizes the shell — use `-e`) |
| “No results yet” / zero devices | Real backend selected, no hardware present |
| Import / DLL errors with hardware extra | NI-DAQmx runtime not installed or mismatched |

Check the startup log line on **stderr** for which backend was selected.

## Resource and port conflicts

| Code / symptom | Meaning |
| --- | --- |
| `-50103` resource reserved | Another process (or stale Python) holds the channel |
| `-200279` buffer overflow | Live consumer stalled; stream is dead — restart live |
| `-201003` device cannot be accessed | Unplugged, powered down, or lost |
| Port already in use | Another dashboard on `DAQ_MCP_DASHBOARD_PORT` |

On Windows, stopping `uv run …` can leave a child `python.exe` holding the
device and port:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*server.py*' }
```

## Measurement / channel errors

| Symptom | What to try |
| --- | --- |
| “physical channel does not support the measurement type” | Wrong `measurement` for that module — use strain/TC/accel as appropriate |
| DSA module rejects on-demand read | Always use timed acquisition (the server does this for specialty paths) |
| Thermocouple rate errors | Lower `rate_hz` (modules are slow; the server also clamps) |
| Allowlist / not writable | Update wiring; confirm direction; enable `DAQ_MCP_ALLOW_WRITE` for outputs |

## Auth / remote

| Symptom | Fix |
| --- | --- |
| Server exits when binding `0.0.0.0` | Set `DAQ_MCP_TOKEN` |
| Dashboard SSE unauthorized | Pass `?token=` in the URL |
| Cloud agent cannot reach host | Tunnel to loopback `/mcp/` with Bearer token |

## Still stuck

File a GitHub issue with OS, Python version, simulated vs real, redacted env,
and the stderr backend line or DAQmx error code. Do not paste tokens or
absolute home-directory paths you care about keeping private.
