"""Manifest loading and tolerance comparisons for validation runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .core import analyze
from .models import AnalysisConfiguration, AnalysisRecord, AnalysisRegion, InputImage
from .oracle import circular_gaussian_oracle, scene_oracle
from .synthetic import SceneManifest, SyntheticScene, generate_scene


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

    selections = manifests or {
        "gaussian_circular": 0,
        "gaussian_elliptical_rotated": 0,
        "two_gaussian_multimodal": 0,
        "airy_sidelobe": 0,
        "background_gradient": 0,
        "hot_dead_pixels": 0,
        "low_snr_seeded": 0,
        "saturated_core": 0,
        "cropped_edge": 0,
    }
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


def run_low_snr_regression(seeds: range = range(32)) -> dict[str, Any]:
    """Aggregate deterministic low-SNR bias and quality-gate evidence."""

    errors: list[float] = []
    statuses: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    cases: list[dict[str, Any]] = []
    for seed in seeds:
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
        "seed_range": [seeds.start, seeds.stop - 1, seeds.step],
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
