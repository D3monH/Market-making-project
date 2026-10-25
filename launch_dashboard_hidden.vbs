Option Explicit

Dim shell, fso, scriptDir, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir
command = Chr(34) & fso.BuildPath(scriptDir, "start_dashboard.bat") & Chr(34)

' Window style 0 = hidden. Do not wait, so the desktop shortcut returns immediately.
shell.Run command, 0, False
