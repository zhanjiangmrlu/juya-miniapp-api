$ErrorActionPreference = "Stop"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    $fallback = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (-not (Test-Path -LiteralPath $fallback)) {
        throw "uv was not found. Install uv before starting the local API."
    }
    $uvPath = $fallback
} else {
    $uvPath = $uv.Source
}

$env:JUYA_ENVIRONMENT = "local"
$env:JUYA_LOCAL_DEV_MODE = "true"
$port = if ($env:PORT) { $env:PORT } else { "8000" }

& $uvPath run uvicorn juya_miniapp_api.main:app --host 0.0.0.0 --port $port
exit $LASTEXITCODE
