$ErrorActionPreference = "Stop"
$processType = if ($env:JUYA_PROCESS_TYPE) { $env:JUYA_PROCESS_TYPE } else { "api" }
$logLevel = if ($env:JUYA_LOG_LEVEL) { $env:JUYA_LOG_LEVEL } else { "INFO" }

if ($processType -eq "api") {
    $port = if ($env:PORT) { $env:PORT } else { "8000" }
    & uvicorn juya_miniapp_api.main:app --host 0.0.0.0 --port $port
    exit $LASTEXITCODE
}

if ($processType -eq "worker") {
    $arguments = @(
        "-A", "juya_miniapp_api.infrastructure.tasks.celery_app:celery_app",
        "worker", "--loglevel", $logLevel
    )
    if ($env:JUYA_ENABLE_BEAT -eq "true") {
        $schedulePath = Join-Path ([System.IO.Path]::GetTempPath()) "celerybeat-schedule"
        $arguments += @("-B", "--schedule", $schedulePath)
    }
    & celery @arguments
    exit $LASTEXITCODE
}

Write-Error "Unsupported JUYA_PROCESS_TYPE: $processType"
exit 64
