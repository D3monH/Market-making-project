$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $repoRoot "launch_dashboard_hidden.vbs"

if (-not (Test-Path $launcher)) {
    throw "找不到启动器：$launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Crypto量化研究平台.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = "Crypto 多周期预测与机构 Alpha 研究平台"
$shortcut.IconLocation = "$env:WINDIR\System32\shell32.dll,167"
$shortcut.Save()

Write-Host ""
Write-Host "已创建桌面快捷方式：" -ForegroundColor Green
Write-Host $shortcutPath -ForegroundColor Cyan
Write-Host ""
Write-Host "以后双击桌面的『Crypto量化研究平台』即可启动。" -ForegroundColor Green
