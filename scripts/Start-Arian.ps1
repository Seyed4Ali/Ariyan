$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$url = 'http://127.0.0.1:8765'
try {
    $null = Invoke-WebRequest -Uri "$url/api/session" -TimeoutSec 2 -UseBasicParsing
    Write-Host "Arian is already available at $url"
} catch {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $pythonExecutable = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python -ErrorAction Stop).Source }
    $serverScript = Join-Path $projectRoot 'server.py'
    Start-Process -FilePath $pythonExecutable -ArgumentList @('-u', ('"{0}"' -f $serverScript)) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot 'data\server.log') -RedirectStandardError (Join-Path $projectRoot 'data\server-error.log')
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $null = Invoke-WebRequest -Uri "$url/api/session" -TimeoutSec 1 -UseBasicParsing
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw 'Arian did not start. Check data\server-error.log.' }
}
Start-Process $url
