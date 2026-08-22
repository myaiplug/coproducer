' Silent launch for Activation Key Maker (no console)
Option Explicit
Dim sh, fso, root, pyw, py, script
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\.venv\Scripts\pythonw.exe"
py = root & "\.venv\Scripts\python.exe"
script = root & "\tools\KeyMaker.py"

If Not fso.FileExists(script) Then
  MsgBox "KeyMaker.py not found:" & vbCrLf & script, vbCritical, "CoProducer Key Maker"
  WScript.Quit 1
End If

sh.Environment("PROCESS")("PYTHONPATH") = root & "\app"
Dim runner
If fso.FileExists(pyw) Then
  runner = """" & pyw & """"
ElseIf fso.FileExists(py) Then
  runner = """" & py & """"
Else
  runner = "py -3.11"
End If

sh.CurrentDirectory = root
sh.Run runner & " -u """ & script & """", 0, False
