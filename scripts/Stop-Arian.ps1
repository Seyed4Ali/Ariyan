$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot 'data\server.pid'
$serverScript = Join-Path $projectRoot 'server.py'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No managed Arian process found.'; exit }
$arianProcessId = [int](Get-Content -LiteralPath $pidFile -Raw)
if ($arianProcessId -le 0) { throw 'Invalid process id.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $arianProcessId"
if ($process -and $process.CommandLine -and $process.CommandLine.Contains($serverScript)) {
    Stop-Process -Id $arianProcessId
    Remove-Item -LiteralPath $pidFile
    Write-Host 'Arian stopped. Broker SL/TP remain active. Time exits require the server.'
} else {
    throw 'Process identity does not match this project; no process was stopped.'
}
