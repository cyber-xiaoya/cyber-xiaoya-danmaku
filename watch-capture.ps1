# watch-capture.ps1 — 盯「B站 / 视频号」真实事件(弹幕、礼物…)是否被抓到
# 用法: powershell -ExecutionPolicy Bypass -File watch-capture.ps1 -Minutes 15
param([int]$Minutes = 15)
$ErrorActionPreference = 'Continue'
$log = 'E:\deepseek-works\multi-danmaku-tts\logs\reader.log'
$deadline = (Get-Date).AddMinutes($Minutes)
$seen = @{}
$counts = @{}
$lastBeat = Get-Date

Write-Output ("[{0:HH:mm:ss}] 开始盯 B站/视频号 事件（最长 {1} 分钟）" -f (Get-Date), $Minutes)
foreach ($p in 'B站', '视频号') {
    $last = (Get-Content $log -Tail 400 -Encoding UTF8 | Select-String "\[$p\]" | Select-Object -Last 1).Line
    Write-Output ("[{0:HH:mm:ss}] {1} 当前: {2}" -f (Get-Date), $p, $last)
}

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 4
    $hits = Get-Content $log -Tail 400 -Encoding UTF8 |
        Select-String '\[(B站|视频号)\] (Chat|Gift|Like|EnterRoom|Follow|Subscribe|SuperChat|Guard)|鉴权成功|鉴权被拒|中控台已挂载|连接被关闭'
    foreach ($h in $hits) {
        $line = $h.Line
        if ($seen.ContainsKey($line)) { continue }
        $seen[$line] = $true
        if ($line -match '\[(B站|视频号)\] (\w+):') {
            $plat = $Matches[1]; $kind = $Matches[2]
            $key = "$plat/$kind"
            $counts[$key] = 1 + [int]$counts[$key]
            Write-Output ("[{0:HH:mm:ss}] 🎯 {1}" -f (Get-Date), $line)
        } else {
            Write-Output ("[{0:HH:mm:ss}] ·  {1}" -f (Get-Date), $line)
        }
    }
    if (((Get-Date) - $lastBeat).TotalSeconds -ge 60) {
        $lastBeat = Get-Date
        $summary = if ($counts.Count) { ($counts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ' } else { '尚未抓到事件' }
        Write-Output ("[{0:HH:mm:ss}] 心跳: {1}" -f (Get-Date), $summary)
    }
}

$summary = if ($counts.Count) { ($counts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ' } else { '一条都没抓到' }
Write-Output ("=== 监控结束: {0} ===" -f $summary)
