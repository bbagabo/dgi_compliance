<#
  Test de communication avec un MCF sur un port serie (dgi_compliance v5.3.1, Helosystems).
  LECTURE SEULE, SANS EFFET FISCAL : uniquement C1h (etat), 2Bh I0..I4 (contribuable) et C2h (etat de la
  connexion DGI, sans RESTART). Aucune facture n'est ouverte, aucune commande fiscale n'est envoyee.

  Utilisation (PowerShell Windows) :
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Test_MCF_port.ps1 -Port COM8
  Si le MCF ne repond pas, reessayer avec -NoDtr (lignes DTR/RTS non activees).
  Le port doit etre libre (fermer la page Poste MCF et tout autre logiciel de caisse avant).
  Un journal Test_MCF_<port>_<date>.txt est ecrit a cote du script.
#>
param([string]$Port = "COM8", [int]$Baud = 115200, [double]$Timeout = 0.6, [switch]$NoDtr)
$ErrorActionPreference = "Stop"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$safe = ($Port -replace '[^A-Za-z0-9]', '_')
$log = Join-Path $here ("Test_MCF_{0}_{1}.txt" -f $safe, (Get-Date -Format "yyyyMMdd_HHmmss"))
function Out-Log([string]$s) { Write-Host $s; Add-Content -Path $log -Value $s -Encoding UTF8 }
function Hex([byte[]]$b) { if (-not $b) { return "" }; ($b | ForEach-Object { $_.ToString("x2") }) -join " " }

function Get-Bcc([byte[]]$core) {
    $sum = 0
    foreach ($b in $core) { $sum = ($sum + $b) -band 0xFFFF }
    [byte[]]@(foreach ($sh in 12, 8, 4, 0) { 0x30 + (($sum -shr $sh) -band 0xF) })
}
function New-Frame([int]$seq, [int]$cmd, [byte[]]$data) {
    $core = [System.Collections.Generic.List[byte]]::new()
    $core.Add([byte](0x20 + 4 + $data.Length)); $core.Add([byte]$seq); $core.Add([byte]$cmd)
    if ($data.Length) { $core.AddRange($data) }
    $core.Add(0x05)
    $c = $core.ToArray()
    $f = [System.Collections.Generic.List[byte]]::new()
    $f.Add(0x01); $f.AddRange($c); $f.AddRange([byte[]](Get-Bcc $c)); $f.Add(0x03)
    , $f.ToArray()
}
function Read-Frame($sp, [double]$sec) {
    $deadline = [DateTime]::UtcNow.AddSeconds($sec)
    $synLimit = [DateTime]::UtcNow.AddSeconds(15)
    $buf = [System.Collections.Generic.List[byte]]::new()
    while ($true) {
        if ([DateTime]::UtcNow -gt $deadline) { return $null }
        if ($sp.BytesToRead -le 0) { Start-Sleep -Milliseconds 5; continue }
        $b = [byte]$sp.ReadByte()
        if ($buf.Count -eq 0) {
            if ($b -eq 0x15) { return "NAK" }
            if ($b -eq 0x16) {                                   # SYN : le MCF travaille, on attend encore
                $ext = [DateTime]::UtcNow.AddMilliseconds(500)
                if ($ext -gt $deadline) { $deadline = if ($ext -lt $synLimit) { $ext } else { $synLimit } }
                continue
            }
            if ($b -ne 0x01) { continue }
        }
        $buf.Add($b)
        if ($b -eq 0x03 -and $buf.Count -ge 14) { return , $buf.ToArray() }
    }
}
function Read-Response([byte[]]$f) {
    $core = [byte[]]$f[1..($f.Length - 6)]
    $rx = [byte[]]$f[($f.Length - 5)..($f.Length - 2)]
    $brk = $core.Length - 1 - 6 - 1
    $data = if ($brk -gt 3) { [byte[]]$core[3..($brk - 1)] } else { [byte[]]@() }
    [pscustomobject]@{
        Seq = $core[1]; Cmd = $core[2]; Data = $data; Sta = [byte[]]$core[($brk + 1)..($brk + 6)]
        Text = [Text.Encoding]::UTF8.GetString($data)
        BccOk = ((Hex $rx) -eq (Hex (Get-Bcc $core))); BrkOk = ($core[$brk] -eq 0x04); AmbOk = ($core[-1] -eq 0x05)
    }
}
$script:seq = 0x1F
function Send-Command($sp, [int]$cmd, [string]$dataText, [double]$sec, [string]$label) {
    $script:seq = if ($script:seq -ge 0xFF -or $script:seq -lt 0x20) { 0x20 } else { $script:seq + 1 }
    $frame = New-Frame $script:seq $cmd ([Text.Encoding]::UTF8.GetBytes($dataText))
    for ($try = 1; $try -le 3; $try++) {
        $sp.DiscardInBuffer()
        $sp.Write($frame, 0, $frame.Length)
        Out-Log ("  -> {0} (essai {1}) : {2}" -f $label, $try, (Hex $frame))
        $t0 = [DateTime]::UtcNow
        $reply = Read-Frame $sp $sec
        $ms = [int]([DateTime]::UtcNow - $t0).TotalMilliseconds
        if ($null -eq $reply) { Out-Log ("  <- pas de reponse en {0} ms" -f $ms); continue }
        if ($reply -is [string]) { Out-Log "  <- NAK (le MCF demande de renvoyer)"; continue }
        Out-Log ("  <- {0} octets en {1} ms : {2}" -f $reply.Length, $ms, (Hex $reply))
        $r = Read-Response $reply
        if (-not ($r.BccOk -and $r.BrkOk -and $r.AmbOk)) { Out-Log "  !! trame invalide (BCC / BRK / AMB)"; continue }
        if ($r.Seq -ne $script:seq -or $r.Cmd -ne $cmd) { Out-Log "  !! reponse d'un autre echange"; continue }
        return $r
    }
    return $null
}
function Unescape([string]$s) { $s -replace '\^x2c;', ',' -replace '\^amp;', '&' -replace '\^lt;', '<' -replace '\^gt;', '>' -replace '\^xa;', "`r" -replace '\^xd;', "`n" }
function Get-Flags([byte[]]$sta) {
    $bits = @{ "0,2" = "date_heure_non_reglee"; "0,1" = "commande_non_valide"; "0,0" = "erreur_syntaxe";
        "1,4" = "rapport_quotidien_requis"; "1,2" = "memoire_reinitialisee"; "1,1" = "commande_interdite_mode_actuel";
        "1,0" = "depassement_sommes_rapport"; "2,3" = "facture_ouverte"; "4,4" = "memoire_pleine";
        "4,3" = "memoire_moins_50"; "4,2" = "enregistrement_invalide"; "4,0" = "erreur_ecriture_memoire" }
    $out = foreach ($k in $bits.Keys) { $i, $bit = $k -split ','; if ($sta.Length -gt [int]$i -and (($sta[[int]$i] -shr [int]$bit) -band 1)) { $bits[$k] } }
    if ($out) { ($out | Sort-Object) -join ", " } else { "aucun" }
}

Out-Log ("=== Test de communication MCF - {0} - port {1} a {2} bit/s, 8N1 ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Port, $Baud)
Out-Log ("Ports serie vus par Windows : {0}" -f (([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object) -join ", "))
try {
    Get-CimInstance Win32_PnPEntity -ErrorAction Stop | Where-Object { $_.Name -match '\(COM\d+\)' } | ForEach-Object {
        Out-Log ("  {0}   [{1}]" -f $_.Name, $_.PNPDeviceID)
    }
} catch { Out-Log "  (description des ports indisponible)" }

$sp = [System.IO.Ports.SerialPort]::new($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$sp.Handshake = [System.IO.Ports.Handshake]::None
$sp.DtrEnable = -not $NoDtr; $sp.RtsEnable = -not $NoDtr      # comme pyserial / Chrome ; -NoDtr si le MCF ne repond pas
$sp.ReadTimeout = 500; $sp.WriteTimeout = 2000
try { $sp.Open() } catch {
    Out-Log ("ECHEC : impossible d'ouvrir {0} : {1}" -f $Port, $_.Exception.Message)
    Out-Log "Le port est peut-etre utilise (page Poste MCF ouverte, logiciel du fabricant) ou le numero COM est faux."
    exit 2
}
$ok = $true
try {
    Start-Sleep -Milliseconds 200
    Out-Log "`n[1] C1h - etat du MCF"
    $r = Send-Command $sp 0xC1 "" $Timeout "C1h"
    if (-not $r) { $ok = $false; Out-Log "ECHEC : le MCF ne repond pas a C1h sur $Port."; throw "stop" }
    $f = @((Unescape $r.Text) -split ',')
    $names = "MID", "NIF", "Date (DT)", "Total factures (TC)", "Factures de vente (FVC)", "Avoirs (FRC)", "Date derniere facture (DFDT)", "Type derniere facture (DFT)", "Signature derniere facture (DFS)", "Numero derniere facture (DFN)", "Montant derniere facture (DMV)"
    for ($i = 0; $i -lt $names.Count; $i++) { Out-Log ("  {0,-34}: {1}" -f $names[$i], $(if ($i -lt $f.Count) { $f[$i] } else { "" })) }
    $rates = if ($f.Count -gt 11) { $f[11..($f.Count - 1)] } else { @() }
    $letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    $rt = for ($j = 0; $j -lt $rates.Count -and $j -lt 26; $j++) { if ($rates[$j] -ne "") { "{0}={1}%" -f $letters[$j], $rates[$j] } }
    Out-Log ("  {0,-34}: {1} groupes : {2}" -f "Groupes de taxation", $rates.Count, ($rt -join "  "))
    $nid = if ($f[0] -match '-') { $f[0].Substring(0, $f[0].LastIndexOf('-')) } else { $f[0] }
    Out-Log ("  {0,-34}: {1}" -f "NID (MID sans le compteur)", $nid)
    Out-Log ("  {0,-34}: {1}  (octets {2})" -f "Etat (STA)", (Get-Flags $r.Sta), (Hex $r.Sta))

    Out-Log "`n[2] 2Bh - informations du contribuable"
    $what = @{ "I0" = "Denomination / nom"; "I1" = "Adresse"; "I2" = "Ville"; "I3" = "Telephone"; "I4" = "Email" }   # protocole v1.0
    foreach ($opt in "I0", "I1", "I2", "I3", "I4") {
        $r2 = Send-Command $sp 0x2B $opt $Timeout "2Bh $opt"
        Out-Log ("  {0} {1,-20}: {2}" -f $opt, ("(" + $what[$opt] + ")"), $(if ($r2) { Unescape $r2.Text } else { "(pas de reponse)" }))
    }

    Out-Log "`n[3] C2h - etat de la connexion du MCF au serveur DGI (sans RESTART)"
    $r3 = Send-Command $sp 0xC2 "" 3.0 "C2h"
    if ($r3) {
        $g = @((Unescape $r3.Text) -split ',')
        $states = @{ "DIS" = "non connecte au reseau"; "CON" = "connecte, aucun envoi en cours"; "TRA" = "envoi de donnees en cours"; "RES" = "redemarrage en cours" }
        $sta = $(if ($g.Count -gt 3) { $g[3] } else { "" })
        Out-Log ("  Transactions envoyees (EC)            : {0}" -f $g[0])
        Out-Log ("  Transactions disponibles dans le MCF (DC) : {0}" -f $(if ($g.Count -gt 1) { $g[1] }))
        Out-Log ("  Derniere connexion reussie a la DGI (DT) : {0}" -f $(if ($g.Count -gt 2) { $g[2] }))
        Out-Log ("  Etat de la connexion (STA)            : {0} ({1})" -f $sta, $(if ($states.ContainsKey($sta)) { $states[$sta] } else { "inconnu" }))
        if ($g.Count -gt 4) { Out-Log ("  Dernier message du MCF (ER, fabricant) : {0}" -f $g[4]) }
    } else { Out-Log "  (pas de reponse a C2h)" }
} catch {
    if ("$_" -ne "stop") { $ok = $false; Out-Log ("ERREUR : {0}" -f $_.Exception.Message) }
} finally {
    $sp.Close()
}
Out-Log ""
if ($ok) { Out-Log "RESULTAT : COMMUNICATION OK avec le MCF sur $Port (lecture seule, aucune facture emise)." }
else { Out-Log "RESULTAT : ECHEC de la communication sur $Port." }
Out-Log "Journal : $log"
