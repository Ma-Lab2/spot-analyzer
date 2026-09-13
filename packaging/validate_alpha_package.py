"""Validate a portable Alpha ZIP and emit repeatable package evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

IDENTITY_PATH = Path(__file__).with_name("build-identity.json")
FORBIDDEN_PARTS = {
    ".git", "tests", "prototype", "__pycache__", ".pytest_cache", "obj", "bin"
}
FORBIDDEN_SUFFIXES = {".py", ".cs", ".xaml"}
REQUIRED_IDENTITY = (
    "package_version", "client_version", "worker_version", "worker_packaging",
    "pyinstaller_version", "analysis_core_version", "standard_profile_version",
    "quality_profile_version",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def failure(message: str) -> None:
    raise ValueError(message)


def validate(package: Path, expected_version: str | None = None) -> dict[str, Any]:
    if not package.is_file():
        failure(f"package does not exist: {package}")
    package_hash = sha256_file(package)
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            failure("package contains duplicate archive members")
        if "manifest.json" not in names:
            failure("package does not contain a root manifest.json")
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            failure("package contains an unsafe archive path")
        manifest_bytes = archive.read("manifest.json")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            failure(f"manifest.json is not valid UTF-8 JSON: {error}")
        if manifest.get("schema") != "spot-analysis-package-v1":
            failure("manifest schema is not spot-analysis-package-v1")
        identity = manifest.get("build_identity")
        if not isinstance(identity, dict) or any(not identity.get(field) for field in REQUIRED_IDENTITY):
            failure("manifest build identity is missing required fields")
        if expected_version is not None and manifest.get("package_version") != expected_version:
            failure(f"package version {manifest.get('package_version')!r} does not match {expected_version!r}")
        client = manifest.get("client", {})
        if client != {"version": identity["client_version"], "target": "win-x64", "self_contained": True}:
            failure("manifest client identity is inconsistent")
        worker = manifest.get("worker", {})
        if worker.get("packaging") != "PyInstaller onedir" or worker.get("executable") != "SpotAnalysis.Worker.exe":
            failure("manifest worker packaging identity is inconsistent")
        analysis = manifest.get("analysis", {})
        if analysis.get("profile_validation") != "provisional":
            failure("manifest must preserve provisional profile validation")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            failure("manifest files must be a list")
        manifest_paths = [entry.get("path") for entry in entries]
        archive_paths = [name for name in names if not name.endswith("/") and name != "manifest.json"]
        if len(manifest_paths) != len(set(manifest_paths)) or set(manifest_paths) != set(archive_paths):
            failure("manifest file list does not exactly cover ZIP files")
        for entry in entries:
            path = entry.get("path")
            if not isinstance(path, str) or not isinstance(entry.get("bytes"), int) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256"))):
                failure(f"invalid manifest entry: {entry!r}")
            payload = archive.read(path)
            if len(payload) != entry["bytes"] or sha256_bytes(payload) != entry["sha256"]:
                failure(f"manifest hash or byte count mismatch: {path}")
            parts = set(Path(path).parts)
            if parts & FORBIDDEN_PARTS or Path(path).suffix.lower() in FORBIDDEN_SUFFIXES:
                failure(f"forbidden source or cache path in package: {path}")
        required = {"SpotAnalysis.App.exe", "SpotAnalysis.Worker.exe", "examples/alpha-example.png"}
        if not required <= set(archive_paths):
            failure(f"package is missing required files: {sorted(required - set(archive_paths))}")
    return {
        "status": "PASS",
        "package": str(package),
        "package_sha256": package_hash,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "package_version": manifest["package_version"],
        "manifest_files": len(entries),
        "checks": [
            "package identity and manifest schema",
            "manifest SHA-256 and byte-count coverage",
            "source, test, cache, Git, and prototype exclusions",
            "required client, worker, and bundled example presence",
        ],
        "human_acceptance": "NOT RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="portable Alpha ZIP")
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    parser.add_argument("--expected-version", help="require this package version")
    args = parser.parse_args()
    try:
        report = validate(args.package.resolve(), args.expected_version)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        print(f"VALIDATION FAILED: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
