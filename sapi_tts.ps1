# SAPI TTS fallback for multi-danmaku-tts reader (ASCII-only; text is passed via env DANMAKU_TEXT)
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Voice
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) {
        try { $s.SelectVoice($Voice) } catch { }
    }
    if (-not $Voice) {
        # auto-pick a Chinese voice if available
        try {
            $zh = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1
            if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) }
        } catch { }
    }
    $s.SetOutputToWaveFile($Out)
    $s.Speak($env:DANMAKU_TEXT)
}
finally {
    try { $s.Dispose() } catch { }
}
