<#
  Prépare une nouvelle version de la Passerelle en une commande.

  Usage :
      .\tools\release.ps1 1.1.7

  Ce script :
    1. met à jour version.py
    2. construit l'exe (venv propre .venv-build)
    3. calcule le sha256 et écrit latest.json (version + url + sha256)
    4. affiche les fichiers à publier et la marche à suivre GitHub

  Il ne publie PAS sur GitHub (pas d'accès) : tu fais l'upload toi-même.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Error "Version invalide : '$Version'. Format attendu : x.y.z (ex. 1.1.7)"
}

$py = Join-Path $root ".venv-build\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "venv de build introuvable ($py). Crée-le : python -m venv .venv-build puis pip install -r requirements.txt"
}

# UTF-8 SANS BOM : un BOM casserait le parsing de version.py / JSON de latest.json.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

Write-Host "[1/4] version.py -> $Version"
$versionPy = @"
"""Version de la Passerelle Optimmo. Source unique de vérité pour l'updater."""
__version__ = "$Version"
"@
[System.IO.File]::WriteAllText((Join-Path $root "version.py"), $versionPy, $utf8NoBom)

# Libère l'exe s'il est verrouillé par une instance en cours.
Get-Process PasserelleOptimmo -ErrorAction SilentlyContinue | Stop-Process -Force
$dist = Join-Path $root "dist\PasserelleOptimmo.exe"
if (Test-Path $dist) { Remove-Item $dist -Force }

Write-Host "[2/4] build de l'exe (PyInstaller)…"
& $py -m PyInstaller --onefile --windowed --noconfirm --clean `
    --name PasserelleOptimmo --icon icon_app.ico --hidden-import pystray._win32 `
    --exclude-module numpy --exclude-module scipy --exclude-module pandas `
    --exclude-module matplotlib --exclude-module PyQt5 --exclude-module IPython `
    --add-data "fonts;fonts" --add-data "icon_tray.png;." `
    --add-data "icon_tray_alert.png;." --add-data "icon_header.png;." `
    --add-data "icon_app.ico;." main.py | Out-Null

if (-not (Test-Path $dist)) { Write-Error "Build échoué : $dist absent." }

Write-Host "[3/4] calcul du sha256 + latest.json"
$hash = (Get-FileHash -Algorithm SHA256 $dist).Hash.ToLower()
$url = "https://github.com/Optimmo-Energies/passerelle/releases/latest/download/PasserelleOptimmo.exe"
$json = @{
    version = $Version
    url     = $url
    sha256  = $hash
    notes   = "Version $Version"
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $root "latest.json"), $json, $utf8NoBom)

$size = [math]::Round((Get-Item $dist).Length / 1MB, 1)
Write-Host ""
Write-Host "[4/4] PRÊT À PUBLIER ($size Mo, sha256=$($hash.Substring(0,12))…)" -ForegroundColor Green
Write-Host ""
Write-Host "Sur https://github.com/Optimmo-Energies/passerelle/releases :"
Write-Host "  1. « Draft a new release »  (NOUVELLE release, pas éditer l'ancienne)"
Write-Host "  2. Tag : v$Version"
Write-Host "  3. Joindre les 2 fichiers :"
Write-Host "       $dist"
Write-Host "       $(Join-Path $root 'latest.json')"
Write-Host "  4. « Set as latest release » coché, pre-release décoché"
Write-Host "  5. Publish release"
