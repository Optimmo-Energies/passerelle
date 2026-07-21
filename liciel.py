import re
import xml.etree.ElementTree as ET
from pathlib import Path

# N° ADEME = nom du fichier XML à la racine du dossier (ex: 2675E0113306A.xml)
_ADEME_PATTERN = re.compile(r"^[A-Z0-9]{13}$")

# XML de dépôt ADEME généré par LICIEL lors de la télétransmission, avant
# attribution du numéro (ex: 1234567-260519172929-1.xml =
# <n° certification>-<horodatage>-<tentative>.xml)
_DEPOT_PATTERN = re.compile(r"^\d+-\d{12}-\d+$")


def _iter_dossiers(liciel_root: str):
    """Itère sur tous les dossiers LICIEL (répertoires contenant un sous-dossier XML)."""
    root = Path(liciel_root)
    if not root.exists():
        return
    for year_dir in root.glob("Dossiers_*"):
        if not year_dir.is_dir():
            continue
        for d in year_dir.iterdir():
            if d.is_dir() and (d / "XML").exists():
                yield d


def _mtime(p: Path) -> float:
    """
    mtime « réel » d'un dossier : max du mtime du répertoire ET du XML le plus
    récent qu'il contient, pour éviter que le mtime du répertoire soit trompeur.
    """
    xml_files = list((p / "XML").glob("*.xml"))
    times = [p.stat().st_mtime] + [f.stat().st_mtime for f in xml_files]
    return max(times)


def has_dpe_mission(dossier: Path) -> bool:
    """
    Un dossier LICIEL contient une mission DPE si la table DPE principale existe
    et n'est pas vide. Sans elle, il n'y a rien à transmettre pour ce dossier.
    """
    general = dossier / "XML" / "Table_Z_DPE_2020_General.xml"
    try:
        return general.exists() and general.stat().st_size > 0
    except OSError:
        return False


def list_dossiers(
    liciel_root: str,
    limit: int | None = None,
    only_with_dpe: bool = False,
) -> list[Path]:
    """
    Retourne les dossiers LICIEL triés du plus récent au plus ancien.

    - limit : ne garder que les N plus récents (None = tous).
    - only_with_dpe : ne garder que ceux qui ont une mission DPE.
    """
    dossiers = list(_iter_dossiers(liciel_root))
    if only_with_dpe:
        dossiers = [d for d in dossiers if has_dpe_mission(d)]
    dossiers.sort(key=_mtime, reverse=True)
    return dossiers[:limit] if limit else dossiers


def find_latest_dossier(liciel_root: str) -> Path | None:
    """Retourne le dossier LICIEL modifié le plus récemment (tous types confondus)."""
    dossiers = list_dossiers(liciel_root, limit=1)
    return dossiers[0] if dossiers else None


def get_xml_files(dossier: Path) -> list[Path]:
    return sorted((dossier / "XML").glob("*.xml"))


def find_ademe_number(dossier: Path) -> str | None:
    """
    N° ADEME = nom du fichier XML 13 caractères à la racine du dossier.
    Si plusieurs XML ADEME coexistent (remplacement, copie…), le plus
    récemment modifié est retenu.
    """
    candidates = [f for f in dossier.iterdir()
                  if f.suffix == ".xml" and _ADEME_PATTERN.match(f.stem)]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime).stem


def find_depot_xml(dossier: Path) -> Path | None:
    """
    Dernier XML de dépôt ADEME du dossier (généré par LICIEL à la
    télétransmission, avant attribution du numéro). None si le DPE n'a
    jamais été télétransmis.
    """
    candidates = [f for f in dossier.iterdir()
                  if f.suffix == ".xml" and _DEPOT_PATTERN.match(f.stem)]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def find_ademe_xml(dossier: Path) -> Path | None:
    """
    XML ADEME du dossier : le XML publié (nommé par le n° ADEME) en
    priorité, sinon le dernier XML de dépôt.
    """
    numero = find_ademe_number(dossier)
    if numero and (dossier / f"{numero}.xml").exists():
        return dossier / f"{numero}.xml"
    return find_depot_xml(dossier)


_PLACEHOLDERS = {"", "#", "0", "non communiqué", "non communique"}


def _clean(value: str | None) -> str:
    v = (value or "").strip()
    return "" if v.lower() in _PLACEHOLDERS else v


def _read_liciel_xml(path: Path):
    """Lit un XML LICIEL (encodage cp1252, sans déclaration)."""
    if not path.exists():
        return None
    try:
        return ET.fromstring(path.read_bytes().decode("cp1252", errors="replace"))
    except Exception:
        return None


def find_donneur_ordre(dossier: Path) -> str:
    """Nom du donneur d'ordre, depuis la table Admin puis la table Bien."""
    do = parse_donneur_ordre(dossier)
    if do.get("entete") or do.get("nom"):
        return do.get("entete") or do.get("nom")
    root = _read_liciel_xml(dossier / "XML" / "Table_General_Bien.xml")
    if root is not None:
        el = root.find("LiColonne_DOrdre_Entete")
        if el is not None and _clean(el.text):
            return _clean(el.text)
    return ""


# Champs de la table Admin retenus pour le donneur d'ordre / propriétaire /
# notaire. Clé de sortie → suffixe LiColonne_ dans Table_Admin_Donnees_Dossiers.
_DONNEUR_ORDRE_FIELDS = {
    "entete": "dordre_Entete",
    "nom": "dordre_nom",
    "adresse": "dordre_adresse",
    "cp": "dordre_cp",
    "ville": "dordre_ville",
    "tel": "dordre_tel",
    "mail": "dordre_mail",
    "type": "dordre_type",
    "proprietaire_entete": "proprietaire_Entete",
    "proprietaire_nom": "proprietaire_nom",
    "proprietaire_adresse": "proprietaire_adresse",
    "proprietaire_cp": "proprietaire_cp",
    "proprietaire_ville": "proprietaire_ville",
    "proprietaire_tel": "proprietaire_tel",
    "proprietaire_mail": "proprietaire_mail",
    "notaire_entete": "notaire_Entete",
    "notaire_nom": "notaire_nom",
    "notaire_adresse": "notaire_adresse",
    "notaire_cp": "notaire_cp",
    "notaire_ville": "notaire_ville",
    "bien_adresse": "bien_adresse",
    "bien_cp": "bien_cp",
    "bien_ville": "bien_ville",
}


def parse_donneur_ordre(dossier: Path) -> dict:
    """
    Informations complètes du donneur d'ordre (+ propriétaire, notaire)
    depuis la table Admin. Sur les dossiers récents, LICIEL range cette
    table dans une archive chiffrée (xml.admin) : les champs sont alors
    vides — seuls les dossiers avec la table en clair sont exploitables.
    """
    root = _read_liciel_xml(dossier / "XML" / "Table_Admin_Donnees_Dossiers.xml")
    result = {k: "" for k in _DONNEUR_ORDRE_FIELDS}
    if root is None:
        return result
    for key, suffix in _DONNEUR_ORDRE_FIELDS.items():
        el = root.find(f"LiColonne_{suffix}")
        if el is not None:
            result[key] = _clean(el.text)
    return result


def read_entreprise(liciel_root: str) -> dict:
    """
    Données société/opérateur depuis DATA_SOCIETE_XML/Donnees_Entreprises.xml
    (format lignes LICIEL : une ligne d'en-têtes puis des lignes de valeurs,
    séparées par <colonne_...> et <item_...>). Retourne la première entreprise.
    """
    path = Path(liciel_root) / "DATA_SOCIETE_XML" / "Donnees_Entreprises.xml"
    if not path.exists():
        return {}
    try:
        text = path.read_bytes().decode("cp1252", errors="replace")
    except OSError:
        return {}
    rows = [row.split("<colonne_Donnees_Entreprises>")
            for row in text.split("<item_Donnees_Entreprises>") if row]
    if len(rows) < 2:
        return {}
    headers, values = rows[0], rows[1]
    data = dict(zip(headers, values))
    return {
        "entreprise": _clean(data.get("Nom_Entreprise")),
        "operateur": _clean(data.get("Nom_Opérateur")),
        "certif_societe": _clean(data.get("Certif_Societe")),
        "certif_num": _clean(data.get("Certif_num")),
    }


def find_adresse(dossier: Path) -> str:
    """
    Adresse du bien. Elle n'existe de façon fiable que dans le XML ADEME officiel
    (champ `label_brut` / `adresse_brut`). Absente pour un DPE non encore publié.
    """
    ademe = find_ademe_number(dossier)
    if not ademe:
        return ""
    f = dossier / f"{ademe}.xml"
    if not f.exists():
        return ""
    try:
        root = ET.fromstring(f.read_bytes().decode("utf-8", errors="replace"))
    except Exception:
        return ""
    for tag in ("label_brut", "adresse_brut"):
        for el in root.iter(tag):
            if el.text and el.text.strip():
                return el.text.strip()
    return ""


def parse_dpe_summary(dossier: Path) -> dict:
    info = {
        "dossier": dossier.name,
        "annee": dossier.parent.name.replace("Dossiers_", ""),
        "has_dpe": has_dpe_mission(dossier),
        "donneur_ordre": find_donneur_ordre(dossier),
        # Détails complets (tel, mail, notaire…) sans équivalent dans le
        # XSD ADEME : transmis via le résumé JSON.
        "donneur_ordre_details": parse_donneur_ordre(dossier),
        "adresse": find_adresse(dossier),
        "classe_energie": "—",
        "classe_co2": "—",
        "consommation": "—",
        "surface": "—",
        "numero_ademe": "—",
        "methode": "—",
        "cout_annuel": "—",
        "co2_valeur": "—",
    }

    general = dossier / "XML" / "Table_Z_DPE_2020_General.xml"
    if general.exists():
        try:
            # Les XML LICIEL n'ont pas de déclaration d'encodage ; on force cp1252
            content = general.read_bytes().decode("cp1252", errors="replace")
            root = ET.fromstring(content)

            def g(tag: str) -> str:
                el = root.find(f"LiColonne_{tag}")
                return el.text.strip() if el is not None and el.text else "—"

            info["classe_energie"] = g("txtLettreEnergie")
            info["classe_co2"] = g("txtLettreCo2")
            info["consommation"] = g("txtValEnergie")
            info["surface"] = g("TxtSurfaceHabitable")
            info["methode"] = g("methode_calcul")
            info["cout_annuel"] = g("txtValCout_Total_details")
            info["co2_valeur"] = g("txtValCo2")
        except Exception:
            pass

    ademe = find_ademe_number(dossier)
    if ademe:
        info["numero_ademe"] = ademe

    return info
