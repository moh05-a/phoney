# Makes the agent start, without a window, every time you sign in to Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1            # turn on
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove    # turn off
#
# It places a shortcut in your Startup folder. The agent has to run in your own session (not as a
# Windows service), because a service cannot launch apps or send media keys to your desktop.

param([switch]$Remove)

$repo     = Split-Path -Parent $PSScriptRoot
$pythonw  = Join-Path $env:USERPROFILE '.venvs\phoney\Scripts\pythonw.exe'
$shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Phoney agent.lnk'

if ($Remove) {
    if (Test-Path $shortcut) { Remove-Item $shortcut; Write-Host 'Autostart removed.' } else { Write-Host 'Autostart was not set.' }
    exit 0
}
if (-not (Test-Path $pythonw)) { Write-Host 'Run scripts\setup.ps1 first.'; exit 1 }

$link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$link.TargetPath       = $pythonw
$link.Arguments        = '-m app'
$link.WorkingDirectory = Join-Path $repo 'desktop\agent'
$link.Description      = 'Phoney desktop agent'
$link.Save()
Write-Host "Autostart set: $shortcut"
Write-Host 'The agent will start at your next sign-in. Its log is in %APPDATA%\Phoney\logs\agent.log'
