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

"""Hardware-agnostic DAQ backend interface.

MCP tools call this interface only. They never import nidaqmx directly.
That boundary keeps the server testable and runnable without NI drivers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from daq_mcp.measurements import AiOptions

TerminalConfig = Literal["default", "rse", "nrse", "diff"]


class DAQBackend(ABC):
    """Operations the MCP tools need from whatever sits under them."""

    @abstractmethod
    def list_devices(self) -> list[dict[str, Any]]:
        """Return visible devices with name, product type, serial, capabilities."""

    @abstractmethod
    def describe_device(self, device: str) -> dict[str, Any]:
        """Return full channel inventory and limits for one device."""

    @abstractmethod
    def read_analog(
        self,
        channel: str,
        samples: int,
        rate_hz: float,
        terminal_config: TerminalConfig,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        """Acquire analog samples (voltage / strain / TC / accel) with min/mean/max."""

    @abstractmethod
    def read_digital(self, channel: str) -> dict[str, Any]:
        """Read one digital line; return {"channel", "value"}."""

    @abstractmethod
    def write_digital(self, channel: str, value: bool) -> dict[str, Any]:
        """Write one digital line and return the read-back state."""

    @abstractmethod
    def write_analog(self, channel: str, voltage: float) -> dict[str, Any]:
        """Write a DC voltage to an AO channel; return applied value."""

    @abstractmethod
    def acquire_waveform(
        self,
        channel: str,
        duration_s: float,
        rate_hz: float,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        """Finite timed acquisition; return samples for the tool to summarize."""

    @abstractmethod
    def self_test(self, device: str) -> dict[str, Any]:
        """Run the device self-test; return pass/fail."""

    # ----------------------------------------------------------------------
    # Continuous acquisition.
    #
    # Unlike every other method here, these three are stateful: the task stays
    # open between calls. A driver reserves a channel for the lifetime of a
    # task, so while a stream is running nothing else in this process (or any
    # other) can open that channel for a one-shot read. Callers are expected
    # to serve snapshot reads of a streaming channel from the buffer instead.
    # ----------------------------------------------------------------------

    @abstractmethod
    def start_stream(
        self,
        channel: str,
        rate_hz: float,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        """Begin continuous acquisition on one channel.

        Returns {"channel", "rate_hz"} on success or {"error"} on failure.
        Starting a stream while one is already running is an error.
        """

    @abstractmethod
    def read_stream(self, max_samples: int = 10_000) -> list[float]:
        """Drain whatever samples have accumulated since the last call.

        Returns an empty list when no samples are ready. Never blocks waiting
        for the buffer to fill.
        """

    @abstractmethod
    def stop_stream(self) -> dict[str, Any]:
        """Stop continuous acquisition and release the channel."""

    @abstractmethod
    def streaming_channel(self) -> str | None:
        """The channel currently being streamed, or None."""
