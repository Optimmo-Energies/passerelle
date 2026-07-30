# Construire et distribuer la Passerelle Optimmo

## 1. Pré-requis (poste de build, une seule fois)

```powershell
cd C:\Users\gabga\optimmo_bridge
python -m pip install -r requirements.txt
```

`requirements.txt` inclut `pyinstaller`. Python 3.12 recommandé (testé).

> La lecture Analys'immo (`analysimo.py`) nécessite `pythonnet` + SQL Server Compact 3.5,
> **uniquement à l'exécution sur un poste qui en a besoin**. Ce n'est pas requis pour
> construire l'exe : `analysimo` est importé paresseusement et n'est pas embarqué.

## 2. Construire l'exécutable

```powershell
.\build.bat
```

Produit **`dist\PasserelleOptimmo.exe`** : un seul fichier (`--onefile`), sans console
(`--windowed`), avec l'icône de l'app, les polices Inter et les icônes de la barre des
tâches embarquées. C'est ce `.exe` qu'on envoie aux collègues pour tester.

> ⚠️ **Builder dans un venv propre, pas dans Anaconda.** Depuis l'environnement Anaconda
> de base, PyInstaller embarque numpy/MKL → exe de ~300 Mo. Dans un venv minimal
> (deps de `requirements.txt` seulement), l'exe tombe à ~76 Mo :
>
> ```powershell
> python -m venv .venv-build
> .venv-build\Scripts\python -m pip install -r requirements.txt
> .venv-build\Scripts\python -m PyInstaller --onefile --windowed --noconfirm --clean ^
>     --name PasserelleOptimmo --icon icon_app.ico --hidden-import pystray._win32 ^
>     --add-data "fonts;fonts" --add-data "icon_tray.png;." ^
>     --add-data "icon_tray_alert.png;." --add-data "icon_header.png;." ^
>     --add-data "icon_app.ico;." main.py
> ```
>
> `build.bat` exclut déjà numpy/scipy/pandas/matplotlib par sécurité si vous buildez
> quand même depuis Anaconda.

## 3. Distribution aux collègues (phase de test)

1. Envoyer `dist\PasserelleOptimmo.exe` (clé USB, lien Drive, etc.).
2. Au premier lancement, Windows SmartScreen peut afficher un avertissement
   (exe non signé) → « Informations complémentaires » → « Exécuter quand même ».
3. L'app apparaît dans la **barre des tâches** (system tray, à côté de l'horloge).
4. Au premier lancement, elle s'inscrit automatiquement au **démarrage de Windows**
   (désactivable via le menu de l'icône → « Lancer au démarrage de Windows »).

> Chaque poste doit avoir LICIEL installé avec ses dossiers dans `C:\LICIEL_Diagnostics`
> (sinon ajuster `liciel_root` dans `%USERPROFILE%\.optimo_bridge\config.json`).

> Pour supprimer SmartScreen à terme : signer l'exe avec un certificat de signature de code
> (voir §7).

## 7. Signature de code (supprimer l'avertissement SmartScreen)

### Pourquoi
- **SmartScreen** (« éditeur inconnu ») disparaît quand l'exe est **signé**. Avec un
  certificat **EV**, la réputation est immédiate → **zéro avertissement dès le 1er
  téléchargement**. Avec un **OV**, il faut attendre que la réputation se construise.
- **Antivirus** : les faux positifs viennent surtout d'UPX et du `--onefile`. Le build
  utilise désormais `--noupx` (build.bat) et `upx=False` (.spec) pour les limiter.

### Certificat recommandé
**Certum EV « Code Signing in the cloud » (SimplySign)** — le moins cher tout compris
(~250–300 €/an), basé en UE (facturation/support FR), **sans token physique** donc
signable directement depuis `build.bat`. Alternative éprouvée : **Sectigo EV** via
revendeur (SignMyCode / CheapSSLWeb, ~280 $/an), en token USB ou cloud SimplySign.

> ⚠️ Depuis le **23 févr. 2026**, un certificat de signature de code dure au max ~15 mois
> (CA/Browser Forum). Ne pas payer « 3 ans » en croyant à un cert unique de 3 ans.

### Checklist d'achat / validation EV (Certum)
1. **Documents société** à préparer :
   - Extrait **KBIS** d'Optimmo Énergies (< 3 mois).
   - **Numéro de TVA intracommunautaire** + SIREN/SIRET.
   - Adresse du siège telle qu'elle figure au KBIS.
2. **Numéro de téléphone de l'entreprise vérifiable** dans un annuaire public
   (Pages Jaunes / annuaire officiel) : l'autorité **appelle ce numéro** pour valider.
   Vérifier que le numéro est bien référencé AVANT de commander (sinon ça bloque).
3. **Email professionnel** au domaine de la société (ex. celui d'Optimmo).
4. Commander le certificat **au nom exact** de la personne morale « Optimmo Énergies »
   (raison sociale identique au KBIS, sinon rejet).
5. Choisir l'option **cloud / SimplySign** (pas de token à recevoir).
6. Passer la **validation** : dépôt des docs → vérification entreprise → appel
   téléphonique → délivrance (compter quelques jours ouvrés).
7. Installer **Certum SimplySign Desktop** (ou l'app mobile pour le code 2FA) sur le
   poste de build : il expose le certificat à `signtool.exe`.

### Activer la signature dans le build
La signature est pilotée par la variable `SIGN_METHOD` dans `build.bat` :

| `SIGN_METHOD` | Usage |
|---|---|
| `none` (défaut) | Pas de signature — phase de test actuelle |
| `signtool` | Certificat sur **token USB** *ou* **Certum SimplySign Desktop** |
| `esigner`  | **SSL.com eSigner** en ligne (CodeSignTool) |

Une fois le certificat installé (Certum SimplySign) :

```powershell
$env:SIGN_METHOD = "signtool"
.\build.bat
```

`build.bat` signe puis **vérifie** l'exe (`signtool verify /pa`). Si `signtool`
manque, installer le **Windows SDK** (composant « Windows SDK Signing Tools »)
ou lancer depuis un *Developer Command Prompt*.

## 4. Mises à jour automatiques (via GitHub Releases)

L'app vérifie au démarrage le manifeste défini par `update_url` (défaut :
`https://github.com/Optimmo-Energies/passerelle/releases/latest/download/latest.json`).
L'astuce `/releases/latest/download/<asset>` pointe **toujours** vers le dernier
release publié → l'URL ne change jamais d'une version à l'autre.

Format de `latest.json` (déjà présent à la racine du repo comme modèle) :

```json
{
  "version": "1.1.0",
  "url": "https://github.com/Optimmo-Energies/passerelle/releases/latest/download/PasserelleOptimmo.exe",
  "notes": "Nouveautés de cette version"
}
```

Procédure de release :
1. Incrémenter `__version__` dans `version.py` **et** la `version` dans `latest.json`.
2. `.\build.bat` (dans le venv propre) → `dist\PasserelleOptimmo.exe`.
3. Créer un nouveau **GitHub Release** et y joindre **2 assets** :
   `PasserelleOptimmo.exe` et `latest.json`.

Au prochain démarrage de chaque poste, si la version distante est plus récente :
l'exe est téléchargé en arrière-plan, et **installé automatiquement à la fermeture**
de l'app (un petit script remplace l'exe verrouillé puis relance la Passerelle).
En mode développement (script non figé), l'app se contente de **notifier** la
disponibilité d'une nouvelle version.

> ⚠️ **Bootstrap** : l'auto-update ne fonctionne que si l'exe **déjà installé**
> connaît la bonne `update_url`. Les exes construits avant ce changement pointaient
> vers un placeholder → il faut installer **une fois manuellement** une version qui
> embarque l'URL GitHub ; ensuite les MAJ sont automatiques.

## 5. Réglages (config.json)

`%USERPROFILE%\.optimo_bridge\config.json` (créé au premier `config.save`) :

| Clé | Rôle | Défaut |
|---|---|---|
| `liciel_root` | Racine des dossiers LICIEL | `C:\LICIEL_Diagnostics` |
| `demo_mode` | `true` = sauvegarde locale, `false` = envoi API réel | `true` |
| `dossier_list_limit` | Nb de dossiers récents listés dans la sélection | `30` |
| `start_at_boot` | Lancement auto à l'ouverture de session | `true` |
| `auto_update` | Recherche de MAJ au démarrage | `true` |
| `update_url` | URL du manifeste de version | API Optimmo |

## 6. Vérifier sans construire l'exe (dev)

```powershell
python main.py
```
