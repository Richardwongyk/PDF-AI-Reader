Option Explicit

Dim shell, fso, projectDir, userProfile, envDir, pythonw, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
userProfile = shell.ExpandEnvironmentStrings("%USERPROFILE%")

envDir = userProfile & "\.conda\envs\pdf_ai_reader_3144"
If Not fso.FileExists(envDir & "\pythonw.exe") Then
    envDir = userProfile & "\anaconda3\envs\pdf_ai_reader_3144"
End If
If Not fso.FileExists(envDir & "\pythonw.exe") Then
    envDir = userProfile & "\.conda\envs\pdf_ai_reader_314"
End If
If Not fso.FileExists(envDir & "\pythonw.exe") Then
    envDir = userProfile & "\anaconda3\envs\pdf_ai_reader_314"
End If

pythonw = envDir & "\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "PDF AI Reader Python environment was not found.", vbCritical, "PDF AI Reader"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectDir
shell.Environment("PROCESS")("PYTHONPATH") = projectDir
shell.Environment("PROCESS")("CONDA_PREFIX") = envDir
shell.Environment("PROCESS")("CONDA_DEFAULT_ENV") = fso.GetFileName(envDir)
shell.Environment("PROCESS")("PATH") = _
    envDir & ";" & _
    envDir & "\Scripts;" & _
    envDir & "\Library\bin;" & _
    envDir & "\Library\usr\bin;" & _
    envDir & "\Library\mingw-w64\bin;" & _
    shell.Environment("PROCESS")("PATH")

command = """" & pythonw & """ """ & projectDir & "\src\main.py"""
shell.Run command, 0, False
