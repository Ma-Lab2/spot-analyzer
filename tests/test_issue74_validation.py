import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCUMENT = ROOT / "docs" / "validation" / "issue-74-packaged-release.md"


def test_issue74_record_captures_identifiable_package_and_manifest_hashes() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "Issue #74 Prototype A packaged release validation" in text
    assert "artifacts/issue-74-20260913-final/spot-analysis-0.1.0-alpha.1-win-x64.zip" in text
    assert "artifacts/validation/issue-74-20260913-final/" in text
    assert re.search(r"ZIP SHA-256: `[0-9a-f]{64}`", text, re.IGNORECASE)
    assert re.search(r"Extracted `manifest\.json` SHA-256: `[0-9a-f]{64}`", text, re.IGNORECASE)
    assert "TO_BE_FILLED" not in text


def test_issue74_record_requires_packaged_executable_and_preserves_boundaries() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    normalized = text.lower()

    for term in (
        "fresh-extraction/spotanalysis.app.exe",
        "fresh-extraction/SpotAnalysis.Worker.exe",
        "record_id",
        "analysis fingerprint",
        "657 manifest entries",
        "human visual acceptance",
        "clean-machine acceptance",
        "NOT RUN",
        "profiles remain provisional",
        "does not\nclose Issue #66",
    ):
        assert term.lower() in normalized

    assert "visual assertion about the WPF window" in text
    assert "no source-tree worker override" in text
