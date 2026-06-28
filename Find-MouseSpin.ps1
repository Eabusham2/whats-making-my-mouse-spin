<#
.SYNOPSIS
    Zero-install version of the mouse-spin detector (Windows PowerShell).

.DESCRIPTION
    Same idea as mouse_spin_detector.py but needs nothing installed - just
    PowerShell, which ships with Windows. It reads the currently displayed
    system cursor and, if it's the busy ring (IDC_WAIT, "full" spin) or the
    working-in-background pointer (IDC_APPSTARTING, "pointer" spin), reports the
    process owning the window under the cursor: name + PID + spin type + whether
    that window is hung.

.PARAMETER Watch
    Keep polling and print whenever the spin state changes.

.PARAMETER IntervalMs
    Poll interval in milliseconds for -Watch (default 200).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Find-MouseSpin.ps1
    powershell -ExecutionPolicy Bypass -File .\Find-MouseSpin.ps1 -Watch
#>
[CmdletBinding()]
param(
    [switch] $Watch,
    [int]    $IntervalMs = 200
)

Add-Type -Namespace MouseSpin -Name Native -MemberDefinition @"
    using System;
    using System.Runtime.InteropServices;

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int x; public int y; }

    [StructLayout(LayoutKind.Sequential)]
    public struct CURSORINFO {
        public uint   cbSize;
        public uint   flags;
        public IntPtr hCursor;
        public POINT  ptScreenPos;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct ICONINFOEXW {
        public uint   cbSize;
        public int    fIcon;
        public uint   xHotspot;
        public uint   yHotspot;
        public IntPtr hbmMask;
        public IntPtr hbmColor;
        public ushort wResID;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szModName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szResName;
    }

    public class Win {
        [DllImport("user32.dll")] public static extern bool GetCursorInfo(ref CURSORINFO pci);
        [DllImport("user32.dll")] public static extern bool GetIconInfoExW(IntPtr hIcon, ref ICONINFOEXW piconinfo);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
        [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll")] public static extern bool IsHungAppWindow(IntPtr hwnd);
        [DllImport("gdi32.dll")]  public static extern bool DeleteObject(IntPtr o);
    }
"@

$OCR_WAIT        = 32514   # full spin
$OCR_APPSTARTING = 32650   # pointer spin
$CURSOR_SHOWING  = 0x1
$GA_ROOT         = 2

function Get-SpinState {
    $ci = New-Object MouseSpin.Native+CURSORINFO
    $ci.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($ci)
    if (-not [MouseSpin.Native+Win]::GetCursorInfo([ref] $ci)) { return $null }

    $showing = ($ci.flags -band $CURSOR_SHOWING) -ne 0
    if (-not $showing -or $ci.hCursor -eq [IntPtr]::Zero) {
        return [pscustomobject]@{ Spin = $null; Pos = $ci.ptScreenPos }
    }

    $info = New-Object MouseSpin.Native+ICONINFOEXW
    $info.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($info)
    $resId = $null
    if ([MouseSpin.Native+Win]::GetIconInfoExW($ci.hCursor, [ref] $info)) {
        if ($info.hbmMask  -ne [IntPtr]::Zero) { [void][MouseSpin.Native+Win]::DeleteObject($info.hbmMask) }
        if ($info.hbmColor -ne [IntPtr]::Zero) { [void][MouseSpin.Native+Win]::DeleteObject($info.hbmColor) }
        $resId = [int] $info.wResID
    }

    $spin = switch ($resId) {
        $OCR_WAIT        { 'full' }
        $OCR_APPSTARTING { 'pointer' }
        default          { $null }
    }
    [pscustomobject]@{ Spin = $spin; Pos = $ci.ptScreenPos; ResId = $resId }
}

function Get-Culprit($pos) {
    $hwnd = [MouseSpin.Native+Win]::WindowFromPoint($pos)
    $via  = 'under-cursor'
    if ($hwnd -eq [IntPtr]::Zero) {
        $hwnd = [MouseSpin.Native+Win]::GetForegroundWindow()
        $via  = 'foreground'
    }
    if ($hwnd -eq [IntPtr]::Zero) { return $null }

    $root = [MouseSpin.Native+Win]::GetAncestor($hwnd, $GA_ROOT)
    if ($root -eq [IntPtr]::Zero) { $root = $hwnd }

    # NB: do NOT use $pid here - it's PowerShell's read-only automatic variable
    # (case-insensitive), so assigning to it throws. Use a distinct name.
    [uint32] $targetPid = 0
    [void][MouseSpin.Native+Win]::GetWindowThreadProcessId($root, [ref] $targetPid)
    $proc = $null
    try { $proc = Get-Process -Id $targetPid -ErrorAction Stop } catch {}
    $hung = [MouseSpin.Native+Win]::IsHungAppWindow($root)

    [pscustomobject]@{
        Name = if ($proc) { $proc.ProcessName } else { '(unknown)' }
        Pid  = $targetPid
        Via  = $via
        Hung = $hung
        Title = if ($proc) { $proc.MainWindowTitle } else { '' }
    }
}

function Report($state) {
    if ($null -eq $state -or $null -eq $state.Spin) { return $false }
    $desc = if ($state.Spin -eq 'full') { 'full spin (busy / wait cursor)' }
            else { 'pointer spin (working-in-background cursor)' }
    $c = Get-Culprit $state.Pos
    Write-Host "SPINNING DETECTED -> $desc" -ForegroundColor Yellow
    if ($null -eq $c) {
        Write-Host "  Could not attribute this to a process. Not possible this time."
        return $true
    }
    Write-Host ("  Culprit: {0}  (PID {1})  [via: {2}]" -f $c.Name, $c.Pid, $c.Via)
    if ($c.Title) { Write-Host ("  Window:  `"{0}`"" -f $c.Title) }
    if ($c.Hung)  { Write-Host "  State:   NOT RESPONDING (window is hung)" -ForegroundColor Red }
    return $true
}

if ($Watch) {
    Write-Host "Watching for a spinning cursor (every $IntervalMs ms). Ctrl+C to stop.`n"
    $last = ''
    while ($true) {
        $state = Get-SpinState
        $sig = if ($state -and $state.Spin) { "$($state.Spin)" } else { 'none' }
        if ($sig -ne $last) {
            if ($sig -eq 'none') { Write-Host "[$(Get-Date -Format HH:mm:ss)] spin stopped.`n" }
            else { Write-Host "[$(Get-Date -Format HH:mm:ss)]" -NoNewline; Write-Host ''; [void](Report $state); Write-Host '' }
        }
        $last = $sig
        Start-Sleep -Milliseconds $IntervalMs
    }
} else {
    $state = Get-SpinState
    if (-not (Report $state)) {
        Write-Host "No spinning cursor right now."
    }
}
