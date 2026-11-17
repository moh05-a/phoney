# One-time setup for the Phoney agent on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Creates a Python 3.13 virtual environment outside the project folder (the project may live in
# OneDrive, and a venv must not be synced), installs the agent, and tells you about the firewall rule.

$ErrorActionPreference = 'Stop'
$repo  = Split-Path -Parent $PSScriptRoot
$venv  = Join-Path $env:USERPROFILE '.venvs\phoney'
$py    = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment in $venv"
    py -3.13 -m venv $venv
    if (-not (Test-Path $py)) { throw 'Python 3.13 was not found. Install it with: winget install Python.Python.3.13' }
}

Write-Host 'Installing the agent...'
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e "$repo\desktop\agent[dev,audio,tray]"
if ($LASTEXITCODE -ne 0) { throw 'Installation failed.' }

Write-Host ''
Write-Host 'Done. Start the agent with:  scripts\start.ps1'
Write-Host ''
Write-Host 'The phone connects to port 7420. If Windows asks whether to allow Python through the firewall,'
Write-Host 'allow it for PRIVATE networks only. To create the rule yourself, run this in an Administrator PowerShell:'
Write-Host ''
Write-Host '  New-NetFirewallRule -DisplayName "Phoney agent" -Direction Inbound -Protocol TCP -LocalPort 7420 -Profile Private -Action Allow'
