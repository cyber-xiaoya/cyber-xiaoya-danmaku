# test_printwindow.ps1 - 测试 PrintWindow 离屏截取(窗口被遮挡时能否取到画面)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing

$sig = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class PW {
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
Add-Type -TypeDefinition $sig -Language CSharp

$target = [IntPtr]::Zero
$cb = [PW+EnumProc]{
    param($h, $l)
    if (-not [PW]::IsWindowVisible($h)) { return $true }
    $sb = New-Object System.Text.StringBuilder 512
    $null = [PW]::GetWindowTextW($h, $sb, 512)
    if ($sb.ToString() -match '视频号直播伴侣') { $script:target = $h; return $false }
    return $true
}
$null = [PW]::EnumWindows($cb, [IntPtr]::Zero)

if ($target -eq [IntPtr]::Zero) { '未找到「视频号直播伴侣」窗口'; exit 1 }

$r = New-Object PW+RECT
$null = [PW]::GetWindowRect($target, [ref]$r)
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
"目标窗口: $w x $h at ($($r.Left),$($r.Top))"

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
# 0x2 = PW_RENDERFULLCONTENT (支持 DirectComposition 渲染的窗口)
$ok = [PW]::PrintWindow($target, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()
"PrintWindow 返回: $ok"

$out = 'E:\deepseek-works\multi-danmaku-tts\logs\pw_test.png'
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)

# 采样判断是否黑屏/空白(取若干点算亮度均值与方差)
$sum = 0.0; $sum2 = 0.0; $n = 0
for ($x = 20; $x -lt $w - 20; $x += 40) {
    for ($y = 20; $y -lt $h - 20; $y += 40) {
        $c = $bmp.GetPixel($x, $y)
        $lum = 0.299 * $c.R + 0.587 * $c.G + 0.114 * $c.B
        $sum += $lum; $sum2 += $lum * $lum; $n++
    }
}
$mean = $sum / $n
$var = ($sum2 / $n) - ($mean * $mean)
$bmp.Dispose()
"采样点 $n 个: 亮度均值={0:N1}  方差={1:N1}" -f $mean, $var
if ($var -gt 50) { "结论: 画面有内容 ✓ (PrintWindow 离屏截图可用, 窗口被遮挡也能抓)" }
else { "结论: 画面近乎纯色 ✗ (该窗口不支持离屏截图, 必须保持窗口可见不被遮挡)" }
"测试图: $out"
