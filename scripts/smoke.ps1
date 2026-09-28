param([Parameter(Mandatory = $true)][string]$BaseUrl)
$response = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/health/ready" -TimeoutSec 10
if ($response.status -ne "ready") {
    throw "Readiness smoke check failed"
}
Write-Host "Readiness smoke check passed"
