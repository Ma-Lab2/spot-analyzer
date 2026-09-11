"""Create the identity manifest for a staged Alpha package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


DEPENDENCIES = ("numpy", "scipy", "Pillow", "rfc8785")


def dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def file_entries(staging: Path, manifest: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        if path.resolve() == manifest.resolve():
            continue
        relative = path.relative_to(staging).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest})
    return entries


def build_manifest(staging: Path, output: Path, version: str) -> dict[str, object]:
    return {
        "schema": "spot-analysis-package-v1",
        "package_version": version,
        "client": {
            "version": version,
            "target": "win-x64",
            "self_contained": True,
        },
        "worker": {
            "version": "worker-contract-v1",
            "packaging": "PyInstaller onedir",
            "executable": "SpotAnalysis.Worker.exe",
        },
        "analysis": {
            "core_version": "analysis-core-v1",
            "standard_profile": "standard-profile-v1",
            "quality_profile": "quality-profile-v1",
            "profile_validation": "provisional",
        },
        "dependencies": dependency_versions(),
        "build_environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "manifest_generator": "packaging/make_manifest.py",
        },
        "files": file_entries(staging, output),
        "source_exclusions": [
            "source code (*.py, *.cs, *.xaml)",
            "tests/",
            "Git metadata (.git/)",
            "Python and .NET caches (__pycache__/, .pytest_cache/, obj/, bin/)",
            "prototype/",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="0.1.0-alpha.1")
    args = parser.parse_args()
    staging = args.staging.resolve()
    output = args.output.resolve()
    if not staging.is_dir():
        parser.error(f"staging directory does not exist: {staging}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_manifest(staging, output, args.version), indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
