@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title TopoLog Batch
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0tools\launch-batch.ps1" %*
if errorlevel 1 (
  echo.
  echo 실행 중 오류가 있었습니다. 위 기록을 확인하세요.
  pause
)
endlocal
