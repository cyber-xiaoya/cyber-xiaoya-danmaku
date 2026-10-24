# watch-wechat.ps1 — 盯「视频号」这一路的挂载与抓取
# 用途: 你手动开调试浏览器 + 进中控台时, 这个脚本把关键事件实时打出来。
# 看什么:
#   ① CDP 9223 是否被监听  → 调试浏览器起来了
#   ② [视频号] 中控台已挂载 → 采集线程接上了
#   ③ [视频号] Chat: ...    → 真抓到评论了
param([int]$Minutes = 12)
$ErrorActionPreference = 'Continue'
$root = 'E:\deepseek-works\multi-danmaku-tts'
$log  = Join-Path $root 'logs\reader.log'
$deadline = (Get-Date).AddMinutes($Minutes)
$seen = @{}
$seenCdp = $false
$mounted = $false
$captures = 0
$lastBeat = Get-Date

Write-Output ("[{0:HH:mm:ss}] 开始监控（最长 {1} 分钟）: {2}" -f (Get-Date), $Minutes, $log)
Write-Output ("[{0:HH:mm:ss}] 当前: {1}" -f (Get-Date), $(
  ($l = Get-Content $log -Tail 400 -Encoding UTF8 | Select-String '\[视频号\]' | Select-Object -Last 1)
  if ($l) { $l.Line } else { '(日志里还没有视频号记录)' }))

while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 5

  if (-not $seenCdp) {
    $c = Get-NetTCPConnection -State Listen -LocalPort 9223 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
      $seenCdp = $true
      Write-Output ("[{0:HH:mm:ss}] ① CDP 9223 已监听 (PID {1}) —— 调试浏览器起来了" -f (Get-Date), $c.OwningProcess)
    }
  }

  $hits = Get-Content $log -Tail 400 -Encoding UTF8 | Select-String '中控台已挂载|\[视频号\] Chat|已自动跳转到直播中控台|\[视频号\].*(失败|断开)'
  foreach ($h in $hits) {
    $line = $h.Line
    if ($seen.ContainsKey($line)) { continue }
    $seen[$line] = $true
    if ($line -match '中控台已挂载') {
      $mounted = $true
      Write-Output ("[{0:HH:mm:ss}] ② ✅ {1}" -f (Get-Date), $line)
    } elseif ($line -match '\[视频号\] Chat') {
      $captures++
      Write-Output ("[{0:HH:mm:ss}] ③ 🎯 {1}" -f (Get-Date), $line)
    } else {
      Write-Output ("[{0:HH:mm:ss}] ⚠ {1}" -f (Get-Date), $line)
    }
  }

  if (((Get-Date) - $lastBeat).TotalSeconds -ge 60) {
    $lastBeat = Get-Date
    $tail = (Get-Content $log -Tail 400 -Encoding UTF8 | Select-String '\[视频号\]' | Select-Object -Last 1).Line
    Write-Output ("[{0:HH:mm:ss}] 心跳: CDP={1} 已挂载={2} 已抓评论={3} ｜ 最新: {4}" -f (Get-Date), $seenCdp, $mounted, $captures, $tail)
  }

  if ($mounted -and $captures -ge 3) { Write-Output ("[{0:HH:mm:ss}] 已抓到 {1} 条, 监控收工" -f (Get-Date), $captures); break }
}

Write-Output ("=== 监控结束: CDP监听={0}, 已挂载={1}, 抓到评论={2} 条 ===" -f $seenCdp, $mounted, $captures)
