$ErrorActionPreference = "Stop"

function Convert-FromCodePoints {
    param([int[]]$CodePoints)
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $repoRoot "launch_dashboard_hidden.vbs"

if (-not (Test-Path $launcher)) {
    throw "Launcher not found: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
# Build the localized shortcut suffix at runtime so this source stays ASCII-only.
$appName = "Crypto" + (Convert-FromCodePoints @(0x91CF, 0x5316, 0x7814, 0x7A76, 0x5E73, 0x53F0))
$shortcutPath = Join-Path $desktop ($appName + ".lnk")

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = "Crypto quantitative research platform"
$shortcut.IconLocation = "$env:WINDIR\System32\shell32.dll,167"
$shortcut.Save()

Write-Host ""
Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host $shortcutPath -ForegroundColor Cyan
Write-Host ""
Write-Host "Double-click the shortcut to start the platform." -ForegroundColor Green
