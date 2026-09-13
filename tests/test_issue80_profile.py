from __future__ import annotations

import json
from pathlib import Path

import pytest

from spot_analyzer import (
    AnalysisConfiguration,
    AnalysisModel,
    AnalysisRegion,
    InputImage,
    PreprocessingConfiguration,
    SpatialCalibration,
    advanced_settings_status,
    default_model_values,
    default_preprocessing_values,
    get_analysis_profile,
    validate_advanced_settings,
)
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.worker import _configuration


ROOT = Path(__file__).parents[1]


def test_profile_json_is_authoritative_for_python_default_objects() -> None:
    profile = get_analysis_profile()
    assert profile["standard_profile"] == "standard-profile-v1"
    assert profile["profile_validation"] == "provisional"
    assert default_preprocessing_values() == profile["preprocessing"]
    assert default_model_values() == profile["model"]
    assert PreprocessingConfiguration().__dict__ == profile["preprocessing"]
    assert AnalysisModel().__dict__ == profile["model"]


def test_partial_request_is_expanded_by_profile_without_wpf_defaults() -> None:
    configuration = _configuration(
        {
            "region": {"x": 4, "y": 4, "width": 8, "height": 8, "confirmed": True},
            "background_region": {"x": 0, "y": 0, "width": 2, "height": 8, "confirmed": True},
            "calibration": {"confirmation": "missing", "x_unit_per_pixel": None, "y_unit_per_pixel": None, "physical_unit": None, "source": None},
            "preprocessing": None,
            "model": None,
        }
    )
    assert configuration.standard_profile == "standard-profile-v1"
    assert configuration.preprocessing == PreprocessingConfiguration()
    assert configuration.model == AnalysisModel()


def test_profile_request_can_use_automatic_external_background_selection() -> None:
    configuration = _configuration(
        {
            "region": {"x": 8, "y": 8, "width": 16, "height": 16, "confirmed": True},
            "background_region": None,
            "calibration": {"confirmation": "missing", "x_unit_per_pixel": None, "y_unit_per_pixel": None, "physical_unit": None, "source": None},
            "preprocessing": None,
            "model": None,
            "automatic_background": True,
        }
    )
    assert configuration.automatic_background is True


def test_advanced_settings_are_allow_list_validated_and_resettable() -> None:
    assert advanced_settings_status(None)["deviated"] is False
    assert advanced_settings_status({"filtering": "gaussian"})["changed_fields"] == ["filtering"]
    assert validate_advanced_settings({"filtering": "gaussian", "dpc": "none"}) == {
        "filtering": "gaussian", "dpc": "none"
    }
    with pytest.raises(ValueError, match="不支持的高级设置"):
        validate_advanced_settings({"snr_invalid_threshold": 1})
    with pytest.raises(ValueError, match="必须在"):
        validate_advanced_settings({"advanced_filter_sigma_pixels": 100})


def test_uncalibrated_record_has_pixel_metrics_but_no_physical_values() -> None:
    scene = generate_scene("gaussian_circular", seed=80)
    outcome = __import__("spot_analyzer").analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
            calibration=SpatialCalibration(),
        ),
    )
    assert outcome.record is not None
    physical = outcome.record.reportable_metrics()["gaussian_fwhm_major"]["domains"]["physical"]
    pixel = outcome.record.reportable_metrics()["gaussian_fwhm_major"]["domains"]["pixel"]
    assert pixel["value"] is not None
    assert physical["value"] is None
    assert physical["status"] == "unavailable"
    assert "calibration_missing" in physical["reason_codes"]
    assert physical["unit"] == "unavailable"


def test_wpf_has_no_placeholder_calibration_or_scientific_defaults() -> None:
    app = ROOT / "src" / "SpotAnalysis.App"
    request = (app / "AnalysisRequest.cs").read_text(encoding="utf-8")
    client = (app / "WorkerClient.cs").read_text(encoding="utf-8")
    window = (app / "MainWindow.xaml").read_text(encoding="utf-8")
    assert '"standard-profile-v1"' in request
    assert "background_signal_sigma_threshold" not in request
    assert "background_signal_sigma_threshold" not in client
    assert 'x:Name="CalibrationXText"' in window and 'Text="1"' not in window[window.index('x:Name="CalibrationXText"'):window.index('x:Name="RoiXText"')]
    assert 'x:Name="AdvancedSettingsExpander"' in window
    assert "已偏离推荐默认值" in (app / "MainWindow.xaml.cs").read_text(encoding="utf-8")
