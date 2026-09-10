import json

import spot_analyzer.validation as validation


def test_complete_validation_persists_machine_and_human_materials(tmp_path, monkeypatch):
    sections = {
        "synthetic": {"status": "passed"},
        "low_snr": {"status": "passed"},
        "real_fixtures": {"status": "incomplete"},
        "report": {"status": "passed"},
        "identity": {"status": "incomplete"},
        "performance": {"status": "incomplete"},
    }
    monkeypatch.setattr(
        validation,
        "run_issue10_validation",
        lambda **kwargs: {
            "issue": 10,
            "validation_contract": "validation-contract-v1",
            "overall_status": "incomplete",
            "sections": sections,
            "identity": {"profile_validation": "provisional"},
            "incomplete_items": ["real_fixtures", "identity", "performance"],
            "environment": {"python": "3.10.11"},
        },
    )

    json_path = tmp_path / "nested" / "result.json"
    markdown_path = tmp_path / "nested" / "acceptance.md"
    result = validation.run_complete_validation(
        output_json=json_path,
        output_markdown=markdown_path,
    )

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    text = markdown_path.read_text(encoding="utf-8")
    assert saved["acceptance"]["validation_complete"] is False
    assert saved["acceptance"]["user_acceptance"] == "pending"
    assert saved["artifacts"]["json"] == str(json_path)
    assert result["artifacts"]["markdown"] == str(markdown_path)
    assert "real_fixtures" in text
    assert "WPF" in text
    assert "user acceptance" in text


def test_formal_incomplete_status_is_not_reported_as_failed():
    section = validation._run_section(
        "performance",
        lambda: {"formal_status": "incomplete", "passed": False},
    )

    assert section["status"] == "incomplete"


def test_complete_validation_forces_formal_performance_workloads(monkeypatch):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {
            "overall_status": "passed",
            "sections": {name: {"status": "passed"} for name in ("synthetic", "low_snr")},
            "identity": {"profile_validation": "provisional"},
            "incomplete_items": [],
        }

    monkeypatch.setattr(validation, "run_issue10_validation", fake_run)
    result = validation.run_complete_validation(performance_repetitions=7)

    assert seen["performance_sizes"] == (256, 1024)
    assert seen["performance_repetitions"] == 7
    assert result["acceptance"]["validation_complete"] is True
    assert result["acceptance"]["user_acceptance"] == "pending"
