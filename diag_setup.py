"""
Détection du logiciel de diagnostic installé (LICIEL ou ADN Evaluation /
Analysimo) et identification assistée de son dossier quand aucun n'est trouvé.

- LICIEL  : dossier racine contenant les dossiers annuels « Dossiers_AAAA »
            et/ou la table société « DATA_SOCIETE_XML » → stocké dans
            cfg["liciel_root"]. LICIEL laissant l'utilisateur enregistrer ses
            dossiers où il veut, un emplacement personnalisé contenant
            directement des dossiers est également accepté.
- Analys'immo : dossier d'installation d'ADN, reconnu à « ADN.exe » et/ou
            « sd.config » → stocké dans cfg["adn_root"]. Le moteur de base
            (SQL Server, LocalDB ou fichier .sdf) est déterminé ensuite par
            `adn_db`, d'après sd.config.

Historique : les versions antérieures ne reconnaissaient Analys'immo qu'à la
présence d'un fichier « ADN_DIAG.sdf », ce qui excluait toutes les
installations sur SQL Server — la majorité des postes en réseau ou en synchro.
cfg["analysimo_sdf"] reste accepté en repli.
"""
import re
from pathlib import Path

import liciel

# Dossier annuel LICIEL (« Dossiers_2026 »).
_ANNEE_DIR = re.compile(r"^Dossiers_\d{4}$", re.IGNORECASE)

# Plafond de dossiers explorés lors de la recherche du .sdf, pour ne jamais
# balayer un disque entier si l'utilisateur pointe une racine trop haute.
_MAX_DIRS_SCANNED = 4000
_MAX_DEPTH = 6


def liciel_present(liciel_root: str) -> bool:
    """LICIEL est exploitable dès lors que son dossier racine existe."""
    return bool(liciel_root) and Path(liciel_root).is_dir()


def adn_present(cfg_or_path) -> bool:
    """
    Analys'immo est exploitable si son dossier d'installation est identifiable.

    Accepte soit la configuration complète, soit — par compatibilité avec les
    versions antérieures — un chemin de fichier `.sdf` ou de dossier.
    """
    import adn_db  # import tardif : évite de charger pythonnet au démarrage

    if isinstance(cfg_or_path, dict):
        cfg = cfg_or_path
        if adn_db.find_adn_root(cfg.get("adn_root", "")) is not None:
            return True
        sdf = cfg.get("analysimo_sdf", "")
        return bool(sdf) and Path(sdf).is_file()

    path = str(cfg_or_path or "")
    if not path:
        return False
    p = Path(path)
    if p.is_file():                      # ancien réglage : chemin du .sdf
        return True
    return adn_db.find_adn_root(path) is not None


def any_source_present(cfg: dict) -> bool:
    """Vrai si au moins un logiciel de diagnostic reconnu est disponible."""
    return liciel_present(cfg.get("liciel_root", "")) or adn_present(cfg)


def _looks_like_liciel(d: Path) -> bool:
    """
    Vrai si `d` peut servir de racine LICIEL : installation standard
    (DATA_SOCIETE_XML / Dossiers_AAAA) ou emplacement personnalisé contenant
    directement des dossiers LICIEL (répertoires avec un sous-dossier XML).
    """
    if (d / "DATA_SOCIETE_XML").is_dir():
        return True
    if any(p.is_dir() for p in d.glob("Dossiers_*")):
        return True
    return liciel.has_dossiers(str(d))


def normalize_liciel_root(path: str) -> str:
    """
    Racine LICIEL à enregistrer pour un dossier choisi par l'utilisateur.
    Un dossier annuel (« Dossiers_2026 ») est remonté d'un cran quand son
    parent est bien une racine LICIEL, pour que les autres années restent
    visibles ; sinon le dossier choisi est conservé tel quel.
    """
    d = Path(path)
    if _ANNEE_DIR.match(d.name) and _looks_like_liciel(d.parent):
        return str(d.parent)
    return str(d)


def _find_sdf(root: Path) -> Path | None:
    """
    Recherche bornée d'une base Analysimo sous `root`. Préfère le nom canonique
    ADN_DIAG.sdf, une base non « SDLDEMO » (démo), puis la plus récente.
    """
    found: list[Path] = []
    seen_dirs = 0
    root_depth = len(root.parts)
    stack = [root]
    while stack and seen_dirs < _MAX_DIRS_SCANNED:
        d = stack.pop()
        seen_dirs += 1
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_file() and e.suffix.lower() == ".sdf":
                    found.append(e)
                elif e.is_dir() and len(e.parts) - root_depth < _MAX_DEPTH:
                    stack.append(e)
            except OSError:
                continue
    if not found:
        return None

    def _rank(p: Path) -> tuple:
        canonical = p.name.lower() == "adn_diag.sdf"
        not_demo = "sdldemo" not in str(p).lower()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        return (canonical, not_demo, mtime)

    return max(found, key=_rank)


def classify_dir(path: str) -> tuple[str, str] | None:
    """
    Identifie le logiciel de diagnostic à partir d'un dossier choisi.
    Retourne (source, valeur) :
      - ("liciel", <racine LICIEL>)         → à stocker dans liciel_root
      - ("adn",    <racine d'installation>)  → à stocker dans adn_root
    Retourne None si le dossier ne correspond à aucun logiciel reconnu.
    """
    import adn_db  # noqa: PLC0415

    root = Path(path)
    if not root.is_dir():
        return None

    # LICIEL en premier : reconnaissance immédiate et sans coût.
    if _looks_like_liciel(root):
        return ("liciel", normalize_liciel_root(str(root)))
    for child in root.glob("*"):
        if child.is_dir() and _looks_like_liciel(child):
            return ("liciel", normalize_liciel_root(str(child)))

    # Analys'immo : le dossier choisi porte ADN.exe / sd.config, ou l'un de ses
    # sous-dossiers directs (cas d'un utilisateur qui désigne « C:\ » ou le
    # dossier parent). On ne balaye pas les emplacements par défaut ici : le
    # dossier désigné doit répondre de lui-même.
    if adn_db.is_adn_root(str(root)):
        return ("adn", str(root))
    for child in sorted(root.glob("*")):
        if child.is_dir() and adn_db.is_adn_root(str(child)):
            return ("adn", str(child))

    # Repli historique : une installation d'évaluation dont on ne retrouve que
    # la base .sdf, sans ADN.exe à proximité.
    sdf = _find_sdf(root)
    if sdf is not None:
        return ("adn", str(sdf.parent.parent.parent
                           if sdf.parent.name.startswith("SDL") else sdf))

    return None


# Libellés lisibles par l'utilisateur.
SOURCE_LABELS = {
    "liciel": "LICIEL Diagnostics",
    "adn": "Analys'immo (ADN)",
}
