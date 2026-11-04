@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title TopoLog Batch
powershell.exe -NoLogo -NoProfile -File "%~dp0tools\launch-batch.ps1" %*
if errorlevel 1 (
  echo.
  echo 실행 결과를 확인하세요. 확인할 항목이 남으면 종료 코드 2로 표시됩니다.
  pause
)
endlocal
