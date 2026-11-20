@echo off
rem Hansung Univ. space reservation MCP installer (Windows). Double-click to run.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

echo === Hansung reservation MCP installer ===

where uv >nul 2>nul
if errorlevel 1 (
  echo [1/3] Installing uv ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  where uv >nul 2>nul
  if errorlevel 1 goto :fail
) else (
  echo [1/3] uv found
)

echo [2/3] Installing program ...
uv tool install --force --reinstall --quiet .
if errorlevel 1 goto :fail

echo [3/3] Setup
for /f "delims=" %%i in ('uv tool dir --bin') do set "BIN=%%i"
"%BIN%\hsu-reserve-mcp.exe" setup
if errorlevel 1 goto :fail

echo.
pause
exit /b 0

:fail
echo.
echo Installation failed. Check the messages above.
pause
exit /b 1
