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

from typing import Any, Callable, TypeVar

from daq_mcp.backend.base import DAQBackend, TerminalConfig

T = TypeVar("T")


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

    def _terminal(self, terminal_config: TerminalConfig):
        from nidaqmx.constants import TerminalConfiguration

        return {
            "default": TerminalConfiguration.DEFAULT,
            "rse": TerminalConfiguration.RSE,
            "nrse": TerminalConfiguration.NRSE,
            "diff": TerminalConfiguration.DIFF,
        }[terminal_config]

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
            return {
                "name": dev.name,
                "product_type": _safe(lambda: dev.product_type, ""),
                "serial_number": _safe(lambda: dev.serial_num, 0),
                "analog_input_channels": _channel_names(dev.ai_physical_chans),
                "analog_output_channels": _channel_names(dev.ao_physical_chans),
                "digital_input_channels": _channel_names(dev.di_lines),
                "digital_output_channels": _channel_names(dev.do_lines),
                "ai_voltage_ranges": _pair_ranges(ai_rng),
                "ao_voltage_ranges": _pair_ranges(ao_rng),
                "max_ai_rate_hz": _safe(lambda: float(dev.ai_max_single_chan_rate), 0.0),
                "max_ao_rate_hz": _safe(lambda: float(dev.ao_max_rate), 0.0),
            }
        except DaqError as exc:
            return _daq_error(exc)

    def read_analog(
        self,
        channel: str,
        samples: int,
        rate_hz: float,
        terminal_config: TerminalConfig,
    ) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        from nidaqmx.errors import DaqError

        if samples < 1:
            return {"error": "samples must be >= 1"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        try:
            with nidaqmx.Task() as task:
                task.ai_channels.add_ai_voltage_chan(
                    channel,
                    terminal_config=self._terminal(terminal_config),
                    min_val=-10.0,
                    max_val=10.0,
                )
                if samples == 1:
                    raw = task.read()
                else:
                    task.timing.cfg_samp_clk_timing(
                        rate=rate_hz,
                        sample_mode=AcquisitionType.FINITE,
                        samps_per_chan=samples,
                    )
                    timeout = max(10.0, samples / rate_hz + 5.0)
                    raw = task.read(
                        number_of_samples_per_channel=samples,
                        timeout=timeout,
                    )
            values = _as_float_list(raw)
            return {
                "channel": channel,
                "samples": values,
                "rate_hz": rate_hz,
                "terminal_config": terminal_config,
                "min": min(values) if values else 0.0,
                "mean": sum(values) / len(values) if values else 0.0,
                "max": max(values) if values else 0.0,
            }
        except DaqError as exc:
            return _daq_error(exc)

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

            # Read back from hardware rather than echoing the request.
            with nidaqmx.Task() as task:
                task.di_channels.add_di_chan(
                    channel,
                    line_grouping=LineGrouping.CHAN_PER_LINE,
                )
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
    ) -> dict[str, Any]:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        from nidaqmx.errors import DaqError

        if duration_s <= 0:
            return {"error": "duration_s must be > 0"}
        if rate_hz <= 0:
            return {"error": "rate_hz must be > 0"}

        n = max(1, int(round(duration_s * rate_hz)))
        try:
            with nidaqmx.Task() as task:
                task.ai_channels.add_ai_voltage_chan(
                    channel,
                    terminal_config=self._terminal("default"),
                    min_val=-10.0,
                    max_val=10.0,
                )
                task.timing.cfg_samp_clk_timing(
                    rate=rate_hz,
                    sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=n,
                )
                timeout = max(10.0, duration_s + 5.0)
                raw = task.read(number_of_samples_per_channel=n, timeout=timeout)
            values = _as_float_list(raw)
            return {
                "channel": channel,
                "samples": values,
                "rate_hz": rate_hz,
                "duration_s": duration_s,
                "sample_count": len(values),
            }
        except DaqError as exc:
            return _daq_error(exc)

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
