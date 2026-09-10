from __future__ import annotations

from spot_analyzer.validation import run_report_validation


def test_report_validation_checks_record_semantics_and_both_exports() -> None:
    result = run_report_validation()

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["checks"]["record_identity"] is True
    assert result["checks"]["record_semantics"] is True
    assert result["checks"]["visual_identity"] is True
    assert result["checks"]["derived_asset_identity"] is True
    assert result["checks"]["profile_axis"] is True
    assert result["checks"]["energy_curve_context"] is True
    assert result["checks"]["quality_status"] is True
    assert result["checks"]["gating_values"] is True
    assert result["checks"]["coordinate_units"] is True
    assert result["checks"]["provenance"] is True
    assert result["checks"]["no_internal_trials"] is True
    assert result["checks"]["export_identity"] is True
    assert {item["format"] for item in result["exports"]} == {"png", "pdf"}
    assert all(item["status"] == "exported" for item in result["exports"])
    assert all(item["uri"].startswith("file:") for item in result["exports"])
    assert all(item["sha256"] for item in result["exports"])


def test_report_validation_can_write_to_declared_directory(tmp_path) -> None:
    result = run_report_validation(tmp_path)

    assert result["passed"] is True
    assert {path.suffix for path in tmp_path.iterdir()} == {".png", ".pdf"}
