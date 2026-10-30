$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonCommand = Get-Command python -ErrorAction Stop
$version = & $pythonCommand.Source -c 'import sys,struct;print(str(sys.version_info.major)+"."+str(sys.version_info.minor)+"/"+str(struct.calcsize("P")*8))'
if ($version.Trim() -ne '3.13/64') { throw 'Install Python 3.13 64-bit and enable Add Python to PATH, then run setup again.' }
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonCommand.Source -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the output above.' }
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'data') -Force | Out-Null
Write-Host 'Setup complete. Open MetaTrader 5, log into your demo account, then run Start-Arian.cmd.'
