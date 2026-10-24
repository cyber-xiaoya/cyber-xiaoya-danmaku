@echo off
cd /d E:\deepseek-works\multi-danmaku-tts
title 调试浏览器 视频号助手
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9223 --user-data-dir=E:\deepseek-works\multi-danmaku-tts\browser-profile https://channels.weixin.qq.com/platform/live/home
echo 已启动带调试端口的 Edge (端口 9223, 独立配置目录)
timeout /t 5 >nul
