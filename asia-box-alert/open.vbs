Option Explicit
Dim fso, sh, dir, pythonw, python, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

If Not fso.FileExists(dir & "\app.py") Then
  MsgBox "Cannot find app.py in:" & vbCrLf & dir, 16, "AsiaBox"
  WScript.Quit 1
End If

pythonw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe")
python = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe")

On Error Resume Next

If fso.FileExists(pythonw) Then
  sh.Run """" & pythonw & """ """ & dir & "\app.py""", 0, False
  If Err.Number = 0 Then WScript.Quit 0
  Err.Clear
End If

cmd = "py -3w """ & dir & "\app.py"""
sh.Run cmd, 0, False
If Err.Number = 0 Then WScript.Quit 0
Err.Clear

cmd = "pythonw """ & dir & "\app.py"""
sh.Run cmd, 0, False
If Err.Number = 0 Then WScript.Quit 0
Err.Clear

If fso.FileExists(python) Then
  sh.Run """" & python & """ """ & dir & "\app.py""", 1, False
  If Err.Number = 0 Then WScript.Quit 0
  Err.Clear
End If

sh.Run "cmd /k py -3 """ & dir & "\app.py""", 1, False
If Err.Number <> 0 Then
  MsgBox "Cannot start Python. Install Python from python.org and check PATH.", 16, "AsiaBox"
End If
