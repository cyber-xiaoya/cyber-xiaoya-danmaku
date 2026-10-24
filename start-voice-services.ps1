# ============================================================================
#  start-voice-services.ps1 — 一键拉起「语音朗读服务」
#      OmniVoice TTS (WSL2 :9877) + 语音桥 (127.0.0.1:8765)
#
#  用途:
#    · 用 DSH 时让「语音朗读插件」能出声；
#    · 也是弹幕朗读全链路的一环（start-danmaku-stack.ps1 会复用它）。
#
#  特征: 幂等 —— 已经在跑的服务自动跳过；不启动 DSH、不开浏览器、不碰 Docker/DUIX。
#  入口: 同目录的 「启动-语音朗读服务.cmd」（纯 ASCII 外壳, 中文提示由本脚本打印）
# ============================================================================
param([switch]$Quiet)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Root     = $PSScriptRoot
$StackPs1 = 'D:\speech-to-speech\start-full-stack.ps1'
$OmniCfg  = 'D:\speech-to-speech\bridge-config.json'
$Bridge   = 'http://127.0.0.1:8765'

function Say($msg, $color = 'Gray') { if (-not $Quiet) { Write-Host $msg -ForegroundColor $color } }

# 注意: 不要写成 "Say (表达式) ` 换行 $(另一个表达式)" —— 反引号续行后接子表达式会被当成新语句(语法错误)。
function SayOk([bool]$ok, [string]$okText, [string]$badText) {
  if ($Quiet) { return }
  Write-Host $(if ($ok) { $okText } else { $badText }) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
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

function Get-OmniBase {
  try {
    $b = (Get-Content $OmniCfg -Raw -Encoding UTF8 | ConvertFrom-Json).omnivoice.base
    if ($b) { return [string]$b }
  } catch {}
  return 'http://127.0.0.1:9877'
}

function Get-BridgeHealth {
  try { return (Invoke-RestMethod "$Bridge/api/health" -TimeoutSec 5) } catch { return $null }
}

Say ''
Say '============================================================' Cyan
Say '  语音朗读服务 · 一键启动' Cyan
Say '  OmniVoice TTS (WSL2 9877) + 语音桥 (127.0.0.1:8765)' Cyan
Say '============================================================' Cyan
Say ''

# ── 1) 先探现状 ────────────────────────────────────────────────────────────
$omniBase = Get-OmniBase
$omniHost = '127.0.0.1'; $omniPort = 9877
if ($omniBase -match 'https?://([^:/]+):(\d+)') { $omniHost = $Matches[1]; $omniPort = [int]$Matches[2] }

$bridgeUp = Test-Port '127.0.0.1' 8765 1200
$omniUp   = Test-Port $omniHost $omniPort 1500
Say ("  现状: 语音桥 8765 = " + $(if ($bridgeUp) { '在线' } else { '未起' }) +
     "  /  OmniVoice = " + $(if ($omniUp) { "在线 ($omniBase)" } else { "未起 ($omniBase)" }))

# ── 2) 缺哪个就交给既有的全栈脚本（它会注入 DEEPSEEK_API_KEY、拉起 WSL、必要时重启带 key 的桥）──
if ($bridgeUp -and $omniUp) {
  Say '  两个服务都已经在跑，无需重复启动。' Green
} else {
  if (Test-Path $StackPs1) {
    Say ''
    Say '  交给 start-full-stack.ps1（-NoDsh -NoBrowser -SkipDockerCheck）…' Cyan
    Say '  （首次/冷启动会加载模型 + CUDA graph，通常 30 秒内，最长 150 秒）' DarkGray
    Say ''
    & powershell -NoProfile -ExecutionPolicy Bypass -File $StackPs1 -NoDsh -NoBrowser -SkipDockerCheck
  } else {
    Say "  ❌ 找不到 $StackPs1 —— 无法自动拉起，请手动启动语音栈" Red
  }
}

# ── 3) 复检 ────────────────────────────────────────────────────────────────
Say ''
Say '  —— 复检 ——' Cyan
$h = $null
for ($i = 1; $i -le 20; $i++) {
  $h = Get-BridgeHealth
  if ($h) { break }
  Start-Sleep -Seconds 2
}
$omniBase = Get-OmniBase
if ($omniBase -match 'https?://([^:/]+):(\d+)') { $omniHost = $Matches[1]; $omniPort = [int]$Matches[2] }
$omniNow = $false
try {
  $r = Invoke-WebRequest $omniBase -UseBasicParsing -TimeoutSec 6
  $omniNow = ($r.StatusCode -eq 200)
} catch { $omniNow = (Test-Port $omniHost $omniPort 1500) }

SayOk ($null -ne $h) ('  ✅ 语音桥 8765      在线  ' + $Bridge) '  ❌ 语音桥 8765      没起来（看 D:\speech-to-speech\logs\bridge.log）'
SayOk $omniNow ('  ✅ OmniVoice 9877   在线  ' + $omniBase) '  ❌ OmniVoice 9877   未响应（看 start-omnivoice-wsl.ps1 的输出）'
if ($h) {
  Say ('     （桥健康: status=' + $h.status + ' tts=' + $h.tts + ' stt=' + $h.stt +
       ' —— tts=false 只是懒加载，第一条语音会触发加载）')
}

Say ''
if ($h -and $omniNow) {
  Say '  完成：现在打开 DSH 说话/开着语音朗读插件即可出声。' Green
  Say '  本窗口可以关闭（服务跑在独立进程里）。' DarkGray
} else {
  Say '  有服务没起来，请把上面的错误信息发出来。' Yellow
}
Say ''
