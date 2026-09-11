from __future__ import annotations

import numpy as np
import pytest

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, PreprocessingConfiguration, analyze
from spot_analyzer.synthetic import generate_scene


ROI = AnalysisRegion(64, 64, 128, 128)
BACKGROUND = AnalysisRegion(32, 64, 32, 128)


def image_with_bad_pixel() -> InputImage:
    scene = generate_scene("gaussian_circular", seed=9)
    data = np.array(scene.input_array, dtype=float, copy=True)
    data[128, 128] = np.nan
    return InputImage(
        data,
        bit_depth=8,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
    )


def test_explicit_advanced_branch_retains_standard_and_records_steps() -> None:
    preprocessing = PreprocessingConfiguration(
        advanced_processing_enabled=True,
        bad_pixel_policy="interpolate",
        filtering="gaussian",
        dpc="gradient",
    )
    outcome = analyze(
        image_with_bad_pixel(),
        AnalysisConfiguration(
            region=ROI,
            background_region=BACKGROUND,
            preprocessing=preprocessing,
        ),
    )

    assert outcome.record is not None
    diagnostics = outcome.record.diagnostics
    assert diagnostics["preprocessing_standard_branch"]["retained"] is True
    advanced = diagnostics["preprocessing_advanced_branch"]
    assert advanced["enabled"] is True
    assert advanced["version"] == "advanced-preprocessing-v1"
    assert advanced["steps"] == (
        "bad_pixel_interpolation-v1",
        "gaussian_filter-v1",
        "dpc_gradient-v1",
    )
    assert "sensitivity" in advanced
    assert "advanced_sensitivity_exceeded" in diagnostics["reasons"] or advanced["sensitivity_gate"] in {
        "passed",
        "caution",
        "invalid",
    }


def test_advanced_branch_requires_explicit_option() -> None:
    with pytest.raises(ValueError, match="explicit branch option"):
        PreprocessingConfiguration(advanced_processing_enabled=True)


def test_advanced_branch_does_not_upgrade_provisional_validity() -> None:
    scene = generate_scene("gaussian_circular", seed=3)
    outcome = analyze(
        InputImage(
            scene.input_array,
            bit_depth=8,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
        ),
        AnalysisConfiguration(
            region=ROI,
            background_region=BACKGROUND,
            preprocessing=PreprocessingConfiguration(
                advanced_processing_enabled=True,
                filtering="gaussian",
            ),
        ),
    )
    assert outcome.record is not None
    assert outcome.record.configuration.profile_validation == "provisional"
    assert all(metric.status.value != "valid" or "provisional_profile" in metric.reason_codes for metric in outcome.record.metrics.values())
