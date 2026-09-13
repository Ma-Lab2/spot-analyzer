"""Versioned NDJSON worker adapter shared by CLI and UI callers."""

from __future__ import annotations

from dataclasses import asdict, fields, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.parse import unquote, urlparse

import numpy as np

from .core import analyze
from .input import decode_png
from .profiles import (
    ProfileValidationError,
    get_analysis_profile,
    resolve_model,
    resolve_preprocessing,
)
from .models import (
    AnalysisConfiguration,
    AnalysisModel,
    AnalysisRecordKind,
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
    """Expand a profile request, while preserving complete historical snapshots.

    New callers may provide only region/calibration and an advanced override
    object.  A request containing a complete v1 configuration is never merged
    with current defaults, so old records retain their original semantics.
    """
    if not isinstance(payload, dict):
        raise TypeError("configuration must be an object")
    if "region" not in payload:
        raise ValueError("configuration.region is required")
    profile_id = payload.get("standard_profile", "standard-profile-v1")
    preprocessing_payload = payload.get("preprocessing")
    model_payload = payload.get("model")
    historical_snapshot = (
        isinstance(preprocessing_payload, dict)
        and set(item.name for item in fields(PreprocessingConfiguration)) <= set(preprocessing_payload)
        and isinstance(model_payload, dict)
        and set(item.name for item in fields(AnalysisModel)) <= set(model_payload)
    )
    try:
        profile = get_analysis_profile(profile_id)
    except ProfileValidationError:
        if not historical_snapshot:
            raise
        # A complete historical snapshot owns its old profile identity. It is
        # parsed without applying today's profile defaults.
        profile = {
            "standard_profile": profile_id,
            "quality_profile": payload.get("quality_profile", "quality-profile-unknown"),
            "profile_validation": payload.get("profile_validation", "provisional"),
            "algorithm_version": payload.get("algorithm_version", "analysis-core-unknown"),
        }
    region_payload = payload["region"]
    if region_payload is not None and not isinstance(region_payload, dict):
        raise TypeError("configuration.region must be an object or null")
    region = AnalysisRegion(**region_payload) if region_payload else None
    background_payload = payload.get("background_region")
    if background_payload is not None and not isinstance(background_payload, dict):
        raise TypeError("configuration.background_region must be an object or null")
    background = AnalysisRegion(**background_payload) if background_payload else None
    calibration_payload = payload.get("calibration", {})
    if not isinstance(calibration_payload, dict):
        raise TypeError("configuration.calibration must be an object")
    calibration = SpatialCalibration(**calibration_payload)

    # Explicit complete snapshots are the historical contract.  Otherwise the
    # profile API supplies defaults and validates only the supported advanced
    # branch, keeping science out of WPF/CLI request builders.
    preprocessing = PreprocessingConfiguration(**resolve_preprocessing(preprocessing_payload))
    model = AnalysisModel(**resolve_model(model_payload))
    allowed = {
        "rref_pixels", "analysis_contract", "standard_profile", "quality_profile",
        "profile_validation", "algorithm_version", "bad_pixel_coordinates",
        "bad_pixel_mask_version", "detection_profile_version",
        "detection_profile_parameters", "automatic_background", "record_kind",
        "measurement_semantics", "measurement_semantics_confirmed",
    }
    options = {key: value for key, value in payload.items() if key in allowed}
    options.setdefault("standard_profile", profile["standard_profile"])
    options.setdefault("quality_profile", profile["quality_profile"])
    options.setdefault("profile_validation", profile["profile_validation"])
    options.setdefault("algorithm_version", profile["algorithm_version"])
    options.setdefault("analysis_contract", "analysis-contract-v1")
    options.setdefault("rref_pixels", None)
    options.setdefault("bad_pixel_coordinates", ())
    options.setdefault("bad_pixel_mask_version", "bad-pixel-mask-v1")
    options.setdefault("record_kind", "preview" if region is None else "formal")
    options.setdefault("measurement_semantics", "relative_intensity_code")
    options.setdefault("measurement_semantics_confirmed", options["record_kind"] == "formal")
    return AnalysisConfiguration(
        region=region, background_region=background, calibration=calibration,
        preprocessing=preprocessing, model=model, **options,
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


def _decode_asset(
    asset: Any,
    *,
    confirm_relative_intensity: bool,
) -> tuple[InputImage | None, FlowStatus, tuple[dict[str, Any], ...]]:
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
        confirm_relative_intensity=confirm_relative_intensity,
        expected_sha256=expected_hash,
    )
    return decoded.image, decoded.flow_status, decoded.diagnostics


def _input_image(payload: dict[str, Any], *, preview: bool = False) -> tuple[InputImage | None, FlowStatus, tuple[dict[str, Any], ...]]:
    if "asset" not in payload:
        return None, FlowStatus.PARAMETER_INVALID, ({"code": "input_asset_required"},)
    # Supported PNG decoding establishes the known relative-intensity-code
    # interpretation for an automatic preview.  Formal requests still carry
    # an explicit confirmation in the request snapshot.
    semantic_confirmed = preview or payload.get("confirm_relative_intensity", False) is True
    image, status, diagnostics = _decode_asset(
        payload["asset"],
        confirm_relative_intensity=semantic_confirmed,
    )
    if image is None or status != FlowStatus.COMPUTED:
        return image, status, diagnostics
    background_asset = payload.get("background_asset")
    if background_asset is None:
        return image, status, diagnostics
    background, background_status, background_diagnostics = _decode_asset(
        background_asset,
        confirm_relative_intensity=semantic_confirmed,
    )
    if background is None or background_status != FlowStatus.COMPUTED:
        return None, background_status, diagnostics + background_diagnostics
    return replace(image, background_frame=background), status, diagnostics + background_diagnostics


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


def _record_profile_snapshot(profile_id: str) -> dict[str, Any] | None:
    try:
        return get_analysis_profile(profile_id)
    except ProfileValidationError:
        return None


def _record_payload(record: Any, derived_assets: list[dict[str, Any]]) -> dict[str, Any]:
    metric_semantics = {
        name: {
            "value": metric.value,
            "physical_value": metric.physical_value,
            "unit": metric.unit,
            "status": metric.status.value,
            "reason_codes": list(metric.reason_codes),
            "method_version": metric.method_version,
            "reported_value": metric.reported_value,
        }
        for name, metric in record.metrics.items()
    }
    arrays = {
        name: {
            "sha256": hashlib.sha256(np.ascontiguousarray(array).tobytes(order="C")).hexdigest(),
            "shape": list(array.shape),
            "dtype": str(array.dtype),
        }
        for name, array in (
            ("input_intensity", record.input_intensity),
            ("corrected_intensity", record.corrected_intensity),
            ("positive_intensity", record.positive_intensity),
            ("fitted_intensity", record.fitted_intensity),
            ("fit_residual_intensity", record.fit_residual_intensity),
            ("measurement_mask", record.measurement_mask),
            ("core_mask", record.core_mask),
        )
    }
    mask_statistics = {
        name: {"true_count": int(np.count_nonzero(array)), "size": int(array.size)}
        for name, array in (
            ("measurement_mask", record.measurement_mask),
            ("core_mask", record.core_mask),
        )
    }
    return {
        "record_id": record.record_id,
        "analysis_fingerprint": record.analysis_fingerprint,
        "record_kind": record.record_kind.value,
        "is_preview": record.is_preview,
        "is_formal": record.is_formal,
        "measurement_semantics": record.measurement_semantics,
        "measurement_semantics_confirmed": record.measurement_semantics_confirmed,
        "workflow_contract": "workflow-contract-v1",
        "flow_status": record.flow_status.value,
        "summary_status": record.summary_status.value,
        "input": dict(record.input_metadata),
        "metrics": record.reportable_metrics(),
        "metric_semantics": metric_semantics,
        "diagnostics": record.diagnostics,
        "arrays": arrays,
        "mask_statistics": mask_statistics,
        "derived_assets": derived_assets,
        "display_projection": {
            "schema": "display-projection-v1",
            "record_id": record.record_id,
            "analysis_fingerprint": record.analysis_fingerprint,
            "curves": record.diagnostics.get("report_curves", {}),
            "layers": [
                {"name": "input_image", "source": "input"},
                {"name": "corrected_intensity", "asset_kind": "corrected_intensity"},
                {"name": "positive_signal", "asset_kind": "positive_intensity"},
                {"name": "fitted_intensity", "asset_kind": "gaussian_fit"},
                {"name": "fit_residual", "asset_kind": "fit_residual"},
                {"name": "measurement_mask", "asset_kind": "measurement_mask"},
                {"name": "core_mask", "asset_kind": "core_mask"},
            ],
        },
        "configuration": asdict(record.configuration),
        # The snapshot is explicit in the record even when the request used
        # profile defaults.  This is provenance, not a second defaults source.
        "profile": {
            "standard_profile": record.configuration.standard_profile,
            "quality_profile": record.configuration.quality_profile,
            "validation": record.configuration.profile_validation,
            "algorithm_version": record.configuration.algorithm_version,
            "snapshot": _record_profile_snapshot(record.configuration.standard_profile),
        },
        "input_shape": list(record.input_shape),
    }


def _failure(flow_status: FlowStatus, diagnostics: tuple[dict[str, Any], ...] | list[dict[str, Any]], request_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "kind": "failed",
        "request_id": request_id,
        "flow_status": flow_status.value,
        "summary_status": None,
        "record": None,
        "metrics": None,
        "diagnostics": list(diagnostics),
    }


def handle_request(request: dict[str, Any]) -> list[dict[str, Any]]:
    request_id = request.get("request_id") if isinstance(request.get("request_id"), str) else None
    if request.get("schema") != SCHEMA:
        return [_failure(FlowStatus.PARAMETER_INVALID, ({"code": "schema_unsupported"},), request_id)]
    workflow = request.get("workflow", {})
    workflow_kind = request.get("record_kind", request.get("analysis_kind"))
    if isinstance(workflow, dict):
        workflow_kind = workflow.get("kind", workflow.get("record_kind", workflow_kind))
    if workflow_kind not in {"preview", "formal"}:
        configuration_hint = request.get("configuration")
        workflow_kind = "preview" if isinstance(configuration_hint, dict) and configuration_hint.get("region") is None else "formal"
    preview = workflow_kind == "preview"
    started = {"schema": EVENT_SCHEMA, "kind": "started", "request_id": request_id, "record_kind": workflow_kind, "flow_status": "processing"}
    lifecycle = request.get("lifecycle", {})
    if lifecycle is None:
        lifecycle = {}
    if not isinstance(lifecycle, dict):
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "lifecycle_invalid"},), request_id)]
    if lifecycle.get("cancel_requested") is True:
        return [started, _failure(FlowStatus.CANCELLED, ({"code": "cancelled_by_caller"},), request_id)]
    timeout_ms = lifecycle.get("timeout_ms")
    deadline = None
    if timeout_ms is not None:
        if not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
            return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "timeout_invalid"},), request_id)]
        deadline = time.monotonic() + float(timeout_ms) / 1000.0
    try:
        input_payload = request["input"]
        if not isinstance(input_payload, dict):
            raise TypeError("input must be an object")
    except (KeyError, TypeError) as error:
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "request_invalid", "message": str(error)},), request_id)]

    image, input_status, input_diagnostics = _input_image(input_payload, preview=preview)
    if image is None:
        return [started, _failure(input_status, input_diagnostics, request_id)]
    try:
        configuration_payload = request["configuration"]
        if not isinstance(configuration_payload, dict):
            raise TypeError("configuration must be an object")
        configuration_payload = dict(configuration_payload)
        configuration_payload.setdefault("record_kind", workflow_kind)
        configuration_payload.setdefault("measurement_semantics_confirmed", not preview)
        configuration = _configuration(configuration_payload)
        output_strategy = request["output_strategy"]
        if not isinstance(output_strategy, dict):
            raise TypeError("output_strategy must be an object")
        if not output_strategy.get("work_directory"):
            raise ValueError("output_strategy.work_directory is required")
        if output_strategy.get("derived_format") != "npy":
            raise ValueError("output_strategy.derived_format must be npy")
    except (KeyError, TypeError, ValueError) as error:
        return [started, _failure(FlowStatus.PARAMETER_INVALID, ({"code": "request_invalid", "message": str(error)},), request_id)]

    if deadline is not None and time.monotonic() >= deadline:
        return [started, _failure(FlowStatus.TIMEOUT, ({"code": "analysis_timeout"},), request_id)]
    outcome = analyze(image, configuration)
    if deadline is not None and time.monotonic() >= deadline:
        return [started, _failure(FlowStatus.TIMEOUT, ({"code": "analysis_timeout"},), request_id)]
    if outcome.record is None:
        return [started, _failure(outcome.flow_status, list(outcome.diagnostics), request_id)]
    try:
        derived_assets = _write_derived_assets(outcome.record, output_strategy)
    except (OSError, ValueError) as error:
        return [
            started,
            _failure(
                FlowStatus.EXPORT_FAILED,
                ({"code": "derived_asset_write_failed", "message": str(error)},), request_id,
            ),
        ]
    result = {
        "schema": RESULT_SCHEMA,
        "kind": "completed",
        "request_id": request_id,
        "record_kind": workflow_kind,
        "flow_status": outcome.flow_status.value,
        "record": _record_payload(outcome.record, derived_assets),
    }
    return [started, _finite(result)]


def _caller_failure(
    flow_status: FlowStatus,
    code: str,
    message: str | None = None,
    **details: Any,
) -> list[dict[str, Any]]:
    diagnostic: dict[str, Any] = {"code": code}
    if message:
        diagnostic["message"] = message
    diagnostic.update(details)
    return [
        {"schema": EVENT_SCHEMA, "kind": "started", "flow_status": FlowStatus.PROCESSING.value},
        _failure(flow_status, (diagnostic,)),
    ]


def _stop_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
    except OSError:
        if process.poll() is not None:
            return
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        if process.poll() is not None:
            return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass


def _decode_process_output(stdout: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"worker emitted invalid NDJSON: {error}") from error
        if not isinstance(message, dict):
            raise ValueError("worker emitted a non-object NDJSON message")
        messages.append(message)
    if not messages:
        return messages
    first = messages[0]
    if first.get("schema") != EVENT_SCHEMA or first.get("kind") != "started":
        raise ValueError("worker protocol must begin with an analysis-event-v1 started message")
    terminals = 0
    for message in messages[1:]:
        schema = message.get("schema")
        kind = message.get("kind")
        if schema == EVENT_SCHEMA and kind == "progress":
            continue
        if schema == RESULT_SCHEMA and kind in {"completed", "failed"}:
            terminals += 1
            if message is not messages[-1]:
                raise ValueError("worker protocol contains messages after its terminal result")
            continue
        raise ValueError("worker protocol contains an invalid analysis event or result message")
    if terminals != 1:
        raise ValueError("worker protocol must contain exactly one terminal result message")
    return messages


def run_worker_process(
    request: Mapping[str, Any],
    *,
    command: Sequence[str] | None = None,
    timeout_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Run one worker child and force-stop it on caller cancellation or timeout."""

    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, (int, float))
        or not np.isfinite(timeout_seconds)
        or timeout_seconds < 0
    ):
        return _caller_failure(FlowStatus.PARAMETER_INVALID, "caller_timeout_invalid")
    executable = tuple(command or (sys.executable, "-m", "spot_analyzer.worker"))
    try:
        process = subprocess.Popen(
            executable,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        return _caller_failure(FlowStatus.ANALYSIS_FAILED, "worker_start_failed", str(error))
    try:
        request_line = json.dumps(_finite(dict(request)), ensure_ascii=False, allow_nan=False) + "\n"
        started_at = time.monotonic()
        pending_input: str | None = request_line
        while True:
            try:
                stdout, stderr = process.communicate(input=pending_input, timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pending_input = None
                if cancel_requested is not None and cancel_requested():
                    _stop_process(process)
                    stdout, stderr = process.communicate(timeout=0.5)
                    return _caller_failure(FlowStatus.CANCELLED, "worker_terminated_cancelled")
                if timeout_seconds is not None and time.monotonic() - started_at >= float(timeout_seconds):
                    _stop_process(process)
                    stdout, stderr = process.communicate(timeout=0.5)
                    return _caller_failure(FlowStatus.TIMEOUT, "worker_terminated_timeout")
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.communicate()
        return _caller_failure(FlowStatus.ANALYSIS_FAILED, "worker_termination_incomplete")
    except (OSError, ValueError, BrokenPipeError) as error:
        if process.poll() is None:
            _stop_process(process)
        return _caller_failure(FlowStatus.ANALYSIS_FAILED, "worker_io_failed", str(error))
    try:
        messages = _decode_process_output(stdout)
    except ValueError as error:
        return _caller_failure(
            FlowStatus.ANALYSIS_FAILED,
            "worker_protocol_invalid",
            str(error),
        )
    if process.returncode != 0:
        return _caller_failure(
            FlowStatus.ANALYSIS_FAILED,
            "worker_crashed",
            stderr.strip() or None,
            returncode=process.returncode,
        )
    if not messages:
        return _caller_failure(FlowStatus.ANALYSIS_FAILED, "worker_no_result")
    return messages


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
        except Exception as error:
            messages = [_failure(FlowStatus.ANALYSIS_FAILED, ({"code": "worker_unhandled_error", "message": str(error)},))]
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
