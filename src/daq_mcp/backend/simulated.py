"""Pure-Python simulated DAQ backend.

No nidaqmx import. Behaves plausibly so tools are useful without hardware:
two fake devices, sine + noisy-DC analog channels, sticky digital lines.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

from daq_mcp.backend.base import DAQBackend, TerminalConfig

# Tunables — keep these easy to find and tweak.
SINE_FREQUENCY_HZ = 1.0
SINE_AMPLITUDE_V = 2.5
SINE_OFFSET_V = 0.0
DC_LEVEL_V = 1.65
NOISE_STD_V = 0.05

_DEVICES: dict[str, dict[str, Any]] = {
    "Dev1": {
        "name": "Dev1",
        "product_type": "PCIe-6363",
        "serial_number": 0x1A2B3C4D,
        "ai_channels": [f"Dev1/ai{i}" for i in range(16)],
        "ao_channels": [f"Dev1/ao{i}" for i in range(4)],
        "di_channels": [f"Dev1/port0/line{i}" for i in range(8)],
        "do_channels": [f"Dev1/port0/line{i}" for i in range(8)],
        "ai_voltage_ranges": [[-10.0, 10.0], [-5.0, 5.0], [-1.0, 1.0]],
        "ao_voltage_ranges": [[-10.0, 10.0], [-5.0, 5.0]],
        "max_ai_rate_hz": 2_000_000.0,
        "max_ao_rate_hz": 2_860_000.0,
    },
    "Dev2": {
        "name": "Dev2",
        "product_type": "USB-6001",
        "serial_number": 0x55AA00FF,
        "ai_channels": [f"Dev2/ai{i}" for i in range(8)],
        "ao_channels": [f"Dev2/ao{i}" for i in range(2)],
        "di_channels": [f"Dev2/port0/line{i}" for i in range(8)],
        "do_channels": [f"Dev2/port0/line{i}" for i in range(8)],
        "ai_voltage_ranges": [[-10.0, 10.0]],
        "ao_voltage_ranges": [[0.0, 5.0]],
        "max_ai_rate_hz": 20_000.0,
        "max_ao_rate_hz": 5_000.0,
    },
}


def _stats(samples: list[float]) -> dict[str, float]:
    n = len(samples)
    if n == 0:
        return {"min": 0.0, "mean": 0.0, "max": 0.0}
    total = sum(samples)
    return {
        "min": min(samples),
        "mean": total / n,
        "max": max(samples),
    }


class SimulatedBackend(DAQBackend):
    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()
        # Digital line state persists across calls (write then read reflects write).
        self._digital: dict[str, bool] = {}
        self._analog_out: dict[str, float] = {}

    def list_devices(self) -> list[dict[str, Any]]:
        result = []
        for dev in _DEVICES.values():
            result.append(
                {
                    "name": dev["name"],
                    "product_type": dev["product_type"],
                    "serial_number": dev["serial_number"],
                    "supports": {
                        "analog_input": bool(dev["ai_channels"]),
                        "analog_output": bool(dev["ao_channels"]),
                        "digital_input": bool(dev["di_channels"]),
                        "digital_output": bool(dev["do_channels"]),
                    },
                }
            )
        return result

    def describe_device(self, device: str) -> dict[str, Any]:
        if device not in _DEVICES:
            return {"error": f"Unknown device {device!r}", "available": sorted(_DEVICES)}
        dev = _DEVICES[device]
        return {
            "name": dev["name"],
            "product_type": dev["product_type"],
            "serial_number": dev["serial_number"],
            "analog_input_channels": list(dev["ai_channels"]),
            "analog_output_channels": list(dev["ao_channels"]),
            "digital_input_channels": list(dev["di_channels"]),
            "digital_output_channels": list(dev["do_channels"]),
            "ai_voltage_ranges": list(dev["ai_voltage_ranges"]),
            "ao_voltage_ranges": list(dev["ao_voltage_ranges"]),
            "max_ai_rate_hz": dev["max_ai_rate_hz"],
            "max_ao_rate_hz": dev["max_ao_rate_hz"],
        }

    def read_analog(
        self,
        channel: str,
        samples: int,
        rate_hz: float,
        terminal_config: TerminalConfig,
    ) -> dict[str, Any]:
        if samples < 1:
            return {"error": "samples must be >= 1"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        values = self._generate_ai(channel, samples, rate_hz)
        return {
            "channel": channel,
            "samples": values,
            "rate_hz": rate_hz,
            "terminal_config": terminal_config,
            **_stats(values),
        }

    def read_digital(self, channel: str) -> dict[str, Any]:
        value = self._digital.get(channel, False)
        return {"channel": channel, "value": value}

    def write_digital(self, channel: str, value: bool) -> dict[str, Any]:
        self._digital[channel] = bool(value)
        return {"channel": channel, "value": self._digital[channel]}

    def write_analog(self, channel: str, voltage: float) -> dict[str, Any]:
        self._analog_out[channel] = float(voltage)
        return {
            "channel": channel,
            "voltage": self._analog_out[channel],
        }

    def acquire_waveform(
        self,
        channel: str,
        duration_s: float,
        rate_hz: float,
    ) -> dict[str, Any]:
        if duration_s <= 0:
            return {"error": "duration_s must be > 0"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        n = max(1, int(round(duration_s * rate_hz)))
        values = self._generate_ai(channel, n, rate_hz)
        return {
            "channel": channel,
            "samples": values,
            "rate_hz": rate_hz,
            "duration_s": duration_s,
            "sample_count": n,
        }

    def self_test(self, device: str) -> dict[str, Any]:
        if device not in _DEVICES:
            return {
                "device": device,
                "passed": False,
                "error": f"Unknown device {device!r}",
            }
        return {"device": device, "passed": True}

    def _generate_ai(self, channel: str, n: int, rate_hz: float) -> list[float]:
        """ai0-style: slow sine. ai1-style: noisy DC. Others: noisy DC at 0 V."""
        t_start = time.monotonic() - self._t0
        dt = 1.0 / rate_hz
        values: list[float] = []

        # Channel suffix decides the waveform family.
        suffix = channel.rsplit("/", 1)[-1]
        for i in range(n):
            t = t_start + i * dt
            noise = self._rng.gauss(0.0, NOISE_STD_V)
            if suffix == "ai0":
                v = (
                    SINE_OFFSET_V
                    + SINE_AMPLITUDE_V * math.sin(2.0 * math.pi * SINE_FREQUENCY_HZ * t)
                    + noise
                )
            elif suffix == "ai1":
                v = DC_LEVEL_V + noise
            else:
                v = noise
            values.append(v)
        return values
