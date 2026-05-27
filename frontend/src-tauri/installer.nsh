; Origin NSIS installer hooks
; Registers Desktop shortcut, protocol handler, and Explorer context menu.

!macro NSIS_HOOK_POSTINSTALL
  ; Desktop shortcut
  CreateShortCut "$DESKTOP\Origin.lnk" "$INSTDIR\origin.exe" "" "$INSTDIR\origin.exe" 0

  ; Register origin:// protocol handler
  WriteRegStr HKCU "Software\Classes\origin" "" "URL:Origin Protocol"
  WriteRegStr HKCU "Software\Classes\origin" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\origin\DefaultIcon" "" "$INSTDIR\origin.exe,0"
  WriteRegStr HKCU "Software\Classes\origin\shell\open\command" "" '"$INSTDIR\origin.exe" "%1"'

  ; Explorer context menu: files
  WriteRegStr HKCU "Software\Classes\*\shell\Origin" "" "Analyze with Origin"
  WriteRegStr HKCU "Software\Classes\*\shell\Origin" "Icon" "$INSTDIR\origin.exe,0"
  WriteRegStr HKCU "Software\Classes\*\shell\Origin\command" "" '"$INSTDIR\origin.exe" --analyze "%1"'

  ; Explorer context menu: folders
  WriteRegStr HKCU "Software\Classes\Directory\shell\Origin" "" "Open in Origin"
  WriteRegStr HKCU "Software\Classes\Directory\shell\Origin" "Icon" "$INSTDIR\origin.exe,0"
  WriteRegStr HKCU "Software\Classes\Directory\shell\Origin\command" "" '"$INSTDIR\origin.exe" --open-folder "%1"'

  ; Explorer context menu: folder background
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\Origin" "" "Open Origin here"
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\Origin" "Icon" "$INSTDIR\origin.exe,0"
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\Origin\command" "" '"$INSTDIR\origin.exe" --open-folder "%V"'

  ; App Paths registration (Start menu search finds Origin)
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\App Paths\origin.exe" "" "$INSTDIR\origin.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\App Paths\origin.exe" "Path" "$INSTDIR"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$DESKTOP\Origin.lnk"

  ; Clean up protocol handler
  DeleteRegKey HKCU "Software\Classes\origin"

  ; Clean up context menu entries
  DeleteRegKey HKCU "Software\Classes\*\shell\Origin"
  DeleteRegKey HKCU "Software\Classes\Directory\shell\Origin"
  DeleteRegKey HKCU "Software\Classes\Directory\Background\shell\Origin"

  ; Clean up App Paths
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\App Paths\origin.exe"
!macroend
