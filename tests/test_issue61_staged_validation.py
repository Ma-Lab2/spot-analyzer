from pathlib import Path


ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "validation" / "issue-61-staged-validation.md"
SCRIPT = ROOT / "packaging" / "invoke-alpha-validation.ps1"


def test_issue61_defines_three_explicit_evidence_boundaries() -> None:
    text = DOC.read_text(encoding="utf-8")
    for term in (
        "Development-machine",
        "Local packaged-client smoke",
        "Clean-machine Alpha acceptance",
        "does not prove",
        "Evidence from one\nstage must not be copied",
        "NOT RUN",
    ):
        assert term in text


def test_issue61_documents_repeatable_commands_and_identity_capture() -> None:
    text = DOC.read_text(encoding="utf-8")
    for term in (
        "invoke-alpha-validation.ps1 -Stage Development",
        "invoke-alpha-validation.ps1 -Stage PackagedSmoke",
        "invoke-alpha-validation.ps1 -Stage CleanMachine",
        "validate_alpha_package.py",
        "ZIP and manifest hashes",
        "status.json",
        "ALPHA-TRIAL-ACCEPTANCE.md",
    ):
        assert term in text


def test_issue61_script_exposes_safe_stage_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for term in (
        'ValidateSet("Development", "PackagedSmoke", "CleanMachine")',
        "package-validation.json",
        "zip-sha256.txt",
        "manifest-sha256.txt",
        'status = "NOT RUN"',
        "Clean-machine acceptance remains NOT RUN",
    ):
        assert term in text


def test_issue61_preserves_non_acceptance_limitations() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "formal\nphysical-accuracy validation" in text
    assert "provisional" in text
    assert "production readiness" in text
