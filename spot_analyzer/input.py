"""PNG input adapter kept outside the analysis core.

The adapter deliberately decodes PNG scanlines itself.  Pillow remains useful for
rendering previews, but it is not allowed to apply colour management or reduce
bit depth to the measurement input.
"""

from __future__ import annotations

from dataclasses import dataclass
import binascii
import hashlib
from pathlib import Path
import struct
from typing import Any
import zlib

import numpy as np

from .models import FlowStatus, InputImage


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class DecodeOutcome:
    flow_status: FlowStatus
    image: InputImage | None
    diagnostics: tuple[dict[str, Any], ...] = ()


def _failure(status: FlowStatus, code: str, message: str, **details: Any) -> DecodeOutcome:
    diagnostic: dict[str, Any] = {"code": code, "message": message}
    diagnostic.update(details)
    return DecodeOutcome(status, None, (diagnostic,))


def _unfilter(raw: bytes, row_bytes: int, height: int, bytes_per_pixel: int) -> bytes:
    expected = height * (row_bytes + 1)
    if len(raw) != expected:
        raise ValueError("PNG scanlines have an invalid length")
    rows: list[bytes] = []
    offset = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset : offset + row_bytes])
        offset += row_bytes
        for index in range(row_bytes):
            left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 255
            elif filter_type == 2:
                current[index] = (current[index] + up) & 255
            elif filter_type == 3:
                current[index] = (current[index] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                estimate = left + up - upper_left
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - upper_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
                current[index] = (current[index] + predictor) & 255
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append(bytes(current))
        previous = current
    return b"".join(rows)


def _parse_png(payload: bytes) -> tuple[np.ndarray, dict[str, Any]]:
    if len(payload) < len(PNG_SIGNATURE) or payload[:8] != PNG_SIGNATURE:
        raise LookupError("input is not a PNG")

    offset = len(PNG_SIGNATURE)
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    idat = bytearray()
    metadata: dict[str, Any] = {}
    saw_iend = False
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise ValueError("PNG chunk is truncated")
        chunk_payload = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length : end])[0]
        actual_crc = binascii.crc32(kind + chunk_payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("PNG chunk checksum is invalid")
        offset = end
        if kind == b"IHDR":
            if ihdr is not None or len(chunk_payload) != 13:
                raise ValueError("PNG IHDR is invalid")
            ihdr = struct.unpack(">IIBBBBB", chunk_payload)
        elif kind == b"IDAT":
            idat.extend(chunk_payload)
        elif kind == b"gAMA" and len(chunk_payload) == 4:
            metadata["gamma"] = struct.unpack(">I", chunk_payload)[0] / 100000.0
        elif kind == b"sRGB" and len(chunk_payload) == 1:
            metadata["srgb"] = chunk_payload[0]
        elif kind == b"iCCP":
            metadata["icc_profile_present"] = True
        elif kind == b"IEND":
            saw_iend = True
            break

    if ihdr is None:
        raise ValueError("PNG is missing IHDR")
    if not saw_iend:
        raise ValueError("PNG is missing IEND")
    width, height, bit_depth, color_type, compression, filtering, interlace = ihdr
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    if compression != 0 or filtering != 0 or interlace != 0:
        raise LookupError("PNG dimensions or encoding are unsupported")
    if color_type == 0 and bit_depth in (8, 16):
        channels = 1
    elif color_type == 2 and bit_depth == 8:
        channels = 3
    elif color_type == 2:
        raise LookupError("only 8-bit equal-channel RGB PNG is supported")
    else:
        raise LookupError("only 8/16-bit grayscale PNG is supported")

    try:
        inflated = zlib.decompress(bytes(idat))
    except zlib.error as error:
        raise ValueError("PNG image data cannot be decompressed") from error
    bytes_per_sample = bit_depth // 8
    row_bytes = width * channels * bytes_per_sample
    pixels = _unfilter(inflated, row_bytes, height, channels * bytes_per_sample)
    if bit_depth == 8:
        values = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, channels)
    else:
        values = np.frombuffer(pixels, dtype=">u2").reshape(height, width, channels)
    channels_identical = channels == 3 and bool(np.array_equal(values[..., 0], values[..., 1]) and np.array_equal(values[..., 1], values[..., 2]))
    if channels == 3 and not channels_identical:
        raise LookupError("RGB channels are not identical")
    data = np.array(values[..., 0], dtype=np.float64, copy=True)
    metadata.update({
        "format": "PNG",
        "mode": "L" if channels == 1 and bit_depth == 8 else "I;16" if channels == 1 else "RGB",
        "png_bit_depth": bit_depth,
        "png_color_type": color_type,
        "channels": channels,
        "channels_identical": channels_identical,
        "gamma_present": "gamma" in metadata,
        "srgb_present": "srgb" in metadata,
        "icc_profile_present": bool(metadata.get("icc_profile_present", False)),
        "byte_order": "big" if bit_depth == 16 else None,
        "source_dtype": f"uint{bit_depth}",
    })
    return data, metadata


def decode_png(
    source: str | Path | bytes,
    *,
    confirm_relative_intensity: bool = False,
    expected_sha256: str | None = None,
) -> DecodeOutcome:
    """Decode supported PNG input without colour, gamma, or bit-depth conversion."""

    if not confirm_relative_intensity:
        return _failure(
            FlowStatus.INPUT_INVALID,
            "encoding_semantic_unconfirmed",
            "必须确认 PNG 像素表示为相对强度，才能用于测量。",
        )
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            payload = path.read_bytes()
            uri_hint = path.resolve().as_uri()
        else:
            payload = bytes(source)
            uri_hint = "memory://png"
    except (OSError, TypeError, ValueError) as error:
        return _failure(FlowStatus.INPUT_DECODE_FAILED, "input_unreadable", f"无法读取输入 PNG：{error}")

    asset_hash = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and asset_hash.lower() != str(expected_sha256).lower():
        return _failure(
            FlowStatus.INPUT_INVALID,
            "input_hash_mismatch",
            "输入 PNG 的 SHA-256 与请求不一致。",
            expected_sha256=expected_sha256,
            actual_sha256=asset_hash,
        )
    try:
        data, metadata = _parse_png(payload)
    except LookupError as error:
        text = str(error)
        if text == "input is not a PNG":
            return _failure(FlowStatus.INPUT_INVALID, "not_png", "输入文件不是 PNG。")
        code = "rgb_channels_not_identical" if "RGB channels" in text else "png_encoding_unsupported"
        return _failure(FlowStatus.INPUT_INVALID, code, f"不支持的 PNG 输入：{text}")
    except (ValueError, zlib.error, struct.error) as error:
        return _failure(FlowStatus.INPUT_DECODE_FAILED, "png_decode_failed", f"PNG 解码失败：{error}")

    bit_depth = int(metadata["png_bit_depth"])
    channels = int(metadata["channels"])
    image = InputImage(
        data,
        dtype=f"uint{bit_depth}",
        bit_depth=bit_depth,
        channels=channels,
        channels_identical=bool(metadata["channels_identical"]),
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
        asset_id="input-" + asset_hash[:16],
        sha256=asset_hash,
        uri_hint=uri_hint,
        byte_order=metadata["byte_order"],
        metadata={**metadata, "uri_hint": uri_hint},
    )
    return DecodeOutcome(
        FlowStatus.COMPUTED,
        image,
        ({
            "code": "png_decoded",
            "uri_hint": uri_hint,
            "sha256": asset_hash,
            "width": int(data.shape[1]),
            "height": int(data.shape[0]),
            "bit_depth": bit_depth,
            "channels": channels,
            "channels_identical": bool(metadata["channels_identical"]),
            "metadata": metadata,
        },),
    )
