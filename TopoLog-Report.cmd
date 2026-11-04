@echo off
setlocal
if "%~1"=="" (
  call "%~dp0TopoLog-Batch.cmd" --refresh-report all
) else (
  call "%~dp0TopoLog-Batch.cmd" --refresh-report %*
)
endlocal
