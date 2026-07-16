; selftest.au3 <match> [<pid>] <logpath>
; Minimal, NO external includes. Confirms AutoIt runs at all, reports whether
; it's elevated, and whether it can find + drive the (elevated) MT5 window.
; With 3 args the second is the terminal64.exe pid (0 = unknown) — same-login
; terminal clones share the exact same window title, so only the pid pins one.

Opt("WinTitleMatchMode", 2)
Opt("SendKeyDelay", 15)

Local $match = ($CmdLine[0] >= 1) ? $CmdLine[1] : "?"
Local $pid = 0
Local $logpath = @ScriptDir & "\selftest.log"
If $CmdLine[0] >= 3 Then
   $pid     = Int($CmdLine[2])
   $logpath = $CmdLine[3]
ElseIf $CmdLine[0] = 2 Then
   $logpath = $CmdLine[2]
EndIf

Local $h = FileOpen($logpath, 2)
If $h = -1 Then Exit 11
FileWrite($h, "started args=" & $CmdLine[0] & " match=" & $match & " pid=" & $pid & @CRLF)
FileWrite($h, "IsAdmin=" & IsAdmin() & @CRLF)

Local $wl = WinList()
FileWrite($h, "windows_total=" & $wl[0][0] & @CRLF)
Local $hMT5 = 0
For $i = 1 To $wl[0][0]
   If $wl[$i][0] <> "" And StringInStr($wl[$i][0], $match) > 0 Then
      FileWrite($h, "  match_win='" & $wl[$i][0] & "' visible=" _
               & (BitAND(WinGetState($wl[$i][1]), 2) > 0 ? 1 : 0) _
               & " pid=" & WinGetProcess($wl[$i][1]) & @CRLF)
      If BitAND(WinGetState($wl[$i][1]), 2) = 0 Then ContinueLoop
      If $pid > 0 And WinGetProcess($wl[$i][1]) <> $pid Then ContinueLoop
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
If $hOpt <> 0 And $pid > 0 And WinGetProcess($hOpt) <> $pid Then $hOpt = 0
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
