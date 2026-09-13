"""PNG input adaptation for the legacy packaged-worker seam.

The decoder delegates to ``spot_analyzer.input`` so the CLI and packaged worker
cannot drift in their acceptance rules or intensity values.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from spot_analyzer.input import decode_png


class InputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_png(path: str | Path, *, expected_sha256: str | None = None,
             intensity_semantics_confirmed: bool = False, uri_hint: str | None = None) -> dict[str, Any]:
    """Read one supported PNG and return the shared input contract."""
    outcome = decode_png(
        path,
        expected_sha256=expected_sha256,
        confirm_relative_intensity=intensity_semantics_confirmed,
    )
    if outcome.image is None:
        diagnostic = outcome.diagnostics[0] if outcome.diagnostics else {}
        code = str(diagnostic.get("code", "png_decode_failed"))
        # Preserve the original short legacy names at this adapter while the
        # structured message and all successful fields use the shared contract.
        code = {"input_hash_mismatch": "hash_mismatch", "encoding_semantic_unconfirmed": "intensity_semantics_unconfirmed", "rgb_channels_not_identical": "rgb_channels_differ"}.get(code, code)
        raise InputError(code, str(diagnostic.get("message", "PNG input was rejected.")))
    image = outcome.image
    metadata = dict(image.metadata)
    samples = [int(value) for value in image.data.ravel(order="C")]
    return {
        "path": str(path),
        "uri_hint": uri_hint or image.uri_hint,
        "sha256": image.sha256,
        "width": int(image.data.shape[1]),
        "height": int(image.data.shape[0]),
        "bit_depth": image.bit_depth,
        "dtype": metadata.get("source_dtype", image.dtype),
        "channels": image.channels,
        "channels_identical": image.channels_identical,
        "color_type": metadata.get("png_color_type"),
        "encoding_semantics": image.encoding_semantic,
        "byte_order": image.byte_order,
        "metadata": metadata,
        "samples": samples,
    }


def _rectangle(value: Any, width: int, height: int, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise InputError("invalid_roi", f"{name} 必须是矩形。")
    fields = ("x", "y", "width", "height")
    if any(isinstance(value.get(field), bool) or not isinstance(value.get(field), int) for field in fields):
        raise InputError("invalid_roi", f"{name} 的坐标和尺寸必须是整数。")
    rectangle = {field: value[field] for field in fields}
    if rectangle["x"] < 0 or rectangle["y"] < 0 or rectangle["width"] <= 0 or rectangle["height"] <= 0:
        raise InputError("invalid_roi", f"{name} 必须有正尺寸和非负原点。")
    if rectangle["x"] + rectangle["width"] > width or rectangle["y"] + rectangle["height"] > height:
        raise InputError("invalid_roi", f"{name} 必须位于输入图像内。")
    return rectangle


def analyze_png(request: dict[str, Any]) -> dict[str, Any]:
    input_data = request.get("input")
    if not isinstance(input_data, dict) or input_data.get("kind") != "png":
        raise InputError("invalid_input", "PNG 输入是必需的。")
    image = read_png(input_data.get("path", ""), expected_sha256=input_data.get("sha256"),
                     intensity_semantics_confirmed=input_data.get("intensity_semantics_confirmed", False),
                     uri_hint=input_data.get("uri_hint"))
    samples = image.pop("samples")
    width, height = image["width"], image["height"]
    configuration = {
        "analysis_region": _rectangle(input_data.get("analysis_region"), width, height, "analysis_region"),
        "background_region": None if input_data.get("background_region") is None else _rectangle(input_data["background_region"], width, height, "background_region"),
        "spatial_calibration": input_data.get("spatial_calibration"),
    }
    total = sum(samples)
    maximum = max(samples) if samples else 0
    identity = {"input": {k: v for k, v in image.items() if k != "path"},
                "analysis_configuration": configuration, "model": {"name": "standard-v1", "version": 1}}
    encoded_identity = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    record_id = hashlib.sha256(encoded_identity).hexdigest()[:24]
    fingerprint = hashlib.sha256(encoded_identity).hexdigest()
    previous = input_data.get("previous_analysis_fingerprint")
    workflow_status = "needs_recalculation" if previous is not None and previous != fingerprint else "completed"
    calibration = configuration["spatial_calibration"] or {}
    calibration_status = calibration.get("status", "missing") if isinstance(calibration, dict) else "missing"
    validity = "warning" if calibration_status == "provisional" else "valid"
    reasons = (["spatial_calibration_missing"] if calibration_status == "missing"
               else ["spatial_calibration_provisional"] if calibration_status == "provisional" else [])
    return {"record_id": record_id, "analysis_fingerprint": fingerprint, "workflow_status": workflow_status,
            "input": image, "analysis_configuration": configuration,
            "result": {"mean_intensity": total / len(samples), "max_intensity": maximum, "pixel_count": len(samples)},
            "measurement_validity": validity, "quality_reason_codes": reasons}
