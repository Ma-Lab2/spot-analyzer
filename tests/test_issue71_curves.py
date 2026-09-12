from dataclasses import replace

import numpy as np

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer.display import DisplayUnavailable, project
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.worker import _record_payload


def _record():
    scene = generate_scene("gaussian_circular", seed=71)
    outcome = analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        AnalysisConfiguration(AnalysisRegion(64, 64, 128, 128), background_region=AnalysisRegion(32, 64, 32, 128)),
    )
    assert outcome.record is not None
    return outcome.record


def test_fixed_record_has_x_y_actual_and_fitted_profiles_and_energy_relationships():
    record = _record()
    curves = record.diagnostics["report_curves"]
    assert len(curves["profile_x"]) == len(curves["profile_x_actual"]) == len(curves["profile_x_fitted"])
    assert len(curves["profile_y"]) == len(curves["profile_y_actual"]) == len(curves["profile_y_fitted"])
    assert len(curves["energy_radius"]) == len(curves["energy_fraction"]) > 0
    assert curves["energy_fraction_unit"] == "fraction"
    assert set(curves["energy_markers"]) == {"ee50", "ee80"}
    assert curves["energy_markers"]["ee50"]["fraction"] == 0.5
    assert curves["energy_markers"]["ee80"]["fraction"] == 0.8


def test_curve_projection_is_read_only_and_record_bound():
    record = _record()
    projection = project(record)
    assert projection.record_id == record.record_id
    assert projection.analysis_fingerprint == record.analysis_fingerprint
    assert isinstance(projection.curves.profile_x_actual, np.ndarray)
    before = record.diagnostics["report_curves"]["profile_x_actual"][0]
    projection.curves.profile_x_actual[0] = 999
    assert record.diagnostics["report_curves"]["profile_x_actual"][0] == before


def test_missing_curve_series_is_structured_unavailable_not_zero():
    record = replace(record := _record(), diagnostics={"report_curves": {"profile_x": [0, 1]}})
    curves = project(record).curves
    assert isinstance(curves.profile_y_actual, DisplayUnavailable)
    assert curves.profile_y_actual.reason_code == "curve_unavailable"
    assert isinstance(curves.energy_radius, DisplayUnavailable)


def test_worker_curve_projection_retains_identity():
    record = _record()
    payload = _record_payload(record, [])
    projection = payload["display_projection"]
    assert projection["record_id"] == record.record_id
    assert projection["analysis_fingerprint"] == record.analysis_fingerprint
    assert projection["curves"]["profile_y_actual"] == record.diagnostics["report_curves"]["profile_y_actual"]
