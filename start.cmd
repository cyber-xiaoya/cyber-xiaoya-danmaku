@echo off
cd /d %~dp0
title 多平台弹幕朗读
rem 视频号需要带调试端口的浏览器, 没开就自动拉起(已在运行则跳过)
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',9223);$c.Close()}catch{Start-Process '%~dp0启动调试浏览器.cmd'}" >nul 2>nul
where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython
echo [错误] 未找到 Python, 请安装 Python 3.10+ 并勾选 Add to PATH
pause
goto :eof
:usepy
py -3 reader.py %*
goto :eof
:usepython
python reader.py %*
