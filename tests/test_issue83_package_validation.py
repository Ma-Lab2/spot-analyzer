import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
VALIDATOR = ROOT / "packaging" / "validate_alpha_package.py"
IDENTITY = json.loads((ROOT / "packaging" / "build-identity.json").read_text(encoding="utf-8"))


def make_package(tmp_path: Path) -> Path:
    stage = tmp_path / "stage"
    (stage / "examples").mkdir(parents=True)
    for name, data in {
        "SpotAnalysis.App.exe": b"client",
        "SpotAnalysis.Worker.exe": b"worker",
        "examples/alpha-example.png": b"png",
    }.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = stage / "manifest.json"
    subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "make_manifest.py"),
         "--staging", str(stage), "--output", str(manifest)],
        check=True,
    )
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in stage.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())
    return package


def test_validator_records_package_and_manifest_identity(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    report_path = tmp_path / "evidence.json"
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(package), "--output", str(report_path),
         "--expected-version", IDENTITY["package_version"]],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["package_sha256"]
    assert len(report["manifest_sha256"]) == 64
    assert report["manifest_files"] == 3
    assert report["human_acceptance"] == "NOT RUN"
    assert json.loads(result.stdout)["package_version"] == IDENTITY["package_version"]


def test_validator_rejects_tampered_member(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "SpotAnalysis.Worker.exe":
                payload += b"tampered"
            target.writestr(info, payload)
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(tampered)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "mismatch" in result.stderr
