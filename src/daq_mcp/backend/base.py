"""Hardware-agnostic DAQ backend interface.

MCP tools call this interface only. They never import nidaqmx directly.
That boundary keeps the server testable and runnable without NI drivers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

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
    ) -> dict[str, Any]:
        """Acquire voltage samples and return them with min/mean/max."""

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
    ) -> dict[str, Any]:
        """Finite timed acquisition; return samples for the tool to summarize."""

    @abstractmethod
    def self_test(self, device: str) -> dict[str, Any]:
        """Run the device self-test; return pass/fail."""
