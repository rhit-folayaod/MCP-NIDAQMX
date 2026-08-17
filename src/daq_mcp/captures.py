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

"""Save acquisition snapshots to disk for later review.

Captures are machine-local (gitignored). Each file is JSON with metrics,
digital state, and the sample window at the moment of capture. The dashboard
can also download CSV without needing a second round-trip.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CAPTURES_DIR = _ROOT / ".daq_mcp_captures"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def captures_dir() -> Path:
    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    return _CAPTURES_DIR


def normalize_capture_name(name: str | None) -> str:
    if name and name.strip():
        cleaned = name.strip()
        if not _NAME_RE.match(cleaned):
            raise ValueError(
                "Capture name must be 1–64 chars, start with a letter or digit, "
                "and use only letters, digits, spaces, _ . -"
            )
        slug = re.sub(r"[^\w.-]+", "_", cleaned, flags=re.UNICODE)
    else:
        slug = "capture"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{slug}_{stamp}"


def build_capture(
    *,
    samples: list[float],
    metrics: dict[str, Any],
    channel: str | None,
    rate_hz: float,
    digital_inputs: dict[str, bool],
    digital_outputs: dict[str, bool],
    profile: str | None,
    label: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Assemble one exportable capture record."""
    name = normalize_capture_name(label)
    return {
        "name": name,
        "label": (label or "").strip() or None,
        "note": (note or "").strip() or None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "channel": channel,
        "rate_hz": rate_hz,
        "sample_count": len(samples),
        "metrics": {
            "latest": metrics.get("latest"),
            "mean": metrics.get("mean"),
            "rms": metrics.get("rms"),
            "peak_to_peak": metrics.get("peak_to_peak"),
            "std_dev": metrics.get("std_dev"),
            "min": metrics.get("min"),
            "max": metrics.get("max"),
            "total_samples": metrics.get("total_samples"),
            "window_samples": metrics.get("window_samples"),
        },
        "digital_inputs": digital_inputs,
        "digital_outputs": digital_outputs,
        "samples": samples,
    }


def metrics_text(capture: dict[str, Any]) -> str:
    """Human-readable summary for the expandable text box."""
    m = capture.get("metrics") or {}
    lines = [
        f"name:            {capture.get('name')}",
        f"captured_at:     {capture.get('captured_at')}",
        f"profile:         {capture.get('profile') or '(none)'}",
        f"channel:         {capture.get('channel')}",
        f"rate_hz:         {capture.get('rate_hz')}",
        f"sample_count:    {capture.get('sample_count')}",
        f"latest (V):      {_fmt(m.get('latest'))}",
        f"mean (V):        {_fmt(m.get('mean'))}",
        f"rms (V):         {_fmt(m.get('rms'))}",
        f"peak_to_peak:    {_fmt(m.get('peak_to_peak'))}",
        f"std_dev:         {_fmt(m.get('std_dev'), 5)}",
        f"min / max (V):   {_fmt(m.get('min'))} / {_fmt(m.get('max'))}",
    ]
    if capture.get("note"):
        lines.append(f"note:            {capture['note']}")
    di = capture.get("digital_inputs") or {}
    do = capture.get("digital_outputs") or {}
    if di:
        lines.append("digital_inputs:")
        for ch, v in sorted(di.items()):
            lines.append(f"  {ch}: {v}")
    if do:
        lines.append("digital_outputs:")
        for ch, v in sorted(do.items()):
            lines.append(f"  {ch}: {v}")
    return "\n".join(lines) + "\n"


def save_capture_json(capture: dict[str, Any]) -> Path:
    path = captures_dir() / f"{capture['name']}.json"
    path.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
    return path


def samples_to_csv(capture: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["index", "voltage_v"])
    for i, v in enumerate(capture.get("samples") or []):
        writer.writerow([i, v])
    return buf.getvalue()


def list_captures(limit: int = 50) -> list[dict[str, Any]]:
    captures_dir()
    files = sorted(_CAPTURES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "name": data.get("name") or path.stem,
                "path": str(path),
                "captured_at": data.get("captured_at"),
                "channel": data.get("channel"),
                "sample_count": data.get("sample_count"),
                "label": data.get("label"),
            }
        )
    return out


def load_capture(name: str) -> dict[str, Any]:
    # Accept bare stem or full filename.
    stem = name[:-5] if name.endswith(".json") else name
    path = captures_dir() / f"{stem}.json"
    if not path.is_file():
        raise ValueError(f"No capture named {name!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Capture file is not a JSON object")
    return data


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
