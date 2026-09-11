"""PNG input adaptation and deterministic standard analysis record creation."""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class InputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("invalid_configuration", f"{name} must be a number")
    result = float(value)
    if result <= 0:
        raise InputError("invalid_configuration", f"{name} must be positive")
    return result


def _rectangle(value: Any, width: int, height: int, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise InputError("invalid_roi", f"{name} must be a rectangle")
    fields = ("x", "y", "width", "height")
    if any(isinstance(value.get(field), bool) or not isinstance(value.get(field), int) for field in fields):
        raise InputError("invalid_roi", f"{name} coordinates and dimensions must be integers")
    rectangle = {field: value[field] for field in fields}
    if rectangle["x"] < 0 or rectangle["y"] < 0 or rectangle["width"] <= 0 or rectangle["height"] <= 0:
        raise InputError("invalid_roi", f"{name} must have positive dimensions and non-negative origin")
    if rectangle["x"] + rectangle["width"] > width or rectangle["y"] + rectangle["height"] > height:
        raise InputError("invalid_roi", f"{name} must be inside the input image")
    return rectangle


def resolve_analysis_configuration(input_data: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Validate and freeze calibration/ROI choices for an analysis record."""
    calibration = input_data.get("spatial_calibration")
    if calibration is None:
        calibration_snapshot: dict[str, Any] = {"status": "missing", "x_units_per_pixel": None, "y_units_per_pixel": None, "units": None, "source": None}
    elif not isinstance(calibration, dict):
        raise InputError("invalid_configuration", "spatial_calibration must be an object")
    else:
        status = calibration.get("status", "provisional")
        if status == "missing":
            calibration_snapshot = {"status": "missing", "x_units_per_pixel": None, "y_units_per_pixel": None, "units": None, "source": None}
        else:
            if status not in ("confirmed", "provisional"):
                raise InputError("invalid_configuration", "calibration status must be confirmed, provisional, or missing")
            units = calibration.get("units")
            source = calibration.get("source")
            if not isinstance(units, str) or not units.strip() or not isinstance(source, str) or not source.strip():
                raise InputError("invalid_configuration", "calibration units and source are required")
            calibration_snapshot = {"status": status, "x_units_per_pixel": _number(calibration.get("x_units_per_pixel"), "x_units_per_pixel"), "y_units_per_pixel": _number(calibration.get("y_units_per_pixel"), "y_units_per_pixel"), "units": units, "source": source}
    roi = _rectangle(input_data.get("analysis_region"), width, height, "analysis_region")
    background = input_data.get("background_region")
    background_snapshot = None if background is None else _rectangle(background, width, height, "background_region")
    return {"version": 1, "spatial_calibration": calibration_snapshot, "analysis_region": roi, "background_region": background_snapshot}


def _unfilter(raw: bytes, row_bytes: int, height: int, bpp: int) -> bytes:
    rows: list[bytes] = []
    offset = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        if offset >= len(raw):
            raise InputError("invalid_png", "PNG scanlines are truncated")
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset : offset + row_bytes])
        offset += row_bytes
        if len(current) != row_bytes:
            raise InputError("invalid_png", "PNG scanlines are truncated")
        for i in range(row_bytes):
            left = current[i - bpp] if i >= bpp else 0
            up = previous[i]
            upper_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 1:
                current[i] = (current[i] + left) & 255
            elif filter_type == 2:
                current[i] = (current[i] + up) & 255
            elif filter_type == 3:
                current[i] = (current[i] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                current[i] = (current[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else upper_left)) & 255
            elif filter_type != 0:
                raise InputError("unsupported_png", f"unsupported PNG filter {filter_type}")
        rows.append(bytes(current))
        previous = current
    return b"".join(rows)


def read_png(path: str | Path, *, expected_sha256: str | None = None,
             intensity_semantics_confirmed: bool = False, uri_hint: str | None = None) -> dict[str, Any]:
    """Read a supported grayscale PNG without modifying the source file."""
    if not intensity_semantics_confirmed:
        raise InputError("intensity_semantics_unconfirmed", "intensity encoding semantics must be confirmed")
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise InputError("input_unreadable", str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest.lower() != str(expected_sha256).lower():
        raise InputError("hash_mismatch", "input SHA-256 does not match the request")
    if not data.startswith(PNG_SIGNATURE):
        raise InputError("unsupported_png", "input is not a PNG")
    offset, idat, ihdr = len(PNG_SIGNATURE), bytearray(), None
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if len(payload) != length:
            raise InputError("invalid_png", "PNG chunk is truncated")
        if kind == b"IHDR":
            if len(payload) != 13:
                raise InputError("invalid_png", "PNG IHDR is invalid")
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break
    if ihdr is None:
        raise InputError("invalid_png", "PNG is missing IHDR")
    width, height, bit_depth, color_type, compression, filtering, interlace = ihdr
    if width <= 0 or height <= 0 or compression != 0 or filtering != 0 or interlace != 0:
        raise InputError("unsupported_png", "PNG dimensions or encoding are unsupported")
    if color_type == 0 and bit_depth in (8, 16):
        channels = 1
    elif color_type == 2 and bit_depth in (8, 16):
        channels = 3
    else:
        raise InputError("unsupported_png", "only 8/16-bit grayscale PNG is supported")
    try:
        inflated = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise InputError("invalid_png", "PNG image data cannot be decompressed") from exc
    bytes_per_sample = bit_depth // 8
    row_bytes = width * channels * bytes_per_sample
    pixels = _unfilter(inflated, row_bytes, height, channels * bytes_per_sample)
    samples: list[int] = []
    step = bytes_per_sample * channels
    for pos in range(0, len(pixels), step):
        values = [int.from_bytes(pixels[pos + c * bytes_per_sample : pos + (c + 1) * bytes_per_sample], "big") for c in range(channels)]
        if channels == 3 and (values[0] != values[1] or values[1] != values[2]):
            raise InputError("rgb_channels_differ", "RGB channels are not identical")
        samples.append(values[0])
    return {
        "path": str(source), "uri_hint": uri_hint, "sha256": digest,
        "width": width, "height": height, "bit_depth": bit_depth,
        "channels": channels, "color_type": color_type,
        "encoding_semantics": "unsigned grayscale intensity, big-endian samples" if bit_depth == 16 else "unsigned grayscale intensity",
        "samples": samples,
    }


def analyze_png(request: dict[str, Any]) -> dict[str, Any]:
    input_data = request.get("input")
    if not isinstance(input_data, dict) or input_data.get("kind") != "png":
        raise InputError("invalid_input", "PNG input is required")
    image = read_png(input_data.get("path", ""), expected_sha256=input_data.get("sha256"),
                     intensity_semantics_confirmed=input_data.get("intensity_semantics_confirmed", False),
                     uri_hint=input_data.get("uri_hint"))
    samples = image.pop("samples")
    configuration = resolve_analysis_configuration(input_data, image["width"], image["height"])
    total = sum(samples)
    maximum = max(samples) if samples else 0
    identity = {"input": {k: v for k, v in image.items() if k != "path"}, "analysis_configuration": configuration, "model": {"name": "standard-v1", "version": 1}}
    record_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    previous = input_data.get("previous_analysis_fingerprint")
    workflow_status = "needs_recalculation" if previous is not None and previous != fingerprint else "completed"
    calibration_status = configuration["spatial_calibration"]["status"]
    validity = "warning" if calibration_status == "provisional" else "valid"
    reasons = (["spatial_calibration_missing"] if calibration_status == "missing" else ["spatial_calibration_provisional"] if calibration_status == "provisional" else [])
    return {"record_id": record_id, "analysis_fingerprint": fingerprint, "workflow_status": workflow_status,
            "input": image, "analysis_configuration": configuration,
            "result": {"mean_intensity": total / len(samples), "max_intensity": maximum, "pixel_count": len(samples)},
            "measurement_validity": validity, "quality_reason_codes": reasons}
