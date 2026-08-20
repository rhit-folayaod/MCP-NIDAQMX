# daq-mcp documentation

| Doc | Contents |
| --- | --- |
| [Architecture](architecture.md) | Process layout, backends, live path, HTTP vs stdio |
| [Safety](safety.md) | Write gate, allowlists, direction, AO clamp |
| [Configuration](configuration.md) | Env vars, CLI flags, Cursor / MCP clients |
| [Tool reference](tools.md) | Every MCP tool and important parameters |
| [Devices](devices.md) | Simulated backend and NI-DAQmx path |
| [Examples](examples.md) | Common workflows (generic channel names) |
| [Limitations](limitations.md) | What this server does not try to do |
| [Troubleshooting](troubleshooting.md) | Driver errors, ports, Windows process leaks |
| [Development history](development.md) | Stages, hardware/MCP failures, and what we changed |

Start with the root [README](../README.md) for install and a short overview.

[development.md](development.md) is written as source material for a later
paper. It is not required to run the server.
