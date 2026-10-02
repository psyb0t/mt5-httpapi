; inspect_options.au3  <window_match>  <logpath>
; Non-destructive inspector for MT5's Tools->Options dialog. NO #includes
; (an undefined include function pops a blocking error dialog). Finds the MT5
; window containing <window_match> (the login), opens Options (Ctrl+O), walks
; every tab, and logs the VISIBLE controls (ClassNN, pos, text) so we can
; identify the Expert Advisors tab index + the WebRequest checkbox/list, then
; cancels with Esc.

Opt("WinTitleMatchMode", 2)
Opt("SendKeyDelay", 15)

Global $gLog = -1

Func LogW($s)
   If $gLog <> -1 Then FileWrite($gLog, $s & @CRLF)
EndFunc

Func DumpVisible($hWin, $tag)
   LogW("--- " & $tag & " ---")
   Local $cl = WinGetClassList($hWin)
   Local $arr = StringSplit(StringStripCR($cl), @LF)
   Local $seen = "|"
   For $i = 1 To $arr[0]
      Local $c = $arr[$i]
      If $c = "" Then ContinueLoop
      If StringInStr($seen, "|" & $c & "|") > 0 Then ContinueLoop
      $seen &= $c & "|"
      For $n = 1 To 80
         Local $ctrl = $c & $n
         Local $pos = ControlGetPos($hWin, "", $ctrl)
         If @error Then ExitLoop
         Local $vis = ControlCommand($hWin, "", $ctrl, "IsVisible", "")
         If $vis = 1 Then
            Local $txt = ControlGetText($hWin, "", $ctrl)
            LogW("  " & $ctrl _
                 & " xywh=" & $pos[0] & "," & $pos[1] & "," & $pos[2] & "," & $pos[3] _
                 & " text='" & StringLeft(StringReplace($txt, @CRLF, " "), 70) & "'")
         EndIf
      Next
   Next
EndFunc

; ---- args ----
; 3 args: match, pid, logpath. 2 args (legacy): match, logpath.
If $CmdLine[0] < 2 Then Exit 10
Global $match = $CmdLine[1]
Global $pid = 0
Global $logpath
If $CmdLine[0] >= 3 Then
   $pid     = Int($CmdLine[2])
   $logpath = $CmdLine[3]
Else
   $logpath = $CmdLine[2]
EndIf

$gLog = FileOpen($logpath, 2)
If $gLog = -1 Then Exit 11
LogW("=== inspect_options match=" & $match & " pid=" & $pid & " ===")

; ---- find + activate MT5 (visible windows only; by owner pid when known —
; same-login terminal clones share the exact same window title) ----
Local $wl = WinList()
Local $hMT5 = 0
For $i = 1 To $wl[0][0]
   Local $h = $wl[$i][1]
   If $wl[$i][0] = "" Then ContinueLoop
   If BitAND(WinGetState($h), 2) = 0 Then ContinueLoop
   If $pid > 0 And WinGetProcess($h) <> $pid Then ContinueLoop
   If StringInStr($wl[$i][0], $match) > 0 Then $hMT5 = $h
Next
If $hMT5 = 0 Then
   LogW("RESULT=FAIL reason=mt5_window_not_found")
   FileClose($gLog)
   Exit 2
EndIf
WinActivate($hMT5)
Sleep(600)
LogW("mt5='" & WinGetTitle($hMT5) & "' win_pid=" & WinGetProcess($hMT5))

; ---- open Options ----
Send("^o")
Local $hOpt = WinWait("Options", "", 10)
If $hOpt <> 0 And $pid > 0 And WinGetProcess($hOpt) <> $pid Then $hOpt = 0
If $hOpt = 0 Then
   LogW("RESULT=FAIL reason=options_not_found")
   FileClose($gLog)
   Exit 3
EndIf
WinActivate($hOpt)
Sleep(300)
LogW("options='" & WinGetTitle($hOpt) & "'")
LogW("classlist=" & StringReplace(WinGetClassList($hOpt), @LF, " | "))

; ---- find tab control, walk tabs ----
Local $tabClass = ""
Local $cls = StringSplit(StringStripCR(WinGetClassList($hOpt)), @LF)
For $i = 1 To $cls[0]
   If StringInStr($cls[$i], "SysTabControl32") > 0 Then
      $tabClass = $cls[$i]
      ExitLoop
   EndIf
Next
LogW("tabClass='" & $tabClass & "'")

If $tabClass <> "" Then
   Local $cnt = ControlCommand($hOpt, "", $tabClass, "GetItemCount", "")
   LogW("tab_count=" & $cnt)
   If $cnt >= 1 Then
      For $t = 1 To $cnt
         ControlCommand($hOpt, "", $tabClass, "CurrentTab", $t)
         Sleep(250)
         DumpVisible($hOpt, "tab#" & $t)
      Next
   Else
      DumpVisible($hOpt, "single")
   EndIf
Else
   DumpVisible($hOpt, "no-tabctl")
EndIf

LogW("RESULT=OK")
Send("{ESC}")
FileClose($gLog)
Exit 0
