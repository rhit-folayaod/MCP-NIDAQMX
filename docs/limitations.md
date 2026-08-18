# Limitations

This server is intentionally scoped. Knowing the edges avoids false
expectations.

## Not a full NI-DAQmx replacement

- No LabVIEW / VeriStand / TestStand integration
- No arbitrary DAQmx task graphs, triggers, or multi-device synchronization
  beyond what the current tools implement
- No counter / timer / PWM / encoder tools yet
- No calibrated sensor database — you pass gage factor, TC type, sensitivity

## Measurement coverage

Voltage, strain (bridge), thermocouple, and IEPE accelerometer paths are
implemented with sensible defaults. Other bridge types, RTDs, microphones,
force/pressure specializations, etc. need additional channel create-calls
before tools can use them.

Defaults may not match your sensor. Wrong bridge configuration, TC type, or
sensitivity produces numbers that look “live” but are wrong — check datasheets.

## One stream, one process

- Only one continuous AI stream at a time
- Only one server process should own a device / dashboard port
- MCP Inspector spawning a second process will collide with a running server

## Safety is configuration-dependent

The allowlist and direction model only protect what you configured. A wrong
profile is as dangerous as no profile once writes are enabled.

## Data locality

Wiring profiles and captures stay on the machine that ran the server. They are
not part of the git repo and are not a multi-user cloud store.

## Affiliation

Not affiliated with, endorsed by, or supported by NI / Emerson. Hardware
behavior and driver errors come from NI-DAQmx and your devices.
