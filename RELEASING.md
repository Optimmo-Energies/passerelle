# Publier une release de la Passerelle Optimmo

Ce document décrit, étape par étape, comment publier une nouvelle version de la
Passerelle. La distribution repose sur **GitHub Releases** + l'auto-update
intégré ([`updater.py`](updater.py)) : chaque poste lit le manifeste `latest.json`
au démarrage et, si une version plus récente est disponible, télécharge le nouvel
`.exe` et l'installe **à la fermeture** de l'application.

> Pour construire l'exe et le contexte général, voir [BUILD.md](BUILD.md).

## Pré-requis (une seule fois par poste de build)

- **`gh` authentifié** sur le repo `Optimmo-Energies/passerelle`
  (`gh auth status`).
- **Un venv de build propre** `.venv-build` avec les dépendances installées.
  On ne build **pas** depuis Anaconda (sinon numpy/MKL → exe ~300 Mo au lieu de
  ~76 Mo) :

  ```bash
  python -m venv .venv-build
  .venv-build/Scripts/python -m pip install -r requirements.txt
  ```

  À refaire après toute modification de `requirements.txt` (ex. l'ajout de
  `windows-toasts`).

## ⚠️ Deux invariants à ne jamais casser

1. **Le repo doit rester PUBLIC.** L'updater télécharge le manifeste et l'exe
   via des requêtes HTTP **non authentifiées**. Sur un repo privé, GitHub
   renvoie `404` sur les assets → `check_manifest` échoue en silence et
   **l'auto-update ne se déclenche jamais**. Si le repo doit redevenir privé, il
   faut d'abord authentifier les requêtes dans `updater.py`.
2. **Le build doit embarquer `windows-toasts` + `winrt`.** La notification de
   fin d'analyse en dépend (extensions C). Le build ci-dessous inclut
   `--collect-all windows_toasts --collect-all winrt` ; sans ça, le toast échoue
   dans l'exe figé (repli silencieux sur une notif pystray sans clic).

## Étapes

### 1. Choisir le numéro de version

Versionnage sémantique `MAJEUR.MINEUR.CORRECTIF` :

- **correctif** (`1.6.0 → 1.6.1`) : bug fix, message, ajustement mineur ;
- **mineur** (`1.6.1 → 1.7.0`) : nouvelle fonctionnalité rétrocompatible ;
- **majeur** : rupture (rare).

### 2. Mettre à jour la version et le manifeste

- [`version.py`](version.py) → `__version__ = "X.Y.Z"` (source unique de vérité,
  lue par l'updater et affichée dans le menu).
- [`latest.json`](latest.json) → `version` = `X.Y.Z`, `notes` = résumé
  utilisateur (sans accents pour éviter les soucis d'encodage), et
  `sha256` = `"PENDING"` (on le remplira après le build).

  > Ne pas toucher à `url` : `/releases/latest/download/PasserelleOptimmo.exe`
  > pointe **toujours** vers la dernière release.

### 3. Construire l'exe (venv propre + flags winrt)

```bash
.venv-build/Scripts/python -m PyInstaller --onefile --windowed --noconfirm --clean --noupx \
  --name PasserelleOptimmo \
  --icon icon_app.ico \
  --hidden-import pystray._win32 \
  --collect-all windows_toasts \
  --collect-all winrt \
  --exclude-module numpy --exclude-module scipy --exclude-module pandas \
  --exclude-module matplotlib --exclude-module PyQt5 --exclude-module IPython \
  --add-data "fonts;fonts" \
  --add-data "icon_tray.png;." \
  --add-data "icon_tray_alert.png;." \
  --add-data "icon_header.png;." \
  --add-data "icon_app.ico;." \
  main.py
```

Résultat : `dist/PasserelleOptimmo.exe` (~76 Mo).

> `build.bat` fait la même chose (et gère la signature de code) mais appelle le
> `pyinstaller` du PATH ; utiliser la commande ci-dessus garantit le build dans
> `.venv-build`. Le `.spec` est régénéré par PyInstaller et **gitignoré**.

### 4. Vérifier l'exe

- Taille ~76 Mo (pas ~300 Mo → sinon build Anaconda, recommencer dans le venv).
- Il démarre sans crash (double-clic → icône dans la barre des tâches).

### 5. Calculer le sha256 et compléter le manifeste

```bash
python -c "import hashlib;print(hashlib.sha256(open(r'dist/PasserelleOptimmo.exe','rb').read()).hexdigest())"
```

Reporter cette valeur dans le champ `sha256` de `latest.json` (l'updater
refuse d'installer un exe dont l'empreinte ne correspond pas).

### 6. Commiter et pousser sur `main`

```bash
git add version.py latest.json
git commit -m "Release vX.Y.Z — <résumé>"
git push origin main
```

### 7. Créer la release GitHub avec les 2 assets

```bash
gh release create vX.Y.Z \
  dist/PasserelleOptimmo.exe latest.json \
  --repo Optimmo-Energies/passerelle \
  --title "vX.Y.Z — <titre>" \
  --notes "<notes markdown>"
```

Les **deux** assets sont indispensables : `PasserelleOptimmo.exe` (le binaire) et
`latest.json` (le manifeste lu par l'updater).

### 8. Vérifier ce qui est réellement servi

```bash
# La release est-elle bien marquée « Latest » ?
gh release list --repo Optimmo-Energies/passerelle | head -2

# Manifeste servi (cache-buster car l'URL /latest/ est cachée par le CDN)
curl -sL "https://github.com/Optimmo-Energies/passerelle/releases/latest/download/latest.json?cb=$(date +%s%N)"

# Exe servi : http 200 + sha identique à celui du manifeste
curl -sL -o /tmp/dl.exe -w "http=%{http_code}\n" \
  https://github.com/Optimmo-Energies/passerelle/releases/latest/download/PasserelleOptimmo.exe
python -c "import hashlib;print(hashlib.sha256(open('/tmp/dl.exe','rb').read()).hexdigest())"
```

> **Cache CDN** : juste après la publication, `/releases/latest/download/latest.json`
> peut renvoyer temporairement l'ancien contenu. Vérifier avec un cache-buster,
> ou directement l'asset par tag :
> `…/releases/download/vX.Y.Z/latest.json`. Le sha du manifeste doit
> correspondre au sha de l'exe.

## Propagation aux postes

Au prochain démarrage de la Passerelle sur chaque poste (déjà équipé d'une
version connaissant la bonne `update_url`) : si la version distante est plus
récente, l'exe est téléchargé en arrière-plan puis **installé à la fermeture** de
l'app (un petit `.bat` remplace l'exe verrouillé et relance la Passerelle).

> **Bootstrap** : un poste dont l'exe pointe vers un ancien `update_url` (ou qui
> n'a jamais eu la Passerelle) doit recevoir **une** installation manuelle de
> `dist/PasserelleOptimmo.exe` ; ensuite les MAJ sont automatiques.

## Rappel config de production

Les `DEFAULTS` de [`config.py`](config.py) sont embarqués dans l'exe et
s'appliquent aux postes **sans** `config.json`. Pour la prod, garder :

- `demo_mode = False` (envoi réel ; `True` → préfixe `[DÉMO]` + sauvegarde locale) ;
- `require_auth = True` (login Espace Pro requis).

> Piège : `config.load()` fait `{**DEFAULTS, **config.json}`. Un poste déjà
> installé conserve les clés présentes dans **son** `config.json` ; changer un
> défaut ne rattrape que les installations neuves.

## Checklist express

- [ ] `version.py` bumpé
- [ ] `latest.json` : `version` + `notes` à jour, `sha256` recalculé
- [ ] Exe buildé dans `.venv-build` (~76 Mo), démarre
- [ ] Commit + push sur `main`
- [ ] `gh release create vX.Y.Z` avec **exe + latest.json**
- [ ] Manifeste servi (cache-buster) et sha de l'exe vérifiés
