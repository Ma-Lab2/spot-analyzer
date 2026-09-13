from __future__ import annotations

import numpy as np

from spot_analyzer import (
    AnalysisConfiguration,
    FocalSpotDetectionProfile,
    InputImage,
    analyze,
    propose_auto_analysis,
)


def image(data: np.ndarray) -> InputImage:
    return InputImage(
        data,
        bit_depth=16,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
    )


def gaussian(shape: tuple[int, int], center: tuple[float, float], amplitude: float = 200.0, sigma: float = 7.0) -> np.ndarray:
    y, x = np.indices(shape, dtype=float)
    return 10.0 + amplitude * np.exp(-((x - center[0]) ** 2 + (y - center[1]) ** 2) / (2 * sigma**2))


def test_single_peak_uses_integrated_energy_and_adaptive_in_bounds_roi() -> None:
    proposal = propose_auto_analysis(image(gaussian((128, 128), (71.0, 55.0))))

    assert proposal.candidate is not None
    assert abs(proposal.candidate.x - 71.0) < 0.2
    assert abs(proposal.candidate.y - 55.0) < 0.2
    assert proposal.diagnostics["selection_reason"] == "largest_integrated_energy"
    assert proposal.region.x >= 0 and proposal.region.y >= 0
    assert proposal.region.x + proposal.region.width <= 128
    assert proposal.region.y + proposal.region.height <= 128
    assert proposal.region.width > proposal.candidate.bounds.width
    assert proposal.background_region is not None
    assert not _overlap(proposal.region, proposal.background_region)


def test_isolated_hot_pixel_is_excluded_before_energy_selection() -> None:
    data = gaussian((128, 128), (64.0, 64.0))
    data[8, 9] = 50000.0

    proposal = propose_auto_analysis(image(data))

    assert proposal.candidate is not None
    assert abs(proposal.candidate.x - 64.0) < 0.2
    assert abs(proposal.candidate.y - 64.0) < 0.2
    assert proposal.diagnostics["isolated_hot_pixel_count"] == 1
    assert proposal.candidate.isolated_hot_pixels_excluded == 1
    assert "automatic_isolated_hot_pixels_excluded" in proposal.diagnostics["reasons"]


def test_near_equal_peaks_select_largest_integrated_energy_and_keep_candidates() -> None:
    data = np.full((128, 128), 10.0)
    data += gaussian((128, 128), (42.0, 64.0), 200.0, 6.0) - 10.0
    data += gaussian((128, 128), (86.0, 64.0), 195.0, 6.0) - 10.0

    first = propose_auto_analysis(image(data))
    second = propose_auto_analysis(image(data))

    assert first.candidate is not None
    assert len(first.candidates) == 2
    assert abs(first.candidate.x - 42.0) < 0.2
    assert first.diagnostics["selected_energy_fraction"] < 0.6
    assert "automatic_multiple_candidates" in first.diagnostics["reasons"]
    assert first.diagnostics == second.diagnostics

    record = analyze(image(data), AnalysisConfiguration(None)).record
    assert record is not None
    assert "multiple_peaks" in record.diagnostics["reasons"]
    assert record.diagnostics["candidate_count"] == 2


def test_low_signal_uses_center_fallback_without_fabricating_candidate() -> None:
    proposal = propose_auto_analysis(image(np.full((64, 80), 10.0)))

    assert proposal.candidate is None
    assert proposal.fallback_used
    assert proposal.diagnostics["selection_reason"] == "no_supported_signal_center_fallback"
    assert proposal.region == type(proposal.region)(16, 8, 48, 48)
    assert "automatic_center_fallback" in proposal.diagnostics["reasons"]


def test_boundary_candidate_is_clamped_and_marked_for_truncation() -> None:
    proposal = propose_auto_analysis(image(gaussian((128, 128), (3.0, 64.0))))

    assert proposal.candidate is not None
    assert proposal.candidate.touches_boundary
    assert proposal.region.x == 0
    assert proposal.region.x + proposal.region.width <= 128
    assert "automatic_candidate_touches_boundary" in proposal.diagnostics["reasons"]


def test_background_protection_excludes_signal_and_reports_support() -> None:
    data = gaussian((128, 128), (64.0, 64.0))
    # This contaminant is outside the selected spot but near a possible
    # background strip; protection must be explicit and deterministic.
    data[32:37, 20:25] += 1000.0
    profile = FocalSpotDetectionProfile(background_protection_dilation_pixels=3)
    proposal = propose_auto_analysis(image(data), profile=profile)

    assert proposal.background_region is not None
    assert not _overlap(proposal.region, proposal.background_region)
    assert proposal.diagnostics["background_support_status"] == "supported"
    assert proposal.diagnostics["background_support_pixels"] >= profile.background_min_support_pixels
    assert proposal.diagnostics["background_protection_pixels"] > 0
    assert proposal.diagnostics == propose_auto_analysis(image(data), profile=profile).diagnostics


def test_background_support_shortage_is_structured_without_full_image_fallback() -> None:
    profile = FocalSpotDetectionProfile(
        roi_min_width_pixels=32,
        roi_min_height_pixels=32,
        background_min_support_pixels=500,
    )
    proposal = propose_auto_analysis(image(np.full((40, 40), 10.0)), profile=profile)

    assert proposal.background_region is None
    assert proposal.diagnostics["background_support_status"] == "insufficient"
    assert "automatic_background_support_insufficient" in proposal.diagnostics["reasons"]


def _overlap(first, second) -> bool:
    if second is None:
        return False
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )
