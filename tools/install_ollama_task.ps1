<#
  install_ollama_task.ps1 -- run ensure_ollama.ps1 at logon.

  Install:    powershell -ExecutionPolicy Bypass -File tools\install_ollama_task.ps1
  Remove:     powershell -ExecutionPolicy Bypass -File tools\install_ollama_task.ps1 -Remove
  Inspect:    Get-ScheduledTask -TaskName 'JARVIS ensure ollama'

  A per-user logon task, NOT a Windows service: no admin rights needed, it is
  visible in Task Scheduler where he can see and disable it, and it removes
  cleanly. A service would need elevation and an extra wrapper for a daemon that
  already knows how to run itself.

  It does not replace Ollama's own Startup shortcut. That shortcut launches the
  TRAY app; this checks the thing JARVIS actually depends on -- the HTTP server on
  11434 -- and starts it only if nothing is listening. The two are complementary,
  and running both is harmless because ensure_ollama.ps1 is idempotent.
#>

[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$taskName = 'JARVIS ensure ollama'
$script = Join-Path $PSScriptRoot 'ensure_ollama.ps1'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "[install_ollama_task] removed '$taskName'."
    } else {
        Write-Output "[install_ollama_task] '$taskName' was not registered."
    }
    exit 0
}

if (-not (Test-Path $script)) { throw "cannot find $script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# A short delay so it does not race the rest of logon, and a retry because the
# network stack and the disk are both busy at that moment.
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description `
    'Starts the ollama HTTP server if nothing is listening on 11434. JARVIS needs it for local vision (llava) and the local LLM fallback. Session 4: it was down for a whole session and every vision feature failed silently.' `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
Write-Output "[install_ollama_task] registered '$taskName' (state: $($t.State))."
Write-Output "[install_ollama_task] it runs 30s after logon, retries twice, and does nothing when the server is already up."
