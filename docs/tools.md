# Tool reference

All tools go through the safety layer in `server.py` (allowlist, write gate,
direction) before the backend. Channel names are fully qualified, e.g.
`Dev1/ai0` (simulator) or `cDAQ1Mod1/ai0` (CompactDAQ module).

## Discovery

| Tool | Purpose |
| --- | --- |
| `list_devices` | Enumerate connected devices (name, product type, serial, capability flags) |
| `describe_device` | Channel inventory, ranges, rates; may include `suggested_measurement` |
| `self_test` | Device built-in self-test pass/fail |

## Analog input

| Tool | Purpose |
| --- | --- |
| `read_analog` | Acquire samples; returns values plus min/mean/max |
| `monitor_analog` | Finite acquisition summarized (mean, RMS, peak-to-peak, std-dev, preview) |

Important shared parameters:

| Parameter | Notes |
| --- | --- |
| `channel` | Must be on the allowlist |
| `measurement` | `voltage` (default), `strain`, `thermocouple`, or `accelerometer` |
| `rate_hz` / `samples` / `duration_s` | Specialty modules clamp rates (e.g. thermocouple is slow) |
| `thermocouple_type` | Default `K` |
| `accel_sensitivity_mv_per_g` | Default `100` |
| `gage_factor` | Default `2.0` for strain |

Use the measurement that matches the module. Plain `voltage` on a strain or
thermocouple module fails at the driver.

While a channel is live-streaming, reads on that channel come from the ring
buffer and include `"source": "live_buffer"`.

## Analog / digital output

| Tool | Purpose |
| --- | --- |
| `write_analog` | DC voltage out (clamped); returns requested vs applied |
| `write_digital` | Drive one DO line; returns hardware read-back |
| `animate_digital` | Chase pattern on all allowlisted DOs, then all off |
| `read_digital` | Read one DI (or readable) line |

Require `DAQ_MCP_ALLOW_WRITE=1` and a writable channel.

## Live acquisition

| Tool | Purpose |
| --- | --- |
| `start_live` | Start continuous stream (optional `measurement=…`) |
| `live_status` | Stats + preview over the rolling window |
| `stop_live` | Stop stream and release the channel |
| `save_capture` | Persist metrics + samples under `.daq_mcp_captures/` |
| `list_captures` | List recent captures on this machine |

Only one live stream at a time.

## Wiring

| Tool | Purpose |
| --- | --- |
| `get_wiring` | Active roles, profile name, allowlist |
| `set_wiring` | Update roles; optional `profile_name` |
| `list_wiring_profiles` | Local profile names |
| `load_wiring_profile` | Activate a saved profile |
| `delete_wiring_profile` | Remove a local profile |

## Design notes

- **One tool ≈ one complete hardware operation** for snapshot tools (open /
  configure / read or write / close).
- **Summaries over raw dumps** for waveforms so agent context stays small.
- **Streaming errors are fatal** except “samples not yet ready”; overflows and
  device loss stop the monitor and surface in `live_status` / the dashboard.
