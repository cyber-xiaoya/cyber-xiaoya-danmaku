# wechat_ocr.ps1 - 屏幕区域截图 + Windows 自带 OCR(zh-Hans-CN), 逐行输出 JSON
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File wechat_ocr.ps1 -X 0 -Y 0 -W 0 -H 0 -Repeat 1
#   区域 W/H 为 0 时截取整屏; -Repeat 0 表示无限循环(每 IntervalMs 一帧)
param(
    [int]$X = 0,
    [int]$Y = 0,
    [int]$W = 0,
    [int]$H = 0,
    [int]$IntervalMs = 1500,
    [int]$Repeat = 1,
    [string]$SaveShot = ''
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language 'zh-Hans-CN'))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Output '{"error":"no ocr engine"}'; exit 1 }

$tmp = Join-Path $env:TEMP ('dsh_ocr_{0}.png' -f $PID)
$script:frame = 0

while ($true) {
    $script:frame++
    $bmp = $null
    try {
        $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
        if ($W -le 0 -or $H -le 0) {
            $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
            $X = $bounds.X; $Y = $bounds.Y; $W = $bounds.Width; $H = $bounds.Height
        }
        $bmp = New-Object System.Drawing.Bitmap $W, $H
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size $W, $H))
        $g.Dispose()
        $bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
        if ($SaveShot -ne '') { $bmp.Save($SaveShot, [System.Drawing.Imaging.ImageFormat]::Png) }
    } finally {
        if ($bmp) { $bmp.Dispose() }
    }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $softwareBitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()

    $lines = @()
    foreach ($line in $result.Lines) {
        $txt = $line.Text
        if ($txt -and $txt.Trim().Length -gt 0) {
            $lines += [ordered]@{ text = $txt.Trim() }
        }
    }
    $payload = [ordered]@{
        frame = $script:frame
        ts    = [int64](([datetime]::UtcNow - [datetime]'1970-01-01').TotalMilliseconds)
        region = @{ x = $X; y = $Y; w = $W; h = $H }
        lines = $lines
    }
    Write-Output ($payload | ConvertTo-Json -Compress -Depth 5)

    if ($Repeat -gt 0 -and $script:frame -ge $Repeat) { break }
    Start-Sleep -Milliseconds $IntervalMs
}

Remove-Item $tmp -Force -ErrorAction SilentlyContinue
