param(
    [int]$IntervalSeconds = 60,
    [int]$WorkerIntervalSeconds = 20
)

$ErrorActionPreference = "Continue"

Add-Type -Namespace Win32 -Name Power -MemberDefinition @"
    [System.Runtime.InteropServices.DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
"@

$ES_CONTINUOUS = [UInt32]"0x80000000"
$ES_SYSTEM_REQUIRED = [UInt32]"0x00000001"
$ES_DISPLAY_REQUIRED = [UInt32]"0x00000002"
$flags = [UInt32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_DISPLAY_REQUIRED)
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$workerScript = Join-Path $repoRoot "tools\keep_awake.ps1"
$logDir = Join-Path $repoRoot "logs"
$logPath = Join-Path $logDir "keep_awake_watchdog.log"

function Write-WatchdogLog {
    param([string]$Message)
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$timestamp $Message"
}

function Set-NoIdleTimeouts {
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
    powercfg /change monitor-timeout-ac 0 | Out-Null
    powercfg /change disk-timeout-ac 0 | Out-Null
    powercfg /change standby-timeout-dc 0 | Out-Null
    powercfg /change hibernate-timeout-dc 0 | Out-Null
    powercfg /change monitor-timeout-dc 0 | Out-Null
    powercfg /change disk-timeout-dc 0 | Out-Null
}

function Start-KeepAwakeWorker {
    $escapedWorkerScript = $workerScript.Replace('"', '\"')
    $args = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$escapedWorkerScript`" -IntervalSeconds $WorkerIntervalSeconds -KeepDisplayOn -SendF15"
    $process = Start-Process -FilePath powershell.exe -ArgumentList $args -WindowStyle Hidden -PassThru
    Write-WatchdogLog "started worker pid=$($process.Id)"
}

Write-WatchdogLog "watchdog started interval=$IntervalSeconds worker_interval=$WorkerIntervalSeconds"

while ($true) {
    try {
        [void][Win32.Power]::SetThreadExecutionState($flags)
        Set-NoIdleTimeouts

        $workers = Get-CimInstance Win32_Process -Filter "name = 'powershell.exe'" |
            Where-Object { $_.CommandLine -like "*keep_awake.ps1*" -and $_.CommandLine -notlike "*keep_awake_watchdog.ps1*" }

        if (-not $workers) {
            Start-KeepAwakeWorker
        }
        else {
            Write-WatchdogLog "worker_count=$($workers.Count)"
        }
    }
    catch {
        Write-WatchdogLog "error=$($_.Exception.Message)"
    }

    Start-Sleep -Seconds $IntervalSeconds
}
