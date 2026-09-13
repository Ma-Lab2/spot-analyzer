[CmdletBinding()]
param(
    [string]$EvidenceRoot = "artifacts\validation\issue-83",
    [string[]]$FixturePath = @(),
    [string]$PackagePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not [System.IO.Path]::IsPathRooted($EvidenceRoot)) { $EvidenceRoot = Join-Path $repoRoot $EvidenceRoot }
$EvidenceRoot = [System.IO.Path]::GetFullPath($EvidenceRoot)
$null = New-Item -ItemType Directory -Force -Path $EvidenceRoot

$fixtures = foreach ($item in $FixturePath) {
    $resolved = Resolve-Path $item
    if ((Get-Item $resolved).PSIsContainer) { Get-ChildItem $resolved -File -Recurse } else { Get-Item $resolved }
}
$fixtureRecords = @($fixtures | Sort-Object FullName -Unique | ForEach-Object {
    $hash = Get-FileHash $_.FullName -Algorithm SHA256
    [pscustomobject]@{
        path = $_.FullName
        file_name = $_.Name
        extension = $_.Extension.ToLowerInvariant()
        bytes = $_.Length
        sha256 = $hash.Hash.ToLowerInvariant()
        read_only = $_.IsReadOnly
    }
})
@{
    status = "PREPARED"
    evidence_boundary = "Inventory and hashes are preparation evidence; no UI, performance, DPI, or clean-machine result is asserted."
    package = if ([string]::IsNullOrWhiteSpace($PackagePath)) { $null } else { [System.IO.Path]::GetFullPath($PackagePath) }
    fixture_count = $fixtureRecords.Count
    fixtures = $fixtureRecords
    required_fixture_inputs = @("three principal real PNGs", "synthetic multi-spot", "isolated bad pixel", "low signal", "saturated", "edge-truncated", "auto-location failure")
    human_acceptance = "NOT RUN"
} | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $EvidenceRoot "fixture-inventory.json") -Encoding utf8

@"
Issue #83 evidence preparation complete.

Fixture hashes are recorded in fixture-inventory.json. Add screenshots, timings, logs,
record IDs, fingerprints, and exports below this evidence root only after observation.
Do not change NOT RUN rows to PASS from this preparation output.
"@ | Set-Content (Join-Path $EvidenceRoot "README.txt") -Encoding utf8
Write-Host "Issue #83 evidence prepared: $EvidenceRoot"
