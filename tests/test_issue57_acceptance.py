import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "packaging" / "make_manifest.py"
spec = importlib.util.spec_from_file_location("make_manifest", MODULE_PATH)
assert spec and spec.loader
make_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_manifest)


def test_build_identity_is_authoritative_for_manifest_and_worker_diagnostics() -> None:
    identity = json.loads((ROOT / "packaging" / "build-identity.json").read_text(encoding="utf-8"))
    assert identity["package_version"] == identity["client_version"]
    assert set(identity["worker_dependencies"]) == set(make_manifest.DEPENDENCIES)
    document = make_manifest.build_manifest(ROOT, ROOT / "manifest.json", identity["package_version"])
    assert document["package_version"] == document["client"]["version"]
    assert document["worker"]["dependencies"] == identity["worker_dependencies"]
    assert document["worker"]["pyinstaller_version"] == identity["pyinstaller_version"]


def test_manifest_rejects_requested_version_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="version mismatch"):
        make_manifest.build_manifest(tmp_path, tmp_path / "manifest.json", "9.9.9")


def test_manifest_rejects_staged_identity_mismatch(tmp_path: Path) -> None:
    identity = json.loads((ROOT / "packaging" / "build-identity.json").read_text(encoding="utf-8"))
    identity["client_version"] = "9.9.9"
    (tmp_path / "build-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    with pytest.raises(ValueError, match="staged build identity"):
        make_manifest.build_manifest(tmp_path, tmp_path / "manifest.json", "0.1.0-alpha.1")


def test_manifest_rejects_missing_worker_dependency_fields(tmp_path: Path) -> None:
    identity = json.loads((ROOT / "packaging" / "build-identity.json").read_text(encoding="utf-8"))
    identity["worker_dependencies"].pop("scipy")
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(identity), encoding="utf-8")
    with pytest.raises(ValueError, match="every locked worker dependency"):
        make_manifest.load_identity(path)


def test_runtime_identity_is_copied_and_exposed_to_diagnostics() -> None:
    project = (ROOT / "src" / "SpotAnalysis.App" / "SpotAnalysis.App.csproj").read_text(encoding="utf-8")
    diagnostics = (ROOT / "src" / "SpotAnalysis.App" / "Diagnostics.cs").read_text(encoding="utf-8")
    assert "build-identity.json" in project
    assert "PyInstallerVersion" in diagnostics
    assert "WorkerDependencies" in diagnostics
    assert '["dependencies"] = BuildIdentity.WorkerDependencies' in diagnostics
