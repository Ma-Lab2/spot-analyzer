from __future__ import annotations

import hashlib
from pathlib import Path
import re

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze
from spot_analyzer.report import ReportSpecification, prepare_report, write_report
from spot_analyzer.synthetic import generate_scene


def _record():
    scene = generate_scene("gaussian_circular")
    outcome = analyze(
        InputImage(
            scene.input_array,
            bit_depth=16,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
            metadata={"source": "issue-44-acceptance"},
            sha256="issue-44-fixture-sha256",
        ),
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        ),
    )
    assert outcome.record is not None
    return outcome.record


def test_exports_are_safe_named_traceable_and_non_overwriting(tmp_path: Path) -> None:
    record = _record()
    specification = ReportSpecification(
        "png", "unsafe report/name: 01", tmp_path, append_timestamp=True
    )
    package = prepare_report(record, specification)
    first = write_report(package, specification)
    second = write_report(package, specification)

    assert first.flow_status == second.flow_status == "exported"
    assert first.path is not None and second.path is not None
    assert first.path != second.path
    assert first.path.parent == tmp_path
    assert first.path.name.startswith("unsafe_report_name_01-")
    assert re.search(r"-\d{8}T\d{6}Z(?:-\d+)?\.png$", first.path.name)
    assert first.record_id == second.record_id == record.record_id
    assert first.sha256 == hashlib.sha256(first.path.read_bytes()).hexdigest()
    assert second.sha256 == hashlib.sha256(second.path.read_bytes()).hexdigest()
    assert not list(tmp_path.glob(".report-*"))


def test_export_failure_is_separate_from_measurement_and_cleans_partial_files(tmp_path: Path) -> None:
    record = _record()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("input remains untouched", encoding="utf-8")
    package = prepare_report(record, ReportSpecification("pdf", "report", tmp_path))

    result = write_report(
        package,
        ReportSpecification("pdf", "report", blocked),
    )

    assert result.flow_status == "export_failed"
    assert result.path is None
    assert result.record_id == record.record_id
    assert result.diagnostics[0]["code"] == "report_directory_create_failed"
    assert blocked.read_text(encoding="utf-8") == "input remains untouched"
    assert record.flow_status.value == "computed"
    assert not list(tmp_path.glob(".report-*"))


def test_pdf_and_png_preserve_one_package_identity(tmp_path: Path) -> None:
    record = _record()
    package = prepare_report(record, ReportSpecification("png", "identity", tmp_path))
    png = write_report(package, ReportSpecification("png", "identity", tmp_path))
    pdf = write_report(package, ReportSpecification("pdf", "identity", tmp_path))

    assert png.flow_status == pdf.flow_status == "exported"
    assert png.record_id == pdf.record_id == package.record_id
    assert png.path is not None and png.path.suffix == ".png"
    assert pdf.path is not None and pdf.path.suffix == ".pdf"
    assert png.path.read_bytes() and pdf.path.read_bytes()
