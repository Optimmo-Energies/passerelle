"""
Détection du logiciel de diagnostic installé (LICIEL ou ADN Evaluation /
Analysimo) et identification assistée de son dossier quand aucun n'est trouvé.

- LICIEL  : dossier racine contenant les dossiers annuels « Dossiers_AAAA »
            et/ou la table société « DATA_SOCIETE_XML » → stocké dans
            cfg["liciel_root"].
- ADN Ev. : base Analysimo « ADN_DIAG.sdf » (généralement sous
            <racine>\\Synchro\\SDL<code>\\ADN_DIAG.sdf) → stocké dans
            cfg["analysimo_sdf"].
"""
from pathlib import Path

# Plafond de dossiers explorés lors de la recherche du .sdf, pour ne jamais
# balayer un disque entier si l'utilisateur pointe une racine trop haute.
_MAX_DIRS_SCANNED = 4000
_MAX_DEPTH = 6


def liciel_present(liciel_root: str) -> bool:
    """LICIEL est exploitable dès lors que son dossier racine existe."""
    return bool(liciel_root) and Path(liciel_root).is_dir()


def adn_present(sdf_path: str) -> bool:
    """ADN Evaluation est exploitable si sa base Analysimo (.sdf) existe."""
    return bool(sdf_path) and Path(sdf_path).is_file()


def any_source_present(cfg: dict) -> bool:
    """Vrai si au moins un logiciel de diagnostic reconnu est disponible."""
    return (liciel_present(cfg.get("liciel_root", ""))
            or adn_present(cfg.get("analysimo_sdf", "")))


def _looks_like_liciel(d: Path) -> bool:
    if (d / "DATA_SOCIETE_XML").is_dir():
        return True
    return any(p.is_dir() for p in d.glob("Dossiers_*"))


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
      - ("liciel", <racine LICIEL>)      → à stocker dans liciel_root
      - ("adn",    <chemin ADN_DIAG.sdf>) → à stocker dans analysimo_sdf
    Retourne None si le dossier ne correspond à aucun logiciel reconnu.
    """
    root = Path(path)
    if not root.is_dir():
        return None

    # LICIEL en premier : reconnaissance immédiate et sans coût.
    if _looks_like_liciel(root):
        return ("liciel", str(root))
    for child in root.glob("*"):
        if child.is_dir() and _looks_like_liciel(child):
            return ("liciel", str(child))

    # ADN Evaluation : recherche de la base .sdf dans l'arborescence.
    sdf = _find_sdf(root)
    if sdf is not None:
        return ("adn", str(sdf))

    return None


# Libellés lisibles par l'utilisateur.
SOURCE_LABELS = {
    "liciel": "LICIEL Diagnostics",
    "adn": "ADN Evaluation (Analys'immo)",
}
