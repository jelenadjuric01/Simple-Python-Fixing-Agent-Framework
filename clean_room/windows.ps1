# A clean Windows. Run this INSIDE a fresh Windows machine — a VM, Windows Sandbox, or a
# cloud box. It is not a container: Windows containers need a Windows host, so there is no
# way to spin one up from macOS or Linux.
#
# Where to get the fresh Windows (any of these):
#   * Windows Sandbox     — Win 11 Pro/Enterprise only, resets completely on close. The
#                           quickest real clean room if you have Pro.
#   * A VM                — UTM (free) or Parallels on Apple Silicon, using the Windows 11
#                           ARM64 image from Microsoft. Snapshot it before testing so you can
#                           roll back instead of rebuilding.
#   * GitHub Actions      — no local VM at all; see clean_room/github-windows.yml.
#
# Then, in that machine's PowerShell, from the course root:
#
#   powershell -ExecutionPolicy Bypass -File clean_room\windows.ps1
#   powershell -ExecutionPolicy Bypass -File clean_room\windows.ps1 -OldPython
#   powershell -ExecutionPolicy Bypass -File clean_room\windows.ps1 -Tier qwen
#
# -OldPython first installs Python 3.11, to reproduce the case that matters: a learner who has
# a Python, just not a new enough one. Without it, a truly clean Windows has no Python at all —
# which setup.py cannot fix, by design, because nothing written in Python can bootstrap Python.

[CmdletBinding()]
param(
    [switch]$OldPython,
    [string]$Tier = "",
    [switch]$DryRun,
    [switch]$HideOllama
)

$ErrorActionPreference = "Continue"

function Show-Fact($label, $value) {
    Write-Host ("  {0,-22} {1}" -f $label, $value)
}

Write-Host "== clean room: Windows =="
Show-Fact "OS" (Get-CimInstance Win32_OperatingSystem).Caption
Show-Fact "Architecture" $env:PROCESSOR_ARCHITECTURE

$ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
Show-Fact "RAM" ("{0:N1} GB" -f ($ramBytes / 1GB))
Write-Host "  (setup.py reads this same number through ctypes GlobalMemoryStatusEx)"

$winget = Get-Command winget -ErrorAction SilentlyContinue
Show-Fact "winget" $(if ($winget) { $winget.Source } else { "NOT PRESENT — setup.py will fall back to uv" })

foreach ($name in @("python", "python3", "py")) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) {
        $version = & $found.Source -V 2>&1
        Show-Fact $name "$($found.Source)  ($version)"
    } else {
        Show-Fact $name "not on PATH"
    }
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
Show-Fact "ollama" $(if ($ollama) { $ollama.Source } else { "not installed" })
Write-Host ""

if ($OldPython) {
    if (-not $winget) {
        Write-Host "-OldPython needs winget, which is not on this machine. Install Python 3.11"
        Write-Host "from python.org instead, then re-run without -OldPython."
        exit 1
    }
    Write-Host "Installing Python 3.11, so setup.py has a too-old interpreter to upgrade..."
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    Write-Host ""
    Write-Host "Open a NEW PowerShell window so PATH picks it up, then re-run this script"
    Write-Host "without -OldPython."
    exit 0
}

# No Python at all is fine now: setup.ps1 installs one. That is the whole reason the entry
# point is PowerShell rather than a .py file.

# setup.ps1 is the entry point now: it gets Python 3.12 first, then runs setup.py.
$arguments = @("-ExecutionPolicy", "Bypass", "-File", "setup.ps1")
if ($Tier)   { $arguments += @("--tier", $Tier) }
if ($DryRun) { $arguments += "--dry-run" }
if ($HideOllama) {
    # A clean Windows has nothing listening on 11434. If this machine does, point elsewhere so
    # the model steps report honestly.
    $arguments += @("--base-url", "http://127.0.0.1:11435/v1")
}

Write-Host "== running: powershell $($arguments -join ' ') =="
Write-Host ""
& powershell @arguments
$status = $LASTEXITCODE
Write-Host ""
Write-Host "== setup.py exited $status =="

Write-Host ""
Write-Host "Things worth checking by hand afterwards, because they are the Windows-only paths:"
Write-Host "  [Environment]::GetEnvironmentVariable('MELLUM_MODEL', 'User')   # what setx wrote"
Write-Host "  Get-Content .agentfix.env"
Write-Host "  ollama ps                                                      # CONTEXT must be 16384"
Write-Host "  python run.py doctor"
exit $status
