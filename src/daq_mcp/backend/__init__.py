"""Backend selection: simulate by force, real NI if available, else simulate."""

from __future__ import annotations

import logging
import os

from daq_mcp.backend.base import DAQBackend

logger = logging.getLogger("daq-mcp")

_backend: DAQBackend | None = None


def get_backend() -> DAQBackend:
    """Return the process-wide backend, creating it on first use."""
    global _backend
    if _backend is None:
        _backend = _select_backend()
    return _backend


def _select_backend() -> DAQBackend:
    if os.getenv("DAQ_MCP_SIMULATE") == "1":
        from daq_mcp.backend.simulated import SimulatedBackend

        logger.info("DAQ_MCP_SIMULATE=1 — using simulated backend")
        return SimulatedBackend()

    try:
        from daq_mcp.backend.nidaqmx_backend import NIDAQmxBackend

        backend = NIDAQmxBackend()
        logger.info("Using real nidaqmx backend")
        return backend
    except Exception as exc:
        from daq_mcp.backend.simulated import SimulatedBackend

        logger.warning(
            "NI-DAQmx unavailable (%s); falling back to simulated backend",
            exc,
        )
        return SimulatedBackend()


def reset_backend() -> None:
    """Clear the cached backend (for tests)."""
    global _backend
    _backend = None
