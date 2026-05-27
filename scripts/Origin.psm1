
# Origin — PowerShell Module
# Proporciona cmdlets nativos para interactuar con Origin desde PowerShell.
# Instalar desde la raiz portable: Import-Module ".\scripts\Origin.psm1"
#
# Cmdlets disponibles:
#   Get-OriginStatus     → Estado del servicio
#   Send-OriginMessage   → Envía mensaje a Origin
#   Get-OriginMemory     → Busca en la memoria de Origin
#   Get-OriginSkills     → Lista skills disponibles
#   Start-Origin         → Inicia Origin en segundo plano
#   Stop-Origin          → Detiene Origin
#   Register-Origin      → Instala Origin en el sistema
#   Invoke-OriginSkill   → Ejecuta una skill directamente

$ORIGIN_ROOT = if ($env:ORIGIN_ROOT) { $env:ORIGIN_ROOT } elseif ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
$ORIGIN_API = "http://127.0.0.1:9001"
$ORIGIN_TIMEOUT = 10

function _Invoke-OriginApi {
    param([string]$Method, [string]$Endpoint, [object]$Body, [int]$Timeout = $ORIGIN_TIMEOUT)
    $url = "$ORIGIN_API$Endpoint"
    $params = @{Uri = $url; Method = $Method; ContentType = "application/json"; UseBasicParsing = $true; TimeoutSec = $Timeout}
    if ($Body) { $params.Body = ($Body | ConvertTo-Json -Compress) }
    try { return Invoke-RestMethod @params -ErrorAction Stop }
    catch { Write-Warning "Origin API error ($Method $Endpoint): $_"; return $null }
}

function Get-OriginStatus {
    try {
        $h = Invoke-RestMethod "$ORIGIN_API/health" -UseBasicParsing -TimeoutSec 5
        $s = Invoke-RestMethod "$ORIGIN_API/core/status" -UseBasicParsing -TimeoutSec 5
        $v = Invoke-RestMethod "$ORIGIN_API/core/version" -UseBasicParsing -TimeoutSec 5
        [PSCustomObject]@{Status = "Running"; Version = $v.version; Skills = $s.skills; Memories = $s.memories; DB = if($s.db_available){"Active"}else{"Fallback"}; LLM = ($s.llm_router -join ", ")}
    } catch { [PSCustomObject]@{Status = "Stopped"; Version = "N/A"; Skills = "N/A"; Memories = "N/A"; DB = "N/A"; LLM = "N/A"} }
}

function Send-OriginMessage {
    param([Parameter(Mandatory)][string]$Message, [switch]$Raw)
    $r = _Invoke-OriginApi -Method POST -Endpoint "/mind/think" -Body @{input = $Message} -Timeout 120
    if (-not $r) { return "Origin no disponible" }
    return if ($Raw) { $r } else { $r.final_answer }
}

function Get-OriginMemory {
    param([Parameter(Mandatory)][string]$Query, [int]$MaxResults = 5)
    $r = _Invoke-OriginApi "/mind/memory/search?query=$Query&top_k=$MaxResults"
    if (-not $r -or -not $r.results) { return @() }
    return $r.results | ForEach-Object { [PSCustomObject]@{Content = $_.content; Type = $_.type; Relevance = [math]::Round($_.relevance, 3)} }
}

function Get-OriginSkills {
    $r = _Invoke-OriginApi "/dashboard/skills"
    if (-not $r -or -not $r.skills) { return @() }
    return $r.skills | ForEach-Object { [PSCustomObject]@{Name = $_.name; Description = $_.description; Executions = $_.execution_count} }
}

function Start-Origin {
    param([switch]$NoBrowser)
    if ((Get-OriginStatus).Status -eq "Running") { Write-Host "Origin ya en ejecucion" -ForegroundColor Yellow; return }
    $pyw = Join-Path $ORIGIN_ROOT "venv\Scripts\pythonw.exe"
    $launcher = Join-Path $ORIGIN_ROOT "origin_launcher.pyw"
    if (-not (Test-Path $pyw)) { Write-Error "Origin no encontrado en $ORIGIN_ROOT"; return }
    Write-Host "Iniciando Origin..." -ForegroundColor Cyan
    Start-Process -FilePath $pyw -ArgumentList "`"$launcher`"" -WindowStyle Hidden
    Start-Sleep -Seconds 3
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:9001" }
    Write-Host "Origin activo — http://127.0.0.1:9001" -ForegroundColor Green
}

function Stop-Origin {
    $sb = Join-Path $ORIGIN_ROOT "stop.bat"
    if (Test-Path $sb) { & $sb }
    else { Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "api.main" } | Stop-Process -Force }
    Write-Host "Origin detenido" -ForegroundColor Yellow
}

function Register-Origin {
    $py = Join-Path $ORIGIN_ROOT "venv\Scripts\python.exe"
    $inst = Join-Path $ORIGIN_ROOT "scripts\install_origin.py"
    if (-not (Test-Path $inst)) { Write-Error "Instalador no encontrado"; return }
    & $py $inst
}

function Invoke-OriginSkill {
    param([Parameter(Mandatory)][string]$SkillName, [Parameter(Mandatory)][hashtable]$Inputs)
    return _Invoke-OriginApi -Method POST -Endpoint "/mind/think" -Body @{input = "usando skill $SkillName con $($Inputs | ConvertTo-Json -Compress)"} -Timeout 60
}

Export-ModuleMember -Function Get-OriginStatus, Send-OriginMessage, Get-OriginMemory, Get-OriginSkills, Start-Origin, Stop-Origin, Register-Origin, Invoke-OriginSkill

