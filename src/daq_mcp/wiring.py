"""Machine-local channel wiring: which pins you treat as inputs vs outputs.

NI-DAQmx can list every channel a device *supports*. It cannot tell you that
line6 drives an LED and line0 reads a button — that is a physical fact. This
module stores your choices in a gitignored file so the dashboard picker and
the MCP allowlist stay in sync without baking bench wiring into the repo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Project root (…/MCP-NIDAQMX), not CWD — the server may be launched from anywhere.
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / ".daq_mcp_wiring.json"


@dataclass
class Wiring:
    device: str = "Dev1"
    live_channel: str = "Dev1/ai0"
    live_rate_hz: float = 1000.0
    analog_inputs: list[str] = field(default_factory=lambda: ["Dev1/ai0", "Dev1/ai1"])
    analog_outputs: list[str] = field(default_factory=list)
    digital_inputs: list[str] = field(
        default_factory=lambda: ["Dev1/port0/line0", "Dev1/port0/line1"]
    )
    digital_outputs: list[str] = field(
        default_factory=lambda: ["Dev1/port0/line6", "Dev1/port0/line7"]
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Wiring:
        def _str_list(key: str, default: list[str]) -> list[str]:
            raw = data.get(key, default)
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list of channel names")
            out: list[str] = []
            for item in raw:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"{key} entries must be non-empty strings")
                out.append(item.strip())
            # Preserve order, drop duplicates.
            seen: set[str] = set()
            unique: list[str] = []
            for ch in out:
                if ch not in seen:
                    seen.add(ch)
                    unique.append(ch)
            return unique

        live = data.get("live_channel", "Dev1/ai0")
        if not isinstance(live, str) or not live.strip():
            raise ValueError("live_channel must be a non-empty string")
        rate = data.get("live_rate_hz", 1000.0)
        if not isinstance(rate, (int, float)) or float(rate) <= 0:
            raise ValueError("live_rate_hz must be a number > 0")
        device = data.get("device", "Dev1")
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device must be a non-empty string")

        return cls(
            device=device.strip(),
            live_channel=live.strip(),
            live_rate_hz=float(rate),
            analog_inputs=_str_list("analog_inputs", ["Dev1/ai0", "Dev1/ai1"]),
            analog_outputs=_str_list("analog_outputs", []),
            digital_inputs=_str_list(
                "digital_inputs", ["Dev1/port0/line0", "Dev1/port0/line1"]
            ),
            digital_outputs=_str_list(
                "digital_outputs", ["Dev1/port0/line6", "Dev1/port0/line7"]
            ),
        )


def default_wiring() -> Wiring:
    return Wiring()


def load_wiring(path: Path | None = None) -> Wiring:
    target = path or _DEFAULT_PATH
    if not target.is_file():
        return default_wiring()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read wiring file {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Wiring file must contain a JSON object")
    return Wiring.from_dict(data)


def save_wiring(wiring: Wiring, path: Path | None = None) -> Path:
    target = path or _DEFAULT_PATH
    target.write_text(
        json.dumps(wiring.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def validate_wiring(
    wiring: Wiring,
    inventory: dict[str, Any] | None = None,
) -> list[str]:
    """Return human-readable problems. Empty list means OK to apply.

    `inventory` is the shape of describe_device when available. Without it we
    only check internal consistency (roles don't overlap, live channel is an
    analog input you selected).
    """
    errors: list[str] = []

    di = set(wiring.digital_inputs)
    do = set(wiring.digital_outputs)
    overlap = di & do
    if overlap:
        errors.append(
            "A digital line cannot be both input and output: "
            + ", ".join(sorted(overlap))
        )

    ai = set(wiring.analog_inputs)
    ao = set(wiring.analog_outputs)
    if ai & ao:
        errors.append(
            "A channel cannot be both analog input and output: "
            + ", ".join(sorted(ai & ao))
        )

    if not wiring.analog_inputs:
        errors.append("Select at least one analog input")
    if wiring.live_channel not in wiring.analog_inputs:
        errors.append(
            f"live_channel {wiring.live_channel!r} must be one of the selected "
            "analog inputs"
        )

    if inventory is None:
        return errors

    known_ai = set(inventory.get("analog_input_channels") or [])
    known_ao = set(inventory.get("analog_output_channels") or [])
    known_di = set(inventory.get("digital_input_channels") or [])
    known_do = set(inventory.get("digital_output_channels") or [])
    # Many DAQ boards list the same DIO lines under both DI and DO; either
    # inventory membership is enough to prove the pin exists.
    known_dio = known_di | known_do

    for ch in wiring.analog_inputs:
        if known_ai and ch not in known_ai:
            errors.append(f"{ch} is not an analog input on this device")
    for ch in wiring.analog_outputs:
        if known_ao and ch not in known_ao:
            errors.append(f"{ch} is not an analog output on this device")
    for ch in wiring.digital_inputs:
        if known_dio and ch not in known_dio:
            errors.append(f"{ch} is not a digital line on this device")
    for ch in wiring.digital_outputs:
        if known_dio and ch not in known_dio:
            errors.append(f"{ch} is not a digital line on this device")

    inv_name = inventory.get("name")
    if inv_name and wiring.device and wiring.device != inv_name:
        # Soft warning as error so the picker stays honest about which device
        # these channel names belong to.
        errors.append(
            f"device {wiring.device!r} does not match inventory device {inv_name!r}"
        )

    return errors


def wiring_path() -> Path:
    return _DEFAULT_PATH
