# Copyright 2026 Timi Folayan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
