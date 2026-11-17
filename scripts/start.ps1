# Starts the Phoney agent in this window. Press Ctrl+C to stop it.
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1

$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $env:USERPROFILE '.venvs\phoney\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Run scripts\setup.ps1 first.'; exit 1 }

Set-Location (Join-Path $repo 'desktop\agent')
& $py -m app @args
