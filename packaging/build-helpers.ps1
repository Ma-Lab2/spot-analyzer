Set-StrictMode -Version Latest

function Compress-WithRetry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,
        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,
        [int]$Attempts = 8,
        [int]$InitialDelayMilliseconds = 150
    )

    if ($Attempts -lt 1) {
        throw "Attempts must be at least one."
    }

    $destinationDirectory = Split-Path -Parent $DestinationPath
    if (-not [string]::IsNullOrWhiteSpace($destinationDirectory)) {
        $null = New-Item -ItemType Directory -Force -Path $destinationDirectory
    }

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            if (Test-Path $DestinationPath) {
                Remove-Item $DestinationPath -Force
            }
            Compress-Archive -Path $SourcePath -DestinationPath $DestinationPath -CompressionLevel Optimal -Force -ErrorAction Stop
            return
        }
        catch [System.IO.IOException] {
            if ($attempt -eq $Attempts) {
                throw
            }
            $delay = $InitialDelayMilliseconds * $attempt
            Write-Verbose "Compression attempt $attempt failed because a file is temporarily unavailable; retrying in ${delay}ms."
            Start-Sleep -Milliseconds $delay
        }
    }

    throw "Compression failed without a captured exception."
}
