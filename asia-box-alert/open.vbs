Option Explicit
' Double-click launcher. Uses the same py -3 / python logic as run.bat.
Dim fso, sh, dir, cmd, rc
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

If Not fso.FileExists(dir & "\app.py") Then
  MsgBox "Cannot find app.py in:" & vbCrLf & dir, 16, "AsiaBox"
  WScript.Quit 1
End If

' 1) py -3  (same as run.bat on your new PC)
cmd = "cmd /c cd /d """ & dir & """ && py -3 updater.py >nul 2>&1 & py -3 app.py"
rc = sh.Run(cmd, 0, True)
If rc = 0 Then WScript.Quit 0

' 2) python
cmd = "cmd /c cd /d """ & dir & """ && python updater.py >nul 2>&1 & python app.py"
rc = sh.Run(cmd, 0, True)
If rc = 0 Then WScript.Quit 0

' 3) show run.bat so you can read the error
MsgBox "open.vbs could not start Python." & vbCrLf & vbCrLf & _
  "Opening run.bat instead. Install Python from python.org and check Add to PATH.", 48, "AsiaBox"
sh.Run """" & dir & "\run.bat""", 1, False
