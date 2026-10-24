@echo off
REM ============================================================
REM  One-click: start Voice TTS services (OmniVoice 9877 + bridge 8765)
REM  NOTE: ASCII-only on purpose. cmd reads .cmd files using the console
REM        code page; a UTF-8 system (65001) would mis-decode Chinese
REM        literals. All Chinese output comes from the .ps1 (UTF-8 BOM).
REM ============================================================
title Voice TTS services (OmniVoice 9877 + bridge 8765)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-voice-services.ps1"
echo.
echo   (Press any key to close this window)
pause >nul
