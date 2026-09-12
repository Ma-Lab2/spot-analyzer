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


def test_wpf_application_opens_the_main_window_on_startup() -> None:
    application = (ROOT / "src" / "SpotAnalysis.App" / "App.xaml").read_text(encoding="utf-8")

    assert 'StartupUri="MainWindow.xaml"' in application


def test_configuration_events_wait_for_window_initialization() -> None:
    window = (ROOT / "src" / "SpotAnalysis.App" / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    assert window.index("InitializeComponent();") < window.index("_configurationReady = true;")
    assert "if (!_configurationReady)\n            return;" in window


def test_portable_build_contract_is_explicit() -> None:
    script = (ROOT / "packaging" / "build-alpha.ps1").read_text(encoding="utf-8")
    helpers = (ROOT / "packaging" / "build-helpers.ps1").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "worker.spec").read_text(encoding="utf-8")
    project = (ROOT / "src" / "SpotAnalysis.App" / "SpotAnalysis.App.csproj").read_text(encoding="utf-8")

    assert "--runtime\", \"win-x64" in script
    assert "--self-contained\", \"true" in script
    assert "SpotAnalysis.Worker.exe" in script
    assert "--onedir" not in script
    assert "PyInstaller version mismatch" in script
    assert "dotnet --list-sdks" in script
    assert "Python 3.12" in script
    assert "Compress-WithRetry" in script
    assert "function Compress-WithRetry" in helpers
    assert "Compress-Archive" in helpers
    assert "catch [System.IO.IOException]" in helpers
    assert "Start-Sleep -Milliseconds" in helpers
    assert "COLLECT(" in spec
    assert "hiddenimports=collect_submodules(\"spot_analyzer\")" in spec
    assert "CopyToOutputDirectory" in project


def test_compress_with_retry_recovers_from_transient_file_lock(tmp_path: Path) -> None:
    if sys.platform != "win32":
        return

    stage = tmp_path / "stage"
    stage.mkdir()
    locked_file = stage / "base_library.zip"
    locked_file.write_bytes(b"nested archive payload")
    destination = tmp_path / "package.zip"
    ready = tmp_path / "lock-ready"
    probe = tmp_path / "compress-retry.ps1"
    helper = str(ROOT / "packaging" / "build-helpers.ps1").replace("'", "''")
    probe.write_text(
        f"""
. '{helper}'
$source = '{str(locked_file).replace("'", "''")}'
$stage = '{str(stage).replace("'", "''")}'
$destination = '{str(destination).replace("'", "''")}'
$ready = '{str(ready).replace("'", "''")}'
$job = Start-Job -ArgumentList $source, $ready -ScriptBlock {{
    param($sourcePath, $readyPath)
    $stream = [System.IO.File]::Open(
        $sourcePath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::None)
    [System.IO.File]::WriteAllText($readyPath, 'ready')
    Start-Sleep -Milliseconds 900
    $stream.Dispose()
}}
$deadline = (Get-Date).AddSeconds(5)
while (-not (Test-Path $ready)) {{
    if ((Get-Date) -gt $deadline) {{ throw 'Timed out waiting for file lock.' }}
    Start-Sleep -Milliseconds 10
}}
try {{
    Compress-WithRetry -SourcePath (Join-Path $stage '*') -DestinationPath $destination
}}
finally {{
    Wait-Job $job | Out-Null
    Receive-Job $job | Out-Null
    Remove-Job $job
}}
if (-not (Test-Path $destination)) {{ throw 'Compression retry did not create the archive.' }}
""",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_build_stage_contains_offline_acceptance_procedure() -> None:
    script = (ROOT / "packaging" / "build-alpha.ps1").read_text(encoding="utf-8")
    assert '"ALPHA-TRIAL-ACCEPTANCE.md"' in script
    assert "manifest.json" in script


def test_clean_machine_acceptance_record_requires_evidence_for_each_status() -> None:
    record = (ROOT / "packaging" / "ALPHA-TRIAL-ACCEPTANCE.md").read_text(encoding="utf-8")
    rows = [line for line in record.splitlines() if line.startswith("| ") and "---" not in line]

    assert "Windows 11 x64" in record
    assert "Package SHA-256" in record
    assert "Manifest:" in record
    assert "provisional" in record
    assert "follow-up" in record.lower()
    assert rows
    for row in rows:
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        if cells[0] == "Scenario":
            continue
        assert cells[2] in {"NOT RUN", "PASS", "FAIL", "BLOCKED"}
        assert cells[3]
        if cells[2] == "PASS":
            assert "not run" not in cells[3].lower()


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
    assert "Package SHA-256" in record
    assert "diagnostic package" in record
    assert "formal physical-accuracy validation" in record
    assert "Severity (blocker, high, medium, low):" in record
    assert "Result | Evidence/notes" in record
