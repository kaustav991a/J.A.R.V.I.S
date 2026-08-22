<#
  ensure_ollama.ps1 -- make sure the local model server is actually listening.

  Run by hand:   powershell -ExecutionPolicy Bypass -File tools\ensure_ollama.ps1
  Run at logon:  registered as the scheduled task "JARVIS ensure ollama"
                 (see tools\install_ollama_task.ps1)

  WHY THIS EXISTS
  ---------------
  Live-gate session 4: every local-vision feature was dead for the whole session
  and nothing said so. `ollama` was not running, so "what's on my screen?" had no
  model to ask and row 12.1 could not be attempted at all.

  Windows already had a Startup shortcut -- but it launches `ollama app.exe`, the
  TRAY application. Quit the tray icon once, or have it fail, and nothing brings
  the server back until the next logon. The thing JARVIS depends on is the HTTP
  server on 11434, not the tray, so that is what this checks and starts.

  Idempotent on purpose: if the API answers, it does nothing and says so. Running
  it twice is harmless, which is what makes it safe to attach to a logon trigger
  and to call from a shell without thinking.
#>

[CmdletBinding()]
param(
    [int]$Port = 11434,
    [int]$WaitSeconds = 25
)

$ErrorActionPreference = 'Stop'
$api = "http://127.0.0.1:$Port/api/tags"

function Test-Ollama {
    try {
        $r = Invoke-WebRequest -Uri $api -TimeoutSec 4 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (Test-Ollama) {
    Write-Output "[ensure_ollama] already listening on $Port -- nothing to do."
    exit 0
}

# Prefer the exe next to the tray app; fall back to PATH.
$exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $exe)) {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { $exe = $cmd.Source }
}
if (-not (Test-Path $exe)) {
    Write-Output "[ensure_ollama] ollama is not installed where expected ($exe) and is not on PATH."
    Write-Output "[ensure_ollama] Nothing started. Local vision and the local LLM fallback stay unavailable."
    exit 2
}

Write-Output "[ensure_ollama] not listening -- starting: $exe serve"
Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden | Out-Null

# Wait for READY, not merely for the process to exist: a started process that is
# not yet accepting connections looks identical to a working one to the caller,
# and the desk's very first vision call would be the thing that discovers it.
# Windows PowerShell 5.1: `$x = try {...} catch {...}` is a PARSE error here --
# assigning from a try statement arrived in PowerShell 7. Plain try/catch.
$started = Get-Date
$deadline = $started.AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    if (Test-Ollama) {
        $models = '?'
        try {
            $body = (Invoke-WebRequest -Uri $api -TimeoutSec 4 -UseBasicParsing).Content
            $models = ($body | ConvertFrom-Json).models.Count
        } catch {
            $models = '?'
        }
        $secs = [int]((Get-Date) - $started).TotalSeconds
        Write-Output "[ensure_ollama] listening on $Port after ${secs}s; $models model(s) available."
        exit 0
    }
}

Write-Output "[ensure_ollama] started the process but nothing answered on $Port within ${WaitSeconds}s."
Write-Output "[ensure_ollama] Treat local vision as unavailable until this is resolved."
exit 3
