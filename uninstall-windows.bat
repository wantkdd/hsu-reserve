@echo off
rem Hansung Univ. space reservation MCP uninstaller (Windows). Double-click to run.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

for /f "delims=" %%i in ('uv tool dir --bin 2^>nul') do set "BIN=%%i"
if not exist "%BIN%\hsu-reserve-mcp.exe" (
  echo Not installed.
  pause
  exit /b 0
)
"%BIN%\hsu-reserve-mcp.exe" uninstall
uv tool uninstall hsu-reserve
echo Done.
pause
