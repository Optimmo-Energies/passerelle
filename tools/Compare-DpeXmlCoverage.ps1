<#
.SYNOPSIS
    Mesure la couverture d'un XML DPE reconstruit par rapport au XML officiel.

.DESCRIPTION
    Enumere tous les chemins XML "feuilles" (noeuds sans enfant element) des deux
    fichiers, en collapsant les collections (siblings de meme nom) en `nom[]`.
    Produit un rapport console + un CSV facon docs/dpe-xml-field-control.csv :
      - chemins presents dans l'officiel ET le reconstruit  => Couvre
      - chemins de l'officiel absents du reconstruit         => Manquant
      - chemins du reconstruit absents de l'officiel         => Extra (a verifier)

.PARAMETER OfficialXml
    Chemin du XML officiel (ex. "0 (DPE 2021).xml").

.PARAMETER ReconstructedXml
    Chemin du XML reconstruit par le plugin (ADN_DPE_Reconstruit_*.xml).

.PARAMETER OutCsv
    Chemin du CSV de sortie. Defaut : .\dpe-coverage-<timestamp>.csv a cote du script.

.EXAMPLE
    .\Compare-DpeXmlCoverage.ps1 `
        -OfficialXml "C:\ADN\Reporting\Diagnostic\Dossier\2026\avril\1ff7c2fa-...\standard\0 (DPE 2021).xml" `
        -ReconstructedXml "$env:USERPROFILE\Desktop\ADN_DPE_Reconstruit_20260623-101500.xml"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OfficialXml,
    [Parameter(Mandatory)] [string] $ReconstructedXml,
    [string] $OutCsv
)

$ErrorActionPreference = 'Stop'

function Get-LeafPaths {
    <#
      Retourne l'ensemble (HashSet) des chemins feuilles d'un document XML.
      Les collections (plusieurs enfants de meme nom sous un meme parent) sont
      notees `nom[]` pour rester stables quel que soit le nombre d'occurrences.
    #>
    param([System.Xml.XmlNode] $Node, [string] $Prefix, [System.Collections.Generic.HashSet[string]] $Acc)

    $childElements = @($Node.ChildNodes | Where-Object { $_.NodeType -eq 'Element' })

    if ($childElements.Count -eq 0) {
        # Noeud feuille : on enregistre son chemin.
        [void]$Acc.Add($Prefix)
        return
    }

    # Compter les occurrences de chaque nom d'enfant pour reperer les collections.
    $countByName = @{}
    foreach ($c in $childElements) {
        if ($countByName.ContainsKey($c.LocalName)) {
            $countByName[$c.LocalName] = $countByName[$c.LocalName] + 1
        } else {
            $countByName[$c.LocalName] = 1
        }
    }

    foreach ($c in $childElements) {
        $isCollectionItem = $countByName[$c.LocalName] -gt 1
        $segment = if ($isCollectionItem) { "$($c.LocalName)[]" } else { $c.LocalName }
        Get-LeafPaths -Node $c -Prefix "$Prefix/$segment" -Acc $Acc
    }
}

function Read-LeafSet {
    param([string] $Path)
    if (-not (Test-Path $Path)) { throw "Fichier introuvable : $Path" }
    [xml]$doc = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $set = [System.Collections.Generic.HashSet[string]]::new()
    Get-LeafPaths -Node $doc.DocumentElement -Prefix "/$($doc.DocumentElement.LocalName)" -Acc $set
    return $set
}

Write-Host "Lecture du XML officiel      : $OfficialXml"
$official = Read-LeafSet -Path $OfficialXml
Write-Host "Lecture du XML reconstruit   : $ReconstructedXml"
$recon = Read-LeafSet -Path $ReconstructedXml

$covered = [System.Collections.Generic.List[string]]::new()
$missing = [System.Collections.Generic.List[string]]::new()
foreach ($p in $official) {
    if ($recon.Contains($p)) { $covered.Add($p) } else { $missing.Add($p) }
}
$extra = [System.Collections.Generic.List[string]]::new()
foreach ($p in $recon) {
    if (-not $official.Contains($p)) { $extra.Add($p) }
}

$total = $official.Count
$pct = if ($total -gt 0) { [math]::Round(100.0 * $covered.Count / $total, 1) } else { 0 }

Write-Host ""
Write-Host "==================== COUVERTURE XML DPE ===================="
Write-Host ("Chemins feuilles officiels : {0}" -f $total)
Write-Host ("Couverts (Couvre)          : {0}  ({1}%)" -f $covered.Count, $pct)
Write-Host ("Manquants                  : {0}" -f $missing.Count)
Write-Host ("En trop (Extra, a verifier): {0}" -f $extra.Count)
Write-Host "============================================================"

if (-not $OutCsv) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutCsv = Join-Path $PSScriptRoot "dpe-coverage-$stamp.csv"
}

$rows = [System.Collections.Generic.List[object]]::new()
foreach ($p in ($official | Sort-Object)) {
    $statut = if ($recon.Contains($p)) { 'Couvre' } else { 'Manquant' }
    $rows.Add([pscustomobject]@{ XmlPath = $p; Statut = $statut })
}
foreach ($p in ($extra | Sort-Object)) {
    $rows.Add([pscustomobject]@{ XmlPath = $p; Statut = 'Extra' })
}
$rows | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "CSV detaille ecrit : $OutCsv"
Write-Host ""
Write-Host "--- Apercu des 25 premiers chemins MANQUANTS ---"
$missing | Sort-Object | Select-Object -First 25 | ForEach-Object { Write-Host "  $_" }
