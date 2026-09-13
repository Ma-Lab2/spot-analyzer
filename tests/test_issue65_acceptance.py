from pathlib import Path


DOCUMENT = Path(__file__).parents[1] / "docs" / "validation" / "issue-65-human-acceptance.md"


def test_human_acceptance_record_is_explicitly_unrun_until_observed() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "does not claim that the client was observed or accepted" in text
    assert "never convert it to PASS from automated tests" in text
    assert "Human sign-off" in text
    assert "development or packaged-client evidence only" in text
    assert ".\\packaging\\invoke-alpha-validation.ps1 -Stage Development" in text
    assert "issue-62-development.md" in text
    assert "issue-61-staged-validation.md" in text


def test_human_acceptance_record_covers_required_interactive_scenarios() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    required_scenarios = (
        "Bundled example",
        "Supported input",
        "Calibration",
        "Invalid calibration",
        "Analysis region",
        "Real analysis",
        "Result identity",
        "Measurements",
        "Invalid/failure path",
        "Result export",
        "Diagnostics privacy",
        "Input immutability",
        "Local logging",
    )
    for scenario in required_scenarios:
        assert f"| {scenario} |" in text


def test_human_acceptance_record_requires_evidence_and_limitations() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "Evidence path / notes" in text
    assert "Evidence index" in text
    assert "Limitations/follow-up issues" in text
    assert "provisional" in text
    assert "physical-accuracy" in text
