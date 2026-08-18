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

"""Real NI-DAQmx backend.

One Task per call, created and closed inside the method. nidaqmx is imported
only here (and only when this backend is selected), so clones without drivers
never pay the import cost on the simulated path.

API calls follow the published nidaqmx docs:
  System.local().devices
  Device.ai_physical_chans / ao_physical_chans / di_lines / do_lines
  Task.ai_channels.add_ai_voltage_chan(..., terminal_config=...)
  Task.timing.cfg_samp_clk_timing(..., sample_mode=AcquisitionType.FINITE, ...)
  Task.di/do_channels.add_*_chan(..., line_grouping=LineGrouping.CHAN_PER_LINE)
  Device.self_test_device()
  nidaqmx.errors.DaqError (error_code + message)
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from daq_mcp.backend.base import DAQBackend, TerminalConfig
from daq_mcp.measurements import (
    DEFAULT_ACCEL_EXCIT_A,
    DEFAULT_GAGE_RESISTANCE,
    AiOptions,
    suggest_measurement,
)

T = TypeVar("T")

# -200284: requested samples not yet acquired. Expected when polling a
# continuous task faster than the hardware fills it; every other code means
# the task is broken.
_BENIGN_STREAM_ERRORS = frozenset({-200284})


def _daq_error(exc: Exception) -> dict[str, Any]:
    """Structured NI error — never let a traceback escape to the model."""
    error_code = getattr(exc, "error_code", None)
    return {
        "error": str(exc),
        "error_code": error_code,
    }


def _as_float_list(data: Any) -> list[float]:
    if hasattr(data, "tolist"):
        data = data.tolist()
    if isinstance(data, (int, float)):
        return [float(data)]
    return [float(x) for x in data]


def _pair_ranges(flat: list[float]) -> list[list[float]]:
    return [[float(flat[i]), float(flat[i + 1])] for i in range(0, len(flat) - 1, 2)]


def _channel_names(collection: Any) -> list[str]:
    try:
        return list(collection.channel_names)
    except Exception:
        return []


def _safe(getter: Callable[[], T], default: T) -> T:
    try:
        return getter()
    except Exception:
        return default


class NIDAQmxBackend(DAQBackend):
    def __init__(self) -> None:
        # Lazy import: selection path is the only place that touches nidaqmx.
        import nidaqmx  # noqa: F401
        from nidaqmx.system import System

        self._System = System
        # Probe the driver so missing DLLs fail at selection, not mid-tool-call.
        _ = list(System.local().devices)

        # The one long-lived task in this class. Guarded by a lock because the
        # monitor thread drains it while MCP tool calls may stop it.
        self._stream_task: Any = None
        self._stream_channel: str | None = None
        self._stream_lock = threading.Lock()
        self._stream_options: AiOptions = AiOptions()

    def _terminal(self, terminal_config: TerminalConfig):
        from nidaqmx.constants import TerminalConfiguration

        return {
            "default": TerminalConfiguration.DEFAULT,
            "rse": TerminalConfiguration.RSE,
            "nrse": TerminalConfiguration.NRSE,
            "diff": TerminalConfiguration.DIFF,
        }[terminal_config]

    def _configure_ai(self, task: Any, channel: str, options: AiOptions) -> None:
        """Create the right DAQmx AI channel type for this measurement."""
        from nidaqmx.constants import (
            AccelSensitivityUnits,
            AccelUnits,
            CJCSource,
            ExcitationSource,
            StrainGageBridgeType,
            StrainUnits,
            TemperatureUnits,
            ThermocoupleType,
        )

        if options.measurement == "voltage":
            task.ai_channels.add_ai_voltage_chan(
                channel,
                terminal_config=self._terminal(options.terminal_config),
                min_val=-10.0,
                max_val=10.0,
            )
            return

        if options.measurement == "strain":
            # CompactDAQ strain modules commonly use quarter-bridge wiring.
            # Full/half configs are rejected by some modules (e.g. NI 9236).
            task.ai_channels.add_ai_strain_gage_chan(
                channel,
                min_val=-0.005,
                max_val=0.005,
                units=StrainUnits.STRAIN,
                strain_config=StrainGageBridgeType.QUARTER_BRIDGE_I,
                voltage_excit_source=ExcitationSource.INTERNAL,
                voltage_excit_val=float(options.strain_excit_v),
                gage_factor=float(options.gage_factor),
                nominal_gage_resistance=DEFAULT_GAGE_RESISTANCE,
            )
            return

        if options.measurement == "thermocouple":
            tc_map = {
                "J": ThermocoupleType.J,
                "K": ThermocoupleType.K,
                "T": ThermocoupleType.T,
                "E": ThermocoupleType.E,
                "N": ThermocoupleType.N,
                "R": ThermocoupleType.R,
                "S": ThermocoupleType.S,
                "B": ThermocoupleType.B,
            }
            task.ai_channels.add_ai_thrmcpl_chan(
                channel,
                min_val=-20.0,
                max_val=120.0,
                units=TemperatureUnits.DEG_C,
                thermocouple_type=tc_map[options.thermocouple_type],
                cjc_source=CJCSource.BUILT_IN,
            )
            return

        if options.measurement == "accelerometer":
            # NI 9234 IEPE path. DEFAULT terminal config is required on this
            # module; PSEUDODIFF/DIFF are rejected.
            task.ai_channels.add_ai_accel_chan(
                channel,
                terminal_config=self._terminal("default"),
                min_val=-50.0,
                max_val=50.0,
                units=AccelUnits.G,
                sensitivity=float(options.accel_sensitivity_mv_per_g),
                sensitivity_units=AccelSensitivityUnits.MILLIVOLTS_PER_G,
                current_excit_source=ExcitationSource.INTERNAL,
                current_excit_val=DEFAULT_ACCEL_EXCIT_A,
            )
            return

        raise ValueError(f"Unsupported measurement {options.measurement!r}")

    def _finite_read(
        self,
        channel: str,
        samples: int,
        rate_hz: float,
        options: AiOptions,
    ) -> list[float]:
        """Always use a sample clock — DSA modules (9234) reject on-demand reads."""
        import nidaqmx
        from nidaqmx.constants import AcquisitionType

        n = max(1, int(samples))
        rate = max(1.0, options.clamp_rate(rate_hz))
        with nidaqmx.Task() as task:
            self._configure_ai(task, channel, options)
            task.timing.cfg_samp_clk_timing(
                rate=rate,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=n,
            )
            timeout = max(10.0, n / rate + 5.0)
            raw = task.read(number_of_samples_per_channel=n, timeout=timeout)
        return _as_float_list(raw)

    def list_devices(self) -> list[dict[str, Any]]:
        from nidaqmx.errors import DaqError

        try:
            result = []
            for device in self._System.local().devices:
                ai = _channel_names(device.ai_physical_chans)
                ao = _channel_names(device.ao_physical_chans)
                di = _channel_names(device.di_lines)
                do = _channel_names(device.do_lines)
                result.append(
                    {
                        "name": device.name,
                        "product_type": _safe(lambda: device.product_type, ""),
                        "serial_number": _safe(lambda: device.serial_num, 0),
                        "supports": {
                            "analog_input": bool(ai),
                            "analog_output": bool(ao),
                            "digital_input": bool(di),
                            "digital_output": bool(do),
                        },
                    }
                )
            return result
        except DaqError as exc:
            return [_daq_error(exc)]

    def describe_device(self, device: str) -> dict[str, Any]:
        from nidaqmx.errors import DaqError

        try:
            names = [d.name for d in self._System.local().devices]
            if device not in names:
                return {"error": f"Unknown device {device!r}", "available": names}

            dev = self._System.local().devices[device]
            ai_rng = _safe(lambda: list(dev.ai_voltage_rngs), [])
            ao_rng = _safe(lambda: list(dev.ao_voltage_rngs), [])
            product = _safe(lambda: dev.product_type, "")
            suggested = suggest_measurement(product)
            return {
                "name": dev.name,
                "product_type": product,
                "serial_number": _safe(lambda: dev.serial_num, 0),
                "analog_input_channels": _channel_names(dev.ai_physical_chans),
                "analog_output_channels": _channel_names(dev.ao_physical_chans),
                "digital_input_channels": _channel_names(dev.di_lines),
                "digital_output_channels": _channel_names(dev.do_lines),
                "ai_voltage_ranges": _pair_ranges(ai_rng),
                "ao_voltage_ranges": _pair_ranges(ao_rng),
                "max_ai_rate_hz": _safe(lambda: float(dev.ai_max_single_chan_rate), 0.0),
                "max_ao_rate_hz": _safe(lambda: float(dev.ao_max_rate), 0.0),
                "suggested_measurement": suggested,
            }
        except DaqError as exc:
            return _daq_error(exc)

    def read_analog(
        self,
        channel: str,
        samples: int,
        rate_hz: float,
        terminal_config: TerminalConfig,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        from nidaqmx.errors import DaqError

        opts = options or AiOptions()
        opts = AiOptions(
            measurement=opts.measurement,
            terminal_config=terminal_config,
            thermocouple_type=opts.thermocouple_type,
            accel_sensitivity_mv_per_g=opts.accel_sensitivity_mv_per_g,
            gage_factor=opts.gage_factor,
            strain_excit_v=opts.strain_excit_v,
        )

        if samples < 1:
            return {"error": "samples must be >= 1"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        rate = opts.clamp_rate(rate_hz)
        try:
            values = self._finite_read(channel, samples, rate, opts)
            return {
                "channel": channel,
                "samples": values,
                "rate_hz": rate,
                "terminal_config": terminal_config,
                **opts.meta(),
                "min": min(values) if values else 0.0,
                "mean": sum(values) / len(values) if values else 0.0,
                "max": max(values) if values else 0.0,
            }
        except DaqError as exc:
            return _daq_error(exc)
        except ValueError as exc:
            return {"error": str(exc)}

    def read_digital(self, channel: str) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.constants import LineGrouping
        from nidaqmx.errors import DaqError

        try:
            with nidaqmx.Task() as task:
                task.di_channels.add_di_chan(
                    channel,
                    line_grouping=LineGrouping.CHAN_PER_LINE,
                )
                raw = task.read()
            value = bool(raw) if isinstance(raw, (bool, int)) else bool(raw[0])
            return {"channel": channel, "value": value}
        except DaqError as exc:
            return _daq_error(exc)

    def write_digital(self, channel: str, value: bool) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.constants import LineGrouping
        from nidaqmx.errors import DaqError

        try:
            with nidaqmx.Task() as task:
                task.do_channels.add_do_chan(
                    channel,
                    line_grouping=LineGrouping.CHAN_PER_LINE,
                )
                task.write(bool(value))
                # Read back from the DO task itself. A separate DI task on the
                # same line reconfigures it as an input, which drops the drive
                # and reports whatever the external circuit pulls the line to
                # rather than what we just wrote.
                raw = task.read()
            actual = bool(raw) if isinstance(raw, (bool, int)) else bool(raw[0])
            return {"channel": channel, "value": actual}
        except DaqError as exc:
            return _daq_error(exc)

    def write_analog(self, channel: str, voltage: float) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.errors import DaqError

        try:
            with nidaqmx.Task() as task:
                task.ao_channels.add_ao_voltage_chan(
                    channel,
                    min_val=-10.0,
                    max_val=10.0,
                )
                task.write(float(voltage))
            return {"channel": channel, "voltage": float(voltage)}
        except DaqError as exc:
            return _daq_error(exc)

    def acquire_waveform(
        self,
        channel: str,
        duration_s: float,
        rate_hz: float,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        from nidaqmx.errors import DaqError

        opts = options or AiOptions()
        if duration_s <= 0:
            return {"error": "duration_s must be > 0"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        rate = opts.clamp_rate(rate_hz)
        n = max(1, int(round(duration_s * rate)))
        try:
            values = self._finite_read(channel, n, rate, opts)
            return {
                "channel": channel,
                "samples": values,
                "rate_hz": rate,
                "duration_s": duration_s,
                "sample_count": len(values),
                **opts.meta(),
            }
        except DaqError as exc:
            return _daq_error(exc)
        except ValueError as exc:
            return {"error": str(exc)}

    def start_stream(
        self,
        channel: str,
        rate_hz: float,
        *,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        from nidaqmx.errors import DaqError

        opts = options or AiOptions()
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}
        rate = opts.clamp_rate(rate_hz)

        with self._stream_lock:
            if self._stream_task is not None:
                return {
                    "error": f"Already streaming {self._stream_channel!r}; stop it first"
                }
            try:
                task = nidaqmx.Task()
                self._configure_ai(task, channel, opts)
                # Ten seconds of slack. An overflow (-200279) is fatal to the
                # task, so the buffer needs to absorb a consumer that stalls on
                # a GC pause or a burst of dashboard traffic, not just jitter.
                task.timing.cfg_samp_clk_timing(
                    rate=rate,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=max(10_000, int(rate * 10)),
                )
                task.start()
            except (DaqError, ValueError) as exc:
                try:
                    task.close()
                except Exception:
                    pass
                if isinstance(exc, ValueError):
                    return {"error": str(exc)}
                return _daq_error(exc)

            self._stream_task = task
            self._stream_channel = channel
            self._stream_options = opts
            return {"channel": channel, "rate_hz": rate, **opts.meta()}

    def read_stream(self, max_samples: int = 10_000) -> list[float]:
        from nidaqmx.errors import DaqError

        with self._stream_lock:
            task = self._stream_task
            if task is None:
                return []
            try:
                available = task.in_stream.avail_samp_per_chan
                if available <= 0:
                    return []
                raw = task.read(
                    number_of_samples_per_channel=min(available, max_samples),
                    timeout=1.0,
                )
            except DaqError as exc:
                # Only "nothing ready yet" is worth ignoring. Anything else --
                # buffer overflow, device unplugged, invalidated task -- leaves
                # the task permanently dead, and swallowing it would make a
                # stalled stream look exactly like an idle one.
                if exc.error_code in _BENIGN_STREAM_ERRORS:
                    return []
                raise
        return _as_float_list(raw)

    def stop_stream(self) -> dict[str, Any]:
        with self._stream_lock:
            task = self._stream_task
            channel = self._stream_channel
            self._stream_task = None
            self._stream_channel = None
        if task is None:
            return {"stopped": None}
        try:
            task.stop()
        except Exception:
            pass
        try:
            task.close()
        except Exception:
            pass
        return {"stopped": channel}

    def streaming_channel(self) -> str | None:
        return self._stream_channel

    def self_test(self, device: str) -> dict[str, Any]:
        from nidaqmx.errors import DaqError

        try:
            names = [d.name for d in self._System.local().devices]
            if device not in names:
                return {
                    "device": device,
                    "passed": False,
                    "error": f"Unknown device {device!r}",
                }
            self._System.local().devices[device].self_test_device()
            return {"device": device, "passed": True}
        except DaqError as exc:
            return {
                "device": device,
                "passed": False,
                **_daq_error(exc),
            }
