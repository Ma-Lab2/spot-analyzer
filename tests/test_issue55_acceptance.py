from __future__ import annotations

from pathlib import Path

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
            metadata={"source": "issue-55-acceptance"},
            sha256="issue-55-fixture-sha256",
        ),
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        ),
    )
    assert outcome.record is not None
    return outcome.record


def test_pdf_png_exports_share_identity_name_policy_and_independent_directories(tmp_path: Path) -> None:
    record = _record()
    input_before = record.input_intensity.copy()
    png_dir = tmp_path / "png-output"
    pdf_dir = tmp_path / "pdf-output"
    png_spec = ReportSpecification("png", "user report/01", png_dir, append_timestamp=True)
    pdf_spec = ReportSpecification("pdf", "user report/01", pdf_dir, append_timestamp=False)

    png_package = prepare_report(record, png_spec)
    pdf_package = prepare_report(record, pdf_spec)
    png = write_report(png_package, png_spec)
    pdf = write_report(pdf_package, pdf_spec)

    assert png.flow_status == pdf.flow_status == "exported"
    assert png.record_id == pdf.record_id == record.record_id
    assert png.path is not None and png.path.parent == png_dir and png.path.suffix == ".png"
    assert pdf.path is not None and pdf.path.parent == pdf_dir and pdf.path.suffix == ".pdf"
    assert png.path.name.startswith("user_report_01-")
    assert pdf.path.name == "user_report_01.pdf"
    assert (record.input_intensity == input_before).all()
    assert not record.input_intensity.flags.writeable


def test_report_preserves_gated_null_values_and_reason_codes() -> None:
    record = _record()
    package = prepare_report(record, ReportSpecification("png", "gated", "."))
    metrics = package.sections["metrics"]
    physical = metrics["gaussian_fwhm_major"]["domains"]["physical"]

    assert physical["value"] is None
    assert physical["status"] == "unavailable"
    assert "calibration_missing" in physical["reason_codes"]
