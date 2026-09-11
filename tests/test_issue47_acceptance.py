from spot_analyzer import validation


def test_issue47_contract_has_all_required_sections_and_bounded_limitations(monkeypatch):
    def fake_base(**kwargs):
        return {
            "issue": 10,
            "sections": {
                name: {"name": name, "status": "passed", "result": {"passed": True}}
                for name in ("synthetic", "low_snr", "real_fixtures", "report", "identity", "performance")
            },
            "identity": {"profile_validation": "provisional"},
            "environment": {"python": "3.12.1", "machine": "test"},
            "incomplete_items": [],
        }

    monkeypatch.setattr(validation, "run_issue10_validation", fake_base)
    result = validation.run_issue47_validation()

    assert result["issue"] == 47
    assert result["required_sections"] == [
        "synthetic", "low_snr", "real_fixtures", "identity", "report",
        "ui_worker_parity", "performance",
    ]
    assert set(result["sections"]) == set(result["required_sections"])
    assert all(section["status"] in {"passed", "failed", "incomplete"} for section in result["sections"].values())
    assert result["sections"]["ui_worker_parity"]["status"] == "incomplete"
    assert len(result["bounded_limitations"]) == 5
    assert result["handoff"] == {
        "profile_validation": "provisional",
        "release_status": "out_of_scope",
        "evidence_kind": "MVP formal development handoff",
    }


def test_issue47_performance_contract_is_formal_workload(monkeypatch):
    seen = {}

    def fake_base(**kwargs):
        seen.update(kwargs)
        return {
            "sections": {name: {"status": "incomplete"} for name in (
                "synthetic", "low_snr", "real_fixtures", "report", "identity", "performance"
            )},
            "identity": {"profile_validation": "provisional"},
            "environment": {},
            "incomplete_items": [],
        }

    monkeypatch.setattr(validation, "run_issue10_validation", fake_base)
    result = validation.run_issue47_validation(performance_repetitions=3)

    assert seen["performance_sizes"] == (256, 1024)
    assert seen["performance_repetitions"] == 3
    assert result["overall_status"] == "incomplete"
