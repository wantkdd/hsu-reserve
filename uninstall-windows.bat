@echo off
setlocal
chcp 65001 >nul
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
for /f "delims=" %%i in ('uv tool dir --bin') do set "BIN=%%i"
"%BIN%\hsu-reserve-mcp.exe" uninstall
if errorlevel 1 goto :fail
uv tool uninstall hsu-reserve
if errorlevel 1 goto :fail
pause
exit /b 0
:fail
echo Removal failed. Check the message above.
pause
exit /b 1
