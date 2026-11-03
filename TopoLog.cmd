@echo off
setlocal
title TopoLog
powershell.exe -NoLogo -NoProfile -File "%~dp0tools\run-demo.ps1" -OpenReport
if errorlevel 1 (
  echo.
  echo TopoLog stopped. Review the warning and the run record above.
  pause
)
endlocal
