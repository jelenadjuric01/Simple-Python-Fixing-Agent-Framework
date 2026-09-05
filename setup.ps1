# Workshop setup for Windows. One command, from the course root:
#
#     powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Like setup.sh, this exists to solve one problem -- getting a Python 3.12 onto the machine --
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

# "Stop", so that a cmdlet which fails stops the script instead of carrying on with $null.
#
# The problem this used to be set to "SilentlyContinue" for is real, but narrower than the
# script: under "Stop", PowerShell 5.x turns anything a NATIVE command writes to stderr into a
# terminating error -- and probing for an interpreter means running commands whose "not found"
# answer IS stderr chatter (py.exe: "no suitable runtime"; uv: "no interpreter found"). Setting
# it script-wide fixed those four calls and silenced every cmdlet in the file at the same time,
# which is a blanket try/catch-and-continue around the whole thing: Join-Path, Test-Path,
# Get-Command or a file write could fail, hand back $null, and be used anyway.
#
# So the tolerance lives where the native calls are instead: `Invoke-Native` and `Invoke-Step`
# each drop the preference to "Continue" in their own function scope, which covers the command
# they run and nothing else. Their exit status is still checked explicitly via $LASTEXITCODE.
$ErrorActionPreference = "Stop"
$Series = "3.12"
$UvUrl = "https://astral.sh/uv/install.ps1"
$PythonOrg = "https://www.python.org/downloads/"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$dryRun = $Passthrough -contains "--dry-run"

function Say($text) { Write-Host $text }

function Invoke-Native {
    <#
      Run a probe and hand back its stdout, tolerating whatever it says on stderr.

      $ErrorActionPreference is assigned inside the function, so it is function-scoped: it
      applies to this call and leaves the script's "Stop" alone. Callers read $LASTEXITCODE,
      which native commands set globally and a function call does not disturb.
    #>
    param([string]$Exe, [string[]]$Arguments, [switch]$Merge)
    $ErrorActionPreference = "Continue"
    if ($Merge) { return (& $Exe @Arguments 2>&1) }
    return (& $Exe @Arguments 2>$null)
}

function Confirm-Step($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) {
        Say "     (not interactive, so nothing is assumed -- re-run with -Yes)"
        return $false
    }
    $reply = Read-Host "     $question [Y/n]"
    return ($reply -eq "" -or $reply -match '^(y|yes)$')
}

function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)
    Say "     `$ $Exe $($Arguments -join ' ')"
    if (-not (Confirm-Step "run it?")) { Say "declined -- nothing was changed"; exit 1 }
    # Function-scoped, as in Invoke-Native: winget and the uv installer both write progress and
    # warnings to stderr, and under "Stop" the first such line would abort the script instead of
    # letting the exit code decide. Output is NOT silenced here -- an install is the one thing
    # the learner should watch.
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { return $false }
    return $true
}

function Test-NewEnough($exe) {
    if (-not $exe) { return $false }
    # The interpreter answers for itself, rather than us parsing a version string.
    Invoke-Native $exe @("-c", "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)") | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Find-Python {
    # The py launcher first: it is the one thing that can name a specific series on Windows.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = Invoke-Native "py" @("-$Series", "-c", "import sys; print(sys.executable)")
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
            $found = Invoke-Native $resolved @("python", "find", $Series)
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
    if (-not $uv) { Say "     uv installed but cannot be found -- open a new PowerShell and retry"; return $null }
    if (-not (Invoke-Step $uv @("python", "install", $Series))) { return $null }
    # `uv python find`, not PATH: uv installs into ~\.local\bin, which this session has not
    # picked up yet.
    $found = Invoke-Native $uv @("python", "find", $Series)
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
        Say "READY (dry run -- nothing was changed)"
        exit 0
    }

    if (Install-WithWinget) { $python = Find-Python }

    if (-not $python) {
        if ($winget -ne "none found") {
            Say "     winget did not produce a Python $Series -- trying the portable uv installer instead."
        }
        $python = Install-WithUv
    }

    if (-not $python -or -not (Test-NewEnough $python)) {
        Say ""
        Say "Could not get Python $Series onto this machine."
        Say "Install it from $PythonOrg, then run this script again."
        exit 1
    }
    Say "[ok]   python: $(Invoke-Native $python @("-V") -Merge) at $python"
}

# Everything from here -- the tier, the models, the context window, .agentfix.env, the
# MELLUM_MODEL variable -- is identical on every OS, so it lives in setup.py and nowhere else.
$arguments = @((Join-Path $root "setup.py"), "--bootstrapped")
if ($Yes) { $arguments += "--yes" }
$arguments += $Passthrough
# Called at script scope, NOT through a function, and this is load-bearing. Inside a function
# `& $exe @args` writes stdout to the FUNCTION's output stream; `exit (Invoke-SetupPy ...)` then
# evaluates that subexpression to completion, collects every line setup.py printed as pipeline
# objects, and throws them away turning the value into an exit code. The learner sees setup start
# and then silence -- and never sees a confirmation prompt, so an interactive run hangs. Measured
# on Windows 11: `setup.ps1` printed setup.py's first two lines and stopped, while the identical
# `python setup.py --dry-run` ran to completion.
#
# "Continue" for the same reason `Invoke-Native` uses it: a traceback out of setup.py belongs on
# the console followed by its exit code, not turned into a terminating error that loses the
# status. Set here rather than in a function scope because there is no cmdlet left to guard --
# this is the last statement in the file.
$ErrorActionPreference = "Continue"
& $python @arguments
exit $LASTEXITCODE
