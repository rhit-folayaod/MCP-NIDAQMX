# Devices: simulated and NI-DAQmx

## Backend selection

1. If `DAQ_MCP_SIMULATE=1` → `SimulatedBackend`
2. Else try `NIDAQmxBackend` (needs NI-DAQmx drivers + `uv sync --extra hardware`)
3. On failure → fall back to simulation and log a clear stderr line

Startup always reports which backend was selected.

## Simulated backend

No NI drivers required (including macOS / CI). Useful for cloning, MCP
Inspector smoke tests, and unit tests.

Fake devices:

| Device | Shape |
| --- | --- |
| `Dev1` | Multi-channel AI/AO/DIO inventory |
| `Dev2` | Smaller inventory |

`Dev1/ai0` is a slow sine with noise; `Dev1/ai1` is a noisy DC level. Digital
lines keep state across calls. Specialty `measurement` values produce
plausible fake strain / °C / g traces for tool testing.

## NI-DAQmx backend

Uses the published `nidaqmx` Python API. Snapshot tools use one Task per call.
Live streaming keeps one continuous Task owned by `LiveMonitor`.

### Measurement types

| `measurement` | Typical hardware | Notes |
| --- | --- | --- |
| `voltage` | Voltage AI modules / multifunction DAQ | Default |
| `strain` | Strain bridge modules | Quarter-bridge defaults; override gage factor / excitation as needed |
| `thermocouple` | Thermocouple modules | Type K + built-in CJC by default; rate capped |
| `accelerometer` | IEPE / DSA modules | Sensitivity in mV/g; sample clock always required |

`describe_device` may set `suggested_measurement` from the product type string
when it recognizes common module families. Treat that as a hint — confirm
against your wiring and sensor datasheet.

### Channel naming

- Multifunction boards often look like `Dev1/ai0`, `Dev1/port0/line0`.
- CompactDAQ places pins on modules, e.g. `cDAQ1Mod3/port0/line0`.
- Always call `list_devices` / `describe_device` on *your* system; names are
  machine-specific.

### Requirements

- Supported OS with NI-DAQmx installed
- Device visible in NI MAX / system device list
- Python package: project optional extra `hardware` (`nidaqmx`)

This project is not affiliated with NI / Emerson. Device support is whatever
NI-DAQmx and your modules expose through the APIs used here.
