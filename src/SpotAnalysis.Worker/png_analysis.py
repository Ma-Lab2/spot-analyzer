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
    total = sum(samples)
    maximum = max(samples) if samples else 0
    record_id = hashlib.sha256((image["sha256"] + json.dumps({"model": "standard-v1", "version": 1}, sort_keys=True)).encode()).hexdigest()[:24]
    fingerprint = hashlib.sha256(json.dumps({k: v for k, v in image.items() if k != "path"}, sort_keys=True).encode()).hexdigest()
    return {"record_id": record_id, "analysis_fingerprint": fingerprint, "workflow_status": "completed",
            "input": image, "result": {"mean_intensity": total / len(samples), "max_intensity": maximum, "pixel_count": len(samples)},
            "measurement_validity": "provisional"}
