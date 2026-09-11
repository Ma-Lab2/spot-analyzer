import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_manifest_generator_records_identity_and_hashes(tmp_path: Path) -> None:
    staging = tmp_path / "stage"
    staging.mkdir()
    payload = staging / "README.md"
    payload.write_text("portable alpha\n", encoding="utf-8")
    manifest = staging / "manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging" / "make_manifest.py"),
            "--staging",
            str(staging),
            "--output",
            str(manifest),
        ],
        check=True,
    )

    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert document["schema"] == "spot-analysis-package-v1"
    assert document["client"] == {
        "version": "0.1.0-alpha.1",
        "target": "win-x64",
        "self_contained": True,
    }
    assert document["worker"]["packaging"] == "PyInstaller onedir"
    assert document["analysis"]["profile_validation"] == "provisional"
    assert document["files"] == [
        {
            "path": "README.md",
            "bytes": payload.stat().st_size,
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        }
    ]
    assert "tests/" in document["source_exclusions"]
    assert "prototype/" in document["source_exclusions"]


def test_portable_build_contract_is_explicit() -> None:
    script = (ROOT / "packaging" / "build-alpha.ps1").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "worker.spec").read_text(encoding="utf-8")
    project = (ROOT / "src" / "SpotAnalysis.App" / "SpotAnalysis.App.csproj").read_text(encoding="utf-8")

    assert "--runtime\", \"win-x64" in script
    assert "--self-contained\", \"true" in script
    assert "SpotAnalysis.Worker.exe" in script
    assert "--onedir" in script
    assert "Compress-Archive" in script
    assert "COLLECT(" in spec
    assert "hiddenimports=collect_submodules(\"spot_analyzer\")" in spec
    assert "CopyToOutputDirectory" in project


def test_bundled_example_is_png_and_readable() -> None:
    example = ROOT / "examples" / "alpha-example.png"
    content = example.read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert content[12:16] == b"IHDR"
    assert int.from_bytes(content[16:20], "big") == 64
    assert int.from_bytes(content[20:24], "big") == 64
    assert content[24] == 8
    assert content[25] == 0


def test_clean_machine_acceptance_record_preserves_evidence_boundary() -> None:
    record = (ROOT / "packaging" / "ALPHA-TRIAL-ACCEPTANCE.md").read_text(encoding="utf-8")

    assert "Windows 11 x64" in record
    assert "spot-analysis-0.1.0-alpha.1-win-x64.zip" in record
    assert "without Python, .NET SDK" in record
    assert "**NOT RUN**" in record
    assert "Package SHA-256" in record
    assert "diagnostic package" in record
    assert "formal physical-accuracy validation" in record
    assert "Severity (blocker, high, medium, low):" in record

    # A template must not accidentally claim that the clean-machine trial passed.
    assert "clean-machine\nrun is **NOT RUN**" in record
    assert "Result | Evidence/notes" in record
