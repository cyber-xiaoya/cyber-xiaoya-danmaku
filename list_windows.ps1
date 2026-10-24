# list_windows.ps1 - 列出可见顶层窗口(标题/类名/进程/位置尺寸), 用于定位采集目标窗口
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$sig = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinEnum {
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
Add-Type -TypeDefinition $sig -Language CSharp

$rows = New-Object System.Collections.ArrayList
$cb = [WinEnum+EnumProc]{
    param($hWnd, $lParam)
    if (-not [WinEnum]::IsWindowVisible($hWnd)) { return $true }
    $sbT = New-Object System.Text.StringBuilder 512
    $null = [WinEnum]::GetWindowTextW($hWnd, $sbT, 512)
    $title = $sbT.ToString()
    if ($title.Trim().Length -eq 0) { return $true }
    $sbC = New-Object System.Text.StringBuilder 256
    $null = [WinEnum]::GetClassNameW($hWnd, $sbC, 256)
    $procId = 0
    $null = [WinEnum]::GetWindowThreadProcessId($hWnd, [ref]$procId)
    $r = New-Object WinEnum+RECT
    $null = [WinEnum]::GetWindowRect($hWnd, [ref]$r)
    $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
    if ($w -lt 200 -or $h -lt 200) { return $true }
    $pname = ''
    try { $pname = (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch { }
    $null = $rows.Add([pscustomobject]@{
        pid = $procId; proc = $pname; cls = $sbC.ToString(); title = $title
        x = $r.Left; y = $r.Top; w = $w; h = $h
    })
    return $true
}
$null = [WinEnum]::EnumWindows($cb, [IntPtr]::Zero)

$rows | Sort-Object -Property @{Expression = { $_.w * $_.h }; Descending = $true } |
    Format-Table pid, proc, x, y, w, h, title -AutoSize | Out-String -Width 400
