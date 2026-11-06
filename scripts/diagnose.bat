@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
echo === SoundMux diagnostics: send this whole text to the developer ===
ver
echo Program folder: %~dp0
echo.
call :check mkvmerge --version
call :check ffmpeg -version
call :check ffprobe -version
echo --- bundled python
"%~dp0runtime\python\python.exe" -I -c "import sys, numpy; print('python', sys.version.split()[0], 'numpy', numpy.__version__)"
echo exit code: !errorlevel!
echo.
echo --- file checksums (SHA-256)
for %%F in (bin\mkvmerge.exe bin\ffmpeg.exe bin\ffprobe.exe SoundMux.exe) do (
  echo %%F
  certutil -hashfile "%~dp0%%F" SHA256 | findstr /v ":"
)
echo.
echo --- service log %~dp0logs\soundmux.log (last lines)
if exist "%~dp0logs\soundmux.log" (powershell -NoProfile -Command "Get-Content -LiteralPath '%~dp0logs\soundmux.log' -Tail 40 -Encoding UTF8") else (echo no log yet)
echo.
pause
exit /b 0

:check
set "out=%temp%\soundmux-diagnose-%1.txt"
"%~dp0bin\%1.exe" %2 >"%out%" 2>&1
set "code=!errorlevel!"
set "first="
set /p first=<"%out%"
echo --- %1: exit code !code! : !first!
del "%out%" >nul 2>&1
exit /b 0
