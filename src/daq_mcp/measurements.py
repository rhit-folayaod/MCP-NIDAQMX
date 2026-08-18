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

"""Analog measurement kinds beyond plain voltage.

CompactDAQ modules like the NI 9236 / 9213 / 9234 need specialized DAQmx
channel types. Tools pass a Measurement literal; backends map it to the
matching create-channel call and unit label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Measurement = Literal["voltage", "strain", "thermocouple", "accelerometer"]

MEASUREMENT_UNITS: dict[Measurement, str] = {
    "voltage": "V",
    "strain": "strain",
    "thermocouple": "deg_C",
    "accelerometer": "g",
}

# Conservative defaults for common specialty-module setups.
# Override per call (gage factor, TC type, sensitivity) for your sensors.
DEFAULT_STRAIN_EXCIT_V = 3.3
DEFAULT_GAGE_FACTOR = 2.0
DEFAULT_GAGE_RESISTANCE = 350.0
DEFAULT_TC_TYPE = "K"
DEFAULT_ACCEL_SENS_MV_PER_G = 100.0
DEFAULT_ACCEL_EXCIT_A = 0.002

# Thermocouple modules are slow; clamp agent-requested rates.
MAX_RATE_HZ: dict[Measurement, float] = {
    "voltage": 500_000.0,
    "strain": 10_000.0,
    "thermocouple": 75.0,
    "accelerometer": 51_200.0,
}


@dataclass(frozen=True)
class AiOptions:
    measurement: Measurement = "voltage"
    terminal_config: Literal["default", "rse", "nrse", "diff"] = "default"
    thermocouple_type: Literal["J", "K", "T", "E", "N", "R", "S", "B"] = "K"
    accel_sensitivity_mv_per_g: float = DEFAULT_ACCEL_SENS_MV_PER_G
    gage_factor: float = DEFAULT_GAGE_FACTOR
    strain_excit_v: float = DEFAULT_STRAIN_EXCIT_V

    def units(self) -> str:
        return MEASUREMENT_UNITS[self.measurement]

    def clamp_rate(self, rate_hz: float) -> float:
        cap = MAX_RATE_HZ[self.measurement]
        return min(float(rate_hz), cap)

    def meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "measurement": self.measurement,
            "units": self.units(),
        }
        if self.measurement == "thermocouple":
            out["thermocouple_type"] = self.thermocouple_type
        if self.measurement == "accelerometer":
            out["accel_sensitivity_mv_per_g"] = self.accel_sensitivity_mv_per_g
        if self.measurement == "strain":
            out["gage_factor"] = self.gage_factor
            out["strain_excit_v"] = self.strain_excit_v
        return out


def suggest_measurement(product_type: str) -> Measurement | None:
    """Best-guess measurement for a module product string from NI-DAQmx."""
    p = product_type.upper()
    if "9236" in p or "9237" in p:
        return "strain"
    if "9213" in p or "9214" in p or "9211" in p:
        return "thermocouple"
    if "9234" in p or "9233" in p or "9232" in p:
        return "accelerometer"
    if "9215" in p or "9205" in p or "9201" in p or "9220" in p:
        return "voltage"
    return None
