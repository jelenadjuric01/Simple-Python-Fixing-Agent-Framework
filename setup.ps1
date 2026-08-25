# Workshop setup for Windows. One command, from the course root:
#
#     powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Like setup.sh, this exists to solve one problem — getting a Python 3.12 onto the machine —
# and then hands over to setup.py for the model work. On Windows that matters more than
# anywhere else: a fresh Windows has no Python at all, so nothing written in Python can be the
# entry point. PowerShell is always there.
#
#     ... -File setup.ps1 -Yes            # assume yes (pre-session run)
#     ... -File setup.ps1 -- --dry-run    # plan only
#     ... -File setup.ps1 -- --tier qwen  # anything after -- is passed to setup.py

[CmdletBinding()]
param(
    [switch]$Yes,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Passthrough = @()
)

$ErrorActionPreference = "Stop"
$Series = "3.12"
$UvUrl = "https://astral.sh/uv/install.ps1"
$PythonOrg = "https://www.python.org/downloads/"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$dryRun = $Passthrough -contains "--dry-run"

function Say($text) { Write-Host $text }

function Confirm-Step($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) {
        Say "     (not interactive, so nothing is assumed — re-run with -Yes)"
        return $false
    }
    $reply = Read-Host "     $question [Y/n]"
    return ($reply -eq "" -or $reply -match '^(y|yes)$')
}

function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)
    Say "     `$ $Exe $($Arguments -join ' ')"
    if (-not (Confirm-Step "run it?")) { Say "declined — nothing was changed"; exit 1 }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { return $false }
    return $true
}

function Test-NewEnough($exe) {
    if (-not $exe) { return $false }
    # The interpreter answers for itself, rather than us parsing a version string.
    & $exe -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Find-Python {
    # The py launcher first: it is the one thing that can name a specific series on Windows.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = & py "-$Series" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found -and (Test-NewEnough $found)) { return $found }
    }
    foreach ($name in @("python3.12", "python3", "python")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        # Skip the Microsoft Store stub, which exists as an app-execution alias and exits 9009.
        if ($command -and $command.Source -notlike "*WindowsApps*" -and (Test-NewEnough $command.Source)) {
            return $command.Source
        }
    }
    foreach ($uv in @("uv", "$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
        $resolved = if ($uv -eq "uv") { (Get-Command uv -ErrorAction SilentlyContinue).Source } else { $uv }
        if ($resolved -and (Test-Path $resolved)) {
            $found = & $resolved python find $Series 2>$null
            if ($found -and (Test-Path $found) -and (Test-NewEnough $found)) { return $found }
        }
    }
    return $null
}

function Install-WithWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    return (Invoke-Step "winget" @("install", "-e", "--id", "Python.Python.$Series",
                                   "--accept-package-agreements", "--accept-source-agreements"))
}

function Install-WithUv {
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
            if (Test-Path $candidate) { $uv = $candidate; break }
        }
    }
    if (-not $uv) {
        Say "     installing uv, to fetch a real CPython:"
        if (-not (Invoke-Step "powershell" @("-ExecutionPolicy", "ByPass", "-c", "irm $UvUrl | iex"))) {
            return $null
        }
        foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
            if (Test-Path $candidate) { $uv = $candidate; break }
        }
    }
    if (-not $uv) { Say "     uv installed but cannot be found — open a new PowerShell and retry"; return $null }
    if (-not (Invoke-Step $uv @("python", "install", $Series))) { return $null }
    # `uv python find`, not PATH: uv installs into ~\.local\bin, which this session has not
    # picked up yet.
    $found = & $uv python find $Series 2>$null
    if ($found -and (Test-Path $found)) { return $found }
    return $null
}

Say "agentfix setup"
$winget = if (Get-Command winget -ErrorAction SilentlyContinue) { "winget" } else { "none found" }
Say "  machine: windows, package manager: $winget"

$python = Find-Python

if (-not $python) {
    Say ""
    Say "[todo] python: nothing here is $Series or newer"
    if ($dryRun) {
        Say "       would install Python $Series with $winget (or uv), then run setup.py under it"
        Say ""
        Say "READY (dry run — nothing was changed)"
        exit 0
    }

    if (Install-WithWinget) { $python = Find-Python }

    if (-not $python) {
        if ($winget -ne "none found") {
            Say "     winget did not produce a Python $Series — trying the portable uv installer instead."
        }
        $python = Install-WithUv
    }

    if (-not $python -or -not (Test-NewEnough $python)) {
        Say ""
        Say "Could not get Python $Series onto this machine."
        Say "Install it from $PythonOrg, then run this script again."
        exit 1
    }
    Say "[ok]   python: $(& $python -V 2>&1) at $python"
}

# Everything from here — the tier, the models, the context window, .agentfix.env, the
# MELLUM_MODEL variable — is identical on every OS, so it lives in setup.py and nowhere else.
$arguments = @((Join-Path $root "setup.py"), "--bootstrapped")
if ($Yes) { $arguments += "--yes" }
$arguments += $Passthrough
& $python @arguments
exit $LASTEXITCODE
