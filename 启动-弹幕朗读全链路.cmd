@echo off
REM ============================================================
REM  One-click: start the whole Danmaku TTS chain
REM  (voice bridge 8765 + OmniVoice 9877 -> reader.py -> tray icon)
REM  NOTE: ASCII-only on purpose (see the other .cmd for the reason);
REM        Chinese output is printed by the .ps1 (UTF-8 BOM).
REM ============================================================
title Danmaku TTS - full chain
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-danmaku-stack.ps1"
echo.
echo   (Press any key to close this window)
pause >nul
