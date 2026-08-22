' Silent CoProducer launch — no black console window.
' Double-click this (or the desktop shortcut) for normal use.
' WScript.Shell.Run detaches from parent job objects (agent shells, etc.)
' so the GUI is not killed when the launcher exits.
Option Explicit
Dim sh, fso, root, pyw, py, script, runner, env
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

' Prefer pythonw (no console). Fall back to python.exe with FreeConsole in app.
If fso.FileExists(pyw) Then
  runner = """" & pyw & """"
ElseIf fso.FileExists(py) Then
  runner = """" & py & """"
Else
  runner = "py -3.11"
End If

' Process-local env for this Run (and children)
sh.Environment("PROCESS")("PYTHONPATH") = root & "\app"
sh.Environment("PROCESS")("PYTHONUNBUFFERED") = "1"
' Dev/local: allow beta bypass when already used on this machine
If sh.Environment("PROCESS")("COPRODUCER_BETA_BYPASS") = "" Then
  sh.Environment("PROCESS")("COPRODUCER_BETA_BYPASS") = "1"
End If

sh.CurrentDirectory = root
' 0 = hidden residual console; False = do not wait (process outlives VBS)
sh.Run runner & " -u """ & script & """", 0, False
