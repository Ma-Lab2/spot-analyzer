from __future__ import annotations

import math

import numpy as np
import pytest

import spot_analyzer.core as core_module
from spot_analyzer import AnalysisConfiguration, AnalysisRegion, FlowStatus, InputImage, MeasurementStatus, SpatialCalibration, analyze
from spot_analyzer.oracle import circular_gaussian_oracle, scene_oracle
from spot_analyzer.synthetic import generate_scene, manifest_json


SHAPE = (256, 256)
ROI = AnalysisRegion(64, 64, 128, 128)
BACKGROUND = AnalysisRegion(32, 64, 32, 128)


def configuration(
    *,
    region: AnalysisRegion = ROI,
    background_region: AnalysisRegion | None = BACKGROUND,
    **kwargs: object,
) -> AnalysisConfiguration:
    return AnalysisConfiguration(
        region=region,
        background_region=background_region,
        **kwargs,
    )


def run(scene_id: str, *, seed: int = 0, **kwargs: object):
    scene = generate_scene(scene_id, seed=seed)
    if scene_id == "cropped_edge":
        kwargs = {
            "region": AnalysisRegion(0, 64, 64, 128),
            "background_region": AnalysisRegion(64, 64, 32, 128),
            **kwargs,
        }
    outcome = analyze(InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), configuration(**kwargs))
    assert outcome.record is not None
    return scene, outcome.record


def test_scene_generation_is_deterministic_and_manifest_is_canonical() -> None:
    first = generate_scene("gaussian_circular", seed=12)
    second = generate_scene("gaussian_circular", seed=12)

    assert first.manifest.to_dict() == second.manifest.to_dict()
    assert np.array_equal(first.reference, second.reference)
    assert np.array_equal(first.input_array, second.input_array)
    assert manifest_json(first) == manifest_json(second)
    assert first.manifest.reference_sha256
    assert first.manifest.input_sha256


def test_circular_gaussian_matches_analytic_oracle_with_profile_cap() -> None:
    scene, record = run("gaussian_circular")
    expected = circular_gaussian_oracle(scene.manifest.parameters["width"])
    metrics = record.metrics

    assert record.diagnostics["quality_status_before_profile_cap"] == "valid"
    assert record.summary_status == MeasurementStatus.CAUTION
    assert metrics["gaussian_fwhm_major"].status == MeasurementStatus.CAUTION
    assert metrics["gaussian_fwhm_major"].reason_codes == ("provisional_profile",)
    assert math.isclose(metrics["gaussian_fwhm_major"].value, expected["fwhm"], rel_tol=0.05)
    assert math.isclose(metrics["gaussian_fwhm_minor"].value, expected["fwhm"], rel_tol=0.05)
    assert math.isclose(metrics["moment_d4sigma_major"].value, expected["d4sigma"], rel_tol=0.05)
    assert math.isclose(metrics["moment_d4sigma_minor"].value, expected["d4sigma"], rel_tol=0.05)
    assert 1.65 <= metrics["moment_d4sigma_major"].value / metrics["gaussian_fwhm_major"].value <= 1.75
    assert math.isclose(metrics["ee50"].value / metrics["gaussian_fwhm_major"].value, 0.5, abs_tol=0.02)
    assert math.isclose(record.diagnostics["center_xy"]["x"], 127.5, abs_tol=0.5)
    assert math.isclose(record.diagnostics["center_xy"]["y"], 127.5, abs_tol=0.5)


def test_rotated_elliptical_gaussian_matches_axes_and_angle() -> None:
    scene, record = run("gaussian_elliptical_rotated")
    expected = scene_oracle(scene.manifest.scene_id, scene.manifest.parameters, scene.reference, scene.manifest.center_xy)
    metrics = record.metrics

    assert math.isclose(metrics["gaussian_fwhm_major"].value, expected["fwhm_major"], rel_tol=0.05)
    assert math.isclose(metrics["gaussian_fwhm_minor"].value, expected["fwhm_minor"], rel_tol=0.05)
    angle = metrics["gaussian_angle"].value
    distance = abs((angle - expected["angle"] + 90.0) % 180.0 - 90.0)
    assert distance <= 2.0
    assert metrics["gaussian_fwhm_major"].status == MeasurementStatus.CAUTION


@pytest.mark.parametrize("seed", range(32))
def test_low_snr_seeded_repeats_and_applies_the_observed_snr_gate(seed: int) -> None:
    first_scene, first = run("low_snr_seeded", seed=seed)
    second_scene, second = run("low_snr_seeded", seed=seed)

    assert first_scene.manifest.input_sha256 == second_scene.manifest.input_sha256
    assert first.summary_status in {MeasurementStatus.CAUTION, MeasurementStatus.INVALID}
    assert first.summary_status == second.summary_status
    assert first.diagnostics["reasons"] == second.diagnostics["reasons"]
    assert first.diagnostics["snr"] == second.diagnostics["snr"]
    reasons = set(first.diagnostics["reasons"])
    snr = first.diagnostics["snr"]
    if snr is None:
        assert "background_noise_unavailable" in reasons
        assert first.metrics["ee50"].reported_value is None
    elif snr < 5:
        assert "low_snr" in reasons
        assert first.metrics["ee50"].reported_value is None
    elif snr < 10:
        assert "low_snr_caution" in reasons
        assert first.metrics["ee50"].status in {MeasurementStatus.CAUTION, MeasurementStatus.INVALID}
    else:
        assert "low_snr" not in reasons
        assert "low_snr_caution" not in reasons


def test_saturation_gates_affected_metrics_without_leaking_values() -> None:
    _, record = run("saturated_core", rref_pixels=10.0)

    assert "saturated_core" in record.diagnostics["reasons"]
    assert record.summary_status == MeasurementStatus.INVALID
    for key in ("gaussian_fwhm_major", "gaussian_fwhm_minor", "moment_d4sigma_major", "ee50", "ee80", "concentration_rref"):
        assert record.metrics[key].status == MeasurementStatus.INVALID
        assert record.metrics[key].reported_value is None


def test_multimodal_and_ring_scenes_report_diagnostics() -> None:
    _, multimodal = run("two_gaussian_multimodal")
    _, ring = run("airy_sidelobe")

    assert "multiple_peaks" in multimodal.diagnostics["reasons"]
    assert multimodal.metrics["peak_center"].status == MeasurementStatus.CAUTION
    assert multimodal.metrics["moment_d4sigma_major"].status == MeasurementStatus.CAUTION
    assert "multiple_peaks" in multimodal.metrics["moment_d4sigma_major"].reason_codes
    assert multimodal.diagnostics["multiple_peak_candidates"]
    assert all(candidate["support_pixels"] >= 9 for candidate in multimodal.diagnostics["multiple_peak_candidates"])
    assert all(not candidate["touches_boundary"] for candidate in multimodal.diagnostics["multiple_peak_candidates"])
    assert "ring_candidate" in ring.diagnostics["reasons"]
    assert "multiple_peaks" not in ring.diagnostics["reasons"]


def test_cropped_scene_records_window_truncation() -> None:
    _, record = run("cropped_edge")

    assert "window_truncated" in record.diagnostics["reasons"]
    assert record.metrics["gaussian_fwhm_major"].reported_value is None
    assert record.metrics["moment_d4sigma_major"].reported_value is None
    assert record.diagnostics["window_truncation_fraction"] <= 0.10
    assert record.metrics["ee50"].status == MeasurementStatus.CAUTION
    assert record.metrics["ee50"].reported_value is not None


def test_unconfirmed_region_is_structured_parameter_failure() -> None:
    scene = generate_scene("gaussian_circular")
    unconfirmed = AnalysisRegion(0, 0, SHAPE[1], SHAPE[0], confirmed=False)
    outcome = analyze(InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), AnalysisConfiguration(unconfirmed))

    assert outcome.record is None
    assert outcome.flow_status.value == "parameter_invalid"
    assert outcome.diagnostics[0]["code"] == "analysis_region_unconfirmed"


def test_background_region_is_required_and_structured() -> None:
    scene = generate_scene("gaussian_circular")
    outcome = analyze(InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), AnalysisConfiguration(ROI))

    assert outcome.record is None
    assert outcome.flow_status.value == "parameter_invalid"
    assert outcome.diagnostics[0]["code"] == "background_region_unconfirmed"


def test_reanalysis_gets_new_record_id_but_same_fingerprint() -> None:
    first_scene, first = run("gaussian_circular")
    _, second = run("gaussian_circular")

    assert first_scene.manifest.input_sha256
    assert first.record_id != second.record_id
    assert first.analysis_fingerprint == second.analysis_fingerprint


def test_profile_validation_cannot_be_promoted_by_request_configuration() -> None:
    with pytest.raises(ValueError, match="locked to provisional"):
        AnalysisConfiguration(ROI, profile_validation="validated")


def test_blank_input_does_not_report_a_fake_peak_center() -> None:
    scene = generate_scene("gaussian_circular")
    blank = InputImage(
        np.zeros_like(scene.input_array),
        bit_depth=8,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
    )
    _, record = run("gaussian_circular")
    outcome = analyze(blank, configuration())

    assert outcome.record is not None
    assert outcome.record.metrics["peak_center"].reported_value is None
    assert outcome.record.metrics["peak_center"].status == MeasurementStatus.INVALID


def test_bad_pixel_mask_is_shared_and_traceable() -> None:
    scene = generate_scene("hot_dead_pixels")
    config = configuration(bad_pixel_coordinates=((135, 127), (120, 120)))
    outcome = analyze(InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), config)

    assert outcome.record is not None
    record = outcome.record
    diagnostics = record.diagnostics
    assert diagnostics["bad_pixel_mask_version"] == "bad-pixel-mask-v1"
    assert diagnostics["bad_pixel_count"] == 2
    assert diagnostics["measurement_invalid_pixels"] == 2
    assert diagnostics["core_masked_pixels"] >= 1
    assert "bad_pixels_present" in diagnostics["reasons"]
    assert "bad_pixel_in_core" in diagnostics["reasons"]
    assert diagnostics["fit"]["fit_valid_pixels"] == diagnostics["fit"]["residual_valid_pixels"]
    assert diagnostics["fit"]["fit_mask_hash"] == diagnostics["fit"]["residual_mask_hash"]
    assert diagnostics["measurement_mask_hash"].startswith("sha256-")


@pytest.mark.parametrize(
    ("masked_count", "expected_reason"),
    ((25, "core_support_caution"), (100, "core_support_insufficient")),
)
def test_core_valid_fraction_uses_profile_thresholds(masked_count: int, expected_reason: str) -> None:
    scene = generate_scene("gaussian_circular")
    coordinates = [
        (x, y)
        for y in range(116, 140)
        for x in range(116, 140)
        if 4.0 <= math.hypot(x - 127.5, y - 127.5) <= 11.0
    ][:masked_count]
    outcome = analyze(
        InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        configuration(bad_pixel_coordinates=tuple(coordinates)),
    )

    assert outcome.record is not None
    assert expected_reason in outcome.record.diagnostics["reasons"]
    if expected_reason == "core_support_caution":
        assert 0.8 <= outcome.record.diagnostics["core_valid_fraction"] < 0.95
    else:
        assert outcome.record.diagnostics["core_valid_fraction"] < 0.8
        assert outcome.record.metrics["ee50"].reported_value is None


def test_bad_pixel_outside_image_is_a_structured_parameter_failure() -> None:
    scene = generate_scene("gaussian_circular")
    outcome = analyze(
        InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        configuration(bad_pixel_coordinates=((999, 3),)),
    )

    assert outcome.record is None
    assert outcome.flow_status == FlowStatus.PARAMETER_INVALID
    assert outcome.diagnostics[0]["code"] == "bad_pixel_out_of_bounds"


def test_memory_input_requires_explicit_intensity_semantic_confirmation() -> None:
    scene = generate_scene("gaussian_circular")

    outcome = analyze(InputImage(scene.input_array), configuration())

    assert outcome.record is None
    assert outcome.flow_status == FlowStatus.INPUT_INVALID
    assert outcome.diagnostics[0]["code"] == "input_semantic_invalid"


def test_background_region_must_not_overlap_analysis_region() -> None:
    scene = generate_scene("gaussian_circular")
    outcome = analyze(
        InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        AnalysisConfiguration(
            region=ROI,
            background_region=AnalysisRegion(64, 64, 16, 16),
        ),
    )

    assert outcome.record is None
    assert outcome.flow_status == FlowStatus.PARAMETER_INVALID
    assert outcome.diagnostics[0]["code"] == "background_region_overlaps_analysis_region"


def test_reportable_length_metrics_have_independently_gated_domains() -> None:
    _, missing = run("gaussian_circular")
    scene = generate_scene("gaussian_circular")
    calibrated = analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        configuration(
            calibration=SpatialCalibration(
                x_unit_per_pixel=0.1,
                y_unit_per_pixel=0.1,
                physical_unit="um",
                source="synthetic-manifest",
                confirmation="confirmed",
            )
        ),
    )

    assert calibrated.record is not None
    missing_domains = missing.reportable_metrics()["gaussian_fwhm_major"]["domains"]
    assert missing_domains["physical"]["value"] is None
    assert missing_domains["physical"]["status"] == "unavailable"
    domains = calibrated.record.reportable_metrics()["gaussian_fwhm_major"]["domains"]
    assert math.isclose(domains["physical"]["value"], 0.1 * domains["pixel"]["value"])
    assert domains["physical"]["unit"] == "um"
    anisotropic = analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        configuration(
            calibration=SpatialCalibration(
                x_unit_per_pixel=0.1,
                y_unit_per_pixel=0.2,
                physical_unit="um",
                source="synthetic-manifest",
                confirmation="confirmed",
            )
        ),
    )
    assert anisotropic.record is not None
    anisotropic_major = anisotropic.record.reportable_metrics()["gaussian_fwhm_major"]["domains"]
    anisotropic_minor = anisotropic.record.reportable_metrics()["gaussian_fwhm_minor"]["domains"]
    assert math.isclose(anisotropic_major["physical"]["value"], 0.2 * anisotropic_major["pixel"]["value"], rel_tol=0.01)
    assert math.isclose(anisotropic_minor["physical"]["value"], 0.1 * anisotropic_minor["pixel"]["value"], rel_tol=0.01)
    anisotropic_ellipticity = anisotropic.record.reportable_metrics()["gaussian_ellipticity"]["domains"]
    assert math.isclose(anisotropic_ellipticity["physical"]["value"], 2.0, rel_tol=0.02)
    assert anisotropic_ellipticity["physical"]["unit"] == "fraction"
    assert "quality_diagnostics" in calibrated.record.metrics


def test_gaussian_fit_failure_returns_analysis_failed_without_record(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = generate_scene("gaussian_circular")

    def fail_fit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced optimizer failure")

    monkeypatch.setattr(core_module, "least_squares", fail_fit)
    outcome = analyze(
        InputImage(scene.input_array, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        configuration(),
    )

    assert outcome.record is None
    assert outcome.flow_status == FlowStatus.ANALYSIS_FAILED
    assert outcome.diagnostics[0]["code"] == "analysis_failed"
