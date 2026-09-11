[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\artifacts"),
    [string]$Version = "0.1.0-alpha.1",
    [switch]$SkipRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

if (-not (Test-Path (Join-Path $repoRoot "src\SpotAnalysis.App\SpotAnalysis.App.csproj"))) {
    throw "The repository root is not valid: $repoRoot"
}
Require-Command "dotnet" "Install the .NET 8 SDK."
Require-Command "python" "Install Python 3.12 and the locked project dependencies."

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

$pyInstallerCheck = & python -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install it with: python -m pip install pyinstaller"
}
$specPath = Join-Path $repoRoot "packaging\worker.spec"
Invoke-Checked "python" @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--distpath", $workerDistPath,
    "--workpath", $workerBuildPath, "--specpath", (Join-Path $repoRoot "packaging"), $specPath
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
Copy-Item (Join-Path $repoRoot "packaging\THIRD-PARTY-NOTICES.txt") (Join-Path $stage "THIRD-PARTY-NOTICES.txt") -Force
Copy-Item (Join-Path $repoRoot "README.md") (Join-Path $stage "README.md") -Force
Copy-Item (Join-Path $repoRoot "profiles\profile-identities.json") (Join-Path $stage "profiles\profile-identities.json") -Force

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
