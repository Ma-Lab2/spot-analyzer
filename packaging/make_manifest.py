"""Create the identity manifest for a staged Alpha package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


IDENTITY_PATH = Path(__file__).with_name("build-identity.json")
DEPENDENCIES = ("numpy", "scipy", "Pillow", "rfc8785")
REQUIRED_IDENTITY_FIELDS = (
    "package_version", "client_version", "worker_version", "worker_packaging",
    "pyinstaller_version", "worker_dependencies", "analysis_core_version",
    "standard_profile_version", "quality_profile_version",
)


def load_identity(path: Path = IDENTITY_PATH) -> dict[str, object]:
    identity = json.loads(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED_IDENTITY_FIELDS if not identity.get(field)]
    if missing:
        raise ValueError(f"build identity is missing required fields: {', '.join(missing)}")
    dependencies = identity["worker_dependencies"]
    if not isinstance(dependencies, dict) or any(
        not isinstance(name, str) or not isinstance(value, str) or not value.strip()
        for name, value in dependencies.items()
    ) or set(dependencies) != set(DEPENDENCIES):
        raise ValueError("build identity worker_dependencies must list every locked worker dependency")
    return identity


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
    identity = load_identity()
    if version != identity["package_version"] or identity["client_version"] != version:
        raise ValueError(
            "build version mismatch: requested package version, identity package version, "
            "and client version must be identical"
        )
    staged_identity_path = staging / "build-identity.json"
    if staged_identity_path.exists() and load_identity(staged_identity_path) != identity:
        raise ValueError("staged build identity does not match the authoritative build identity")
    return {
        "schema": "spot-analysis-package-v1",
        "package_version": identity["package_version"],
        "build_identity": identity,
        "client": {
            "version": identity["client_version"],
            "target": "win-x64",
            "self_contained": True,
        },
        "worker": {
            "version": identity["worker_version"],
            "packaging": identity["worker_packaging"],
            "pyinstaller_version": identity["pyinstaller_version"],
            "executable": "SpotAnalysis.Worker.exe",
            "dependencies": identity["worker_dependencies"],
        },
        "analysis": {
            "core_version": identity["analysis_core_version"],
            "standard_profile": identity["standard_profile_version"],
            "quality_profile": identity["quality_profile_version"],
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
    parser.add_argument("--version", default=None)
    args = parser.parse_args()
    identity = load_identity()
    version = identity["package_version"] if args.version is None else args.version
    staging = args.staging.resolve()
    output = args.output.resolve()
    if not staging.is_dir():
        parser.error(f"staging directory does not exist: {staging}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_manifest(staging, output, version), indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
