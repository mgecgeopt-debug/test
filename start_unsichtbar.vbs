' Startet start.bat ohne sichtbares Fenster (fuer den Autostart).
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ordner = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = ordner
shell.Run """" & ordner & "\start.bat""", 0, False
