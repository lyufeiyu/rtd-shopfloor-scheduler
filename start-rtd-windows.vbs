Option Explicit

Dim shell, fileSystem, folder, startPrefix, scriptPath, command, exitCode

If WScript.Arguments.Count = 1 Then
    scriptPath = WScript.Arguments(0)
ElseIf WScript.Arguments.Count = 0 Then
    Set fileSystem = CreateObject("Scripting.FileSystemObject")
    folder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
    startPrefix = ChrW(&H542F) & ChrW(&H52A8)
    scriptPath = fileSystem.BuildPath(folder, startPrefix & "RTD-Windows.ps1")
Else
    MsgBox "The RTD launcher received invalid arguments.", vbCritical, "RTD startup"
    WScript.Quit 2
End If

command = "powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File """ _
    & scriptPath & """"

Set shell = CreateObject("WScript.Shell")
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    MsgBox "RTD failed to start. Check storage\logs\launcher-windows-error.log.", _
        vbCritical, "RTD startup"
End If

WScript.Quit exitCode
