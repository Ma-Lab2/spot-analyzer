"""Versioned NDJSON worker adapter shared by CLI and UI callers."""

from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, TextIO
from urllib.parse import unquote, urlparse

import numpy as np

from .core import analyze
from .input import decode_png
from .models import (
    AnalysisConfiguration,
    AnalysisModel,
    AnalysisRegion,
    FlowStatus,
    InputImage,
    PreprocessingConfiguration,
    SpatialCalibration,
)


SCHEMA = "analysis-request-v1"
EVENT_SCHEMA = "analysis-event-v1"
RESULT_SCHEMA = "analysis-result-v1"


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, Mapping):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def _configuration(payload: dict[str, Any]) -> AnalysisConfiguration:
    required = {
        "region",
        "background_region",
        "calibration",
        "preprocessing",
        "model",
        "rref_pixels",
        "analysis_contract",
        "standard_profile",
        "quality_profile",
        "profile_validation",
        "algorithm_version",
        "bad_pixel_coordinates",
        "bad_pixel_mask_version",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"configuration snapshot is incomplete: {', '.join(missing)}")
    for key, configuration_type in (
        ("calibration", SpatialCalibration),
        ("preprocessing", PreprocessingConfiguration),
        ("model", AnalysisModel),
    ):
        nested = payload[key]
        if not isinstance(nested, dict):
            raise TypeError(f"configuration.{key} must be an object")
        nested_missing = sorted({item.name for item in fields(configuration_type)} - nested.keys())
        if nested_missing:
            raise ValueError(
                f"configuration.{key} snapshot is incomplete: {', '.join(nested_missing)}"
            )
    region_payload = payload["region"]
    region = AnalysisRegion(**region_payload)
    background_payload = payload.get("background_region")
    background = AnalysisRegion(**background_payload) if background_payload else None
    calibration_payload = payload.get("calibration", {})
    calibration = SpatialCalibration(**calibration_payload)
    preprocessing = PreprocessingConfiguration(**payload.get("preprocessing", {}))
    model = AnalysisModel(**payload.get("model", {}))
    allowed = {
        "rref_pixels",
        "analysis_contract",
        "standard_profile",
        "quality_profile",
        "profile_validation",
        "algorithm_version",
        "bad_pixel_coordinates",
        "bad_pixel_mask_version",
    }
    options = {key: value for key, value in payload.items() if key in allowed}
    return AnalysisConfiguration(
        region=region,
        background_region=background,
        calibration=calibration,
        preprocessing=preprocessing,
        model=model,
        **options,
    )


def _asset_path(asset: dict[str, Any]) -> Path:
    if "path" in asset:
        return Path(str(asset["path"]))
    uri = asset.get("uri")
    if not uri:
        raise ValueError("asset path or file URI is required")
    parsed = urlparse(str(uri))
    if parsed.scheme != "file":
        raise ValueError("only file asset URIs are supported")
    decoded = unquote(parsed.path)
    if sys.platform == "win32" and decoded.startswith("/") and len(decoded) >= 3 and decoded[2] == ":":
        decoded = decoded[1:]
    return Path(decoded)


def _input_image(payload: dict[str, Any]) -> tuple[InputImage | None, FlowStatus, tuple[dict[str, Any], ...]]:
    asset = payload.get("asset")
    if asset is not None:
        if not isinstance(asset, dict):
            return None, FlowStatus.PARAMETER_INVALID, ({"code": "asset_reference_invalid"},)
        expected_hash = asset.get("expected_sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            return None, FlowStatus.PARAMETER_INVALID, ({"code": "expected_hash_required"},)
        try:
            path = _asset_path(asset)
        except (TypeError, ValueError) as error:
            return None, FlowStatus.PARAMETER_INVALID, ({"code": "asset_reference_invalid", "message": str(error)},)
        if not path.is_file():
            return None, FlowStatus.INPUT_INVALID, ({"code": "asset_unavailable", "uri_hint": str(path)},)
        decoded = decode_png(
            path,
            confirm_relative_intensity=payload.get("confirm_relative_intensity", False) is True,
            expected_sha256=expected_hash,
        )
        return decoded.image, decoded.flow_status, decoded.diagnostics

    return None, FlowStatus.PARAMETER_INVALID, ({"code": "input_asset_required"},)


def _write_derived_assets(record: Any, output_strategy: dict[str, Any]) -> list[dict[str, Any]]:
    if output_strategy.get("derived_format") != "npy":
        raise ValueError("output_strategy.derived_format must be npy")
    work_directory = Path(str(output_strategy["work_directory"]))
    work_directory.mkdir(parents=True, exist_ok=True)
    assets = []
    for kind, array in (
        ("corrected_intensity", record.corrected_intensity),
        ("positive_intensity", record.positive_intensity),
        ("gaussian_fit", record.fitted_intensity),
        ("fit_residual", record.fit_residual_intensity),
        ("measurement_mask", record.measurement_mask),
        ("core_mask", record.core_mask),
    ):
        canonical = np.ascontiguousarray(array)
        target = work_directory / f"{record.record_id}-{kind}.npy"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".asset-",
                suffix=".npy",
                dir=work_directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                np.save(temporary, canonical, allow_pickle=False)
            payload = temporary_path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            os.replace(temporary_path, target)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        assets.append(
            {
                "asset_id": f"asset-{digest[:16]}",
                "kind": kind,
                "record_id": record.record_id,
                "uri": target.resolve().as_uri(),
                "sha256": digest,
                "format": "npy",
                "width": int(canonical.shape[1]),
                "height": int(canonical.shape[0]),
                "generation_parameters": {
                    "dtype": str(canonical.dtype),
                    "shape": list(canonical.shape),
                    "order": "C",
                    "allow_pickle": False,
                },
                "generation_version": "derived-array-v1",
            }
        )
    return assets


def _record_payload(record: Any, derived_assets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "analysis_fingerprint": record.analysis_fingerprint,
        "flow_status": record.flow_status.value,
        "summary_status": record.summary_status.value,
        "input": dict(record.input_metadata),
        "metrics": record.reportable_metrics(),
        "diagnostics": record.diagnostics,
        "derived_assets": derived_assets,
        "configuration": asdict(record.configuration),
        "input_shape": list(record.input_shape),
    }


def _failure(flow_status: FlowStatus, diagnostics: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "kind": "failed",
        "flow_status": flow_status.value,
        "summary_status": None,
        "record": None,
        "metrics": None,
        "diagnostics": list(diagnostics),
    }


def handle_request(request: dict[str, Any]) -> list[dict[str, Any]]:
    if request.get("schema") != SCHEMA:
        return [_failure(FlowStatus.PARAMETER_INVALID, ({"code": "schema_unsupported"},))]
    started = {"schema": EVENT_SCHEMA, "kind": "started", "flow_status": "processing"}
    lifecycle = request.get("lifecycle", {})
    if lifecycle is None:
        lifecycle = {}
    if not isinstance(lifecycle, dict):
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "lifecycle_invalid"},))]
    if lifecycle.get("cancel_requested") is True:
        return [started, _failure(FlowStatus.CANCELLED, ({"code": "cancelled_by_caller"},))]
    timeout_ms = lifecycle.get("timeout_ms")
    deadline = None
    if timeout_ms is not None:
        if not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
            return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "timeout_invalid"},))]
        deadline = time.monotonic() + float(timeout_ms) / 1000.0
    try:
        input_payload = request["input"]
        if not isinstance(input_payload, dict):
            raise TypeError("input must be an object")
    except (KeyError, TypeError) as error:
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "request_invalid", "message": str(error)},))]

    image, input_status, input_diagnostics = _input_image(input_payload)
    if image is None:
        return [started, _failure(input_status, input_diagnostics)]
    try:
        configuration_payload = request["configuration"]
        if not isinstance(configuration_payload, dict):
            raise TypeError("configuration must be an object")
        configuration = _configuration(configuration_payload)
        output_strategy = request["output_strategy"]
        if not isinstance(output_strategy, dict):
            raise TypeError("output_strategy must be an object")
        if not output_strategy.get("work_directory"):
            raise ValueError("output_strategy.work_directory is required")
        if output_strategy.get("derived_format") != "npy":
            raise ValueError("output_strategy.derived_format must be npy")
    except (KeyError, TypeError, ValueError) as error:
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "request_invalid", "message": str(error)},))]

    if deadline is not None and time.monotonic() >= deadline:
        return [started, _failure(FlowStatus.TIMEOUT, ({"code": "analysis_timeout"},))]
    outcome = analyze(image, configuration)
    if deadline is not None and time.monotonic() >= deadline:
        return [started, _failure(FlowStatus.TIMEOUT, ({"code": "analysis_timeout"},))]
    if outcome.record is None:
        return [started, _failure(outcome.flow_status, list(outcome.diagnostics))]
    try:
        derived_assets = _write_derived_assets(outcome.record, output_strategy)
    except (OSError, ValueError) as error:
        return [
            started,
            _failure(
                FlowStatus.EXPORT_FAILED,
                ({"code": "derived_asset_write_failed", "message": str(error)},),
            ),
        ]
    result = {
        "schema": RESULT_SCHEMA,
        "kind": "completed",
        "flow_status": outcome.flow_status.value,
        "record": _record_payload(outcome.record, derived_assets),
    }
    return [started, _finite(result)]


def run_worker(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Process exactly one request; diagnostics never enter stdout outside NDJSON."""

    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("request must be a JSON object")
            messages = handle_request(request)
        except json.JSONDecodeError as error:
            messages = [_failure(FlowStatus.PARAMETER_INVALID, ({"code": "invalid_json", "message": str(error)},))]
        except TypeError as error:
            messages = [_failure(FlowStatus.PARAMETER_INVALID, ({"code": "request_invalid", "message": str(error)},))]
        for message in messages:
            stdout.write(
                json.dumps(
                    _finite(message),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
            stdout.flush()
        break


if __name__ == "__main__":
    run_worker()
