<#
  Valide le mapping du plugin sur les .sdf reels d'Analys'immo (dossier de demo DURAND).
  - Extrait chaque requete `const string sql = """ ... """;` du service de reconstruction.
  - Adapte la syntaxe T-SQL -> SQL CE (dbo. retire, TOP n -> TOP(n)).
  - Resout les ids du dossier DURAND puis rejoue chaque requete.
  - Reporte: base ciblee, lignes retournees, ou erreur.
#>
$ErrorActionPreference = 'Stop'
[Reflection.Assembly]::LoadFrom("C:\Program Files\Microsoft SQL Server Compact Edition\v3.5\Desktop\System.Data.SqlServerCe.dll") | Out-Null

$dir   = "C:\ADN_Evaluation\Synchro\SDLDEMO"
$db = @{
  diag = "$dir\ADN_DIAG.sdf"
  dpe  = "$dir\ADN_DIAG_DPE2012.sdf"
  rg   = "$dir\ADN_RG.sdf"
}
$service = "C:\Users\gabga\Downloads\optimo-plugin\src\Optimo.Infrastructure\Discovery\LocalDbDpeXmlReconstructionService.cs"

function Adapt([string]$sql) {
  $s = $sql -replace 'dbo\.', ''
  $s = [regex]::Replace($s, '\bTOP\s+(\d+)\b', 'TOP($1)')
  return $s
}

function Open-Db([string]$path) {
  $cn = New-Object System.Data.SqlServerCe.SqlCeConnection "Data Source=$path;File Mode=Read Only;Temp Path=$env:TEMP;"
  $cn.Open(); $cn
}

# SQL CE lie par POSITION : on remplace chaque @param par '?' et on ajoute un parametre par occurrence.
function Run-Sql($cn, [string]$sql, [hashtable]$vals) {
  $cmd = $cn.CreateCommand()
  $order = @()
  foreach ($m in [regex]::Matches($sql, '@\w+')) { $order += $m.Value }
  $cmd.CommandText = [regex]::Replace($sql, '@\w+', '?')
  for ($i = 0; $i -lt $order.Count; $i++) {
    $name = $order[$i]
    $v = if ($vals.ContainsKey($name)) { $vals[$name] } else { [DBNull]::Value }
    [void]$cmd.Parameters.AddWithValue("@p$i", $v)   # nom unique; SQL CE lie par position
  }
  $rd = $cmd.ExecuteReader()
  $rows = @()
  while ($rd.Read()) { $o = [ordered]@{}; for ($i=0; $i -lt $rd.FieldCount; $i++) { $o[$rd.GetName($i)] = $rd.GetValue($i) }; $rows += [pscustomobject]$o }
  $rd.Close()
  ,$rows
}

function Pick-Db([string]$sql) {
  if ($sql -match '\b(Employe|Utilisateur|Societe|Interlocuteur)\b' -and $sql -notmatch 'DossierInterlocuteur') { return 'rg' }
  if ($sql -match 'XDPE') { return 'dpe' }
  return 'diag'
}

# ---- Extraction des requetes + nom de methode associe ----
$text = Get-Content $service -Raw
$methodMatches = [regex]::Matches($text, '(Load\w+Async)\s*\(')
$sqlMatches = [regex]::Matches($text, '(?s)const string sql = """\r?\n(.*?)\r?\n\s*""";')
"Requetes extraites : $($sqlMatches.Count)"
""

# Index char -> nom methode (derniere methode declaree avant la requete)
function Method-Before([int]$idx) {
  $name = '?'
  foreach ($mm in $methodMatches) { if ($mm.Index -lt $idx) { $name = $mm.Groups[1].Value } else { break } }
  $name
}

# ---- Phase 1 : resoudre les ids du dossier DURAND ----
$vals = @{ '@Reference' = '2026 - DURAND 17.10.25'; '@DossierId' = -1; '@MissionId' = -4; '@TargetDate' = [datetime]'2026-01-08' }

$cnDiag = Open-Db $db.diag
$dossierSql = Adapt ($sqlMatches[0].Groups[1].Value)   # LoadDossierSnapshot est la 1ere
$dossier = Run-Sql $cnDiag $dossierSql $vals
if ($dossier.Count -gt 0) {
  $vals['@EmployeeId'] = $dossier[0].idEmploye
  "Dossier resolu : idDossier=$($dossier[0].idDossier) idMission=$($dossier[0].idMission) idEmploye=$($dossier[0].idEmploye) guid=$($dossier[0].guidSdl)"
} else { "ATTENTION: dossier DURAND introuvable via la requete du service" }
$cnDiag.Close()

# lot principal
$cnDpe = Open-Db $db.dpe
$lotSql = Adapt ($sqlMatches | Where-Object { $_.Groups[1].Value -match 'XDPEsaisieLot' -and $_.Groups[1].Value -match 'Etat actuel du logement' } | Select-Object -First 1).Groups[1].Value
$lot = Run-Sql $cnDpe $lotSql $vals
if ($lot.Count -gt 0) { $vals['@CurrentLotId'] = $lot[0].idSaisieLot; "Lot principal : idSaisieLot=$($lot[0].idSaisieLot)" } else { "ATTENTION: lot principal introuvable"; $vals['@CurrentLotId'] = 0 }
$cnDpe.Close()
""
"Parametres resolus : " + (($vals.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join '  ')
"".PadRight(90,'=')

# ---- Phase 2 : rejouer toutes les requetes ----
$conns = @{ diag = (Open-Db $db.diag); dpe = (Open-Db $db.dpe); rg = (Open-Db $db.rg) }
$report = @()
foreach ($m in $sqlMatches) {
  $name = Method-Before $m.Index
  $raw  = $m.Groups[1].Value
  $sql  = Adapt $raw
  $dbk  = Pick-Db $raw
  $status = ''; $n = $null
  try { $r = Run-Sql $conns[$dbk] $sql $vals; $n = $r.Count; $status = 'OK' }
  catch { $status = 'ERREUR: ' + ($_.Exception.Message -replace '\s+',' ') }
  $report += [pscustomobject]@{ Methode = $name; Base = $dbk; Lignes = $n; Statut = $status }
}
$conns.Values | ForEach-Object { $_.Close() }

$report | Format-Table -AutoSize -Wrap
""
"RESUME : " + (($report | Where-Object Statut -eq 'OK').Count) + " OK / " + (($report | Where-Object { $_.Statut -ne 'OK' }).Count) + " en erreur sur $($report.Count)"
