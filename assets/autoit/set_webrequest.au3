; set_webrequest.au3  <window_match>  [<pid>]  <urlfile>  <logpath>
; Adds the URLs in <urlfile> (one per line) to MT5's Tools->Options->Expert
; Advisors "Allow WebRequest for listed URL" list, for the terminal whose
; window title contains <window_match> (the login). NO #includes (an undefined
; include function pops a blocking error dialog).
;
; <pid> (optional, 0 = unknown) is the terminal64.exe process id. Cloned
; terminals of the same account have IDENTICAL window titles, so the login is
; ambiguous — the pid is the only thing that pins the right terminal. When
; given, both the main window and the Options dialog are matched by owner pid.
;
; MT5's URL list is a SysListView32 with a greyed "add new URL like ..." row at
; the bottom; double-clicking it opens an inline edit. We type the URL + Enter,
; which commits and produces a fresh add-row below. Then OK.

Opt("WinTitleMatchMode", 2)
Opt("SendKeyDelay", 20)
Opt("SendKeyDownDelay", 5)
Opt("MouseCoordMode", 2)   ; coords relative to the control for ControlClick

Global $gLog = -1
Func LogW($s)
   If $gLog <> -1 Then FileWrite($gLog, $s & @CRLF)
EndFunc

; Scan Button1..25 for the first VISIBLE control whose text contains $needle.
Func FindButtonByText($hWin, $needle)
   For $n = 1 To 25
      Local $ctrl = "Button" & $n
      ControlGetPos($hWin, "", $ctrl)
      If @error Then ContinueLoop
      If ControlCommand($hWin, "", $ctrl, "IsVisible", "") <> 1 Then ContinueLoop
      If StringInStr(ControlGetText($hWin, "", $ctrl), $needle) > 0 Then Return $ctrl
   Next
   Return ""
EndFunc

Func DumpControls($hWin, $tag)
   LogW("  [" & $tag & "] classlist=" & StringReplace(WinGetClassList($hWin), @LF, " | "))
EndFunc

; Log the current listview rows (so we can verify what MT5 actually holds).
Func DumpList($hWin, $tag)
   Local $cnt = ControlListView($hWin, "", "SysListView321", "GetItemCount")
   Local $s = ""
   For $r = 0 To $cnt - 1
      $s &= "[" & ControlListView($hWin, "", "SysListView321", "GetText", $r, 0) & "] "
   Next
   LogW("  list(" & $tag & ") count=" & $cnt & " items=" & $s)
   Return $cnt
EndFunc

; Visible top-level window for this terminal: by owner pid when known
; (title as tie-break among the pid's windows), else by title substring.
; WinList() alone is NOT enough — it returns hidden windows, and same-login
; terminal clones share the exact same title.
Func FindMainWindow($match, $pid)
   Local $wl = WinList()
   Local $best = 0
   For $i = 1 To $wl[0][0]
      Local $h = $wl[$i][1]
      Local $title = $wl[$i][0]
      If $title = "" Then ContinueLoop
      If BitAND(WinGetState($h), 2) = 0 Then ContinueLoop   ; visible only
      If $pid > 0 Then
         If WinGetProcess($h) <> $pid Then ContinueLoop
         If StringInStr($title, $match) > 0 Then Return $h
         If $best = 0 Then $best = $h
      Else
         If StringInStr($title, $match) > 0 Then $best = $h
      EndIf
   Next
   Return $best
EndFunc

; Focus the terminal and open Tools->Options (Ctrl+O), retrying, and only
; accept an Options window owned by our pid (another terminal's dialog, or
; any window with "Options" in its title, must not be driven).
Func OpenOptions($hMT5, $pid)
   For $try = 1 To 3
      WinActivate($hMT5)
      If WinWaitActive($hMT5, "", 3) = 0 Then
         LogW("  activate attempt " & $try & " failed (active='" & WinGetTitle("[ACTIVE]") & "')")
         ContinueLoop
      EndIf
      Send("^o")
      Local $t = TimerInit()
      While TimerDiff($t) < 5000
         Local $hOpt = WinWait("Options", "", 1)
         If $hOpt <> 0 Then
            If $pid = 0 Or WinGetProcess($hOpt) = $pid Then Return $hOpt
            LogW("  ignoring foreign Options window (pid=" & WinGetProcess($hOpt) & ")")
         EndIf
      WEnd
      LogW("  ctrl+o attempt " & $try & ": no Options dialog")
   Next
   Return 0
EndFunc

; ---- args ----
; 4 args: match, pid, urlfile, logpath. 3 args (legacy caller): match,
; urlfile, logpath with pid unknown.
If $CmdLine[0] < 3 Then Exit 10
Global $match = $CmdLine[1]
Global $pid = 0
Global $urlfile, $logpath
If $CmdLine[0] >= 4 Then
   $pid     = Int($CmdLine[2])
   $urlfile = $CmdLine[3]
   $logpath = $CmdLine[4]
Else
   $urlfile = $CmdLine[2]
   $logpath = $CmdLine[3]
EndIf

$gLog = FileOpen($logpath, 2)
If $gLog = -1 Then Exit 11
LogW("=== set_webrequest match=" & $match & " pid=" & $pid & " ===")

; ---- read urls ----
Global $raw = FileRead($urlfile)
Global $urls = StringSplit(StringStripCR($raw), @LF)
Local $n_urls = 0
For $i = 1 To $urls[0]
   If StringStripWS($urls[$i], 3) <> "" Then $n_urls += 1
Next
LogW("urls_in_file=" & $n_urls)

; ---- find + activate MT5 ----
Local $hMT5 = FindMainWindow($match, $pid)
If $hMT5 = 0 Then
   LogW("RESULT=FAIL reason=mt5_window_not_found")
   FileClose($gLog)
   Exit 2
EndIf
LogW("mt5='" & WinGetTitle($hMT5) & "' win_pid=" & WinGetProcess($hMT5))

; ---- open Options ----
Local $hOpt = OpenOptions($hMT5, $pid)
If $hOpt = 0 Then
   LogW("RESULT=FAIL reason=options_not_found")
   FileClose($gLog)
   Exit 3
EndIf
WinActivate($hOpt)
Sleep(400)

; ---- ensure the Expert Advisors tab is active (WebRequest checkbox present) ----
Local $cb = FindButtonByText($hOpt, "WebRequest")
Local $tries = 0
While $cb = "" And $tries < 12
   Send("^{TAB}")          ; cycle property-sheet tabs
   Sleep(250)
   $cb = FindButtonByText($hOpt, "WebRequest")
   $tries += 1
WEnd
If $cb = "" Then
   LogW("RESULT=FAIL reason=webrequest_checkbox_not_found")
   Send("{ESC}")
   FileClose($gLog)
   Exit 4
EndIf
LogW("checkbox=" & $cb & " text='" & ControlGetText($hOpt, "", $cb) & "'")

; ---- ensure the checkbox is checked (real click triggers MT5's enable logic) ----
If ControlCommand($hOpt, "", $cb, "IsChecked", "") <> 1 Then
   ControlClick($hOpt, "", $cb)
   Sleep(300)
   LogW("checkbox now checked=" & ControlCommand($hOpt, "", $cb, "IsChecked", ""))
Else
   LogW("checkbox already checked")
EndIf

; ---- the URL list ----
Local $lp = ControlGetPos($hOpt, "", "SysListView321")
If @error Then
   LogW("RESULT=FAIL reason=listview_not_found")
   Send("{ESC}")
   FileClose($gLog)
   Exit 5
EndIf
LogW("list xywh=" & $lp[0] & "," & $lp[1] & "," & $lp[2] & "," & $lp[3])
Local $rowH = 17

; ---- clear existing entries (a PUT sets the full list) ----
; Select the first data row and press Delete, repeatedly. The greyed "add new
; URL" row can't be deleted, so extra iterations are harmless no-ops.
DumpList($hOpt, "before-clear")
For $k = 1 To 40
   Local $before = ControlListView($hOpt, "", "SysListView321", "GetItemCount")
   If $before <= 0 Then ExitLoop
   ControlClick($hOpt, "", "SysListView321", "left", 1, Int($lp[2] / 2), 10)
   Sleep(60)
   Send("{DELETE}")
   Sleep(90)
   If ControlListView($hOpt, "", "SysListView321", "GetItemCount") >= $before Then ExitLoop
Next
DumpList($hOpt, "after-clear")

; ---- add each url ----
Local $added = 0
Local $idx = 0
For $i = 1 To $urls[0]
   Local $u = StringStripWS($urls[$i], 3)
   If $u = "" Then ContinueLoop
   ; add-row is the last row: relative Y grows by rowH per existing entry
   Local $ry = 10 + ($idx * $rowH)
   If $ry > $lp[3] - 4 Then $ry = $lp[3] - 8       ; clamp into the control
   ControlClick($hOpt, "", "SysListView321", "left", 2, Int($lp[2] / 2), $ry)
   Sleep(250)
   If $i = 1 Then DumpControls($hOpt, "after-first-dblclick")
   ; type the URL into whatever inline edit appeared, then commit
   Send("^a")            ; select any placeholder text
   Send($u, 1)           ; raw send (URLs contain / : . which are literal in raw mode)
   Sleep(120)
   Send("{ENTER}")
   Sleep(300)
   LogW("typed[" & $idx & "] ry=" & $ry & " url=" & $u)
   $added += 1
   $idx += 1
Next
DumpList($hOpt, "final")

; ---- confirm with OK ----
Local $ok = FindButtonByText($hOpt, "OK")
If $ok = "" Then $ok = "Button8"
ControlClick($hOpt, "", $ok)
Sleep(400)

LogW("RESULT=OK added=" & $added)
FileClose($gLog)
Exit 0
