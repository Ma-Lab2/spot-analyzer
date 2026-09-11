import json
from pathlib import Path

import spot_analyzer.validation as validation


def test_complete_validation_persists_machine_and_human_materials(tmp_path, monkeypatch):
    sections = {
        "synthetic": {"status": "passed"},
        "low_snr": {"status": "passed"},
        "real_fixtures": {
            "status": "incomplete",
            "result": {
                "manifest_sha256": "sha256-manifest",
                "fixtures": [{"relative_path": "fixture.png", "sha256": "fixture-sha256"}],
                "bounded_limitations": [{
                    "code": "physical_truth_absent",
                    "affected_fixtures": "all",
                    "unknown_fields": ["instrument", "acquired_at"],
                    "observed": "No gAMA, sRGB, or iCCP metadata was present.",
                    "consequence": "Absolute physical accuracy is not established.",
                    "acceptance": "requires_explicit_human_acceptance_or_rejection",
                }],
            },
        },
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
            "environment": {
                "python": "3.12.14",
                "python_implementation": "CPython",
                "numpy": "2.2.6",
                "scipy": "1.15.3",
                "pillow": "12.2.0",
                "rfc8785": "0.1.4",
                "spot_analyzer": "0.1.0",
            },
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
    assert saved["acceptance"]["human_decision_required"] is True
    assert saved["acceptance"]["decision_options"] == [
        "accept_current_bounded_results",
        "request_specification_changes",
    ]
    limitation_codes = {item["code"] for item in saved["acceptance"]["bounded_limitations"]}
    assert limitation_codes == {"physical_truth_absent", "identity", "performance"}
    assert saved["artifacts"]["json"] == str(json_path)
    assert result["artifacts"]["markdown"] == str(markdown_path)
    for name, section in sections.items():
        assert f"`{name}`: `{section['status']}`" in text
    for value in ("3.12.14", "2.2.6", "1.15.3", "12.2.0", "0.1.4"):
        assert value in text
    assert "sha256-manifest" in text
    assert "fixture-sha256" in text
    assert "unknown fields: instrument, acquired_at" in text
    assert "No gAMA, sRGB, or iCCP metadata was present." in text
    assert "requires_explicit_human_acceptance_or_rejection" in text
    assert "Required evidence is incomplete for identity." in text
    assert "Required evidence is incomplete for performance." in text
    assert "WPF" in text
    assert "Accept current bounded results" in text
    assert "Request specification changes" in text
    assert "user acceptance" in text


def test_formal_artifacts_record_explicit_bounded_results_acceptance():
    repository = Path(__file__).resolve().parents[1]
    result = json.loads(
        (repository / "docs/validation/issue-10-formal-results.json").read_text(
            encoding="utf-8-sig"
        )
    )
    text = (
        repository / "docs/validation/issue-10-formal-acceptance.md"
    ).read_text(encoding="utf-8")

    acceptance = result["acceptance"]
    limitation_codes = {
        item["code"] for item in acceptance["bounded_limitations"]
    }
    assert result["overall_status"] == "incomplete"
    assert result["sections"]["real_fixtures"]["status"] == "incomplete"
    assert acceptance["validation_complete"] is False
    assert acceptance["user_acceptance"] == "accepted_current_bounded_results"
    assert acceptance["bounded_results_accepted"] is True
    assert acceptance["human_decision_required"] is False
    assert acceptance["issue_10_completion_condition_met"] is True
    assert acceptance["decision"] == {
        "acceptance_candidate_commit": "d8f27a14e8a80e8c229f22c24c4e6f088616151c",
        "date": "2026-09-11",
        "original_text": "接受",
        "recorded_interpretation": "accept_current_bounded_results",
    }
    assert set(acceptance["accepted_bounded_limitation_codes"]) == limitation_codes
    assert len(limitation_codes) == 5
    assert acceptance["production_release"] == "out_of_scope"
    assert result["identity"]["profile_validation"] == "provisional"
    assert "the user stated exactly: `接受`" in text
    assert "does not convert the real-fixture evidence" in text
    assert "Production release remains out of scope" in text


def test_acceptance_materials_use_renderable_newlines():
    result = {
        "overall_status": "incomplete",
        "validation_contract": "validation-contract-v1",
        "identity": {"profile_validation": "provisional"},
        "sections": {"synthetic": {"status": "passed"}},
        "incomplete_items": [],
    }

    text = validation._acceptance_materials(result)

    assert "\n" in text
    assert "\\n" not in text
    assert text.splitlines()[0] == "# Issue #10 Validation Acceptance Record"


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
