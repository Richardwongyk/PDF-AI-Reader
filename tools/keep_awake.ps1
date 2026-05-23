param(
    [int]$IntervalSeconds = 30,
    [switch]$KeepDisplayOn,
    [switch]$SendF15
)

$ErrorActionPreference = "Stop"

Add-Type -Namespace Win32 -Name Power -MemberDefinition @"
    [System.Runtime.InteropServices.DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
"@

Add-Type -Namespace Win32 -Name Keyboard -MemberDefinition @"
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
"@

$ES_CONTINUOUS = [UInt32]"0x80000000"
$ES_SYSTEM_REQUIRED = [UInt32]"0x00000001"
$ES_DISPLAY_REQUIRED = [UInt32]"0x00000002"
$VK_F15 = 0x7E
$KEYEVENTF_KEYUP = 0x0002

$flags = [UInt32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
if ($KeepDisplayOn) {
    $flags = [UInt32]($flags -bor $ES_DISPLAY_REQUIRED)
}

try {
    while ($true) {
        [void][Win32.Power]::SetThreadExecutionState($flags)
        if ($SendF15) {
            [Win32.Keyboard]::keybd_event([byte]$VK_F15, 0, 0, [UIntPtr]::Zero)
            [Win32.Keyboard]::keybd_event([byte]$VK_F15, 0, $KEYEVENTF_KEYUP, [UIntPtr]::Zero)
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)
}
