# Origin PowerShell Integration
# Usage:
#   origin "What time is it?"           — Quick question
#   origin-analyze C:\file.txt          — Analyze a file
#   origin-clipboard                    — Analyze clipboard content
#   origin-status                       — Check Origin status
#   origin-open                         — Open Origin window
#
# Installation (run once):
#   . .\scripts\origin.ps1
#   Install-OriginProfile
#
# Or add to your PowerShell profile manually:
#   Add-Content $PROFILE ". <ruta-a-Origin>\scripts\origin.ps1"

$script:Origin_API = "http://localhost:9001"

function origin {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0, ValueFromRemainingArguments)]
        [string[]]$Query
    )
    $message = ($Query -join " ").Trim()
    if (-not $message) {
        Write-Host "[Origin] " -ForegroundColor Cyan -NoNewline
        Write-Host "Usage: origin `"your question here`""
        return
    }
    try {
        $body = @{ message = $message; context_tags = @("terminal") } | ConvertTo-Json -Depth 3
        $response = Invoke-RestMethod -Uri "$script:Origin_API/chat" -Method POST -Body $body -ContentType "application/json; charset=utf-8" -TimeoutSec 30
        if ($response.reply) {
            Write-Host ""
            Write-Host "[Origin] " -ForegroundColor Cyan -NoNewline
            Write-Host $response.reply
            Write-Host ""
        } else {
            Write-Host "[Origin] No response received." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[Origin] Error: Origin backend not reachable at $script:Origin_API" -ForegroundColor Red
        Write-Host "         Make sure Origin is running." -ForegroundColor DarkGray
    }
}

function origin-analyze {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0, Mandatory)]
        [string]$Path
    )
    if (-not (Test-Path $Path)) {
        Write-Host "[Origin] File not found: $Path" -ForegroundColor Red
        return
    }
    $content = Get-Content $Path -Raw -Encoding utf8 -ErrorAction SilentlyContinue
    if (-not $content) {
        Write-Host "[Origin] Could not read file or file is empty." -ForegroundColor Yellow
        return
    }
    $truncated = if ($content.Length -gt 6000) { $content.Substring(0, 6000) + "`n[...truncated...]" } else { $content }
    $fileName = Split-Path $Path -Leaf
    Write-Host "[Origin] Analyzing $fileName..." -ForegroundColor Cyan
    origin "Analyze this file ($fileName):`n`n$truncated"
}

function origin-clipboard {
    [CmdletBinding()]
    param()
    $text = Get-Clipboard -ErrorAction SilentlyContinue
    if (-not $text) {
        Write-Host "[Origin] Clipboard is empty." -ForegroundColor Yellow
        return
    }
    $preview = if ($text.Length -gt 80) { $text.Substring(0, 80) + "..." } else { $text }
    Write-Host "[Origin] Clipboard: " -ForegroundColor Cyan -NoNewline
    Write-Host $preview -ForegroundColor DarkGray
    origin "Analyze this clipboard content and give a useful response:`n`n$text"
}

function origin-status {
    [CmdletBinding()]
    param()
    try {
        $health = Invoke-RestMethod -Uri "$script:Origin_API/health" -TimeoutSec 5
        Write-Host ""
        Write-Host "  Origin  Status Report" -ForegroundColor Cyan
        Write-Host "  $('=' * 32)" -ForegroundColor DarkCyan
        Write-Host "  Backend:      " -NoNewline -ForegroundColor Gray
        if ($health.status -eq "ok") { Write-Host "ONLINE" -ForegroundColor Green } else { Write-Host $health.status -ForegroundColor Red }
        if ($health.skills) {
            Write-Host "  Skills:       $($health.skills.registered) registered" -ForegroundColor Gray
        }
        if ($health.llm) {
            Write-Host "  LLM:          $($health.llm.providers_available -join ', ')" -ForegroundColor Gray
        }
        if ($health.subsystems) {
            $running = ($health.subsystems.PSObject.Properties | Where-Object { $_.Value -eq "running" -or $_.Value -eq "active" }).Count
            $total = $health.subsystems.PSObject.Properties.Count
            Write-Host "  Subsystems:   $running/$total active" -ForegroundColor Gray
        }
        if ($health.memory) {
            Write-Host "  Memory:       $($health.memory.memories) items" -ForegroundColor Gray
        }
        Write-Host ""
    } catch {
        Write-Host ""
        Write-Host "  Origin  OFFLINE" -ForegroundColor Red
        Write-Host "  Backend not reachable at $script:Origin_API" -ForegroundColor DarkGray
        Write-Host ""
    }
}

function origin-open {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)]
        [ValidateSet("home", "chat", "systems", "camera", "music", "settings")]
        [string]$Section = "home"
    )
    Start-Process "origin://$Section" -ErrorAction SilentlyContinue
}

function Install-OriginProfile {
    [CmdletBinding()]
    param()
    $profilePath = $PROFILE
    $sourcePath = if ($PSCommandPath) { $PSCommandPath } else { Join-Path (Get-Location) "scripts\origin.ps1" }
    $sourceLine = ". `"$sourcePath`""
    if (-not (Test-Path $profilePath)) {
        New-Item -ItemType File -Path $profilePath -Force | Out-Null
        Write-Host "[Origin] Created PowerShell profile at $profilePath" -ForegroundColor Green
    }
    $existing = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue
    if ($existing -and $existing.Contains("origin.ps1")) {
        Write-Host "[Origin] Already installed in profile." -ForegroundColor Yellow
        return
    }
    Add-Content $profilePath "`n# Origin CLI integration`n$sourceLine"
    Write-Host "[Origin] Installed to PowerShell profile." -ForegroundColor Green
    Write-Host "         Restart your terminal or run: $sourceLine" -ForegroundColor DarkGray
}

Write-Host "[Origin] " -ForegroundColor Cyan -NoNewline
Write-Host "CLI loaded. Commands: origin, origin-analyze, origin-clipboard, origin-status, origin-open" -ForegroundColor DarkGray
