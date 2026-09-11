from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer import validation
from spot_analyzer.core import _propagated_fwhm_uncertainty
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.validation import (
    load_manifest,
    run_low_snr_regression,
    run_issue10_validation,
    run_manifest_regression,
    run_performance_baseline,
    run_real_fixture_validation,
    run_report_validation,
)


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
    assert first_outcome.record.diagnostics["fit"]["fit_covariance"] is not None
    assert first_outcome.record.diagnostics["fit_uncertainty"]["available"] is True


def test_fwhm_uncertainty_follows_canonical_axes_and_physical_covariance() -> None:
    parameters = [100.0, 0.0, 10.0, 11.0, 2.0, 4.0, math.radians(30.0)]
    covariance = [[0.0 for _ in range(7)] for _ in range(7)]
    covariance[4][4] = 0.8**2
    covariance[5][5] = 0.2**2
    covariance[6][6] = math.radians(5.0) ** 2
    covariance[5][6] = covariance[6][5] = 0.01

    uncertainty = _propagated_fwhm_uncertainty(
        parameters,
        covariance,
        0.1,
        0.2,
        major_sigma_index=5,
        minor_sigma_index=4,
    )

    factor = math.sqrt(8.0 * math.log(2.0))
    assert uncertainty["pixel_major"] == pytest.approx(factor * 0.2)
    assert uncertainty["pixel_minor"] == pytest.approx(factor * 0.8)
    assert uncertainty["physical_major"] > 0.0
    assert uncertainty["physical_minor"] > 0.0


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


def test_performance_baseline_reports_structured_workload_and_formal_status() -> None:
    report = run_performance_baseline(sizes=(256,), repetitions=1)

    workload = report["workloads"][0]
    assert workload["image_size"] == {"width": 256, "height": 256}
    assert workload["repetitions"] == 1
    assert workload["warmup"] == {
        "runs": 1,
        "timed": False,
        "modes": {"core": "unmeasured_analyze", "worker": "unmeasured_process_png_analysis_derived_write"},
    }
    for kind in ("core", "worker"):
        assert len(workload[kind]["runs_seconds"]) == 1
        assert workload[kind]["p50_seconds"] >= 0
        assert workload[kind]["p95_seconds"] >= 0
        assert workload[kind]["max_seconds"] >= 0
    assert report["formal_status"] == "incomplete"
    assert report["incomplete_reason"] == "formal performance workload contract was not used"
    environment = report["environment"]
    assert environment["formal_environment"] is (
        environment["formal_python"] and all(environment["formal_dependencies"].values())
    )


def test_nonformal_performance_workload_is_incomplete(monkeypatch) -> None:
    scene = SimpleNamespace(
        input_array=validation.np.zeros((2, 2), dtype=validation.np.uint8),
        manifest=SimpleNamespace(bit_depth=8),
    )
    monkeypatch.setattr(validation, "_performance_environment", lambda: {"formal_environment": True})
    monkeypatch.setattr(validation, "_benchmark_scene", lambda size: scene)
    monkeypatch.setattr(validation, "_benchmark_configuration", lambda size: object())
    monkeypatch.setattr(validation, "analyze", lambda *args, **kwargs: SimpleNamespace(record=object()))
    monkeypatch.setattr(validation, "_worker_request", lambda *args, **kwargs: {})
    monkeypatch.setattr("spot_analyzer.worker.run_worker_process", lambda request: [{"kind": "completed"}])
    clock = iter(float(index) for index in range(1000))
    monkeypatch.setattr(validation.time, "perf_counter", lambda: next(clock))
    result = validation.run_performance_baseline(sizes=(128, 512), repetitions=10)

    assert result["formal_workload"] is False
    assert result["formal_status"] == "incomplete"
    assert result["passed"] is False
    assert result["formal_workload_contract"]["violations"] == [
        "formal_workload_requires_sizes_256_and_1024"
    ]


def test_empty_performance_workload_is_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(validation, "_performance_environment", lambda: {"formal_environment": True})
    result = validation.run_performance_baseline(sizes=(), repetitions=10)

    assert result["workloads"] == []
    assert result["formal_workload"] is False
    assert result["formal_status"] == "incomplete"
    assert result["passed"] is False


def _stub_performance_measurements(
    monkeypatch,
    *,
    formal_environment: bool,
    core_seconds: float,
    worker_seconds: float,
) -> None:
    scene = SimpleNamespace(
        input_array=validation.np.zeros((2, 2), dtype=validation.np.uint8),
        manifest=SimpleNamespace(bit_depth=8),
    )
    monkeypatch.setattr(
        validation,
        "_performance_environment",
        lambda: {"formal_environment": formal_environment},
    )
    monkeypatch.setattr(validation, "_benchmark_scene", lambda size: scene)
    monkeypatch.setattr(validation, "_benchmark_configuration", lambda size: object())
    monkeypatch.setattr(validation, "analyze", lambda *args, **kwargs: SimpleNamespace(record=object()))
    monkeypatch.setattr(validation, "_worker_request", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "spot_analyzer.worker.run_worker_process",
        lambda request: [{"kind": "completed"}],
    )
    samples = []
    for _ in range(20):
        samples.extend((0.0, core_seconds))
        samples.extend((10.0, 10.0 + worker_seconds))
    monkeypatch.setattr(validation.time, "perf_counter", lambda: samples.pop(0))


def test_formal_performance_target_violation_fails_section(monkeypatch) -> None:
    _stub_performance_measurements(
        monkeypatch,
        formal_environment=True,
        core_seconds=2.01,
        worker_seconds=5.01,
    )

    result = validation.run_performance_baseline(sizes=(256, 1024), repetitions=10)
    section = validation._run_section("performance", lambda: result)

    assert result["formal_status"] == "failed"
    assert result["passed"] is False
    assert result["workloads"][1]["core"]["target_passed"] is False
    assert result["workloads"][1]["worker"]["target_passed"] is False
    assert section["status"] == "failed"


def test_unavailable_performance_environment_remains_incomplete(monkeypatch) -> None:
    _stub_performance_measurements(
        monkeypatch,
        formal_environment=False,
        core_seconds=2.01,
        worker_seconds=5.01,
    )

    result = validation.run_performance_baseline(sizes=(1024,), repetitions=1)
    section = validation._run_section("performance", lambda: result)

    assert result["formal_status"] == "incomplete"
    assert result["passed"] is False
    assert result["workloads"][0]["core"]["target_passed"] is False
    assert result["workloads"][0]["worker"]["target_passed"] is False
    assert section["status"] == "incomplete"


def test_performance_warmup_is_not_in_hot_samples(monkeypatch) -> None:
    calls = 0
    original_analyze = validation.analyze

    def counted_analyze(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(validation, "analyze", counted_analyze)
    report = validation.run_performance_baseline(sizes=(256,), repetitions=1)

    assert calls == 2
    assert len(report["workloads"][0]["core"]["runs_seconds"]) == 1
    assert report["workloads"][0]["warmup"]["timed"] is False


def test_issue10_validation_runs_default_performance_workloads(monkeypatch) -> None:
    seen = {}

    def fake_performance_baseline(**kwargs):
        seen.update(kwargs)
        return {
            "formal_status": "incomplete",
            "passed": False,
            "workloads": [
                {"image_size": {"width": size, "height": size}}
                for size in kwargs["sizes"]
            ],
            "incomplete_reason": "formal performance evidence requires Python 3.12 and locked dependencies",
        }

    monkeypatch.setattr(validation, "run_performance_baseline", fake_performance_baseline)
    report = run_issue10_validation(
        manifests={},
        seeds=(),
        real_manifest_path="missing-real-fixtures.json",
        golden_vector_path="missing-golden-vectors.json",
    )

    assert seen == {"sizes": (256, 1024), "repetitions": 10}
    performance = report["sections"]["performance"]
    assert performance["status"] == "incomplete"
    assert [item["image_size"] for item in performance["result"]["workloads"]] == [
        {"width": 256, "height": 256},
        {"width": 1024, "height": 1024},
    ]
    assert "performance" in report["incomplete_items"]


def test_issue10_validation_preserves_custom_performance_configuration(monkeypatch) -> None:
    seen = {}

    def fake_performance_baseline(**kwargs):
        seen.update(kwargs)
        return {"status": "failed", "passed": False, "workloads": []}

    monkeypatch.setattr(validation, "run_performance_baseline", fake_performance_baseline)
    report = run_issue10_validation(
        manifests={},
        seeds=(),
        real_manifest_path="missing-real-fixtures.json",
        golden_vector_path="missing-golden-vectors.json",
        performance_sizes=(128, 512),
        performance_repetitions=1,
    )

    assert seen == {"sizes": (128, 512), "repetitions": 1}
    assert report["sections"]["performance"]["status"] == "failed"
    assert report["overall_status"] == "failed"


def test_real_fixture_validation_marks_missing_root_incomplete(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "real-fixture-manifest-v1", "root": str(tmp_path / "missing"), "fixtures": []}), encoding="utf-8")

    report = run_real_fixture_validation(manifest)

    assert report["status"] == "incomplete"
    assert report["passed"] is False
    assert report["incomplete_reason"] == "fixture_root_unavailable"
    assert report["behavioral_evidence_only"] is True


def test_real_fixture_validation_marks_empty_manifest_incomplete(tmp_path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "real-fixture-manifest-v1",
        "root": str(root),
        "fixtures": [],
    }), encoding="utf-8")

    report = run_real_fixture_validation(manifest)

    assert report["status"] == "incomplete"
    assert report["passed"] is False
    assert report["fixture_count"] == 0
    assert report["incomplete_reason"] == "fixture_manifest_empty"
    assert report["behavioral_evidence_only"] is True


def test_real_fixture_validation_rejects_placeholder_metadata(tmp_path) -> None:
    from PIL import Image

    root = tmp_path / "fixtures"
    root.mkdir()
    path = root / "sample.png"
    y, x = np.mgrid[:256, :256]
    pixels = (10 + 200 * np.exp(-((x - 127.5) ** 2 + (y - 127.5) ** 2) / (2 * 12 ** 2))).astype(np.uint8)
    Image.fromarray(pixels).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "real-fixture-manifest-v1",
        "root": str(root),
        "fixtures": [{
            "relative_path": path.name,
            "sha256": digest,
            "kind": "gray_input",
            "analysis_region": {"x": 32, "y": 32, "width": 192, "height": 192, "confirmed": True},
            "background_region": {"x": 0, "y": 0, "width": 32, "height": 32, "confirmed": True},
            "provenance": {"source_asset": "未记录（历史 PNG）", "source_snapshot": "snapshot-1"},
            "acquisition": {"instrument": "camera-1", "acquired_at": "2026-01-01T00:00:00Z", "metadata_version": "fixture-metadata-v1"},
        }],
    }), encoding="utf-8")

    report = run_real_fixture_validation(manifest)
    fixture = report["fixtures"][0]

    assert report["status"] == "incomplete"
    assert report["passed"] is False
    assert fixture["status"] == "incomplete"
    assert fixture["metadata_validation"] == "incomplete"
    assert fixture["metadata_issues"] == ["provenance_source_asset_unknown"]
    assert "provenance_source_asset_unknown" in fixture["reason_codes"]
    assert fixture["behavioral_validation"] == "passed"
    assert fixture["behavioral_evidence_only"] is True


def test_real_fixture_validation_rejects_placeholder_acquisition_metadata(tmp_path) -> None:
    from PIL import Image

    root = tmp_path / "fixtures"
    root.mkdir()
    path = root / "sample.png"
    y, x = np.mgrid[:256, :256]
    pixels = (10 + 200 * np.exp(-((x - 127.5) ** 2 + (y - 127.5) ** 2) / (2 * 12 ** 2))).astype(np.uint8)
    Image.fromarray(pixels).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "real-fixture-manifest-v1",
        "root": str(root),
        "fixtures": [{
            "relative_path": path.name,
            "sha256": digest,
            "kind": "gray_input",
            "analysis_region": {"x": 32, "y": 32, "width": 192, "height": 192, "confirmed": True},
            "background_region": {"x": 0, "y": 0, "width": 32, "height": 32, "confirmed": True},
            "provenance": {"source_asset": "sample.png", "source_snapshot": "snapshot-1"},
            "acquisition": {"instrument": "unknown", "acquired_at": "2026-01-01T00:00:00Z", "metadata_version": "fixture-metadata-v1"},
        }],
    }), encoding="utf-8")

    report = run_real_fixture_validation(manifest)
    fixture = report["fixtures"][0]

    assert report["status"] == "incomplete"
    assert fixture["metadata_issues"] == ["acquisition_instrument_unknown"]
    assert "acquisition_instrument_unknown" in fixture["reason_codes"]
    assert fixture["behavioral_validation"] == "passed"


def test_real_fixture_validation_rejects_hash_mismatch(tmp_path) -> None:
    from PIL import Image

    root = tmp_path / "fixtures"
    root.mkdir()
    Image.new("L", (8, 8), color=0).save(root / "sample.png")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "real-fixture-manifest-v1",
        "root": str(root),
        "fixtures": [{
            "relative_path": "sample.png",
            "sha256": "0" * 64,
            "kind": "gray_input",
            "analysis_region": {"x": 0, "y": 0, "width": 8, "height": 8},
        }],
    }), encoding="utf-8")

    report = run_real_fixture_validation(manifest)

    assert report["status"] == "failed"
    assert report["fixtures"][0]["status"] == "failed"
    assert "input_hash_mismatch" in report["fixtures"][0]["reason_codes"]


def test_real_fixture_validation_records_manifest_identity_and_repeatability() -> None:
    report = run_real_fixture_validation()

    assert report["manifest_status"] == "loaded"
    assert report["manifest_sha256"].startswith("sha256-")
    assert report["behavioral_evidence_only"] is True
    assert report["absolute_physical_accuracy_claim"] is False
    assert "absolute_physical_accuracy" in report["evidence_scope"]["excluded_claims"]
    limitation_codes = {item["code"] for item in report["bounded_limitations"]}
    assert limitation_codes == {
        "independent_physical_ground_truth_absent",
        "historical_acquisition_metadata_unrecoverable",
        "rgb_acquisition_timezone_unknown",
        "png_color_management_matrix_absent",
        "acquisition_condition_matrix_absent",
    }
    grayscale = [item for item in report["fixtures"] if item["kind"] != "rgb_display_excluded"]
    assert all(item["behavioral_validation"] == "passed" for item in grayscale)
    assert all(item["repeatability_passed"] is True for item in grayscale)
    assert all(item["metadata_validation"] == "incomplete" for item in grayscale)
    assert all(item["color_management"]["metadata_present"] is False for item in report["fixtures"])
    rgb = next(item for item in report["fixtures"] if item["kind"] == "rgb_display_excluded")
    assert rgb["metadata_validation"] == "passed"
    assert rgb["manifest_metadata"]["acquisition"]["metadata_version"] == "DTLC V1.00"
    assert report["status"] == "incomplete"
