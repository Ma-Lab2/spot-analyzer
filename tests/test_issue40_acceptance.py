from __future__ import annotations

import math

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, MeasurementStatus, SpatialCalibration, analyze
from spot_analyzer.synthetic import generate_scene


ROI = AnalysisRegion(64, 64, 128, 128)
BACKGROUND = AnalysisRegion(32, 64, 32, 128)


def _run():
    scene = generate_scene("gaussian_elliptical_rotated", seed=17)
    configuration = AnalysisConfiguration(
        ROI,
        background_region=BACKGROUND,
        calibration=SpatialCalibration(
            x_unit_per_pixel=0.25,
            y_unit_per_pixel=0.5,
            physical_unit="mm",
            source="issue-40-test",
            confirmation="confirmed",
        ),
    )
    image = InputImage(
        scene.input_array,
        bit_depth=8,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
    )
    outcome = analyze(image, configuration)
    assert outcome.record is not None
    return outcome.record


def test_issue40_profile_metrics_provenance_and_domains() -> None:
    record = _run()
    expected = {
        "moment_d4sigma_major",
        "moment_d4sigma_minor",
        "moment_angle",
        "gaussian_fwhm_major",
        "gaussian_fwhm_minor",
        "gaussian_ellipticity",
        "gaussian_angle",
        "ee50",
        "ee80",
        "concentration_rref",
        "fit_residual",
        "peak_center",
    }
    assert expected <= set(record.metrics)
    assert record.configuration.standard_profile == "standard-profile-v1"
    assert record.configuration.quality_profile == "quality-profile-v1"
    assert record.configuration.profile_validation == "provisional"
    assert record.diagnostics["quality_status_before_profile_cap"] == "valid"
    assert record.summary_status == MeasurementStatus.CAUTION
    assert "provisional_profile" in record.metrics["gaussian_fwhm_major"].reason_codes

    for name in expected:
        metric = record.metrics[name]
        assert metric.method_version
        assert metric.unit
        assert isinstance(metric.reason_codes, tuple)

    reportable = record.reportable_metrics()
    fwhm = reportable["gaussian_fwhm_major"]["domains"]
    assert fwhm["pixel"]["unit"] == "px"
    assert fwhm["physical"]["unit"] == "mm"
    assert fwhm["physical"]["value"] is not None
    assert fwhm["physical"]["value"] != fwhm["pixel"]["value"]


def test_issue40_repeat_is_deterministic_except_record_identity() -> None:
    first = _run()
    second = _run()
    assert first.record_id != second.record_id
    assert first.analysis_fingerprint == second.analysis_fingerprint
    assert first.summary_status == second.summary_status
    assert first.diagnostics["reasons"] == second.diagnostics["reasons"]
    assert {
        key: metric.value for key, metric in first.metrics.items()
    } == {
        key: metric.value for key, metric in second.metrics.items()
    }
