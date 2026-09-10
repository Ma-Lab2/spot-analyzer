"""Manifest loading and tolerance comparisons for validation runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping

import numpy as np
from PIL import Image


VALIDATION_CONTRACT_VERSION = "validation-contract-v1"
VALIDATION_SECTION_STATUSES = frozenset({"passed", "failed", "incomplete"})
_PROFILE_VALIDATION = "provisional"

from .core import analyze
from .identity import validate_golden_vectors
from .input import decode_png
from .models import AnalysisConfiguration, AnalysisRecord, AnalysisRegion, InputImage
from .oracle import circular_gaussian_oracle, scene_oracle
from .report import ReportSpecification, prepare_report, write_report
from .synthetic import GENERATOR_VERSION, SceneManifest, SyntheticScene, generate_scene


@dataclass(frozen=True)
class Comparison:
    key: str
    measured: float | None
    expected: float | None
    passed: bool
    relative_error: float | None = None
    absolute_error: float | None = None
    reason: str = ""


def load_manifest(path: str | Path) -> list[SceneManifest]:
    """Load one manifest or a list of manifests from canonical JSON."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload if isinstance(payload, list) else [payload]
    manifests = []
    for entry in entries:
        normalized = dict(entry)
        for key in ("shape", "pixel_pitch_um", "center_xy"):
            if key in normalized:
                normalized[key] = tuple(normalized[key])
        for key in ("expected_reported_statuses", "required_reason_codes", "forbidden_reason_codes"):
            if key in normalized:
                normalized[key] = tuple(normalized[key])
        manifests.append(SceneManifest(**normalized))
    return manifests


def angle_distance_degrees(measured: float, expected: float) -> float:
    return abs((measured - expected + 90.0) % 180.0 - 90.0)


def compare_scalar(
    key: str,
    measured: float | None,
    expected: float | None,
    *,
    relative_tolerance: float | None = None,
    absolute_tolerance: float | None = None,
    angle: bool = False,
) -> Comparison:
    if measured is None or expected is None:
        return Comparison(key, measured, expected, measured is None and expected is None, reason="missing value")
    difference = angle_distance_degrees(measured, expected) if angle else abs(measured - expected)
    relative = difference / abs(expected) if expected != 0 else None
    passed = True
    if absolute_tolerance is not None:
        passed = difference <= absolute_tolerance
    if relative_tolerance is not None and expected != 0:
        passed = passed and relative <= relative_tolerance
    return Comparison(key, measured, expected, passed, relative, difference)


def compare_scene(record: AnalysisRecord, scene: SyntheticScene) -> tuple[Comparison, ...]:
    """Compare available clean-scene measurements against its oracle."""

    expected = scene_oracle(scene.manifest.scene_id, scene.manifest.parameters, scene.reference, scene.manifest.center_xy)
    comparisons: list[Comparison] = []
    metric_map = {
        "fwhm": "gaussian_fwhm_major",
        "fwhm_major": "gaussian_fwhm_major",
        "fwhm_minor": "gaussian_fwhm_minor",
        "d4sigma": "moment_d4sigma_major",
        "d4sigma_major": "moment_d4sigma_major",
        "d4sigma_minor": "moment_d4sigma_minor",
        "angle": "gaussian_angle",
        "ee50": "ee50",
        "ee80": "ee80",
    }
    for expected_key, metric_key in metric_map.items():
        if expected_key not in expected or metric_key not in record.metrics:
            continue
        tolerance = (
            scene.manifest.tolerances.get("fwhm_relative")
            if expected_key.startswith("fwhm")
            else scene.manifest.tolerances.get("d4sigma_relative")
            if expected_key.startswith("d4sigma")
            else None
        )
        absolute = (
            scene.manifest.tolerances.get("angle_absolute_degrees")
            if expected_key == "angle"
            else scene.manifest.tolerances.get("ee_radius_absolute_fwhm_fraction", 0.0)
            * expected.get("fwhm", expected.get("fwhm_major", 1.0))
            if expected_key.startswith("ee")
            else None
        )
        comparisons.append(compare_scalar(expected_key, record.metrics[metric_key].value, expected[expected_key], relative_tolerance=tolerance, absolute_tolerance=absolute, angle=expected_key == "angle"))
    center = record.diagnostics.get("center_xy", {})
    center_tolerance = scene.manifest.tolerances.get("center_absolute_pixels")
    if center_tolerance is not None:
        comparisons.append(
            compare_scalar(
                "center_x",
                center.get("x"),
                scene.manifest.center_xy[0],
                absolute_tolerance=center_tolerance,
            )
        )
        comparisons.append(
            compare_scalar(
                "center_y",
                center.get("y"),
                scene.manifest.center_xy[1],
                absolute_tolerance=center_tolerance,
            )
        )
    return tuple(comparisons)


def _scene_configuration(scene: SyntheticScene) -> AnalysisConfiguration:
    height, width = scene.input_array.shape
    if scene.manifest.scene_id == "cropped_edge":
        region = AnalysisRegion(0, 64, 64, 128)
        background_region = AnalysisRegion(64, 64, 32, 128)
    else:
        region = AnalysisRegion(64, 64, 128, 128)
        background_region = AnalysisRegion(32, 64, 32, 128)
    bad_pixels: tuple[tuple[int, int], ...] = ()
    if scene.manifest.scene_id == "hot_dead_pixels":
        bad_pixels = (
            tuple(scene.manifest.parameters["hot_pixel"]),
            tuple(scene.manifest.parameters["dead_pixel"]),
        )
    return AnalysisConfiguration(
        region=region,
        background_region=background_region,
        bad_pixel_coordinates=bad_pixels,
    )


def run_manifest_regression(manifests: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Run deterministic synthetic scenes and return JSON-ready results."""

    selections = ({
        "gaussian_circular": 0,
        "gaussian_elliptical_rotated": 0,
        "two_gaussian_multimodal": 0,
        "airy_sidelobe": 0,
        "background_gradient": 0,
        "hot_dead_pixels": 0,
        "low_snr_seeded": 0,
        "saturated_core": 0,
        "cropped_edge": 0,
    } if manifests is None else dict(manifests))
    results = []
    for scene_id, seed in selections.items():
        scene = generate_scene(scene_id, seed=seed)
        configuration = _scene_configuration(scene)
        outcome = analyze(InputImage(scene.input_array, bit_depth=scene.manifest.bit_depth, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), configuration)
        comparisons = (
            compare_scene(outcome.record, scene)
            if outcome.record is not None and scene.manifest.comparison_policy == "analytic"
            else ()
        )
        actual_reasons = set(outcome.record.diagnostics.get("reasons", ())) if outcome.record is not None else {item.get("code") for item in outcome.diagnostics}
        actual_summary = outcome.record.summary_status.value if outcome.record is not None else None
        before_cap = outcome.record.diagnostics.get("quality_status_before_profile_cap") if outcome.record is not None else None
        expected_status = scene.manifest.expected_quality_status
        status_match = (
            before_cap == "valid" if expected_status == "valid" else
            (before_cap == "invalid" or actual_summary == "invalid") if expected_status == "invalid" else
            actual_summary in {"caution", "invalid"}
        )
        reasons_match = set(scene.manifest.required_reason_codes).issubset(actual_reasons)
        forbidden_reasons_absent = set(scene.manifest.forbidden_reason_codes).isdisjoint(actual_reasons)
        reported_status_match = actual_summary in scene.manifest.expected_reported_statuses
        versions_match = (
            configuration.analysis_contract == scene.manifest.analysis_contract
            and configuration.standard_profile == scene.manifest.standard_profile
            and configuration.quality_profile == scene.manifest.quality_profile
            and configuration.profile_validation == scene.manifest.profile_validation
        )
        comparisons_passed = all(comparison.passed for comparison in comparisons)
        metric_outcomes = (
            {
                key: {
                    "status": metric.status.value,
                    "reported_value_is_null": metric.reported_value is None,
                    "reason_codes": list(metric.reason_codes),
                }
                for key, metric in outcome.record.metrics.items()
            }
            if outcome.record is not None
            else {}
        )
        metric_gates_passed = outcome.record is not None and all(
            outcome_item["reported_value_is_null"]
            == (outcome_item["status"] in {"invalid", "unavailable"})
            for outcome_item in metric_outcomes.values()
        )
        results.append({
            "scene_id": scene_id,
            "seed": seed,
            "manifest": scene.manifest.to_dict(),
            "flow_status": outcome.flow_status.value,
            "summary_status": actual_summary,
            "quality_status_before_profile_cap": before_cap,
            "comparison_policy": scene.manifest.comparison_policy,
            "reasons": sorted(actual_reasons),
            "required_reason_codes_passed": reasons_match,
            "forbidden_reason_codes_passed": forbidden_reasons_absent,
            "status_expectation_passed": status_match,
            "reported_status_passed": reported_status_match,
            "versions_passed": versions_match,
            "comparisons_passed": comparisons_passed,
            "metric_gates_passed": metric_gates_passed,
            "metric_outcomes": metric_outcomes,
            "passed": (
                reasons_match
                and forbidden_reasons_absent
                and status_match
                and reported_status_match
                and versions_match
                and comparisons_passed
                and metric_gates_passed
            ),
            "comparisons": [comparison.__dict__ for comparison in comparisons],
        })
    return {"generator_version": "synthetic-scenes-v1", "results": results}


def run_low_snr_regression(seeds: Iterable[int] = range(32)) -> dict[str, Any]:
    """Aggregate deterministic low-SNR bias and quality-gate evidence."""

    seed_values = tuple(int(seed) for seed in seeds)
    errors: list[float] = []
    statuses: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    cases: list[dict[str, Any]] = []
    for seed in seed_values:
        scene = generate_scene("low_snr_seeded", seed=seed)
        outcome = analyze(
            InputImage(scene.input_array, bit_depth=scene.manifest.bit_depth, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
            _scene_configuration(scene),
        )
        if outcome.record is None:
            cases.append({"seed": seed, "flow_status": outcome.flow_status.value, "passed": False})
            continue
        record = outcome.record
        status = record.summary_status.value
        statuses[status] = statuses.get(status, 0) + 1
        reasons = tuple(record.diagnostics.get("reasons", ()))
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        expected = circular_gaussian_oracle(float(scene.manifest.parameters["width"]))
        measured = record.metrics["gaussian_fwhm_major"].value
        error = None
        if measured is not None and np.isfinite(measured):
            error = float(measured - expected["fwhm"])
            errors.append(error)
        gated = (
            record.metrics["gaussian_fwhm_major"].reported_value is None
            and record.metrics["ee50"].reported_value is None
        )
        snr = record.diagnostics.get("snr")
        if snr is None:
            gate_passed = "background_noise_unavailable" in reasons and gated
        elif snr < 5:
            gate_passed = "low_snr" in reasons and gated
        elif snr < 10:
            gate_passed = (
                "low_snr_caution" in reasons
                and record.metrics["ee50"].status.value in {"caution", "invalid"}
            )
        else:
            gate_passed = "low_snr" not in reasons and "low_snr_caution" not in reasons
        cases.append(
            {
                "seed": seed,
                "flow_status": outcome.flow_status.value,
                "summary_status": status,
                "snr": snr,
                "reasons": list(reasons),
                "fwhm_error_px": error,
                "quantitative_values_gated": gated,
                "analysis_fingerprint": record.analysis_fingerprint,
                "passed": gate_passed,
            }
        )
    error_array = np.asarray(errors, dtype=np.float64)
    if error_array.size:
        median = float(np.median(error_array))
        mad = float(np.median(np.abs(error_array - median)))
        p95 = float(np.percentile(np.abs(error_array), 95))
    else:
        median = mad = p95 = None
    return {
        "scene_id": "low_snr_seeded",
        "seed_range": _seed_range(seed_values),
        "case_count": len(cases),
        "bias": {
            "metric": "gaussian_fwhm_major",
            "unit": "px",
            "median_error": median,
            "mad_error": mad,
            "p95_absolute_error": p95,
            "available_case_count": int(error_array.size),
        },
        "status_distribution": statuses,
        "reason_code_distribution": reason_counts,
        "passed": bool(cases) and all(case["passed"] for case in cases),
        "cases": cases,
    }


def _seed_range(seeds: tuple[int, ...]) -> list[int] | None:
    """Describe an ordered seed collection without requiring a ``range`` input."""

    if not seeds:
        return None
    if len(seeds) == 1:
        step = 1
    else:
        step = seeds[1] - seeds[0]
        if any(right - left != step for left, right in zip(seeds, seeds[1:])):
            step = 0
    return [seeds[0], seeds[-1], step]


def _package_version(distribution: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return None


def _validation_environment() -> dict[str, Any]:
    """Return stable, JSON-ready execution identity for a validation run."""

    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "numpy": np.__version__,
        "scipy": _package_version("scipy"),
        "pillow": _package_version("pillow"),
        "spot_analyzer": _package_version("spot-analyzer") or "0.1.0",
    }


def _section_status(*, passed: bool, available: bool = True) -> str:
    if not available:
        return "incomplete"
    return "passed" if passed else "failed"


def _run_section(name: str, operation: Any, *, available: bool = True) -> dict[str, Any]:
    """Execute one section while preserving the aggregate result contract."""

    if not available:
        return {"name": name, "status": "incomplete", "result": None, "incomplete_reason": "no evidence"}
    try:
        result = operation()
    except Exception as exc:  # validation must report a failed section, not hide it in a crash
        return {
            "name": name,
            "status": "failed",
            "result": None,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    if result.get("status") in VALIDATION_SECTION_STATUSES:
        status = str(result["status"])
    elif "passed" in result:
        status = _section_status(passed=bool(result["passed"]))
    elif "results" in result:
        passed = bool(result["results"]) and all(item.get("passed", False) for item in result["results"])
        status = _section_status(passed=passed)
    else:
        status = "incomplete"
    return {"name": name, "status": status, "result": result}


def _aggregate_status(sections: Mapping[str, Mapping[str, Any]]) -> str:
    statuses = {section.get("status") for section in sections.values()}
    if "failed" in statuses:
        return "failed"
    if "incomplete" in statuses:
        return "incomplete"
    return "passed" if statuses and statuses <= VALIDATION_SECTION_STATUSES else "incomplete"


def _array_digest(array: Any) -> str:
    values = np.asarray(array)
    return "sha256-" + hashlib.sha256(values.tobytes(order="C")).hexdigest()


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in forbidden or _contains_key(item, forbidden) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _report_validation_record() -> AnalysisRecord | None:
    scene = generate_scene("gaussian_circular", seed=0)
    configuration = _scene_configuration(scene)
    outcome = analyze(
        InputImage(
            scene.input_array,
            bit_depth=scene.manifest.bit_depth,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
        ),
        configuration,
    )
    return outcome.record


def run_report_validation(output_directory: str | Path | None = None) -> dict[str, Any]:
    """Validate report semantics and exports from one immutable analysis record."""

    record = _report_validation_record()
    if record is None:
        return {"status": "failed", "passed": False, "incomplete_reason": "analysis_record_unavailable"}
    expected_arrays = {
        "输入图像": record.input_intensity,
        "校正强度图": record.corrected_intensity,
        "正信号图": record.positive_intensity,
        "高斯拟合": record.fitted_intensity,
        "拟合残差": record.fit_residual_intensity,
        "测量有效 mask": record.measurement_mask,
        "核心 mask": record.core_mask,
    }
    checks: dict[str, bool] = {
        "record_identity": False,
        "visual_identity": False,
        "profile_axis": False,
        "energy_curve_context": False,
        "provenance": False,
        "no_internal_trials": False,
        "exports": False,
    }
    export_results: list[dict[str, Any]] = []
    temporary_context = tempfile.TemporaryDirectory(prefix="spot-report-validation-") if output_directory is None else None
    try:
        destination = Path(output_directory) if output_directory is not None else Path(temporary_context.name)
        package = prepare_report(record, ReportSpecification("png", "issue-15-validation", destination))
        checks["record_identity"] = (
            package.record_id == record.record_id
            and package.analysis_fingerprint == record.analysis_fingerprint
            and package.sections["summary"]["record_id"] == record.record_id
            and package.sections["summary"]["analysis_fingerprint"] == record.analysis_fingerprint
        )
        checks["visual_identity"] = all(
            name in package.visuals and _array_digest(package.visuals[name]) == _array_digest(array)
            for name, array in expected_arrays.items()
        )
        curves = package.sections["diagnostics"].get("report_curves", {})
        profile_x = np.asarray(curves.get("profile_x_pixels", ()), dtype=float)
        profile = np.asarray(curves.get("profile", ()), dtype=float)
        fitted_profile = np.asarray(curves.get("fitted_profile", ()), dtype=float)
        energy_radius = np.asarray(curves.get("energy_radius", ()), dtype=float)
        energy_fraction = np.asarray(curves.get("energy_fraction", ()), dtype=float)
        checks["profile_axis"] = (
            profile_x.size > 0
            and profile_x.size == profile.size == fitted_profile.size
            and np.array_equal(profile_x, np.arange(profile_x.size, dtype=float))
        )
        reportable = package.sections.get("metrics", {})
        checks["energy_curve_context"] = (
            energy_radius.size == energy_fraction.size
            and energy_radius.size > 0
            and np.all(np.diff(energy_radius) >= 0)
            and bool(curves.get("energy_radius_unit"))
            and all(key in reportable for key in ("ee50", "ee80"))
        )
        provenance = package.sections.get("provenance", {})
        checks["provenance"] = all(
            provenance.get(key) for key in (
                "analysis_contract", "standard_profile", "quality_profile",
                "algorithm_version", "parameter_snapshot_hash", "export_contract",
                "software", "report_schema",
            )
        ) and bool(provenance.get("software", {}).get("build"))
        checks["no_internal_trials"] = not _contains_key(
            package.sections,
            {
                "fit_initial_parameters", "fit_cost", "fit_nfev", "fit_covariance",
                "fit_parameters", "fit_active_mask", "fit_optimality",
                "standard_fwhm_estimate", "advanced_fwhm_estimate", "sensitivity_fraction", "sensitivity",
            },
        )
        for format_name in ("png", "pdf"):
            specification = ReportSpecification(format_name, f"issue-15-{format_name}", destination)
            exported = write_report(prepare_report(record, specification), specification)
            export_results.append({
                "format": format_name,
                "status": exported.flow_status,
                "uri": exported.path.as_uri() if exported.path is not None else None,
                "sha256": exported.sha256,
            })
        checks["exports"] = all(
            item["status"] == "exported" and bool(item["uri"]) and bool(item["sha256"])
            for item in export_results
        )
    except Exception as exc:
        return {
            "status": "failed",
            "passed": False,
            "checks": checks,
            "exports": export_results,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    finally:
        if temporary_context is not None:
            temporary_context.cleanup()
    passed = all(checks.values())
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "record_id": record.record_id,
        "analysis_fingerprint": record.analysis_fingerprint,
        "checks": checks,
        "exports": export_results,
        "behavioral_evidence_only": False,
    }


def _worker_parity_validation() -> dict[str, Any]:
    """Compare one direct analysis with the real NDJSON worker child process."""
    from .worker import run_worker_process

    scene = generate_scene("gaussian_circular", seed=0)
    configuration = _scene_configuration(scene)
    with tempfile.TemporaryDirectory(prefix="spot-identity-parity-") as temporary:
        root = Path(temporary)
        request = _worker_request(scene, configuration, root / "input.png", root / "assets")
        decoded = decode_png(
            request["input"]["asset"]["path"],
            confirm_relative_intensity=True,
            expected_sha256=request["input"]["asset"]["expected_sha256"],
        )
        if decoded.image is None:
            return {"status": "failed", "passed": False, "reason": "direct_input_decode_failed"}
        direct = analyze(decoded.image, configuration)
        if direct.record is None:
            return {"status": "failed", "passed": False, "reason": "direct_analysis_failed"}
        messages = run_worker_process(request)
    terminal = messages[-1] if messages else {}
    worker_record = terminal.get("record") if terminal.get("kind") == "completed" else None
    if not isinstance(worker_record, Mapping):
        return {
            "status": "failed",
            "passed": False,
            "reason": "worker_analysis_failed",
            "terminal": terminal,
        }
    direct_record = direct.record
    direct_metrics = direct_record.reportable_metrics()
    worker_metrics = worker_record.get("metrics")
    checks = {
        "flow_status": direct.flow_status.value == worker_record.get("flow_status"),
        "summary_status": direct_record.summary_status.value == worker_record.get("summary_status"),
        "analysis_fingerprint": direct_record.analysis_fingerprint == worker_record.get("analysis_fingerprint"),
        "metrics": direct_metrics == worker_metrics,
        "diagnostic_reasons": sorted(direct_record.diagnostics.get("reasons", ()))
        == sorted(worker_record.get("diagnostics", {}).get("reasons", ())),
    }
    passed = all(checks.values())
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "checks": checks,
        "analysis_fingerprint": direct_record.analysis_fingerprint,
    }


def run_identity_validation(
    golden_vector_path: str | Path = Path("docs/validation/issue-10-fingerprint-golden-vectors.json"),
) -> dict[str, Any]:
    """Validate golden vectors and direct-core/worker identity parity."""
    golden = validate_golden_vectors(golden_vector_path)
    try:
        parity = _worker_parity_validation()
    except Exception as exc:
        parity = {
            "status": "failed",
            "passed": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    statuses = {golden.get("status"), parity.get("status")}
    status = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "passed"
    return {
        "status": status,
        "passed": status == "passed",
        "golden_vectors": golden,
        "worker_parity": parity,
        "incomplete_reason": (
            "formal_identity_validation_requires_python_3_12"
            if status == "incomplete" and golden.get("status") == "incomplete"
            else None
        ),
    }


def run_issue10_validation(
    manifests: Mapping[str, int] | None = None,
    seeds: Iterable[int] = range(32),
    real_manifest_path: str | Path = Path("docs/validation/issue-10-real-fixtures.json"),
    real_fixture_root: str | Path | None = None,
    golden_vector_path: str | Path = Path("docs/validation/issue-10-fingerprint-golden-vectors.json"),
    performance_sizes: Iterable[int] | None = None,
    performance_repetitions: int = 10,
) -> dict[str, Any]:
    """Run the Issue #10 synthetic and low-SNR sections in one JSON-ready operation.

    This is intentionally the first vertical slice of the complete Issue #10
    validation. Later sections can add entries to ``sections`` without changing
    the section status or environment contract established here.
    """

    seed_values = tuple(int(seed) for seed in seeds)
    synthetic_available = manifests is None or bool(manifests)
    low_snr_available = bool(seed_values)
    sections = {
        "synthetic": _run_section(
            "synthetic",
            lambda: run_manifest_regression(manifests),
            available=synthetic_available,
        ),
        "low_snr": _run_section(
            "low_snr",
            lambda: run_low_snr_regression(seed_values),
            available=low_snr_available,
        ),
        "real_fixtures": _run_section(
            "real_fixtures",
            lambda: run_real_fixture_validation(real_manifest_path, root=real_fixture_root),
        ),
        "report": _run_section(
            "report",
            run_report_validation,
        ),
        "identity": _run_section(
            "identity",
            lambda: run_identity_validation(golden_vector_path),
        ),
        "performance": _run_section(
            "performance",
            lambda: run_performance_baseline(
                sizes=performance_sizes if performance_sizes is not None else (256, 1024),
                repetitions=performance_repetitions,
            ),
            available=performance_sizes is not None,
        ),
    }
    identity = {
        "validation_contract": VALIDATION_CONTRACT_VERSION,
        "analysis_contract": "analysis-contract-v1",
        "standard_profile": "standard-profile-v1",
        "quality_profile": "quality-profile-v1",
        "profile_validation": _PROFILE_VALIDATION,
        "generator_version": GENERATOR_VERSION,
    }
    incomplete_items = [
        name for name, section in sections.items() if section["status"] == "incomplete"
    ]
    return {
        "issue": 10,
        "contract": VALIDATION_CONTRACT_VERSION,
        "validation_contract": VALIDATION_CONTRACT_VERSION,
        "overall_status": _aggregate_status(sections),
        "status": _aggregate_status(sections),
        "sections": sections,
        "identity": identity,
        "environment": _validation_environment(),
        "incomplete_items": incomplete_items,
    }


def _real_fixture_configuration(entry: Mapping[str, Any]) -> AnalysisConfiguration:
    """Build an analysis configuration from a controlled fixture manifest entry."""

    region = AnalysisRegion(**dict(entry["analysis_region"]))
    background = entry.get("background_region")
    return AnalysisConfiguration(
        region=region,
        background_region=AnalysisRegion(**dict(background)) if background else None,
    )


def _fixture_result_base(entry: Mapping[str, Any], path: Path) -> dict[str, Any]:
    return {
        "relative_path": str(entry.get("relative_path", "")),
        "kind": entry.get("kind", "gray_input"),
        "path": str(path),
        "manifest_metadata": {
            "input_semantics": entry.get("input_semantics", "relative_intensity_code"),
            "acquisition": entry.get("acquisition", entry.get("acquisition_metadata")),
            "provenance": entry.get("provenance", entry.get("provenance_metadata")),
        },
        "behavioral_evidence_only": True,
    }


def _validate_real_fixture(entry: Mapping[str, Any], path: Path) -> dict[str, Any]:
    result = _fixture_result_base(entry, path)
    expected_hash = str(entry.get("sha256", ""))
    if not path.is_file():
        result.update({"status": "incomplete", "passed": False, "reason_codes": ["asset_unavailable"]})
        return result
    decoded = decode_png(path, confirm_relative_intensity=True, expected_sha256=expected_hash)
    result["sha256"] = expected_hash
    result["actual_sha256"] = expected_hash
    result["flow_status"] = decoded.flow_status.value
    if decoded.image is None:
        codes = [str(item["code"]) for item in decoded.diagnostics if "code" in item]
        actual = next((item.get("actual_sha256") for item in decoded.diagnostics if item.get("actual_sha256")), None)
        if actual is not None:
            result["actual_sha256"] = actual
        result.update({"status": "failed", "passed": False, "reason_codes": codes, "diagnostics": list(decoded.diagnostics)})
        return result
    image = decoded.image
    result.update({
        "actual_sha256": image.sha256,
        "input_semantics_confirmed": image.encoding_semantic_confirmed,
        "channels": image.channels,
        "channels_identical": image.channels_identical,
    })
    kind = str(entry.get("kind", "gray_input"))
    if kind == "rgb_display_excluded":
        passed = image.channels == 3 and image.channels_identical
        result.update({"status": "passed" if passed else "failed", "passed": passed,
                       "reason_codes": [] if passed else ["rgb_display_not_excluded"]})
        return result
    if image.channels != 1:
        result.update({"status": "failed", "passed": False, "reason_codes": ["non_grayscale_measurement_input"]})
        return result
    try:
        configuration = _real_fixture_configuration(entry)
        first = analyze(image, configuration)
        second = analyze(image, configuration)
    except Exception as exc:
        result.update({"status": "failed", "passed": False,
                       "reason_codes": ["fixture_analysis_failed"],
                       "error": {"type": type(exc).__name__, "message": str(exc)}})
        return result
    if first.record is None or second.record is None:
        result.update({"status": "failed", "passed": False, "reason_codes": ["fixture_analysis_failed"]})
        return result
    record = first.record
    reasons = set(str(code) for code in record.diagnostics.get("reasons", ()))
    expected_statuses = set(str(status) for status in entry.get("expected_summary_status", entry.get("expected_reported_status", [])))
    required = set(str(code) for code in entry.get("required_reason_codes", ()))
    forbidden = set(str(code) for code in entry.get("forbidden_reason_codes", ()))
    status_passed = not expected_statuses or record.summary_status.value in expected_statuses
    reason_passed = required.issubset(reasons) and forbidden.isdisjoint(reasons)
    repeatability_passed = (
        record.analysis_fingerprint == second.record.analysis_fingerprint
        and record.summary_status.value == second.record.summary_status.value
        and tuple(record.diagnostics.get("reasons", ())) == tuple(second.record.diagnostics.get("reasons", ()))
    )
    result.update({
        "status": "passed" if status_passed and reason_passed and repeatability_passed else "failed",
        "passed": status_passed and reason_passed and repeatability_passed,
        "summary_status": record.summary_status.value,
        "analysis_fingerprint": record.analysis_fingerprint,
        "repeatability_passed": repeatability_passed,
        "required_reason_codes": sorted(required),
        "forbidden_reason_codes": sorted(forbidden),
        "reason_codes": sorted(reasons),
        "required_reason_codes_passed": required.issubset(reasons),
        "forbidden_reason_codes_passed": forbidden.isdisjoint(reasons),
        "status_expectation_passed": status_passed,
        "input_metadata": dict(record.input_metadata),
    })
    return result


def run_real_fixture_validation(
    manifest_path: str | Path = Path("docs/validation/issue-10-real-fixtures.json"),
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate controlled real PNG assets without claiming physical accuracy."""

    manifest_file = Path(manifest_path)
    if not manifest_file.is_file():
        return {"status": "incomplete", "passed": False, "manifest_status": "unavailable",
                "incomplete_reason": "manifest_unavailable", "fixtures": []}
    raw = manifest_file.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "failed", "passed": False, "manifest_status": "invalid",
                "error": {"type": type(exc).__name__, "message": str(exc)}, "fixtures": []}
    if not isinstance(payload, Mapping) or not isinstance(payload.get("fixtures"), list):
        return {"status": "failed", "passed": False, "manifest_status": "invalid",
                "incomplete_reason": "fixtures_required", "fixtures": []}
    manifest_root = Path(root) if root is not None else Path(str(payload.get("root", "")))
    manifest_identity = "sha256-" + __import__("hashlib").sha256(raw).hexdigest()
    if not manifest_root.is_dir():
        return {"status": "incomplete", "passed": False, "manifest_status": "loaded",
                "manifest_schema": payload.get("schema"), "manifest_version": payload.get("schema"),
                "manifest_sha256": manifest_identity, "root": str(manifest_root),
                "behavioral_evidence_only": True, "absolute_physical_accuracy_claim": False,
                "incomplete_reason": "fixture_root_unavailable", "fixtures": []}
    fixtures = []
    for entry in payload["fixtures"]:
        if not isinstance(entry, Mapping) or not entry.get("relative_path"):
            fixtures.append({"status": "failed", "passed": False, "reason_codes": ["manifest_entry_invalid"]})
            continue
        relative = Path(str(entry["relative_path"]))
        path = (manifest_root / relative).resolve()
        try:
            path.relative_to(manifest_root.resolve())
        except ValueError:
            fixtures.append({"status": "failed", "passed": False, "relative_path": str(relative), "reason_codes": ["asset_path_escape"]})
            continue
        fixtures.append(_validate_real_fixture(entry, path))
    statuses = [item["status"] for item in fixtures]
    status = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "passed"
    return {
        "status": status,
        "passed": status == "passed",
        "manifest_status": "loaded",
        "manifest_schema": payload.get("schema"),
        "manifest_version": payload.get("schema"),
        "manifest_sha256": manifest_identity,
        "root": str(manifest_root),
        "behavioral_evidence_only": True,
        "absolute_physical_accuracy_claim": False,
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
    }


def _percentile95(values: list[float]) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), 95))


def _memory_snapshot() -> dict[str, Any]:
    """Return best-effort process memory metadata without adding a dependency."""

    try:
        import psutil

        process = psutil.Process()
        return {"current_mb": process.memory_info().rss / (1024 * 1024), "source": "psutil"}
    except Exception:
        return {"current_mb": None, "source": "unavailable"}


def _performance_environment() -> dict[str, Any]:
    environment = _validation_environment()
    environment["memory"] = _memory_snapshot()
    environment["formal_python"] = sys.version_info[:2] == (3, 12)
    environment["formal_dependencies"] = {
        "numpy": environment["numpy"] == "2.2.6",
        "scipy": environment["scipy"] == "1.15.3",
        "pillow": environment["pillow"] == "12.2.0",
    }
    environment["formal_environment"] = environment["formal_python"] and all(
        environment["formal_dependencies"].values()
    )
    return environment


def _benchmark_summary(times: list[float]) -> dict[str, Any]:
    return {
        "runs_seconds": [float(value) for value in times],
        "p50_seconds": float(statistics.median(times)),
        "p95_seconds": _percentile95(times),
        "max_seconds": float(max(times)),
    }


def _benchmark_scene(size: int) -> SyntheticScene:
    return generate_scene("gaussian_circular", seed=0, shape=(size, size))


def _benchmark_configuration(size: int) -> AnalysisConfiguration:
    margin = size // 4
    region_size = size // 2
    return AnalysisConfiguration(
        AnalysisRegion(margin, margin, region_size, region_size),
        background_region=AnalysisRegion(size // 8, margin, size // 8, region_size),
    )


def _worker_request(scene: SyntheticScene, configuration: AnalysisConfiguration, path: Path, work: Path) -> dict[str, Any]:
    payload = io.BytesIO()
    Image.fromarray(scene.input_array.astype(np.uint8), mode="L").save(payload, format="PNG")
    path.write_bytes(payload.getvalue())
    digest = hashlib.sha256(payload.getvalue()).hexdigest()
    from .worker import SCHEMA

    return {
        "schema": SCHEMA,
        "input": {"asset": {"path": str(path), "expected_sha256": digest}, "confirm_relative_intensity": True},
        "configuration": asdict(configuration),
        "output_strategy": {"work_directory": str(work), "derived_format": "npy"},
    }


def run_performance_baseline(
    *,
    sizes: Iterable[int] = (256, 1024),
    repetitions: int = 10,
) -> dict[str, Any]:
    """Measure core hot runs and worker end-to-end runs for declared workloads."""

    from .worker import run_worker_process

    size_values = tuple(int(size) for size in sizes)
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    environment = _performance_environment()
    workloads: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="spot-performance-") as temporary:
        root = Path(temporary)
        for size in size_values:
            scene = _benchmark_scene(size)
            configuration = _benchmark_configuration(size)
            core_times: list[float] = []
            for _ in range(repetitions):
                started = time.perf_counter()
                outcome = analyze(
                    InputImage(scene.input_array, bit_depth=scene.manifest.bit_depth, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
                    configuration,
                )
                elapsed = time.perf_counter() - started
                if outcome.record is None:
                    raise RuntimeError(f"core benchmark failed for {size}x{size}")
                core_times.append(elapsed)
            worker_times: list[float] = []
            for index in range(repetitions):
                request = _worker_request(scene, configuration, root / f"{size}-{index}.png", root / f"assets-{size}-{index}")
                started = time.perf_counter()
                messages = run_worker_process(request)
                elapsed = time.perf_counter() - started
                if not messages or messages[-1].get("kind") != "completed":
                    raise RuntimeError(f"worker benchmark failed for {size}x{size}")
                worker_times.append(elapsed)
            core = _benchmark_summary(core_times)
            worker = _benchmark_summary(worker_times)
            workloads.append({
                "image_size": {"width": size, "height": size},
                "repetitions": repetitions,
                "core": {"mode": "hot_analyze", **core, "target_p95_seconds": 2.0 if size == 1024 else None, "target_passed": size != 1024 or core["p95_seconds"] <= 2.0},
                "worker": {"mode": "cold_process_png_analysis_derived_write", **worker, "target_p95_seconds": 5.0 if size == 1024 else None, "target_passed": size != 1024 or worker["p95_seconds"] <= 5.0},
            })
    observed = all(item["core"]["target_passed"] and item["worker"]["target_passed"] for item in workloads)
    return {
        "workloads": workloads,
        "environment": environment,
        "formal_status": "passed" if environment["formal_environment"] and observed else "incomplete",
        "passed": bool(workloads) and environment["formal_environment"] and observed,
        "incomplete_reason": None if environment["formal_environment"] else "formal performance evidence requires Python 3.12 and locked dependencies",
    }


# Short public spelling for callers that do not need to name the parent issue.
run_validation = run_issue10_validation
