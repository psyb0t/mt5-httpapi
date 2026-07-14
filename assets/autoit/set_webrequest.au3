; set_webrequest.au3  <window_match>  <urlfile>  <logpath>
; Adds the URLs in <urlfile> (one per line) to MT5's Tools->Options->Expert
; Advisors "Allow WebRequest for listed URL" list, for the terminal whose
; window title contains <window_match> (the login). NO #includes (an undefined
; include function pops a blocking error dialog).
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

; ---- args ----
If $CmdLine[0] < 3 Then Exit 10
Global $match   = $CmdLine[1]
Global $urlfile = $CmdLine[2]
Global $logpath = $CmdLine[3]

$gLog = FileOpen($logpath, 2)
If $gLog = -1 Then Exit 11
LogW("=== set_webrequest match=" & $match & " ===")

; ---- read urls ----
Global $raw = FileRead($urlfile)
Global $urls = StringSplit(StringStripCR($raw), @LF)
Local $n_urls = 0
For $i = 1 To $urls[0]
   If StringStripWS($urls[$i], 3) <> "" Then $n_urls += 1
Next
LogW("urls_in_file=" & $n_urls)

; ---- find + activate MT5 ----
Local $wl = WinList()
Local $hMT5 = 0
For $i = 1 To $wl[0][0]
   If $wl[$i][0] <> "" And StringInStr($wl[$i][0], $match) > 0 Then $hMT5 = $wl[$i][1]
Next
If $hMT5 = 0 Then
   LogW("RESULT=FAIL reason=mt5_window_not_found")
   FileClose($gLog)
   Exit 2
EndIf
WinActivate($hMT5)
Sleep(600)

; ---- open Options ----
Send("^o")
Local $hOpt = WinWait("Options", "", 10)
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
