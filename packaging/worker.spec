"""PyInstaller spec for the portable Alpha worker."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent
WORKER = ROOT / "src" / "SpotAnalysis.Worker"

analysis = Analysis(
    [str(WORKER / "worker.py")],
    pathex=[str(WORKER), str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "packaging" / "build-identity.json"), ".")],
    hiddenimports=collect_submodules("spot_analyzer"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SpotAnalysis.Worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="SpotAnalysis.Worker",
)
