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

"""Live acquisition: one background thread owning a continuous stream.

Why this exists: the MCP tools are snapshots by design, and MCP itself is
request/response, so neither can express "show me the signal as it happens".
This module keeps a rolling window of recent samples that both the dashboard
and the MCP tools can read without either one opening its own DAQ task.

Threading model: exactly one thread touches the backend's stream methods and
the digital-input poll. Everything it produces is published under a lock, so
readers (the dashboard's event stream, MCP tool calls) never block acquisition.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Any, Callable

from daq_mcp.measurements import AiOptions

logger = logging.getLogger("daq-mcp")

# How often the monitor drains the hardware buffer. Fast enough to feel live,
# slow enough that we read meaningful chunks rather than spinning on empties.
_POLL_INTERVAL_S = 0.05

# Dashboard frames only need ~1 s of waveform. Captures still use the full
# rolling window; summarizing 20 k samples on every SSE tick stalls the UI.
_DISPLAY_SAMPLES = 4_096

# Digital inputs are polled at a fraction of the analog rate. Each read opens
# and closes a task, so there is no point doing it every loop.
_DIGITAL_POLL_INTERVAL_S = 0.1

# Snapshots are shared between viewers for this long. Below the digital poll
# interval, so caching never makes the UI staler than the data already is.
_SNAPSHOT_CACHE_TTL_S = 0.08


def summarize(samples: list[float]) -> dict[str, float]:
    """Mean / RMS / peak-to-peak / std-dev for a window of samples.

    One pass rather than five. Dashboard frames summarize the short display
    buffer; captures still walk the full rolling window in export_window.
    """
    n = len(samples)
    if n == 0:
        return {
            "mean": 0.0,
            "rms": 0.0,
            "peak_to_peak": 0.0,
            "std_dev": 0.0,
            "min": 0.0,
            "max": 0.0,
        }
    total = 0.0
    total_sq = 0.0
    lo = hi = samples[0]
    for x in samples:
        total += x
        total_sq += x * x
        if x < lo:
            lo = x
        elif x > hi:
            hi = x
    mean = total / n
    mean_sq = total_sq / n
    # Rounding can drive this fractionally below zero for a constant signal.
    variance = max(0.0, mean_sq - mean * mean)
    return {
        "mean": mean,
        "rms": math.sqrt(mean_sq),
        "peak_to_peak": hi - lo,
        "std_dev": math.sqrt(variance),
        "min": lo,
        "max": hi,
    }


class LiveMonitor:
    """Owns the continuous acquisition thread and the rolling sample window."""

    def __init__(
        self,
        backend_getter: Callable[[], Any],
        *,
        window_samples: int = 20_000,
    ) -> None:
        self._get_backend = backend_getter
        self._window_samples = window_samples

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cache_lock = threading.Lock()
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}

        self._samples: deque[float] = deque(maxlen=window_samples)
        self._display: deque[float] = deque(maxlen=_DISPLAY_SAMPLES)
        self._channel: str | None = None
        self._rate_hz: float = 0.0
        self._total_samples: int = 0
        self._error: str | None = None
        self._failed: bool = False
        self._options: AiOptions = AiOptions()

        # Digital state. Inputs are polled; outputs are cached on write, since
        # reading an output line back would require reconfiguring it.
        self._digital_inputs: dict[str, bool] = {}
        self._digital_outputs: dict[str, bool] = {}
        self._poll_inputs: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        channel: str,
        rate_hz: float,
        *,
        poll_inputs: list[str] | None = None,
        options: AiOptions | None = None,
    ) -> dict[str, Any]:
        opts = options or AiOptions()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"error": f"Already streaming {self._channel!r}"}

            result = self._get_backend().start_stream(channel, rate_hz, options=opts)
            if "error" in result:
                return result

            self._samples.clear()
            self._display.clear()
            self._channel = channel
            self._rate_hz = float(result.get("rate_hz", rate_hz))
            self._options = opts
            self._total_samples = 0
            self._error = None
            self._failed = False
            self._poll_inputs = list(poll_inputs or [])
            self._digital_inputs = {}
            self._stop_event.clear()

            self._thread = threading.Thread(
                target=self._run, name="daq-live-monitor", daemon=True
            )
            self._thread.start()

        # A snapshot cached from the previous session would otherwise be served
        # as if it belonged to this one.
        self._invalidate_cache()
        logger.info(
            "live monitor started on %s at %.0f Hz (%s)",
            channel,
            self._rate_hz,
            opts.measurement,
        )
        return {
            "channel": channel,
            "rate_hz": self._rate_hz,
            **opts.meta(),
        }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop_event.set()
        if thread is not None:
            thread.join(timeout=2.0)

        result = self._get_backend().stop_stream()
        self._invalidate_cache()
        with self._lock:
            channel = self._channel
            self._channel = None
            self._rate_hz = 0.0
            self._options = AiOptions()
        logger.info("live monitor stopped (%s)", channel)
        return result

    def is_streaming(self, channel: str | None = None) -> bool:
        with self._lock:
            if self._channel is None:
                return False
            return channel is None or channel == self._channel

    @property
    def rate_hz(self) -> float:
        with self._lock:
            return self._rate_hz

    @property
    def options(self) -> AiOptions:
        with self._lock:
            return self._options

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    # -- the acquisition thread -------------------------------------------

    def _run(self) -> None:
        backend = self._get_backend()
        next_digital_poll = 0.0

        while not self._stop_event.is_set():
            try:
                chunk = backend.read_stream()
            except Exception as exc:
                # The task is dead; retrying would spin forever while the UI
                # kept claiming everything was fine. Stop and report instead.
                message = str(exc).strip().splitlines()[0]
                logger.error("live acquisition failed, stopping: %s", message)
                # Release the channel so one-shot reads still work afterwards;
                # a dead task would otherwise keep it reserved.
                try:
                    backend.stop_stream()
                except Exception:
                    pass
                with self._lock:
                    self._error = message
                    self._failed = True
                    self._channel = None
                self._publish_snapshot()
                break

            if chunk:
                with self._lock:
                    self._samples.extend(chunk)
                    self._display.extend(chunk)
                    self._total_samples += len(chunk)

            now = time.monotonic()
            if self._poll_inputs and now >= next_digital_poll:
                next_digital_poll = now + _DIGITAL_POLL_INTERVAL_S
                states: dict[str, bool] = {}
                for line in self._poll_inputs:
                    try:
                        result = backend.read_digital(line)
                        if "error" not in result:
                            states[line] = bool(result["value"])
                    except Exception:
                        continue
                if states:
                    with self._lock:
                        self._digital_inputs.update(states)

            self._publish_snapshot()
            self._stop_event.wait(_POLL_INTERVAL_S)

    # -- readers -----------------------------------------------------------

    def recent(self, count: int) -> list[float]:
        """The most recent `count` samples, oldest first."""
        with self._lock:
            if count >= len(self._samples):
                return list(self._samples)
            return list(self._samples)[-count:]

    def set_digital_output(self, channel: str, value: bool) -> None:
        with self._lock:
            self._digital_outputs[channel] = bool(value)

    def snapshot(self, *, max_points: int = 400) -> dict[str, Any]:
        """Everything the dashboard needs for one frame.

        Cached briefly: every connected browser tab polls this, and without a
        cache the cost of summarizing the window scales with the number of
        viewers -- paid out of the acquisition thread's share of the GIL.
        """
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(max_points)
            if cached is not None and now - cached[0] < _SNAPSHOT_CACHE_TTL_S:
                return cached[1]

        result = self._build_snapshot(max_points)
        with self._cache_lock:
            self._cache[max_points] = (now, result)
        return result

    def _publish_snapshot(self) -> None:
        """Refresh the frame cache on the acquisition thread, not the SSE loop."""
        snap = self._build_snapshot(400)
        now = time.monotonic()
        with self._cache_lock:
            self._cache[400] = (now, snap)

    def _build_snapshot(self, max_points: int) -> dict[str, Any]:
        with self._lock:
            samples = list(self._display)
            window_n = len(self._samples)
            channel = self._channel
            rate_hz = self._rate_hz
            total = self._total_samples
            error = self._error
            digital_in = dict(self._digital_inputs)
            digital_out = dict(self._digital_outputs)
            opts = self._options

        stats = summarize(samples)
        return {
            "streaming": channel is not None,
            "channel": channel,
            "rate_hz": rate_hz,
            "total_samples": total,
            "window_samples": window_n,
            "error": error,
            "trace": _downsample(samples, max_points),
            "latest": samples[-1] if samples else None,
            "digital_inputs": digital_in,
            "digital_outputs": digital_out,
            **opts.meta(),
            **stats,
        }

    def export_window(self) -> dict[str, Any]:
        """Full rolling window + metrics for saving a test result."""
        with self._lock:
            samples = list(self._samples)
            channel = self._channel
            rate_hz = self._rate_hz
            total = self._total_samples
            digital_in = dict(self._digital_inputs)
            digital_out = dict(self._digital_outputs)
            opts = self._options
        stats = summarize(samples)
        return {
            "channel": channel,
            "rate_hz": rate_hz,
            "total_samples": total,
            "window_samples": len(samples),
            "latest": samples[-1] if samples else None,
            "samples": samples,
            "digital_inputs": digital_in,
            "digital_outputs": digital_out,
            **opts.meta(),
            **stats,
        }


def _downsample(samples: list[float], max_points: int) -> list[float]:
    n = len(samples)
    if n <= max_points:
        return list(samples)
    if max_points <= 1:
        return [samples[-1]] if n else []
    step = (n - 1) / (max_points - 1)
    return [samples[int(round(i * step))] for i in range(max_points)]
