from pathlib import Path


ROOT = Path(__file__).parents[1]


def _record() -> str:
    return (ROOT / "docs" / "validation" / "issue-63-packaged-smoke.md").read_text(
        encoding="utf-8"
    )


def test_packaged_record_defines_artifact_identity_and_isolation() -> None:
    record = _record()

    assert "local packaged-client stage" in record
    assert "spot-analysis-0.1.0-alpha.1-win-x64.zip" in record
    assert "ZIP SHA-256" in record
    assert "Manifest SHA-256" in record
    assert "fresh directory" in record
    assert "development worker override" in record
    assert "not clean-machine acceptance" in record


def test_packaged_record_covers_build_and_package_contract() -> None:
    record = _record()

    for term in (
        "build-alpha.ps1",
        "build preflight",
        "win-x64",
        "PyInstaller onedir",
        "every staged file",
        "or test trees",
        "Python/C#/XAML source",
        "Git metadata",
        "HTML prototype",
    ):
        assert term in record


def test_packaged_matrix_covers_user_visible_smoke_paths() -> None:
    record = _record()
    rows = [line for line in record.splitlines() if line.startswith("| ") and "---" not in line]

    assert "Packaged-client smoke matrix" in record
    assert len(rows) >= 12
    expected = (
        "Client startup",
        "Bundled example",
        "Supported input",
        "Configuration",
        "Processing/completed",
        "Measurement validity",
        "Export and collision",
        "Diagnostics/privacy",
        "Immutability",
        "Local logging",
        "Failure/invalid path",
    )
    for scenario in expected:
        assert any(row.startswith(f"| {scenario} |") for row in rows)

    for row in rows:
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        if cells[0] == "Scenario":
            continue
        assert len(cells) == 4
        assert cells[2] in {"NOT RUN", "PASS", "FAIL", "BLOCKED"}
        assert cells[3]


def test_packaged_record_preserves_privacy_logging_and_alpha_boundaries() -> None:
    record = _record()

    assert "%LOCALAPPDATA%\\SpotAnalysis\\logs" in record
    assert "original image is absent" in record
    assert "explicit attachment" in record
    assert "profiles remain provisional" in record
    assert "physical-accuracy" in record
    assert "issue #64" in record
