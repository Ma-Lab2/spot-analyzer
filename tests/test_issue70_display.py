from __future__ import annotations

from dataclasses import replace

import numpy as np

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer.display import DisplayUnavailable, project
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.worker import _record_payload, _write_derived_assets


def _record():
    scene = generate_scene("gaussian_circular", seed=70)
    outcome = analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        AnalysisConfiguration(AnalysisRegion(64, 64, 128, 128), background_region=AnalysisRegion(32, 64, 32, 128)),
    )
    assert outcome.record is not None
    return outcome.record


def test_display_projection_exposes_all_seven_record_bound_layers_without_mutating_record():
    record = _record()
    before = record.corrected_intensity.copy()
    projection = project(record)

    assert [layer.name for layer in projection.layers] == [
        "input_image", "corrected_intensity", "positive_signal", "fitted_intensity",
        "fit_residual", "measurement_mask", "core_mask",
    ]
    assert {layer.record_id for layer in projection.layers} == {record.record_id}
    assert projection.analysis_fingerprint == record.analysis_fingerprint
    corrected = projection.layers[1].data
    assert isinstance(corrected, np.ndarray)
    corrected[0, 0] = 999999
    assert np.array_equal(record.corrected_intensity, before)


def test_display_projection_preserves_record_identity_and_has_structured_na_for_missing_curve():
    record = _record()
    record_without_curves = replace(record, diagnostics={})
    projection = project(record_without_curves)

    assert projection.record_id == record.record_id
    assert projection.analysis_fingerprint == record.analysis_fingerprint
    assert isinstance(projection.curves.actual_profile, DisplayUnavailable)
    assert projection.curves.actual_profile.reason_code == "curve_unavailable"


def test_worker_record_exposes_versioned_display_projection_with_same_record_identity(tmp_path):
    record = _record()
    assets = _write_derived_assets(record, {"derived_format": "npy", "work_directory": str(tmp_path)})
    payload = _record_payload(record, assets)

    projection = payload["display_projection"]
    assert projection["schema"] == "display-projection-v1"
    assert projection["record_id"] == record.record_id
    assert len(projection["layers"]) == 7
    assert {asset["record_id"] for asset in assets} == {record.record_id}


def test_display_projection_settings_are_not_analysis_configuration():
    record = _record()
    projection_a = project(record)
    projection_b = project(record)
    assert projection_a.analysis_fingerprint == projection_b.analysis_fingerprint
    assert projection_a.record_id == projection_b.record_id
    assert np.array_equal(projection_a.layers[1].data, projection_b.layers[1].data)
