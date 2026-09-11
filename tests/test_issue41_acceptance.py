from __future__ import annotations

from dataclasses import replace

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, FlowStatus, InputImage, MeasurementStatus, analyze
from spot_analyzer.synthetic import generate_scene


ROI = AnalysisRegion(64, 64, 128, 128)
BACKGROUND = AnalysisRegion(32, 64, 32, 128)


def run(scene_id: str, *, configuration: AnalysisConfiguration | None = None):
    scene = generate_scene(scene_id, seed=0)
    configuration = configuration or AnalysisConfiguration(region=ROI, background_region=BACKGROUND)
    return analyze(
        InputImage(
            scene.input_array,
            bit_depth=8,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
        ),
        configuration,
    )


def test_flow_status_is_separate_from_metric_quality_and_invalid_values_are_redacted() -> None:
    outcome = run("saturated_core", configuration=AnalysisConfiguration(
        region=ROI,
        background_region=BACKGROUND,
        rref_pixels=10.0,
    ))

    assert outcome.flow_status is FlowStatus.COMPUTED
    assert outcome.record is not None
    assert outcome.record.summary_status is MeasurementStatus.INVALID
    metric = outcome.record.metrics["gaussian_fwhm_major"]
    assert metric.status is MeasurementStatus.INVALID
    assert metric.reported_value is None
    assert outcome.record.reportable_metrics()["gaussian_fwhm_major"]["domains"]["pixel"]["value"] is None


def test_diagnostics_cover_low_snr_multipeak_and_truncation_with_stable_codes() -> None:
    _, low_snr = _record("low_snr_seeded", seed=7)
    assert low_snr.diagnostics["snr"] is not None
    assert any(code in low_snr.diagnostics["reasons"] for code in ("low_snr", "low_snr_caution"))

    _, multimodal = _record("two_gaussian_multimodal")
    assert "multiple_peaks" in multimodal.diagnostics["reasons"]
    assert multimodal.metrics["moment_d4sigma_major"].status is MeasurementStatus.CAUTION

    _, cropped = _record("cropped_edge")
    assert "window_truncated" in cropped.diagnostics["reasons"]
    assert cropped.metrics["gaussian_fwhm_major"].reported_value is None

    for record in (low_snr, multimodal, cropped):
        assert record.diagnostics["reasons"] == tuple(sorted(record.diagnostics["reasons"]))
        assert record.flow_status is FlowStatus.COMPUTED


def _record(scene_id: str, *, seed: int = 0):
    scene = generate_scene(scene_id, seed=seed)
    configuration = AnalysisConfiguration(region=ROI, background_region=BACKGROUND)
    if scene_id == "cropped_edge":
        configuration = replace(
            configuration,
            region=AnalysisRegion(0, 64, 64, 128),
            background_region=AnalysisRegion(64, 64, 32, 128),
        )
    outcome = analyze(
        InputImage(
            scene.input_array,
            bit_depth=8,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
        ),
        configuration,
    )
    assert outcome.record is not None
    return scene, outcome.record
