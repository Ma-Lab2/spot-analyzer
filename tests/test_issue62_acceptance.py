from pathlib import Path


RECORD = Path(__file__).parents[1] / "docs" / "validation" / "issue-62-development.md"


def test_issue62_record_defines_development_evidence_boundary_and_commands():
    text = RECORD.read_text(encoding="utf-8")

    assert "Issue: #62" in text
    assert "development-machine" in text
    assert "clean-machine" in text
    assert "acceptance" in text
    assert "python -m pytest -q" in text
    assert "dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj" in text
    assert "artifacts/validation/issue-62-development/" in text


def test_issue62_record_covers_required_interactive_scenarios():
    text = RECORD.read_text(encoding="utf-8")
    required_scenarios = (
        "Bundled example",
        "8-bit input",
        "16-bit input",
        "Invalid input",
        "Calibration",
        "Invalid calibration",
        "Analysis region",
        "Real analysis",
        "Metrics",
        "Failure paths",
        "Result export",
        "Diagnostics",
        "Immutability",
        "Local logs",
    )

    for scenario in required_scenarios:
        assert f"| {scenario} |" in text


def test_issue62_record_does_not_promote_provisional_profiles():
    text = RECORD.read_text(encoding="utf-8")

    assert "profiles remain provisional" in text
    assert "not physically validated" in text
    assert "not production approved" in text
