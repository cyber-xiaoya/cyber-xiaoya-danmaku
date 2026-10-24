# test_uia.ps1 - 用 Windows UI Automation 读取指定窗口的文本元素(免截图/免OCR)
param([string]$TitleMatch = '视频号直播伴侣', [int]$MaxItems = 80)

$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$sig = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class UE {
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
}
'@
Add-Type -TypeDefinition $sig -Language CSharp

$hwnds = New-Object System.Collections.ArrayList
$cb = [UE+EnumProc]{
    param($h, $l)
    $sb = New-Object System.Text.StringBuilder 512
    $null = [UE]::GetWindowTextW($h, $sb, 512)
    if ($sb.ToString() -match $TitleMatch) { $null = $script:hwnds.Add([pscustomobject]@{ h = $h; t = $sb.ToString() }) }
    return $true
}
$null = [UE]::EnumWindows($cb, [IntPtr]::Zero)

if ($hwnds.Count -eq 0) { "未找到标题匹配 '$TitleMatch' 的窗口"; exit 1 }
foreach ($item in $hwnds) { "匹配窗口: '$($item.t)' 句柄=$($item.h)" }

$h = $hwnds[0].h
try {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
} catch { "FromHandle 失败: $($_.Exception.Message)"; exit 1 }
if (-not $root) { 'UIA 无法获取该窗口元素'; exit 1 }

"窗口名: $($root.Current.Name)   类: $($root.Current.ClassName)"

$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                     [System.Windows.Automation.Condition]::TrueCondition)
"后代元素总数: $($all.Count)"

$texts = @()
foreach ($el in $all) {
    try {
        $ct = $el.Current.ControlType.ProgrammaticName
        $nm = $el.Current.Name
        if ($nm -and $nm.Trim().Length -gt 0) {
            $texts += [pscustomobject]@{ type = ($ct -replace 'ControlType\.', ''); text = $nm.Trim() }
        }
    } catch { }
}
"含文本元素数: $($texts.Count)"
"--- 前 $MaxItems 条 ---"
$texts | Select-Object -First $MaxItems | ForEach-Object { "[$($_.type)] $($_.text)" }
