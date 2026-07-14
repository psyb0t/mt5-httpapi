; selftest.au3 <match> <logpath>
; Minimal, NO external includes. Confirms AutoIt runs at all, reports whether
; it's elevated, and whether it can find + drive the (elevated) MT5 window.

Opt("WinTitleMatchMode", 2)
Opt("SendKeyDelay", 15)

Local $match   = ($CmdLine[0] >= 1) ? $CmdLine[1] : "?"
Local $logpath = ($CmdLine[0] >= 2) ? $CmdLine[2] : @ScriptDir & "\selftest.log"

Local $h = FileOpen($logpath, 2)
If $h = -1 Then Exit 11
FileWrite($h, "started args=" & $CmdLine[0] & " match=" & $match & @CRLF)
FileWrite($h, "IsAdmin=" & IsAdmin() & @CRLF)

Local $wl = WinList()
FileWrite($h, "windows_total=" & $wl[0][0] & @CRLF)
Local $hMT5 = 0
For $i = 1 To $wl[0][0]
   If $wl[$i][0] <> "" And StringInStr($wl[$i][0], $match) > 0 Then
      FileWrite($h, "  match_win='" & $wl[$i][0] & "'" & @CRLF)
      $hMT5 = $wl[$i][1]
   EndIf
Next
If $hMT5 = 0 Then
   FileWrite($h, "RESULT=FAIL reason=no_mt5_window" & @CRLF)
   FileClose($h)
   Exit 2
EndIf

WinActivate($hMT5)
Sleep(600)
FileWrite($h, "active_title='" & WinGetTitle("[ACTIVE]") & "'" & @CRLF)

Send("^o")
Local $hOpt = WinWait("Options", "", 8)
If $hOpt = 0 Then
   FileWrite($h, "RESULT=FAIL reason=options_did_not_open (input blocked? UIPI/elevation)" & @CRLF)
   FileClose($h)
   Exit 3
EndIf
FileWrite($h, "options_opened title='" & WinGetTitle($hOpt) & "'" & @CRLF)
Send("{ESC}")
FileWrite($h, "RESULT=OK" & @CRLF)
FileClose($h)
Exit 0
