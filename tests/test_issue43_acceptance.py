from __future__ import annotations

from pathlib import Path

import numpy as np

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer.report import ReportSpecification, prepare_report, write_report
from spot_analyzer.synthetic import generate_scene


def _record():
    scene = generate_scene("gaussian_circular")
    image = InputImage(
        scene.input_array,
        bit_depth=16,
        encoding_semantic="relative_intensity_code",
        encoding_semantic_confirmed=True,
        metadata={"source": "issue-43-acceptance"},
        sha256="fixture-sha256",
    )
    outcome = analyze(
        image,
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        ),
    )
    assert outcome.record is not None
    return outcome.record


def test_report_package_carries_immutable_record_provenance_and_preview_semantics() -> None:
    record = _record()
    package = prepare_report(record, ReportSpecification("png", "preview", "."))

    assert package.record_id == record.record_id
    assert package.analysis_fingerprint == record.analysis_fingerprint
    assert package.sections["input"]["sha256"] == "fixture-sha256"
    assert "calibration" in package.sections
    assert "preprocessing" in package.sections
    assert "configuration" in package.sections
    assert "analysis_model" in package.sections
    assert "metrics" in package.sections
    assert "diagnostics" in package.sections
    assert package.sections["provenance"]["report_schema"] == "report-package-v1"
    assert "校正强度图" in package.visuals

    # Preview settings/package mutation cannot alter the source record or fingerprint.
    before = record.analysis_fingerprint
    try:
        package.sections["summary"]["record_id"] = "changed"
    except TypeError:
        pass
    assert record.analysis_fingerprint == before
    assert package.analysis_fingerprint == before
    assert np.asarray(package.visuals["校正强度图"]).flags.writeable is False

    diagnostics = package.sections["diagnostics"]
    fit = diagnostics.get("fit", {})
    assert "fit_initial_parameters" not in fit
    assert "fit_covariance" not in fit


def test_png_and_pdf_exports_use_the_same_report_package(tmp_path: Path) -> None:
    record = _record()
    png_spec = ReportSpecification("png", "same-package", tmp_path)
    pdf_spec = ReportSpecification("pdf", "same-package", tmp_path)
    package = prepare_report(record, png_spec)
    png = write_report(package, png_spec)
    pdf = write_report(package, pdf_spec)

    assert png.flow_status == "exported"
    assert pdf.flow_status == "exported"
    assert png.record_id == pdf.record_id == package.record_id
    assert png.path is not None and png.path.suffix == ".png"
    assert pdf.path is not None and pdf.path.suffix == ".pdf"
    assert png.path.read_bytes()
    assert pdf.path.read_bytes()
