# 配置B站Cookie向导 (multi-danmaku-tts) — UTF-8 BOM, PS 5.1 兼容
$ErrorActionPreference = 'Stop'
$proj = 'E:\deepseek-works\multi-danmaku-tts'
$cfgPath = Join-Path $proj 'config.json'
$logDir = Join-Path $proj 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'bili-cookie-wizard.log'

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Write-Host "==== 配置B站Cookie ====" -ForegroundColor Cyan
Write-Host ""
Write-Host "获取步骤 (在电脑浏览器上):"
Write-Host "  1. 打开 https://www.bilibili.com 并登录你的主号"
Write-Host "  2. 按 F12 打开开发者工具, 切到 [网络/Network] 标签"
Write-Host "  3. 按 Ctrl+R 刷新页面"
Write-Host "  4. 在请求列表里点任意一条 api.bilibili.com 的请求"
Write-Host "  5. 在 [请求标头/Request Headers] 里找到 Cookie 这一行"
Write-Host "  6. 右键 -> 复制值 (整串, 通常几KB长)"
Write-Host ""
Write-Host "复制好后回到这里按回车, 会自动从剪贴板读取。" -ForegroundColor Yellow
Read-Host "准备好了就按回车"

$raw = [string](Get-Clipboard -Raw)
Write-Log ("clipboard length: " + $raw.Length)

if ([string]::IsNullOrWhiteSpace($raw)) {
    Write-Host "[X] 剪贴板是空的" -ForegroundColor Red
    Write-Log "fail: empty clipboard"
    Read-Host "按回车退出"
    exit 1
}

$cookie = $raw.Trim()
# 兼容整行 "Cookie: xxx" 的复制
if ($cookie -match '^(?i)cookie:\s*(.+)$') { $cookie = $Matches[1].Trim() }

if ($cookie -notmatch '(?i)SESSDATA=') {
    Write-Host "[X] 这不是登录Cookie (没有 SESSDATA 字段)" -ForegroundColor Red
    Write-Host "    请确认: 登录状态下, 从 [请求标头] 的 Cookie 行完整复制" -ForegroundColor Yellow
    Write-Log "fail: no SESSDATA"
    Read-Host "按回车退出"
    exit 1
}

if ($cookie.Length -lt 80) {
    Write-Host "[X] Cookie 长度可疑 ($($cookie.Length) 字符), 正常应有几百字符" -ForegroundColor Red
    Write-Log ("fail: too short " + $cookie.Length)
    Read-Host "按回车退出"
    exit 1
}

# 掩码确认
$head = $cookie.Substring(0, [Math]::Min(32, $cookie.Length))
$mask = $head + "..." + "(" + $cookie.Length + " 字符)"
Write-Host ""
Write-Host "识别到登录Cookie:" -ForegroundColor Green
Write-Host "  $mask"
Write-Host ""
$ok = Read-Host "写入 config.json? (Y/N)"
if ($ok -notin @('Y', 'y', 'yes', 'YES')) {
    Write-Host "已取消, 未做任何修改"
    Write-Log "cancelled by user"
    Read-Host "按回车退出"
    exit 0
}

# 写入 config.json (保留其他配置)
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$cfg.bilibili.enabled = $true
if (-not $cfg.bilibili.PSObject.Properties['cookie']) {
    $cfg.bilibili | Add-Member -NotePropertyName cookie -NotePropertyValue $cookie
} else {
    $cfg.bilibili.cookie = $cookie
}
$json = $cfg | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))

# 回读校验(掩码)
$check = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).bilibili.cookie
if ($check -eq $cookie) {
    Write-Host ""
    Write-Host "[OK] Cookie 已写入 config.json" -ForegroundColor Green
    Write-Log ("success: len=" + $cookie.Length)
    Write-Host "下次启动朗读器自动生效 (昵称不再打码, 事件流更完整)"
} else {
    Write-Host "[X] 写入校验失败" -ForegroundColor Red
    Write-Log "fail: verify mismatch"
}
Read-Host "按回车退出"
