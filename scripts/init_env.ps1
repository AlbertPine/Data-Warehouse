$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$settings = Join-Path $project '.env'
if (Test-Path -LiteralPath $settings) {
    Write-Output 'Existing project configuration is ready.'
    return
}
$keys = 'CLICKHOUSE_PASSWORD','CLICKHOUSE_BI_PASSWORD','SUPERSET_SECRET_KEY','SUPERSET_DB_PASSWORD','SUPERSET_ADMIN_PASSWORD'
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $lines = foreach ($key in $keys) {
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        $secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        "$key=$secret"
    }
    $stream = [System.IO.File]::Open($settings, [System.IO.FileMode]::CreateNew)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, [System.Text.Encoding]::ASCII)
        try {
            foreach ($line in $lines) {
                $writer.WriteLine($line)
            }
        } finally {
            $writer.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
    Write-Output 'Created local project configuration.'
} finally {
    $rng.Dispose()
}
