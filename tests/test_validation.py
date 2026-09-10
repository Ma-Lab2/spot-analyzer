from __future__ import annotations

import json

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.validation import load_manifest, run_low_snr_regression, run_manifest_regression


def test_manifest_loader_round_trips_scene_manifest(tmp_path) -> None:
    scene = generate_scene("gaussian_circular", seed=4)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(scene.manifest.to_dict()), encoding="utf-8")

    loaded = load_manifest(path)

    assert loaded == [scene.manifest]


def test_manifest_regression_returns_structured_results() -> None:
    report = run_manifest_regression({"gaussian_circular": 0})

    assert report["generator_version"] == "synthetic-scenes-v1"
    result = report["results"][0]
    assert result["flow_status"] == "computed"
    assert result["summary_status"] == "caution"
    assert result["passed"] is True
    assert result["comparisons_passed"] is True
    assert all(comparison["passed"] for comparison in result["comparisons"])


def test_manifest_regression_uses_diagnostic_policy_for_perturbed_scenes() -> None:
    report = run_manifest_regression({"airy_sidelobe": 0})

    result = report["results"][0]
    assert result["comparison_policy"] == "diagnostic"
    assert result["comparisons"] == []
    assert result["required_reason_codes_passed"] is True
    assert result["status_expectation_passed"] is True
    assert result["passed"] is True


def test_complete_manifest_matrix_passes_its_declared_evidence_policy() -> None:
    report = run_manifest_regression()

    assert len(report["results"]) == 9
    assert all(result["metric_gates_passed"] for result in report["results"])
    assert all(result["passed"] for result in report["results"])


def test_fingerprint_uses_rfc8785_and_records_canonicalizer_provenance() -> None:
    scene = generate_scene("gaussian_circular", seed=7)
    first = InputImage(
        scene.input_array,
        bit_depth=8,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
        metadata={"gain": 2, "exposure": 10},
    )
    second = InputImage(
        scene.input_array,
        bit_depth=8,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
        metadata={"exposure": 10, "gain": 2},
    )
    configuration = AnalysisConfiguration(
        AnalysisRegion(64, 64, 128, 128),
        background_region=AnalysisRegion(32, 64, 32, 128),
    )

    first_outcome = analyze(first, configuration)
    second_outcome = analyze(second, configuration)

    assert first_outcome.record is not None
    assert second_outcome.record is not None
    assert first_outcome.record.analysis_fingerprint == second_outcome.record.analysis_fingerprint
    assert first_outcome.record.diagnostics["canonicalizer_version"] == "rfc8785-python-0.1.4"


def test_low_snr_aggregate_records_bias_and_gate_distribution() -> None:
    report = run_low_snr_regression()

    assert report["case_count"] == 32
    assert report["passed"] is True
    assert sum(report["status_distribution"].values()) == 32
    assert report["reason_code_distribution"]["low_snr"] > 0
    assert report["reason_code_distribution"]["low_snr_caution"] > 0
    assert report["bias"]["available_case_count"] > 0
    assert report["bias"]["median_error"] is not None
    assert report["bias"]["mad_error"] is not None
    assert report["bias"]["p95_absolute_error"] is not None
    severe_cases = [case for case in report["cases"] if case["snr"] is None or case["snr"] < 5]
    assert severe_cases
    assert all(case["quantitative_values_gated"] for case in severe_cases)
