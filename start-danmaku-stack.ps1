# ============================================================================
#  start-danmaku-stack.ps1 — 一键拉起「弹幕 TTS 朗读全链路」
#
#  链路: 语音桥 8765 + OmniVoice 9877  →  朗读器 reader.py（含 douyinLive 采集 + 托盘图标）
#
#  特征:
#    · 幂等: 已经在跑的环节自动跳过（重复启动会让每条弹幕读两遍，必须挡住）；
#    · 清理孤儿: 直接关控制台窗口(✕)时 Python 不走 finally, 会留下独立的 douyinLive.exe
#      占着 1089 端口 —— 这里会在"没有朗读器在跑"时把它清掉, 免得新实例连不上采集服务；
#    · 朗读器在**独立可见控制台窗口**里启动 —— 你可以直接在里面敲 vol / gap / read 命令；
#    · 结束后打印一张体检表（各环节状态 + 当前音量/间隔/开关）。
#
#  入口: 同目录的 「启动-弹幕朗读全链路.cmd」（纯 ASCII 外壳, 中文提示由本脚本打印）
# ============================================================================
param([switch]$Quiet)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Root      = $PSScriptRoot
$VoicePs1  = Join-Path $Root 'start-voice-services.ps1'
$ReaderCmd = Join-Path $Root 'start.cmd'          # 即「cd 到本目录 + py -3 reader.py」
$Config    = Join-Path $Root 'config.json'
$DanLog    = Join-Path $Root 'logs\douyinLive.log'
$Bridge    = 'http://127.0.0.1:8765'

function Say($msg, $color = 'Gray') { if (-not $Quiet) { Write-Host $msg -ForegroundColor $color } }
function Line([bool]$ok, [string]$name, [string]$extra = '') {
  if ($Quiet) { return }
  $mark = if ($ok) { '✅' } else { '❌' }
  Write-Host ("    $mark $name $extra") -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}
function LineWarn([string]$name, [string]$extra = '') {
  if ($Quiet) { return }
  Write-Host ("    ⚠ $name $extra") -ForegroundColor Yellow
}

function Test-Port([string]$h, [int]$p, [int]$ms = 1500) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $t = $c.BeginConnect($h, $p, $null, $null)
    $ok = $t.AsyncWaitHandle.WaitOne($ms)
    $c.Close()
    return [bool]$ok
  } catch { return $false }
}
function Get-ProcsByCmd([string]$pattern, [string[]]$names) {
  # ⚠️ 必须同时按进程名过滤: 否则自己的 pwsh/cmd 命令行里含 "douyinLive" 等关键字时会被误判成"服务在跑"
  return @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $pattern -and ($names -contains $_.Name)
  })
}
function Get-ReaderProcs { return (Get-ProcsByCmd 'reader\.py' @('python.exe', 'py.exe', 'pythonw.exe')) }
function Get-DouyinProcs { return @(Get-CimInstance Win32_Process -Filter "Name='douyinLive.exe'") }
function Get-TrayProcs { return (Get-ProcsByCmd 'tray\.ps1' @('powershell.exe', 'pwsh.exe')) }

Say ''
Say '============================================================' Cyan
Say '  弹幕 TTS 朗读 · 全链路一键启动' Cyan
Say '  语音桥 8765 + OmniVoice 9877 → 朗读器 → 托盘图标' Cyan
Say '============================================================' Cyan
Say ''

# ── 1) 语音服务（复用 start-voice-services.ps1；已在线会自动跳过）────────────
Say '  [1/4] 语音朗读服务（OmniVoice + 语音桥）' Cyan
if (Test-Path $VoicePs1) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $VoicePs1 -Quiet
} else {
  Say "        ❌ 找不到 $VoicePs1" Red
}
$bridgeOk = $false
try { $h = Invoke-RestMethod "$Bridge/api/health" -TimeoutSec 5; $bridgeOk = $true } catch { $bridgeOk = $false }
Say ('        ' + $(if ($bridgeOk) { '✅ 语音桥在线' } else { '⚠ 语音桥未就绪（弹幕会回退系统 SAPI 机器音）' })) $(if ($bridgeOk) { 'Green' } else { 'Yellow' })

# ── 2) 朗读器（已在跑就跳过；先清孤儿；再在新窗口里拉起）────────────────────
Say ''
Say '  [2/4] 朗读器（reader.py + douyinLive）' Cyan
$reader = Get-ReaderProcs
if ($reader.Count -gt 0) {
  Say ('        已经在跑，跳过（避免双份语音）。PID: ' + (($reader | ForEach-Object { $_.ProcessId }) -join ', ')) Yellow
} else {
  # 孤儿清理: douyinLive.exe 的父进程已不存在, 且没有朗读器在跑 → 是上次被 ✕ 关窗留下的
  $allProcs = Get-CimInstance Win32_Process
  $orphans = @()
  foreach ($d in ($allProcs | Where-Object { $_.Name -eq 'douyinLive.exe' })) {
    $parentAlive = @($allProcs | Where-Object { $_.ProcessId -eq $d.ParentProcessId }).Count -gt 0
    if (-not $parentAlive) { $orphans += $d }
  }
  foreach ($o in $orphans) {
    Say ('        清理孤儿采集进程 douyinLive.exe PID ' + $o.ProcessId + '（父进程已退出, 却占着 1089 端口）') Yellow
    Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($orphans.Count -gt 0) { Start-Sleep -Seconds 2 }

  if (Test-Path $ReaderCmd) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$ReaderCmd`""
    Say '        已在新窗口里启动（那个窗口里可以直接敲 vol / gap / read 命令）' Green
    # 等它把采集服务 + 托盘一起拉起来（正常 5~15 秒, 最多等 40 秒）
    for ($i = 1; $i -le 20; $i++) {
      Start-Sleep -Seconds 2
      $rNow = Get-ReaderProcs
      $dNow = Get-DouyinProcs
      if ($rNow.Count -gt 0 -and $dNow.Count -gt 0) { break }
    }
  } else {
    Say "        ❌ 找不到 $ReaderCmd" Red
  }
}

# ── 3) 弹幕流状态（房间没开播是正常的，等开播会自动连）──────────────────────
Say ''
Say '  [3/4] 抖音弹幕流' Cyan
$flowOk = $false; $flowWarn = $false; $flowTxt = '(暂无日志)'
if (Test-Path $DanLog) {
  $tail = Get-Content $DanLog -Tail 20 -Encoding UTF8
  $lastConn = $tail | Where-Object { $_ -match '上游 WebSocket 已就绪|WebSocket 连接成功' } | Select-Object -Last 1
  $lastOff  = $tail | Where-Object { $_ -match '未开播|连接已结束' } | Select-Object -Last 1
  if ($lastConn) { $flowOk = $true; if ($lastConn -match '^(\S+ \S+)') { $flowTxt = '(最近连接 ' + $Matches[1] + ')' } }
  if ($lastOff) {
    $offTime = ''; if ($lastOff -match '^(\S+ \S+)') { $offTime = $Matches[1] }
    if ($flowOk) { $flowTxt += '   最近未开播记录: ' + $offTime }
    else { $flowWarn = $true; $flowTxt = "(房间当前未开播（$offTime）—— 开播后会自动连接, 属正常)" }
  } elseif (-not $flowOk) { $flowTxt = '(日志里没看到连接记录)' }
}
if ($flowOk) { Line $true '抖音弹幕流  ' $flowTxt }
elseif ($flowWarn) { LineWarn '抖音弹幕流 ' $flowTxt }
else { Line $false '抖音弹幕流  ' $flowTxt }

# ── 4) 体检表 ──────────────────────────────────────────────────────────────
Say ''
Say '  [4/4] 链路体检' Cyan

# 直接内联查询 + 3 秒后复查一次取较大值(进程切换的瞬间不会误报 ❌)
function CountNow([string]$kind) {
  switch ($kind) {
    'reader'  { return @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'py.exe', 'pythonw.exe') -and $_.CommandLine -and $_.CommandLine -match 'reader\.py' }).Count }
    'douyin'  { return @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'douyinLive.exe' }).Count }
    'tray'    { return @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -and $_.CommandLine -match 'tray\.ps1' }).Count }
  }
  return 0
}
function CountStable([string]$kind) {
  $a = CountNow $kind
  if ($a -gt 0) { return $a }
  Start-Sleep -Seconds 3
  return (CountNow $kind)
}
function PidsOf([string]$kind) {
  switch ($kind) {
    'reader' { return (Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'py.exe', 'pythonw.exe') -and $_.CommandLine -match 'reader\.py' } | ForEach-Object { $_.ProcessId }) -join ',' }
    'douyin' { return (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'douyinLive.exe' } | ForEach-Object { $_.ProcessId }) -join ',' }
    'tray'   { return (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'tray\.ps1' } | ForEach-Object { $_.ProcessId }) -join ',' }
  }
  return ''
}

$nReader = CountStable 'reader'
$nDouyin = CountStable 'douyin'
$nTray   = CountStable 'tray'

# 朗读器在跑但托盘没起(比如上次是 ✕ 关窗留下的孤儿占着互斥名) → 单独补一个托盘
if ($nReader -gt 0 -and $nTray -eq 0) {
  $TrayPs1 = Join-Path $Root 'tray.ps1'
  if (Test-Path $TrayPs1) {
    Say '        朗读器在跑但没有托盘图标 → 单独补起一个托盘' Yellow
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $TrayPs1, '-Config', $Config) -WindowStyle Hidden
    Start-Sleep -Seconds 5
    $nTray = CountNow 'tray'
  }
}

$omniBase  = 'http://127.0.0.1:9877'
# 可选: 若你另有语音桥配置(含 omnivoice.base), 用环境变量 DSH_VOICE_BRIDGE_CFG 指过去
if ($env:DSH_VOICE_BRIDGE_CFG -and (Test-Path $env:DSH_VOICE_BRIDGE_CFG)) {
    try { $omniBase = [string](Get-Content $env:DSH_VOICE_BRIDGE_CFG -Raw -Encoding UTF8 | ConvertFrom-Json).omnivoice.base } catch {}
}
$omniHost = '127.0.0.1'; $omniPort = 9877
if ($omniBase -match 'https?://([^:/]+):(\d+)') { $omniHost = $Matches[1]; $omniPort = [int]$Matches[2] }
$omniNow = Test-Port $omniHost $omniPort 2000

Line $bridgeOk '语音桥 8765        ' $Bridge
Line $omniNow  'OmniVoice 9877     ' $omniBase
Line ($nReader -gt 0) '朗读器 reader.py   ' $(if ($nReader -gt 0) { '(PID ' + (PidsOf 'reader') + ')' } else { '(没起来！看它窗口里的报错)' })
Line ($nDouyin -gt 0) '抖音采集 douyinLive' $(if ($nDouyin -gt 0) { '(PID ' + (PidsOf 'douyin') + ')' } else { '(没起来！)' })
Line ($nTray -gt 0)   '托盘图标           ' $(if ($nTray -gt 0) { '(PID ' + (PidsOf 'tray') + '，右下角紫色图标)' } else { '(未起：朗读器启动后会自动带, 也可单独跑 tray.ps1)' })

Say ''
Say '    —— 当前设置（config.json）——' DarkGray
try {
  $c = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
  $vol = [Math]::Round([double]$c.tts.volume * 100)
  $gap = [double]$c.tts.gap_seconds
  $gapTxt = if ($gap -le 0) { '关闭（来一条读一条）' } else { "$([Math]::Round($gap)) 秒" }
  Say ("      音量 $vol%   ·   朗读间隔 $gapTxt   ·   只读最新弹幕: " + $(if ($c.tts.latest_only) { '开' } else { '关' }))
  $map = [ordered]@{ comments = '观众留言'; gifts = '礼物'; joins = '进场'; follows = '关注'; likes = '点赞'; guards = '舰长/守护'; superchats = '醒目留言' }
  $on = @(); $off = @()
  foreach ($k in $map.Keys) { if ($c.read.$k) { $on += $map[$k] } else { $off += $map[$k] } }
  Say ("      朗读开: " + $(if ($on.Count) { $on -join ' ' } else { '（无）' }))
  Say ("      朗读关: " + $(if ($off.Count) { $off -join ' ' } else { '（无）' }))
  Say '      调整：托盘右键（音量/间隔/开关），或在朗读器窗口敲 help'
} catch { Say '      读 config.json 失败，跳过' Yellow }

Say ''
if ($bridgeOk -and $omniNow -and $nReader -gt 0 -and $nDouyin -gt 0) {
  Say '  全链路已就绪 ✅  朗读器窗口里可以敲 vol / gap / read 命令' Green
  if (-not $flowOk) { Say '  （房间未开播时收不到弹幕是正常的，开播会自动连上）' DarkGray }
} else {
  Say '  有环节没起来，请看上面 ❌ 那几行；日志: logs\reader.log、logs\douyinLive.log、logs\tray.log' Yellow
}
Say ''
