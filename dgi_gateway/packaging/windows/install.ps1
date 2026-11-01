# Installation de la passerelle DGI sur un poste Windows 10/11 du site.
# PowerShell **administrateur**, depuis le dossier dgi_gateway :
#   Set-ExecutionPolicy -Scope Process Bypass; .\packaging\windows\install.ps1
#
# Prerequis : Python 3.10+ installe depuis python.org avec "Install for all users" coche
# (une installation dans le profil d'un utilisateur n'est pas accessible au compte SYSTEM).
#
# ETAT : script fourni avec l'increment 1, NON TESTE sur un poste Windows reel.
# Le demarrage se fait par une tache planifiee SYSTEM (au demarrage, relance automatique) ;
# un vrai service Windows signe est prevu a l'increment 2.
#Requires -RunAsAdministrator
param(
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"

$Src    = (Resolve-Path "$PSScriptRoot\..\..").Path
$Prefix = Join-Path $env:ProgramFiles "DGI Gateway"
$Data   = Join-Path $env:ProgramData "DGI Gateway"
$Task   = "DGI Gateway"

if (-not $Python) {
    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Python = (& py.exe -3 -c "import sys; print(sys.executable)").Trim() }
    else { $Python = (Get-Command python.exe -ErrorAction Stop).Source }
}
& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 ou plus recent requis ($Python)." }
if ($Python -like "$env:LOCALAPPDATA*") {
    throw "Python est installe dans le profil utilisateur ($Python) : reinstallez-le 'for all users'."
}

New-Item -ItemType Directory -Force -Path $Prefix, $Data | Out-Null
# Dossier de donnees (cle chiffree DPAPI, journal, configuration) : SYSTEM et Administrateurs seulement.
# La cle est chiffree avec DPAPI en portee machine : c'est cette ACL qui empeche un autre compte du
# poste de la lire.
& icacls.exe $Data /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

& $Python -m venv (Join-Path $Prefix "venv")
$VPy = Join-Path $Prefix "venv\Scripts\python.exe"
& $VPy -m pip install --quiet --upgrade pip
& $VPy -m pip install --quiet --upgrade $Src
Copy-Item (Join-Path $Src "README.md") (Join-Path $Prefix "README.md") -Force
$Exe = Join-Path $Prefix "venv\Scripts\dgi-gateway.exe"

& $Exe selftest | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Autotest sur simulateur en echec." }
Write-Host "Programme installe :" (& $Exe --version) "- autotest OK"

$Log = Join-Path $Data "gateway.log"
$action    = New-ScheduledTaskAction -Execute $Exe -Argument "--log-file `"$Log`" run"
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null

Write-Host @"

Etapes suivantes (PowerShell administrateur, voir $Prefix\README.md) :
  1. & '$Exe' init --site https://VOTRE-SITE.frappe.cloud --gateway-id GW-XXXX-01 --port COM3
  2. & '$Exe' discover
  3. & '$Exe' enroll --code CODE-AFFICHE-DANS-ERPNEXT
     -> comparer l'empreinte affichee avec celle d'ERPNext, puis approuver dans ERPNext
  4. Start-ScheduledTask -TaskName '$Task'    (journal : $Log)
"@
