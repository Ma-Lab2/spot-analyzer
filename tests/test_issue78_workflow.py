from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
from PIL import Image

from spot_analyzer import (
    AnalysisConfiguration,
    AnalysisRecordKind,
    AnalysisRegion,
    InputImage,
    analyze,
)
from spot_analyzer.report import ReportSpecification, ReportWorkItem, batch_export_reports, prepare_report
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.worker import handle_request


def _image() -> InputImage:
    scene = generate_scene("gaussian_circular", seed=78)
    return InputImage(
        scene.input_array,
        bit_depth=16,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
    )


def test_automatic_analysis_is_an_immutable_non_exportable_preview() -> None:
    outcome = analyze(_image(), AnalysisConfiguration(None))

    assert outcome.record is not None
    assert outcome.record.record_kind is AnalysisRecordKind.PREVIEW
    assert outcome.record.is_preview and not outcome.record.is_formal
    assert outcome.record.configuration.region is not None
    assert outcome.record.measurement_semantics == "relative_intensity_code"
    assert outcome.record.measurement_semantics_confirmed is False
    assert not batch_export_reports(
        [ReportWorkItem(outcome.record)], ReportSpecification(output_directory=".")
    ).exported_count
    try:
        prepare_report(outcome.record, ReportSpecification(output_directory="."))
    except ValueError as error:
        assert "preview" in str(error)
    else:
        raise AssertionError("preview records must not be reportable")


def test_final_configuration_creates_formal_record_with_new_identity() -> None:
    image = _image()
    preview = analyze(image, AnalysisConfiguration(None)).record
    assert preview is not None
    formal_configuration = replace(
        preview.configuration,
        record_kind=AnalysisRecordKind.FORMAL,
        measurement_semantics_confirmed=True,
        region=AnalysisRegion(
            preview.configuration.region.x,
            preview.configuration.region.y,
            preview.configuration.region.width,
            preview.configuration.region.height,
            confirmed=True,
        ),
    )
    formal = analyze(image, formal_configuration).record
    assert formal is not None
    assert formal.is_formal
    assert formal.measurement_semantics_confirmed
    assert formal.record_id != preview.record_id
    assert formal.configuration.region == preview.configuration.region
    assert formal.analysis_fingerprint != preview.analysis_fingerprint


def test_worker_workflow_kind_is_structured_and_preserves_request_identity(tmp_path) -> None:
    source = tmp_path / "spot.png"
    Image.fromarray(np.asarray(_image().data, dtype=np.uint16)).save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = {
        "schema": "analysis-request-v1",
        "request_id": "preview-78",
        "workflow": {"kind": "preview"},
        "input": {"asset": {"path": str(source), "expected_sha256": digest}},
        "configuration": {"region": None, "automatic_background": True},
        "output_strategy": {"work_directory": str(tmp_path / "assets"), "derived_format": "npy"},
    }

    messages = handle_request(request)
    assert messages[0]["request_id"] == "preview-78"
    record = messages[-1]["record"]
    assert messages[-1]["request_id"] == "preview-78"
    assert record["record_kind"] == "preview"
    assert record["is_preview"] is True
    assert record["measurement_semantics"] == "relative_intensity_code"
    assert record["measurement_semantics_confirmed"] is False
