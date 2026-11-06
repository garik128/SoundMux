@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0src-tauri\target\debug\SoundMux.exe" (
  echo Native build is missing. See README.md.
  pause
  exit /b 1
)
start "" "%~dp0src-tauri\target\debug\SoundMux.exe"
