' Silent CoProducer TEST launch (beta bypass) — no console window at all.
Option Explicit
Dim sh, fso, root, pyw, py, script, env
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\.venv\Scripts\pythonw.exe"
py = root & "\.venv\Scripts\python.exe"
script = root & "\CoProducerDesktop.py"

If Not fso.FileExists(script) Then
  MsgBox "CoProducerDesktop.py not found:" & vbCrLf & script, vbCritical, "CoProducer"
  WScript.Quit 1
End If

' Beta bypass for local/dev testing
sh.Environment("PROCESS")("COPRODUCER_BETA_BYPASS") = "1"
sh.Environment("PROCESS")("PYTHONUNBUFFERED") = "1"
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
' 0 = hidden window; False = do not wait
sh.Run runner & " -u """ & script & """", 0, False
