# ============================================================================
#  tray.ps1 — 弹幕朗读器托盘图标(音量 / 朗读开关 一键调)
#
#  为什么用 PowerShell + WinForms:
#    reader.py 已支持 config.json 热加载(音量 + read.* 开关, 2 秒内生效),
#    所以托盘只要读写同一个 config.json 就够了 —— 不需要 IPC、不需要装任何第三方库。
#
#  用法:
#    powershell -ExecutionPolicy Bypass -File tray.ps1 [-Config <路径>]
#    powershell -ExecutionPolicy Bypass -File tray.ps1 -SelfTest          # 只打印菜单结构+接线自检(不显示图标)
#    powershell -ExecutionPolicy Bypass -File tray.ps1 -DoAction vol:0.25 # 执行某个动作后退出(测试用)
#
#  菜单动作 tag 约定:
#    vol:<数值>     设置音量          tune:up / tune:down   音量 ×1.25 / ÷1.25(约 ±2 dB)
#    flag:<键>      切换某个朗读开关   open:config / open:logs
#    vipmin:<钻数>  礼物观众VIP门槛   gap:<秒>              朗读间隔
#    restart:reader 重启朗读器(改完 config.json 里不热加载的项用这个, 托盘自己活着)
#    exit:tray      只关托盘          exit:reader           请求朗读器退出(写哨兵文件)并关托盘
#
#  ⚠️ 两个坑(已修, 别再踩):
#    ① 菜单必须 `$ni.ContextMenuStrip = $menu` 挂上去, 否则点图标什么都不弹;
#    ② 子菜单项的点击不会冒泡到根菜单的 ItemClicked —— 每个菜单项都要自己 add_Click。
# ============================================================================
param(
  [string]$Config = "",
  [switch]$SelfTest,
  [string]$DoAction = ""
)
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Root = $PSScriptRoot
if (-not $Config) { $Config = Join-Path $Root 'config.json' }
$LogDir = Join-Path $Root 'logs'
$TrayLog = Join-Path $LogDir 'tray.log'
$QuitFlag = Join-Path $LogDir '.tray-quit'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 朗读开关 → 中文名(与 reader.py 的 READ_NAMES 保持一致)
$FlagNames = [ordered]@{
  comments   = '念弹幕内容（关＝不复读只回应）'
  gifts      = '礼物'
  follows    = '关注'
  likes      = '点赞'
  guards     = '舰长/守护'
  superchats = '醒目留言'
  joins      = '进场'
  vip_gifters = '礼物观众留言优先'
}
$VolSteps = @(0, 10, 15, 20, 25, 30, 35, 50, 70, 100)

function Write-TrayLog([string]$msg) {
  try {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    [IO.File]::AppendAllText($TrayLog, $line + [Environment]::NewLine, $Utf8NoBom)
  } catch {}
}

function Get-Cfg {
  try {
    if (-not (Test-Path $Config)) { return $null }
    return (Get-Content -Path $Config -Raw -Encoding UTF8 | ConvertFrom-Json)
  } catch {
    Write-TrayLog "读取配置失败: $($_.Exception.Message)"
    return $null
  }
}

function Save-Cfg($cfg) {
  try {
    $json = $cfg | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Config, $json, $Utf8NoBom)
    return $true
  } catch {
    Write-TrayLog "写配置失败: $($_.Exception.Message)"
    return $false
  }
}

function Get-Volume {
  $cfg = Get-Cfg
  if ($cfg -and $cfg.tts -and ($cfg.tts.PSObject.Properties.Name -contains 'volume')) {
    return [double]$cfg.tts.volume
  }
  return 1.0
}

function Get-Flag([string]$key) {
  $cfg = Get-Cfg
  if ($cfg -and $cfg.read -and ($cfg.read.PSObject.Properties.Name -contains $key)) {
    return [bool]$cfg.read.$key
  }
  return $true
}

function Set-Volume([double]$v) {
  $v = [Math]::Max(0.0, [Math]::Min(1.0, $v))
  $cfg = Get-Cfg
  if (-not $cfg) { return $false }
  if (-not ($cfg.PSObject.Properties.Name -contains 'tts')) {
    $cfg | Add-Member -NotePropertyName tts -NotePropertyValue (New-Object psobject)
  }
  if ($cfg.tts.PSObject.Properties.Name -contains 'volume') { $cfg.tts.volume = [Math]::Round($v, 3) }
  else { $cfg.tts | Add-Member -NotePropertyName volume -NotePropertyValue ([Math]::Round($v, 3)) }
  return (Save-Cfg $cfg)
}

function Set-Flag([string]$key, [bool]$val) {
  $cfg = Get-Cfg
  if (-not $cfg) { return $false }
  if (-not ($cfg.PSObject.Properties.Name -contains 'read')) {
    $cfg | Add-Member -NotePropertyName read -NotePropertyValue (New-Object psobject)
  }
  if ($cfg.read.PSObject.Properties.Name -contains $key) { $cfg.read.$key = $val }
  else { $cfg.read | Add-Member -NotePropertyName $key -NotePropertyValue $val }
  return (Save-Cfg $cfg)
}

function Toggle-Flag([string]$key) {
  $cur = Get-Flag $key
  if (Set-Flag $key (-not $cur)) { return (-not $cur) }
  return $cur
}

# ── 朗读节奏: 间隔 / 只读最新 ──
function Get-Gap {
  $cfg = Get-Cfg
  if ($cfg -and $cfg.tts -and ($cfg.tts.PSObject.Properties.Name -contains 'gap_seconds')) {
    return [double]$cfg.tts.gap_seconds
  }
  return 5.0
}

function Set-Gap([double]$v) {
  $v = [Math]::Max(0, [Math]::Min(120, $v))
  $cfg = Get-Cfg
  if (-not $cfg) { return $false }
  if (-not ($cfg.PSObject.Properties.Name -contains 'tts')) {
    $cfg | Add-Member -NotePropertyName tts -NotePropertyValue (New-Object psobject)
  }
  if ($cfg.tts.PSObject.Properties.Name -contains 'gap_seconds') { $cfg.tts.gap_seconds = [Math]::Round($v, 2) }
  else { $cfg.tts | Add-Member -NotePropertyName gap_seconds -NotePropertyValue ([Math]::Round($v, 2)) }
  return (Save-Cfg $cfg)
}

function Get-LatestOnly {
  $cfg = Get-Cfg
  if ($cfg -and $cfg.tts -and ($cfg.tts.PSObject.Properties.Name -contains 'latest_only')) {
    return [bool]$cfg.tts.latest_only
  }
  return $true
}

# ── 礼物观众 VIP 门槛(抖币) ──
function Get-VipMin {
  $cfg = Get-Cfg
  if ($cfg -and $cfg.read -and ($cfg.read.PSObject.Properties.Name -contains 'vip_min_diamond')) {
    return [int]$cfg.read.vip_min_diamond
  }
  return 0
}

function Set-VipMin([int]$v) {
  $v = [Math]::Max(0, [Math]::Min(100000, $v))
  $cfg = Get-Cfg
  if (-not $cfg) { return $false }
  if (-not ($cfg.PSObject.Properties.Name -contains 'read')) {
    $cfg | Add-Member -NotePropertyName read -NotePropertyValue (New-Object psobject)
  }
  if ($cfg.read.PSObject.Properties.Name -contains 'vip_min_diamond') { $cfg.read.vip_min_diamond = $v }
  else { $cfg.read | Add-Member -NotePropertyName vip_min_diamond -NotePropertyValue $v }
  return (Save-Cfg $cfg)
}

function Set-LatestOnly([bool]$v) {
  $cfg = Get-Cfg
  if (-not $cfg) { return $false }
  if (-not ($cfg.PSObject.Properties.Name -contains 'tts')) {
    $cfg | Add-Member -NotePropertyName tts -NotePropertyValue (New-Object psobject)
  }
  if ($cfg.tts.PSObject.Properties.Name -contains 'latest_only') { $cfg.tts.latest_only = $v }
  else { $cfg.tts | Add-Member -NotePropertyName latest_only -NotePropertyValue $v }
  return (Save-Cfg $cfg)
}

# ── 重启朗读器(托盘自己活着) ──
# 为什么需要: config.json 里"热加载"的只有音量 + READ_FLAGS 那几个开关;
#   vip_min_diamond / vip_gift_ttl_minutes / read.templates / tts.* 都是启动时读一次的
#   → 改完必须重启 reader 才生效。以前得把整条链路关掉再开, 现在点这一项就行。
function Get-ReaderProcs {
  # ⚠️ 必须 `return ,$list`(逗号包一层): PowerShell 5.1 的 return 会把数组**展开**,
  #    只剩一个对象时调用方 `.Count` 会变成 $null → `$null -eq 0` 为假 → 判断全错
  #    (2026-10-09 实测: 重启后误报"没检测到进程"就是这么来的)
  $list = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'reader\.py' })
  return ,$list
}

function Restart-Reader {
  $before = @(Get-ReaderProcs).Count
  # ① 优雅退出: 复用托盘"退出朗读器"的哨兵, reader 收到后会顺带停掉 douyinLive 服务实例
  try {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
    [IO.File]::WriteAllText($QuitFlag, (Get-Date -Format 's'), $Utf8NoBom)
    Write-TrayLog '重启: 已写退出哨兵'
  } catch { Write-TrayLog "重启: 写哨兵失败 $($_.Exception.Message)" }
  # ② 等它退干净(最多 12 秒), 不退就强杀
  $deadline = (Get-Date).AddSeconds(12)
  while ((Get-Date) -lt $deadline -and @(Get-ReaderProcs).Count -gt 0) { Start-Sleep -Milliseconds 400 }
  foreach ($p in @(Get-ReaderProcs)) {
    try { Stop-Process -Id $p.ProcessId -Force; Write-TrayLog "重启: 强杀 PID $($p.ProcessId)" } catch {}
  }
  Start-Sleep -Milliseconds 600
  # ③ 清残留哨兵(reader 启动时也会清, 这里双保险), 再重新拉起
  try { if (Test-Path $QuitFlag) { Remove-Item $QuitFlag -Force } } catch {}
  $cmd = Join-Path $Root 'start.cmd'
  if (-not (Test-Path $cmd)) { return "❌ 找不到 $cmd" }
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'start.cmd' `
    -WorkingDirectory $Root -WindowStyle Minimized
  # 等新实例真正起来(python 启动 + 采集器连接要几秒; 固定等 3 秒会误报"没检测到")
  $t2 = (Get-Date).AddSeconds(20)
  while ((Get-Date) -lt $t2 -and @(Get-ReaderProcs).Count -eq 0) { Start-Sleep -Milliseconds 500 }
  $after = @(Get-ReaderProcs).Count
  Write-TrayLog "重启完成: 前 $before 个进程 → 后 $after 个进程"
  # 托盘自己也换代(否则菜单里看不到本次改的新项):
  #   本脚本有单实例保护(互斥体 Local\DanmakuTTSTray) —— 直接再起一个会"让位退出",
  #   所以起一个分离助手: 等本托盘退出(释放互斥体)后再拉起新托盘。
  try {
    if ($script:ni) {
      $helper = "Start-Sleep -Milliseconds 2600; Start-Process -FilePath 'powershell' " +
                "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden'," +
                "'-File','$PSCommandPath','-Config','$Config') -WindowStyle Hidden"
      Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden',
        '-Command', $helper) -WindowStyle Hidden
      Write-TrayLog '重启: 已安排新托盘(助手等本实例退出后拉起)'
      $script:exitTimer = New-Object System.Windows.Forms.Timer
      $script:exitTimer.Interval = 2000
      $script:exitTimer.add_Tick({
        $script:exitTimer.Stop()
        try { $script:ni.Visible = $false; $script:ni.Dispose() } catch {}
        [System.Windows.Forms.Application]::Exit()
      })
      $script:exitTimer.Start()
    }
  } catch { Write-TrayLog "安排新托盘失败: $($_.Exception.Message)" }
  if ($after -gt 0) { return "朗读器已重启（$before 个进程 → $after 个）· 配置已重载, 托盘 2 秒后换新" }
  return "重启命令已发出但没检测到进程, 请看 $LogDir\reader.log"
}

# 返回: 提示文本 / 'exit:tray' / 'exit:reader' / ''
function Invoke-Action([string]$tag) {
  if ($tag -like 'vol:*') {
    $v = [double]($tag.Substring(4))
    if (Set-Volume $v) { return "音量 → $([Math]::Round($v*100))%" }
  } elseif ($tag -like 'gap:*') {
    $v = [double]($tag.Substring(4))
    if (Set-Gap $v) {
      if ($v -le 0) { return "朗读间隔已关闭（来一条读一条）" }
      return "朗读间隔 → $([Math]::Round($v)) 秒（读完一条等这么久，再念那时最新的一条）"
    }
  } elseif ($tag -eq 'latest:toggle') {
    $now = -not (Get-LatestOnly)
    if (Set-LatestOnly $now) { return "只读最新弹幕：$(if ($now) {'开（积压的旧弹幕丢弃）'} else {'关（按顺序逐条念完）'})" }
  } elseif ($tag -like 'vipmin:*') {
    $v = [int]($tag.Substring(7))
    if (Set-VipMin $v) {
      $hint = '（点「重启朗读器」后生效）'
      if ($v -le 0) { return "礼物VIP门槛：任何礼物都算（含免费小心心）$hint" }
      return "礼物VIP门槛：≥ $v 钻（约 $([Math]::Round($v/10.0,1)) 元）才登记为VIP$hint"
    }
  } elseif ($tag -eq 'restart:reader') {
    return (Restart-Reader)
  } elseif ($tag -eq 'tune:up' -or $tag -eq 'tune:down') {
    # 感知步进: ±5 个百分点在响度上只有 ±1.3 dB, 断续语音里基本听不出来,
    # 用户会以为"调了没生效"。改成等比 ×1.25 / ÷1.25(每步约 ±2 dB);
    # 音量为 0(静音)时"大一点"直接给 0.1, 否则乘不上去会卡在静音。
    $cur = [Math]::Max(0.0, [Math]::Min(1.0, (Get-Volume)))
    if ($tag -eq 'tune:up') {
      $v = if ($cur -le 0.001) { 0.1 } else { $cur * 1.25 }
    } else {
      $v = $cur / 1.25
    }
    $v = [Math]::Round([Math]::Max(0.0, [Math]::Min(1.0, $v)), 3)
    if (Set-Volume $v) { return "音量 → $([Math]::Round($v*100))%" }
  } elseif ($tag -like 'flag:*') {
    $key = $tag.Substring(5)
    $now = Toggle-Flag $key
    $name = if ($FlagNames.Contains($key)) { $FlagNames[$key] } else { $key }
    return "朗读【$name】已$(if ($now) {'开启'} else {'关闭'})"
  } elseif ($tag -eq 'open:config') {
    Start-Process notepad.exe -ArgumentList "`"$Config`""
    return "已打开配置文件"
  } elseif ($tag -eq 'open:logs') {
    Start-Process explorer.exe -ArgumentList "`"$LogDir`""
    return "已打开日志目录"
  } elseif ($tag -eq 'exit:tray') {
    return 'exit:tray'
  } elseif ($tag -eq 'exit:reader') {
    try {
      if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
      [IO.File]::WriteAllText($QuitFlag, (Get-Date -Format 's'), $Utf8NoBom)
      Write-TrayLog '已写入退出哨兵, 请求朗读器退出'
    } catch { Write-TrayLog "写退出哨兵失败: $($_.Exception.Message)" }
    return 'exit:reader'
  }
  return ""
}

# ── 菜单项工厂: 每个项都自己挂 Click(子菜单项不会冒泡到根菜单!) ──
$script:itemCount = 0
$script:handlerCount = 0

function Add-TrayItem {
  param($Parent, [string]$Text, [string]$Tag = "", [string]$Kind = "root")
  # ⚠️ 不能写成 `$coll = if (...) { $Parent.Items } else {...}`:
  #    PowerShell 的 if 表达式会把"空集合"吞成 $null、"单元素集合"拆成单个对象,
  #    于是 $coll.Add() 会报 null / 报 ToolStripSeparator 没有 Add 方法(菜单就建不起来)。
  if ($Kind -eq 'sub') { $it = $Parent.DropDownItems.Add($Text) }
  else                 { $it = $Parent.Items.Add($Text) }
  $script:itemCount++
  if ($Tag) {
    $it.Tag = $Tag
    $it.add_Click({ param($sender, $e) Invoke-TrayTag ([string]$sender.Tag) })
    $script:handlerCount++
  }
  return $it
}

function New-TrayMenu {
  $menu = New-Object System.Windows.Forms.ContextMenuStrip
  $vol = Get-Volume

  $hdr = Add-TrayItem $menu "当前音量: $([Math]::Round($vol*100))%" "hdr"
  $hdr.Enabled = $false
  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

  Add-TrayItem $menu "音量 小一点  (÷1.25)" "tune:down" | Out-Null
  Add-TrayItem $menu "音量 大一点  (×1.25)" "tune:up"   | Out-Null

  $volMenu = Add-TrayItem $menu "音量档位"
  foreach ($pct in $VolSteps) {
    $it = Add-TrayItem $volMenu $(if ($pct -eq 0) { '静音 (0%)' } else { "$pct%" }) "vol:$([double]$pct/100)" 'sub'
    $it.Checked = ([Math]::Abs($vol - $pct/100.0) -lt 0.001)
  }

  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  $gapMenu = Add-TrayItem $menu "朗读间隔（读完等多久再读下一条）"
  $gapMenu.Tag = 'gapmenu'
  foreach ($sec in @(0, 2, 3, 5, 8, 10, 15)) {
    $it = Add-TrayItem $gapMenu $(if ($sec -eq 0) { '关闭（来一条读一条）' } else { "$sec 秒 · 期间只读最新" }) "gap:$sec" 'sub'
    $it.Checked = ([Math]::Abs((Get-Gap) - $sec) -lt 0.01)
  }
  $lo = Add-TrayItem $menu "只读最新弹幕（积压的丢弃）" "latest:toggle"
  $lo.Checked = Get-LatestOnly
  # 打赏过的观众留言: 插队 + 强制朗读(不受"只读最新"和朗读间隔限制)
  $vip = Add-TrayItem $menu "礼物观众留言优先（打赏过的观众插队必读）" "flag:vip_gifters"
  $vip.Checked = Get-Flag 'vip_gifters'
  $vip.CheckOnClick = $false
  $vipMinMenu = Add-TrayItem $menu "礼物观众VIP门槛"
  $vipMinMenu.Tag = 'vipminmenu'
  foreach ($dv in @(0, 2, 5, 10, 50, 100)) {
    $label = if ($dv -eq 0) { '任何礼物（含免费小心心）' } else { "≥ $dv 钻（约 $([Math]::Round($dv/10.0,1)) 元）" }
    $it = Add-TrayItem $vipMinMenu $label "vipmin:$dv" 'sub'
    $it.Checked = ((Get-VipMin) -eq $dv)
  }

  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  $readMenu = Add-TrayItem $menu "朗读哪些（点击切换）"
  foreach ($k in $FlagNames.Keys) {
    $it = Add-TrayItem $readMenu $FlagNames[$k] "flag:$k" 'sub'
    $it.Checked = Get-Flag $k
    $it.CheckOnClick = $false     # 勾选以配置文件为准, 由 Update-TrayUi 同步
  }

  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  Add-TrayItem $menu "重启朗读器（改完配置点这里）" "restart:reader" | Out-Null
  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  Add-TrayItem $menu "打开 config.json" "open:config" | Out-Null
  Add-TrayItem $menu "打开日志目录" "open:logs" | Out-Null
  $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  Add-TrayItem $menu "退出托盘（朗读器继续跑）" "exit:tray" | Out-Null
  Add-TrayItem $menu "退出朗读器（含本托盘）" "exit:reader" | Out-Null
  return $menu
}

# 点击统一入口(需要 $script:ni / $script:trayMenu, 由正常模式赋值)
function Invoke-TrayTag([string]$tag) {
  if (-not $tag -or $tag -eq 'hdr') { return }
  Write-TrayLog "点击: $tag"
  $msg = Invoke-Action $tag
  if ($msg -eq 'exit:tray' -or $msg -eq 'exit:reader') {
    if ($script:ni) { $script:ni.Visible = $false; $script:ni.Dispose() }
    [System.Windows.Forms.Application]::Exit()
    return
  }
  if ($msg) {
    Update-TrayUi
    try { if ($script:ni) { $script:ni.ShowBalloonTip(1500, "弹幕朗读器", $msg, [System.Windows.Forms.ToolTipIcon]::Info) } } catch {}
  }
}

function Update-TrayUi {
  $vol = Get-Volume
  if ($script:ni) { $script:ni.Text = "弹幕朗读 · 音量 $([Math]::Round($vol*100))%" }
  if (-not $script:trayMenu) { return }
  $script:trayMenu.Items[0].Text = "当前音量: $([Math]::Round($vol*100))%"
  $gap = Get-Gap
  foreach ($it in $script:trayMenu.Items) {
    if ($it.Tag -eq 'latest:toggle') { $it.Checked = Get-LatestOnly }
    if ($it.Tag -like 'flag:*') { $it.Checked = (Get-Flag $it.Tag.Substring(5)) }
    if ($it.Tag -eq 'vipminmenu') {
      $cur = Get-VipMin
      $it.Text = "礼物观众VIP门槛（现在 $(if ($cur -le 0) {'任何礼物'} else {"≥$cur 钻"})）"
    }
    if ($it.Tag -eq 'gapmenu') { $it.Text = "朗读间隔（现在 $(if ($gap -le 0) {'关闭'} else {"$([Math]::Round($gap)) 秒"})）" }
    if ($it.DropDownItems.Count -gt 0) {
      foreach ($sub in $it.DropDownItems) {
        if ($sub.Tag -like 'flag:*') { $sub.Checked = (Get-Flag $sub.Tag.Substring(5)) }
        elseif ($sub.Tag -like 'vol:*') { $sub.Checked = ([Math]::Abs($vol - [double]$sub.Tag.Substring(4)) -lt 0.001) }
        elseif ($sub.Tag -like 'gap:*') { $sub.Checked = ([Math]::Abs($gap - [double]$sub.Tag.Substring(4)) -lt 0.01) }
        elseif ($sub.Tag -like 'vipmin:*') { $sub.Checked = ((Get-VipMin) -eq [int]$sub.Tag.Substring(7)) }
      }
    }
  }
}

# ── 自检: 打印菜单结构 + 接线检查(不显示图标) ──
if ($SelfTest) {
  $m = New-TrayMenu
  Write-Host "config = $Config"
  Write-Host "音量    = $([Math]::Round((Get-Volume)*100))%"
  Write-Host "菜单结构:"
  function Show-Items($items, $indent) {
    foreach ($it in $items) {
      $tag = if ($it.Tag) { $it.Tag } else { '-' }
      $chk = if ($it.Checked) { '[x]' } else { '[ ]' }
      $en  = if ($it.Enabled) { '' } else { ' (disabled)' }
      Write-Host ("{0}{1} {2}   tag={3}{4}" -f $indent, $chk, $it.Text, $tag, $en)
      if ($it.DropDownItems.Count -gt 0) { Show-Items $it.DropDownItems ($indent + '    ') }
    }
  }
  Show-Items $m.Items '  '
  # 接线检查: 菜单是否真的能挂到 NotifyIcon 上
  $probe = New-Object System.Windows.Forms.NotifyIcon
  $probe.ContextMenuStrip = $m
  $attached = ($null -ne $probe.ContextMenuStrip)
  $probe.Dispose()
  Write-Host ""
  Write-Host "接线自检:"
  Write-Host ("  菜单项总数            = {0}" -f $script:itemCount)
  Write-Host ("  带点击处理器的项      = {0}" -f $script:handlerCount)
  Write-Host ("  可挂到 NotifyIcon 上  = {0}" -f $attached)
  $sub = ($m.Items | Where-Object { $_.DropDownItems.Count -gt 0 } | Measure-Object).Count
  $subItems = 0; foreach ($i in $m.Items) { $subItems += $i.DropDownItems.Count }
  Write-Host ("  含子菜单的顶级项      = {0} (子项共 {1})" -f $sub, $subItems)
  if (-not $attached) { Write-Host "  ❌ 菜单未能挂载" } else { Write-Host "  ✅ 菜单已可挂载, 每个可点项都有处理器" }
  exit 0
}

if ($DoAction) {
  $r = Invoke-Action $DoAction
  Write-Host "action=$DoAction  ->  $r"
  exit 0
}

# ── 正常模式: 显示托盘图标 ──
$mutex = New-Object System.Threading.Mutex($false, 'Local\DanmakuTTSTray')
if (-not $mutex.WaitOne(0)) {
  Write-TrayLog '已有托盘实例在跑, 本进程退出'
  exit 0
}

$bmp = New-Object System.Drawing.Bitmap 32, 32
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)
$bg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 110, 84, 168))
$g.FillEllipse($bg, 0, 0, 32, 32)
$white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
$g.FillRectangle($white, 9, 14, 3, 5)
$g.FillRectangle($white, 14, 11, 3, 11)
$g.FillRectangle($white, 19, 8, 3, 17)
$g.Dispose()
$icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())

$ni = New-Object System.Windows.Forms.NotifyIcon
$script:ni = $ni
$ni.Icon = $icon
$ni.Visible = $true
$ni.Text = "弹幕朗读器"

$menu = New-TrayMenu
$script:trayMenu = $menu
$ni.ContextMenuStrip = $menu          # ← 关键: 挂上去, 否则点图标什么都不弹
Update-TrayUi

# 左键也弹菜单(很多人习惯左键点托盘)
$ni.add_MouseClick({
  param($sender, $e)
  if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left -and $script:trayMenu) {
    $script:trayMenu.Show([System.Windows.Forms.Cursor]::Position)
  }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.add_Tick({ Update-TrayUi })
$timer.Start()

Write-TrayLog ("托盘已启动 (config=$Config, 音量={0}%, 菜单项={1}, 处理器={2}, 菜单已挂载={3})" -f `
  [Math]::Round((Get-Volume)*100), $script:itemCount, $script:handlerCount, ($null -ne $ni.ContextMenuStrip))
try { $ni.ShowBalloonTip(2500, "弹幕朗读器", "托盘已就绪：左键或右键点这个图标就能调音量和朗读开关", [System.Windows.Forms.ToolTipIcon]::Info) } catch {}

[System.Windows.Forms.Application]::Run()
$timer.Stop()
$ni.Visible = $false
$ni.Dispose()
Write-TrayLog '托盘已退出'
