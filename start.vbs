' VBScript wrapper to run start.bat without showing a console window.
' Double-click this file to launch RenewalTracker silently.
'
' The Flask server will start and automatically open in your browser.
' Errors will appear in a popup dialog.

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get the directory where this script is located
strScriptPath = WScript.ScriptFullName
strScriptDir = objFSO.GetParentFolderName(strScriptPath)

' Run start.bat with hidden window (0 = hide, True = wait for completion)
intReturn = objShell.Run("""" & strScriptDir & "\start.bat""", 0, True)

' If there was an error, show a dialog
If intReturn <> 0 Then
    objShell.Popup "RenewalTracker failed to start. Check that Python 3.11+ is installed." & vbCrLf & vbCrLf & _
        "Error code: " & intReturn & vbCrLf & vbCrLf & _
        "Install Python from: https://www.python.org/downloads/" & vbCrLf & _
        "(Make sure to tick 'Add python.exe to PATH')", 0, "RenewalTracker Error", 16
End If
