Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c ""set PYTHONPATH=" & shell.CurrentDirectory & " && conda run -n pdf_ai_reader_314 python src\main.py""", 0, False
