"""PNG input adapter kept outside the analysis core."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .models import FlowStatus, InputImage


@dataclass(frozen=True)
class DecodeOutcome:
    flow_status: FlowStatus
    image: InputImage | None
    diagnostics: tuple[dict[str, Any], ...] = ()


def decode_png(
    source: str | Path | bytes,
    *,
    confirm_relative_intensity: bool = False,
    expected_sha256: str | None = None,
) -> DecodeOutcome:
    """Decode an 8/16-bit grayscale PNG without silent color or gamma conversion."""

    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            payload = path.read_bytes()
            uri_hint = path.resolve().as_uri()
        else:
            payload = bytes(source)
            uri_hint = "memory://png"
        asset_hash = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None and asset_hash.lower() != expected_sha256.lower():
            return DecodeOutcome(
                FlowStatus.INPUT_INVALID,
                None,
                ({"code": "input_hash_mismatch", "expected_sha256": expected_sha256, "actual_sha256": asset_hash},),
            )
        with Image.open(BytesIO(payload)) as image:
            if image.format != "PNG":
                return DecodeOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "not_png", "format": image.format},))
            mode = image.mode
            info = dict(image.info)
            ihdr_bit_depth = payload[24] if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 29 else None
            metadata = {
                "format": image.format,
                "mode": mode,
                "info_keys": sorted(info),
                "gamma": info.get("gamma"),
                "srgb": info.get("srgb"),
                "icc_profile_present": "icc_profile" in info,
                "png_bit_depth": ihdr_bit_depth,
            }
            channels_identical = False
            if mode == "L":
                data = np.asarray(image, dtype=np.uint8)
                bit_depth = 8
                channels = 1
            elif mode in {"I;16", "I;16L", "I;16B", "I"}:
                data = np.asarray(image, dtype=np.uint16)
                bit_depth = 16
                channels = 1
            elif mode == "RGB":
                channels_array = np.asarray(image, dtype=np.uint8)
                if not np.array_equal(channels_array[..., 0], channels_array[..., 1]) or not np.array_equal(channels_array[..., 0], channels_array[..., 2]):
                    return DecodeOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "rgb_channels_not_identical"},))
                data = channels_array[..., 0]
                bit_depth = 8
                channels = 3
                channels_identical = True
            else:
                return DecodeOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "png_mode_unsupported", "mode": mode},))
            if not confirm_relative_intensity:
                return DecodeOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "encoding_semantic_unconfirmed", "metadata_keys": sorted(info)},))
            if ihdr_bit_depth is not None and ihdr_bit_depth != bit_depth:
                return DecodeOutcome(
                    FlowStatus.INPUT_INVALID,
                    None,
                    ({"code": "png_bit_depth_mismatch", "ihdr_bit_depth": ihdr_bit_depth, "decoded_bit_depth": bit_depth},),
                )
            byte_order = "big" if bit_depth == 16 else None
            input_image = InputImage(
                data,
                bit_depth=bit_depth,
                channels=channels,
                channels_identical=channels_identical,
                encoding_semantic="relative_intensity_code",
                encoding_semantic_confirmed=True,
                asset_id="input-" + asset_hash[:16],
                sha256=asset_hash,
                uri_hint=uri_hint,
                byte_order=byte_order,
                metadata=metadata,
            )
            return DecodeOutcome(
                FlowStatus.COMPUTED,
                input_image,
                ({"uri_hint": uri_hint, "png_mode": mode, "metadata": metadata, "byte_order": byte_order},),
            )
    except (OSError, ValueError, SyntaxError) as error:
        return DecodeOutcome(FlowStatus.INPUT_DECODE_FAILED, None, ({"code": "png_decode_failed", "message": str(error)},))
