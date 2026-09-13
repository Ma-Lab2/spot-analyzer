[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Development", "PackagedSmoke", "CleanMachine")]
    [string]$Stage,
    [string]$EvidenceRoot = "",
    [string]$PackagePath = "",
    [string]$ExpectedVersion = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $repoRoot ("artifacts\validation\issue-61-{0}" -f $Stage.ToLowerInvariant())
} elseif (-not [System.IO.Path]::IsPathRooted($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $repoRoot $EvidenceRoot
}
$EvidenceRoot = [System.IO.Path]::GetFullPath($EvidenceRoot)
$null = New-Item -ItemType Directory -Force -Path $EvidenceRoot

function Get-RepoRelativePath {
    param([string]$Path)
    $rootUri = New-Object System.Uri(($repoRoot.TrimEnd("\") + "\"))
    $pathUri = New-Object System.Uri($Path)
    return $rootUri.MakeRelativeUri($pathUri).ToString().Replace("/", "\")
}

function Write-CommandResult {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$Arguments,
        [string]$OutputPath,
        [scriptblock]$Action
    )
    $status = "PASS"
    try {
        & $Action *> $OutputPath
        if ($LASTEXITCODE -ne 0) { $status = "FAIL" }
    } catch {
        $_ | Out-String | Set-Content -Path $OutputPath -Encoding utf8
        $status = "BLOCKED"
    }
    [pscustomobject]@{
        name = $Name
        command = ($Command + " " + ($Arguments -join " ")).Trim()
        status = $status
        output = Get-RepoRelativePath $OutputPath
    }
}

$results = [System.Collections.Generic.List[object]]::new()

if ($Stage -eq "Development") {
    $results.Add((Write-CommandResult "Python version" "python" @("--version") (Join-Path $EvidenceRoot "python-version.txt") { & python --version }))
    $results.Add((Write-CommandResult "Python dependencies" "python" @("-m", "pip", "freeze") (Join-Path $EvidenceRoot "pip-freeze.txt") { & python -m pip freeze }))
    $results.Add((Write-CommandResult "Automated tests" "python" @("-m", "pytest", "-q") (Join-Path $EvidenceRoot "pytest.txt") { Push-Location $repoRoot; try { & python -m pytest -q } finally { Pop-Location } }))
    $results.Add((Write-CommandResult ".NET environment" "dotnet" @("--info") (Join-Path $EvidenceRoot "dotnet-info.txt") { & dotnet --info }))
    $results.Add((Write-CommandResult "WPF Release build" "dotnet" @("build", "src/SpotAnalysis.App/SpotAnalysis.App.csproj", "--configuration", "Release") (Join-Path $EvidenceRoot "dotnet-build.txt") { Push-Location $repoRoot; try { & dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj --configuration Release } finally { Pop-Location } }))
    @{
        stage = "development-machine"
        evidence_boundary = "development results do not prove packaged or clean-machine portability"
        results = @($results)
        interactive_ui = "NOT RUN: launch and observe the WPF client using docs/validation/issue-61-staged-validation.md"
    } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $EvidenceRoot "status.json") -Encoding utf8
}
elseif ($Stage -eq "PackagedSmoke") {
    if ([string]::IsNullOrWhiteSpace($PackagePath)) { throw "-PackagePath is required for PackagedSmoke." }
    if (-not [System.IO.Path]::IsPathRooted($PackagePath)) { $PackagePath = Join-Path $repoRoot $PackagePath }
    $PackagePath = (Resolve-Path $PackagePath).Path
    $validatorArguments = @("packaging/validate_alpha_package.py", $PackagePath, "--output", (Join-Path $EvidenceRoot "package-validation.json"))
    if (-not [string]::IsNullOrWhiteSpace($ExpectedVersion)) { $validatorArguments += @("--expected-version", $ExpectedVersion) }
    $results.Add((Write-CommandResult "Package contract validator" "python" $validatorArguments (Join-Path $EvidenceRoot "package-validation.txt") { Push-Location $repoRoot; try { & python @validatorArguments } finally { Pop-Location } }))
    Get-FileHash $PackagePath -Algorithm SHA256 | Out-File (Join-Path $EvidenceRoot "zip-sha256.txt") -Encoding utf8
    $extract = Join-Path $EvidenceRoot "extracted"
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive -LiteralPath $PackagePath -DestinationPath $extract
    $requiredExtracted = @("SpotAnalysis.App.exe", "SpotAnalysis.Worker.exe", "manifest.json", "examples\alpha-example.png")
    $missingExtracted = @($requiredExtracted | Where-Object { -not (Test-Path (Join-Path $extract $_)) })
    if ($missingExtracted.Count -gt 0) { throw "Fresh extraction is missing required files: $($missingExtracted -join ', ')" }
    Get-ChildItem $extract -Recurse -File | Select-Object FullName, Length | Out-File (Join-Path $EvidenceRoot "extracted-files.txt") -Encoding utf8
    Get-FileHash (Join-Path $extract "manifest.json") -Algorithm SHA256 | Out-File (Join-Path $EvidenceRoot "manifest-sha256.txt") -Encoding utf8
    @{
        stage = "local-packaged-client-smoke"
        evidence_boundary = "local package evidence does not prove clean-machine portability"
        results = @($results)
        interactive_ui = "NOT RUN: launch only from extracted and record observations in the packaged smoke matrix"
        extracted_directory = Get-RepoRelativePath $extract
    } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $EvidenceRoot "status.json") -Encoding utf8
}
else {
    @"
Issue #61 clean-machine acceptance is intentionally NOT RUN by this script.

Transfer the preserved ZIP and its recorded SHA-256 to an eligible Windows 11 x64
standard-user machine. Follow packaging/ALPHA-TRIAL-ACCEPTANCE.md, record observed
results and evidence paths for every row, and do not use this file or development
or packaged-smoke evidence as a PASS.
"@ | Set-Content (Join-Path $EvidenceRoot "NOT-RUN.txt") -Encoding utf8
    @{
        stage = "clean-machine-acceptance"
        status = "NOT RUN"
        reason = "Human observation on an eligible clean target is required."
        acceptance_record = "packaging/ALPHA-TRIAL-ACCEPTANCE.md"
    } | ConvertTo-Json | Set-Content (Join-Path $EvidenceRoot "status.json") -Encoding utf8
}

Write-Host ("Issue #61 {0} stage evidence: {1}" -f $Stage, $EvidenceRoot)
Write-Host "Clean-machine acceptance remains NOT RUN unless recorded by a human tester on the target machine."
