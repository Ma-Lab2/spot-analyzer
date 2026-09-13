from __future__ import annotations

from dataclasses import replace

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, MeasurementStatus, Metric, analyze
from spot_analyzer.report import (
    ReportSpecification,
    ReportWorkItem,
    batch_export_reports,
    prepare_report,
    report_text,
)
from spot_analyzer.synthetic import generate_scene


def _record(name: str = "input-image.png"):
    scene = generate_scene("gaussian_circular")
    outcome = analyze(
        InputImage(
            scene.input_array,
            bit_depth=8,
            encoding_semantic="relative_intensity_code",
            encoding_semantic_confirmed=True,
            uri_hint=f"memory://{name}",
            metadata={"file_name": name},
        ),
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        ),
    )
    assert outcome.record is not None
    return outcome.record


def test_default_report_is_png_timestamped_and_names_input_in_chinese(tmp_path) -> None:
    record = _record("camera-01.png")
    specification = ReportSpecification(output_directory=tmp_path)
    package = prepare_report(record, specification)
    outcome = __import__("spot_analyzer.report", fromlist=["write_report"]).write_report(package, specification)

    assert package.format == "png"
    assert package.report_name == "camera-01 焦斑分析报告"
    assert outcome.path is not None
    assert outcome.path.name.startswith("camera-01_焦斑分析报告-")
    assert outcome.path.suffix == ".png"


def test_report_text_explicitly_gates_metric_values_and_keeps_reason_codes() -> None:
    record = _record()
    metrics = dict(record.metrics)
    metrics["gated_invalid"] = Metric(123.4, "mm", MeasurementStatus.INVALID, ("core_clipped",))
    metrics["gated_caution"] = Metric(2.5, "mm", MeasurementStatus.CAUTION, ("low_snr",))
    metrics["gated_unavailable"] = Metric(999.0, "mm", MeasurementStatus.UNAVAILABLE, ("rref_missing",))
    record = replace(record, metrics=metrics, summary_status=MeasurementStatus.INVALID)

    text = report_text(prepare_report(record, ReportSpecification(report_name="审计", output_directory=".")))

    assert "总体测量有效性" in text
    assert "无效 (invalid)" in text
    assert "需复核 (caution)" in text
    assert "不可用 (unavailable)" in text
    assert "gated_invalid: N/A" in text
    assert "gated_unavailable: N/A" in text
    assert "123.4" not in text
    assert "999.0" not in text
    assert "core_clipped" in text and "low_snr" in text and "rref_missing" in text


def test_batch_export_skips_preview_stale_and_nonformal_and_isolates_failure(tmp_path) -> None:
    first = _record("first.png")
    preview = _record("preview.png")
    stale = _record("stale.png")
    nonformal = _record("nonformal.png")
    items = [
        ReportWorkItem(first),
        ReportWorkItem(preview, is_preview=True),
        ReportWorkItem(stale, is_stale=True),
        ReportWorkItem(nonformal, is_formal=False),
    ]

    summary = batch_export_reports(items, ReportSpecification(output_directory=tmp_path))

    assert summary.exported_count == 1
    assert summary.skipped_count == 3
    assert summary.failed_count == 0
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert {item.diagnostics[0]["code"] for item in summary.outcomes if item.flow_status == "skipped"} == {
        "report_item_preview",
        "report_item_stale",
        "report_item_not_formal",
    }


def test_batch_export_continues_after_one_item_failure(tmp_path) -> None:
    first = _record("first.png")
    second = _record("second.png")
    items = [ReportWorkItem(first), ReportWorkItem(second)]
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")

    summary = batch_export_reports(items, ReportSpecification(output_directory=blocked))

    assert summary.exported_count == 0
    assert summary.failed_count == 2
    assert summary.skipped_count == 0
    assert all(item.path is None for item in summary.outcomes)
