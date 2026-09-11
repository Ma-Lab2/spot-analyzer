[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\artifacts"),
    [string]$Version = "",
    [switch]$SkipRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$identityPath = Join-Path $repoRoot "packaging\build-identity.json"
$identity = Get-Content $identityPath -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = $identity.package_version }
if ($Version -ne $identity.package_version -or $identity.client_version -ne $Version) {
    throw "Build version mismatch: -Version must match build-identity.json package_version and client_version."
}
$outputRoot = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
}
$stage = Join-Path $outputRoot "spot-analysis-$Version-win-x64"
$zipPath = Join-Path $outputRoot "spot-analysis-$Version-win-x64.zip"
$publishPath = Join-Path $outputRoot "wpf-publish"
$workerDistPath = Join-Path $outputRoot "worker-dist"
$workerBuildPath = Join-Path $outputRoot "worker-build"

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

function Require-Command {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required. $InstallHint"
    }
}

function Invoke-PythonCheck {
    param([string]$Code, [string]$FailureMessage)
    $result = (& python -c $Code 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
    return $result
}

if (-not (Test-Path (Join-Path $repoRoot "src\SpotAnalysis.App\SpotAnalysis.App.csproj"))) {
    throw "The repository root is not valid: $repoRoot"
}
Require-Command "dotnet" "Install the .NET 8 SDK."
Require-Command "python" "Install Python 3.12 and the locked project dependencies."

$sdkVersions = (& dotnet --list-sdks 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sdkVersions -notmatch "(?m)^8\.") {
    throw "A .NET 8 SDK is required. Install it before building the portable package."
}
$pythonVersion = Invoke-PythonCheck "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" "Python 3.12 is required."
if ($pythonVersion -ne "3.12") {
    throw "Python 3.12 is required; found $pythonVersion. Activate the supported Python environment."
}
$pythonArchitecture = Invoke-PythonCheck "import struct; print(struct.calcsize('P') * 8)" "Could not determine Python architecture."
if ($pythonArchitecture -ne "64") {
    throw "A 64-bit Python interpreter is required; found ${pythonArchitecture}-bit."
}
$pyInstallerVersion = Invoke-PythonCheck "import PyInstaller; print(PyInstaller.__version__)" "PyInstaller 6.14.2 is required. Install the locked build dependency."
if ($pyInstallerVersion -ne $identity.pyinstaller_version) {
    throw "PyInstaller version mismatch: installed $pyInstallerVersion, identity requires $($identity.pyinstaller_version)."
}
Invoke-PythonCheck @"
import importlib.metadata as metadata
import json
from pathlib import Path
identity = json.loads(Path(r'$identityPath').read_text(encoding='utf-8'))
for name, expected in identity['worker_dependencies'].items():
    actual = metadata.version(name)
    if actual != expected:
        raise SystemExit(f'{name} version mismatch: installed {actual}, identity requires {expected}')
print('locked worker dependencies verified')
"@ "Locked worker dependency versions do not match build identity."

$null = New-Item -ItemType Directory -Force -Path $outputRoot
foreach ($path in @($stage, $publishPath, $workerDistPath, $workerBuildPath)) {
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$null = New-Item -ItemType Directory -Force -Path $stage

$projectPath = Join-Path $repoRoot "src\SpotAnalysis.App\SpotAnalysis.App.csproj"
if (-not $SkipRestore) {
    Invoke-Checked "dotnet" @("restore", $projectPath, "--runtime", "win-x64")
}
$publishArguments = @(
    "publish", $projectPath, "--configuration", "Release", "--runtime", "win-x64",
    "--self-contained", "true", "-p:PublishSingleFile=false", "-p:PublishTrimmed=false",
    "--output", $publishPath
)
if ($SkipRestore) { $publishArguments += "--no-restore" }
Invoke-Checked "dotnet" $publishArguments
Copy-Item -Path (Join-Path $publishPath "*") -Destination $stage -Recurse -Force

$specPath = Join-Path $repoRoot "packaging\worker.spec"
Invoke-Checked "python" @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $workerDistPath,
    "--workpath", $workerBuildPath, $specPath
)
$workerRoot = Join-Path $workerDistPath "SpotAnalysis.Worker"
if (-not (Test-Path (Join-Path $workerRoot "SpotAnalysis.Worker.exe"))) {
    throw "PyInstaller did not produce SpotAnalysis.Worker.exe in $workerRoot"
}
Copy-Item -Path (Join-Path $workerRoot "*") -Destination $stage -Recurse -Force

# The development project copies sources for local runs; a portable package carries
# only the published client and frozen worker, never source or test trees.
Get-ChildItem $stage -Recurse -File | Where-Object { $_.Extension -in @(".py", ".cs", ".xaml") } | Remove-Item -Force
Get-ChildItem $stage -Recurse -Directory | Where-Object { $_.Name -in @("spot_analyzer", "tests", "prototype", ".git", "__pycache__", ".pytest_cache", "obj", "bin") } | Sort-Object FullName -Descending | Remove-Item -Recurse -Force

$null = New-Item -ItemType Directory -Force -Path (Join-Path $stage "examples"), (Join-Path $stage "profiles")
Copy-Item (Join-Path $repoRoot "examples\alpha-example.png") (Join-Path $stage "examples\alpha-example.png") -Force
Copy-Item (Join-Path $repoRoot "packaging\QUICKSTART.md") (Join-Path $stage "QUICKSTART.md") -Force
Copy-Item (Join-Path $repoRoot "packaging\ALPHA-TRIAL-ACCEPTANCE.md") (Join-Path $stage "ALPHA-TRIAL-ACCEPTANCE.md") -Force
Copy-Item (Join-Path $repoRoot "packaging\THIRD-PARTY-NOTICES.txt") (Join-Path $stage "THIRD-PARTY-NOTICES.txt") -Force
Copy-Item (Join-Path $repoRoot "README.md") (Join-Path $stage "README.md") -Force
Copy-Item (Join-Path $repoRoot "profiles\profile-identities.json") (Join-Path $stage "profiles\profile-identities.json") -Force
Copy-Item $identityPath (Join-Path $stage "build-identity.json") -Force

$manifestPath = Join-Path $stage "manifest.json"
Invoke-Checked "python" @(
    (Join-Path $repoRoot "packaging\make_manifest.py"), "--staging", $stage,
    "--output", $manifestPath, "--version", $Version
)

$forbidden = Get-ChildItem $stage -Recurse -File | Where-Object {
    $_.FullName -match "\\(tests|prototype|\.git|__pycache__|\.pytest_cache|obj|bin)(\\|$)" -or
    $_.Extension -in @(".py", ".cs", ".xaml")
}
if ($forbidden) {
    throw "Portable package contains excluded source/test files: $($forbidden.FullName -join ', ')"
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Portable Alpha package: $zipPath"
Write-Host "Staged package directory: $stage"
