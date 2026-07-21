"""
Reconstruction du XML DPE au format ADEME (modèle DPE_complet) depuis les
tables internes LICIEL, pour les dossiers dont le DPE n'a jamais été
télétransmis (aucun XML de dépôt ni publié disponible).

Architecture :
- `Ctx` charge et indexe les tables du dossier (Composants par type
  d'élément, General, Resultats_Calcul, Details_Calcul, table Admin).
- Un builder par famille d'éléments produit les blocs donnee_entree /
  donnee_intermediaire à partir de la ligne Composants correspondante
  (colonne `Clef` = `reference` ADEME, blob `XML_Actuel` +
  `Calcul_Composant_xml` = champs de saisie et identifiants ADEME).
- `build_dpe()` assemble le document complet.

Les mappings sont issus d'une rétro-ingénierie validée contre les XML de
dépôt générés par LICIEL lui-même (harnais rebuild_diff) : voir les specs
spec_*.md du chantier.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import liciel
import liciel_tables

NIL = object()  # sentinelle : émettre le champ avec xsi:nil="true"

_XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", _XSI)
ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")


# ── Aides valeurs ────────────────────────────────────────────────────────────
def dec(v: str | None) -> str:
    """Décimal LICIEL (virgule) → décimal ADEME (point)."""
    return (v or "").strip().replace(",", ".").replace(" ", "")


def num(v: str | None) -> float | None:
    try:
        return float(dec(v))
    except ValueError:
        return None


def date_fr(v: str | None) -> str:
    """jj/mm/aaaa → aaaa-mm-jj (inchangé si déjà ISO ou vide)."""
    v = (v or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", v)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else v


def _dfmt(d: Decimal) -> str:
    """Rendu décimal sans exponent ni zéros finaux (style .NET)."""
    s = format(d.normalize(), "f")
    return s if s != "-0" else "0"


def div1000(v: str | None) -> str:
    """Valeur LICIEL en Wh → kWh, sans perte de précision."""
    return _dfmt(Decimal(dec(v) or "0") / 1000)


def x1000(v: str | None) -> str:
    """Valeur LICIEL en kW → W."""
    return _dfmt(Decimal(dec(v) or "0") * 1000)


def rnd(v: str | None, n: int) -> str:
    """Arrondi à n décimales, zéros finaux supprimés (rendu LICIEL)."""
    f = round(float(dec(v) or "0"), n)
    s = f"{f:.{n}f}".rstrip("0").rstrip(".")
    return s or "0"


def trunc(v: str | None) -> str:
    """Entier tronqué (les *_m2 ADEME sont tronqués, jamais arrondis)."""
    return str(int(float(dec(v) or "0")))


def add(parent: ET.Element, tag: str, value=None) -> ET.Element:
    """
    Ajoute <tag> à parent. value None → non émis (retourne un élément
    détaché), NIL → xsi:nil="true", str → texte.
    """
    if value is None:
        return ET.Element(tag)
    el = ET.SubElement(parent, tag)
    if value is NIL:
        el.set(f"{{{_XSI}}}nil", "true")
    elif value != "":
        el.text = str(value)
    return el


# ── Contexte dossier ─────────────────────────────────────────────────────────
class Ctx:
    """Tables LICIEL d'un dossier, chargées et indexées."""

    def __init__(self, dossier: Path):
        self.dossier = dossier
        self.composants = liciel_tables.load_composants(dossier)
        # Lignes brutes (blobs non aplatis) : nécessaires pour les ponts
        # thermiques dont le blob contient des groupes répétés.
        self.raw = liciel_tables.parse_table(
            dossier / "XML" / "Table_Z_DPE_2020_Composants.xml")
        self.by_clef = {c.get("Clef"): c for c in self.composants}
        self.general = liciel_tables.load_single_row(dossier, "General")
        self.resultats = liciel_tables.load_single_row(dossier, "Resultats_Calcul")
        # Details_Calcul : une ligne par usage (Chauffage / ECSanitaires /
        # Eclairage / Climatisation / Auxiliaires×SousTypeEnum).
        self.details = [liciel_tables.row_fields(r) for r in liciel_tables.parse_table(
            dossier / "XML" / "Table_Z_DPE_2020_Details_Calcul.xml")]
        self.justificatifs = [liciel_tables.row_fields(r) for r in liciel_tables.parse_table(
            dossier / "XML" / "Table_Z_DPE_2020_Justificatifs.xml")]
        self.entretien = [liciel_tables.row_fields(r) for r in liciel_tables.parse_table(
            dossier / "XML" / "Table_Z_DPE_2020_Gestion_Entretien.xml")]
        op_rows = liciel_tables.parse_table(
            dossier / "XML" / "Table_General_Operateurs_General.xml")
        self.operateurs = op_rows[0] if op_rows else {}
        self.admin = liciel.parse_donneur_ordre(dossier)
        self.now = datetime.now()

    def dc_rows(self, type_: str | None = None, sous: str | None = None,
                energie: str | None = None) -> list[dict]:
        """Lignes Details_Calcul filtrées (type d'usage, sous-type, énergie)."""
        rows = self.details
        if type_ is not None:
            rows = [r for r in rows if r.get("Type") == type_]
        if sous is not None:
            rows = [r for r in rows if r.get("SousTypeEnum") == sous]
        if energie is not None:
            rows = [r for r in rows if r.get("enum_type_energie_id") == energie]
        return rows

    def dc_sum(self, col: str, type_: str | None = None, sous: str | None = None,
               energie: str | None = None) -> str:
        """Somme d'une colonne Details_Calcul (Decimal, rendu sans exponent)."""
        rows = self.dc_rows(type_, sous, energie)
        total = sum((Decimal(dec(r.get(col) or "0")) for r in rows), Decimal(0))
        return _dfmt(total)

    def by_element(self, *names: str) -> list[dict]:
        """Lignes Composants dont la colonne Elements est dans `names`."""
        return [c for c in self.composants if c.get("Elements") in names]

    def g(self, key: str, default: str = "") -> str:
        return self.general.get(key, default)

    def r(self, key: str, default: str = "") -> str:
        return self.resultats.get(key, default)


# ── Accès aux blobs d'une ligne Composants ───────────────────────────────────
def _a(row: dict, key: str, default: str = "") -> str:
    """Champ du blob XML_Actuel (`key` relatif, ex 'Calcul_Composant_xml/tv_umur0_id')."""
    return row.get(f"XML_Actuel/{key}", default)


# LICIEL encode ≤ ≥ < > par des jetons dans les libellés.
_SYMBOLS = (("li_infegale", "≤"), ("li_supegale", "≥"),
            ("li_inf", "<"), ("li_sup", ">"))


def _description(row: dict) -> str:
    """description ADEME = `Elements_nom - Descriptions` (jetons décodés)."""
    d = row.get("Descriptions", "")
    for token, sym in _SYMBOLS:
        d = d.replace(token, sym)
    nom = (row.get("Elements_nom") or "").strip()
    return f"{nom} - {d}" if nom else d


def _is_lnc(row: dict) -> bool:
    """Paroi donnant sur un local non chauffé (bloc LNC dans le blob)."""
    return f"XML_Actuel/Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_id_cfg_isolation_lnc_id" in row


def _de_head(de: ET.Element, row: dict) -> None:
    """
    Tête commune de donnee_entree des parois (mur/PB/PH) : description,
    reference, reference_lnc, tv_coef_reduction_deperdition_id puis, pour
    les parois LNC, surface_aiu/aue et enum_cfg_isolation_lnc_id.
    """
    add(de, "description", _description(row))
    add(de, "reference", row.get("Clef", ""))
    lnc = _is_lnc(row)
    add(de, "reference_lnc", ("LNC" + row.get("Clef", "")) if lnc else NIL)
    # Absent du blob pour l'adjacence 22 (local chauffé) → constante 283.
    add(de, "tv_coef_reduction_deperdition_id",
        _a(row, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_tv_coef_reduction_deperdition_id") or "283")
    if lnc:
        add(de, "surface_aiu", dec(_a(row, "Text_donnant_sur_Sch")))
        add(de, "surface_aue", dec(_a(row, "Text_donnant_sur_Sext")))
        add(de, "enum_cfg_isolation_lnc_id",
            _a(row, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_id_cfg_isolation_lnc_id"))
    add(de, "enum_type_adjacence_id",
        _a(row, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_enum_type_adjacence_id"))


def _type_isolation(row: dict) -> str:
    """enum_type_isolation_id depuis les flags Chk_isolation_* (ITR > ITI > ITE > aucune)."""
    if _a(row, "Chk_isolation_ITR") == "1":
        return "5"
    iti = _a(row, "Chk_isolation_ITI") == "1"
    ite = _a(row, "Chk_isolation_ITE") == "1"
    if iti and ite:
        return "6"
    if iti:
        return "3"
    if ite:
        return "4"
    if _a(row, "Chk_isolation_aucune") == "1":
        return "2"
    return "1"


def _type_doublage(row: dict) -> str:
    """enum_type_doublage_id : doublage pris en compte seulement si paroi non isolée."""
    if _a(row, "Chk_isolation_aucune") == "1":
        if _a(row, "Chk_Doublage_sup15mm") == "1":
            return "4"
        if _a(row, "Chk_Doublage_inf15mm") == "1":
            return "3"
    return "2"


_EPAISSEUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*cm")


def build_mur(ctx: "Ctx", row: dict) -> ET.Element:
    mur = ET.Element("mur")
    de = ET.SubElement(mur, "donnee_entree")
    _de_head(de, row)
    add(de, "enum_orientation_id", _a(row, "Calcul_Composant_xml/enum_orientation_id"))
    add(de, "surface_paroi_totale", dec(row.get("Surface_Actuel")))
    add(de, "surface_paroi_opaque", dec(row.get("Surface_Actuel")))
    add(de, "paroi_lourde", _a(row, "lourd_inertie"))
    add(de, "tv_umur0_id", _a(row, "Calcul_Composant_xml/tv_umur0_id"))
    m = _EPAISSEUR.search(_a(row, "Cmb_Epaisseur_Txt"))
    if m:
        add(de, "epaisseur_structure", dec(m.group(1)))
    add(de, "enum_materiaux_structure_mur_id",
        _a(row, "Calcul_Composant_xml/enum_materiaux_structure_mur_id"))
    # Le blob ment parfois sur cette méthode : U0 vient toujours des tables.
    add(de, "enum_methode_saisie_u0_id", "2")
    add(de, "enduit_isolant_paroi_ancienne", "0")
    add(de, "enum_type_doublage_id", _type_doublage(row))
    add(de, "enum_type_isolation_id", _type_isolation(row))
    methode_u = _a(row, "Calcul_Composant_xml/enum_methode_saisie_u_id")
    if methode_u == "6":
        add(de, "resistance_isolation", dec(_a(row, "Cmb_R_isolant")))
    add(de, "enum_methode_saisie_u_id", methode_u)
    di = ET.SubElement(mur, "donnee_intermediaire")
    add(di, "b", dec(_a(row, "b_calcule")))
    add(di, "umur", dec(_a(row, "U_calcule")))
    add(di, "umur0", dec(_a(row, "Calcul_Composant_xml/u_structure")))
    return mur


def build_plancher(ctx: "Ctx", row: dict, kind: str) -> ET.Element:
    """kind = 'bas' (plancher) ou 'haut' (plafond)."""
    el = ET.Element(f"plancher_{kind}")
    de = ET.SubElement(el, "donnee_entree")
    _de_head(de, row)
    add(de, "surface_paroi_opaque", dec(row.get("Surface_Actuel")))
    add(de, "paroi_lourde", _a(row, "lourd_inertie"))
    tv_u0 = _a(row, f"Calcul_Composant_xml/tv_u{'pb' if kind == 'bas' else 'ph'}0_id")
    add(de, f"tv_u{'pb' if kind == 'bas' else 'ph'}0_id", tv_u0)
    add(de, f"enum_type_plancher_{kind}_id", tv_u0)
    add(de, "enum_methode_saisie_u0_id", "2")
    add(de, "enum_type_isolation_id", _type_isolation(row))
    add(de, "enum_methode_saisie_u_id",
        _a(row, "Calcul_Composant_xml/enum_methode_saisie_u_id"))
    if kind == "bas":
        add(de, "calcul_ue", "0")
    di = ET.SubElement(el, "donnee_intermediaire")
    add(di, "b", dec(_a(row, "b_calcule")))
    u = dec(_a(row, "U_calcule"))
    if kind == "bas":
        add(di, "upb", u)
        add(di, "upb_final", u)
        add(di, "upb0", dec(_a(row, "Calcul_Composant_xml/u_structure")))
    else:
        add(di, "uph", u)
        add(di, "uph0", dec(_a(row, "Calcul_Composant_xml/u_structure")))
    return el


# ── Menuiseries (baies, portes) ──────────────────────────────────────────────
def _host_mur(ctx: "Ctx", row: dict) -> dict:
    """Ligne du mur porteur d'une menuiserie (CmbPositionnement = Clef mur)."""
    return ctx.by_clef.get(_a(row, "CmbPositionnement"), {})


def _adjacence(host: dict) -> tuple[str, str, bool]:
    """(enum_type_adjacence_id, tv_coef_reduction_deperdition_id, lnc) du mur porteur."""
    if not host:
        return "1", "1", False
    adj = _a(host, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_enum_type_adjacence_id") or "1"
    tv = _a(host, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_tv_coef_reduction_deperdition_id") or "1"
    return adj, tv, _is_lnc(host)


_LEADING_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def build_baie(ctx: "Ctx", row: dict) -> ET.Element:
    baie = ET.Element("baie_vitree")
    de = ET.SubElement(baie, "donnee_entree")
    host = _host_mur(ctx, row)
    adj, tv_coef, lnc = _adjacence(host)
    add(de, "description", _description(row))
    add(de, "reference", row.get("Clef", ""))
    add(de, "reference_paroi", _a(row, "CmbPositionnement"))
    add(de, "reference_lnc", ("LNC" + row.get("Clef", "")) if lnc else NIL)
    add(de, "tv_coef_reduction_deperdition_id", tv_coef)
    add(de, "enum_type_adjacence_id", adj)
    if lnc:  # ordre extrapolé du cas porte (baie LNC absente du corpus)
        add(de, "surface_aiu", dec(_a(row, "data_fiche_technique/Text_donnant_sur_Sch")))
        add(de, "surface_aue", dec(_a(row, "data_fiche_technique/Text_donnant_sur_Sext")))
        add(de, "enum_cfg_isolation_lnc_id",
            _a(host, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_id_cfg_isolation_lnc_id"))
    add(de, "surface_totale_baie", dec(_a(row, "TxtSurface")))
    add(de, "nb_baie", _a(row, "TxtQuantite"))
    add(de, "tv_ug_id", _a(row, "Calcul_Composant_xml/tv_ug_id_0"))
    add(de, "enum_type_vitrage_id", _a(row, "Calcul_Composant_xml/enum_type_vitrage_id_0"))
    add(de, "enum_inclinaison_vitrage_id", _a(row, "Calcul_Composant_xml/enum_inclinaison_vitrage_id"))
    add(de, "enum_type_gaz_lame_id", _a(row, "Calcul_Composant_xml/enum_type_gaz_lame_id_0"))
    m = _LEADING_NUM.search(_a(row, "data_fiche_technique/cmb_Fenetre_Epaisseur_text"))
    if m:
        add(de, "epaisseur_lame", dec(m.group(1)))
    add(de, "vitrage_vir", _a(row, "Calcul_Composant_xml/vitrage_vir_0"))
    add(de, "enum_methode_saisie_perf_vitrage_id",
        _a(row, "Calcul_Composant_xml/enum_methode_saisie_perf_vitrage_id"))
    add(de, "tv_uw_id", _a(row, "Calcul_Composant_xml/tv_uw_id_0"))
    add(de, "enum_type_materiaux_menuiserie_id",
        _a(row, "Calcul_Composant_xml/enum_type_materiaux_menuiserie_id_0"))
    add(de, "enum_type_baie_id", _a(row, "Calcul_Composant_xml/enum_type_baie_id_0"))
    double = _a(row, "Calcul_Composant_xml/Uw_trouve_1") not in ("", "0")
    add(de, "double_fenetre", "1" if double else "0")
    add(de, "uw_1", dec(_a(row, "Calcul_Composant_xml/Uw_trouve_0")))
    add(de, "sw_1", dec(_a(row, "Calcul_Composant_xml/valeur_Sw_0")))
    fermeture = _a(row, "Calcul_Composant_xml/enum_type_fermeture_id") or "1"
    if fermeture != "1":
        add(de, "tv_deltar_id", _a(row, "Calcul_Composant_xml/tv_deltar_id"))
        add(de, "tv_ujn_id", _a(row, "Calcul_Composant_xml/tv_ujn_id"))
    add(de, "enum_type_fermeture_id", fermeture)
    add(de, "presence_protection_solaire_hors_fermeture",
        _a(row, "chk_protection_solaire_hors_fermeture") or "0")
    add(de, "presence_retour_isolation", "0")
    add(de, "presence_joint", _a(row, "menuiserie_avec_joints") or "0")
    add(de, "largeur_dormant", "10" if _a(row, "Chk_dormant_large") == "1" else "5")
    add(de, "tv_sw_id", _a(row, "Calcul_Composant_xml/tv_sw_id_0"))
    add(de, "enum_type_pose_id", _a(row, "Calcul_Composant_xml/enum_type_pose_id_0"))
    add(de, "enum_orientation_id", _a(row, "Calcul_Composant_xml/enum_orientation_id"))
    add(de, "tv_coef_masque_proche_id",
        _a(row, "Calcul_Composant_xml/tv_coef_masque_proche_id") or "19")
    lointain = _a(row, "Calcul_Composant_xml/tv_coef_masque_lointain_homogene_id")
    if lointain:
        add(de, "tv_coef_masque_lointain_homogene_id", lointain)
    add(de, "masque_lointain_non_homogene_collection", NIL)
    di = ET.SubElement(baie, "donnee_intermediaire")
    add(di, "b", dec(_a(row, "b_calcule")))
    add(di, "ug", dec(_a(row, "Calcul_Composant_xml/Ug_trouve_0")))
    add(di, "uw", dec(_a(row, "Calcul_Composant_xml/uw_composant")))
    if fermeture != "1":
        add(di, "ujn", dec(_a(row, "Calcul_Composant_xml/Ujn_trouve")))
    add(di, "u_menuiserie", dec(_a(row, "Calcul_Composant_xml/u_composant")))
    add(di, "sw", dec(_a(row, "Calcul_Composant_xml/valeur_Sw")))
    add(di, "fe1", dec(_a(row, "Calcul_Composant_xml/valeur_Fe1")))
    add(di, "fe2", dec(_a(row, "Calcul_Composant_xml/valeur_Fe2")))
    add(baie, "baie_vitree_double_fenetre", NIL)
    return baie


def build_porte(ctx: "Ctx", row: dict) -> ET.Element:
    porte = ET.Element("porte")
    de = ET.SubElement(porte, "donnee_entree")
    host = _host_mur(ctx, row)
    adj, tv_coef, lnc = _adjacence(host)
    add(de, "description", _description(row))
    add(de, "reference", row.get("Clef", ""))
    add(de, "reference_paroi", _a(row, "CmbPositionnement"))
    add(de, "reference_lnc", ("LNC" + row.get("Clef", "")) if lnc else NIL)
    if lnc:
        add(de, "enum_cfg_isolation_lnc_id",
            _a(host, "Calcul_Composant_xml/Parroie_Donnant_Sur_ADEME_id_cfg_isolation_lnc_id"))
    add(de, "enum_type_adjacence_id", adj)
    add(de, "tv_coef_reduction_deperdition_id", tv_coef)
    if lnc:
        add(de, "surface_aiu", dec(_a(row, "data_fiche_technique/Text_donnant_sur_Sch")))
        add(de, "surface_aue", dec(_a(row, "data_fiche_technique/Text_donnant_sur_Sext")))
    add(de, "surface_porte", dec(_a(row, "TxtSurface")))
    add(de, "tv_uporte_id", _a(row, "Calcul_Composant_xml/tv_uporte_id"))
    add(de, "enum_methode_saisie_uporte_id", "1")
    add(de, "enum_type_porte_id", _a(row, "Calcul_Composant_xml/enum_type_porte_id"))
    add(de, "nb_porte", _a(row, "TxtQuantite"))
    add(de, "largeur_dormant", "10" if _a(row, "Chk_dormant_large") == "1" else "5")
    add(de, "presence_retour_isolation", "0")
    add(de, "presence_joint", _a(row, "menuiserie_avec_joints") or "0")
    add(de, "enum_type_pose_id", _a(row, "Calcul_Composant_xml/enum_type_pose_id"))
    di = ET.SubElement(porte, "donnee_intermediaire")
    add(di, "uporte", dec(_a(row, "Calcul_Composant_xml/u_composant")))
    add(di, "b", dec(_a(row, "b_calcule")))
    return porte


# ── Ponts thermiques ─────────────────────────────────────────────────────────
_PT_TAG = re.compile(r"<(\w+)>([^<]*)</\1>")


def _pt_groups(ctx: "Ctx") -> list[dict]:
    """
    Groupes du blob de la ligne 'Ponts Thermiques' : contenu de
    Calcul_Composant_xml découpé sur le littéral <###>, dans l'ordre.
    """
    import html as _html
    for raw in ctx.raw:
        if raw.get("Elements") == "Ponts Thermiques":
            blob = raw.get("XML_Actuel", "")
            if "&lt;" in blob and "<" not in blob:
                blob = _html.unescape(blob)
            m = re.search(r"<Calcul_Composant_xml>(.*?)(?:</Calcul_Composant_xml>|$)",
                          blob, re.S)
            if not m:
                return []
            return [dict(_PT_TAG.findall(part)) for part in m.group(1).split("<###>")]
    return []


def build_ponts_thermiques(ctx: "Ctx") -> list[ET.Element]:
    """
    Un élément ADEME par groupe actif (reference_elt présent). L'index PT_i
    court sur TOUS les groupes, désactivés compris (numérotation à trous,
    conforme au dépôt LICIEL).
    """
    out = []
    for i, grp in enumerate(_pt_groups(ctx), start=1):
        if not grp.get("reference_elt"):
            continue
        pt = ET.Element("pont_thermique")
        de = ET.SubElement(pt, "donnee_entree")
        add(de, "description", grp.get("description", ""))
        add(de, "reference", f"PT_{i}")
        add(de, "reference_1", grp.get("reference_elt", ""))
        add(de, "reference_2", grp.get("reference_paroie", ""))
        add(de, "tv_pont_thermique_id", grp.get("tv_pont_thermique_id", ""))
        pct = num(grp.get("pourcentage")) or 0.0
        add(de, "pourcentage_valeur_pont_thermique", f"{pct / 100:g}")
        add(de, "l", dec(grp.get("longueur")))
        add(de, "enum_methode_saisie_pont_thermique_id", "1")
        add(de, "enum_type_liaison_id", grp.get("enum_type_liaison_id", ""))
        di = ET.SubElement(pt, "donnee_intermediaire")
        add(di, "k", dec(grp.get("valeur_k_brut")))
        out.append(pt)
    return out


# ── Systèmes : ventilation, chauffage, ECS ───────────────────────────────────
# Identifiants de tables de valeurs ADEME que LICIEL ne stocke pas : résolus
# par correspondances observées (corpus + XML publiés). Les rendements, eux,
# sont recopiés exactement — un id TV inconnu est signalé par "".
_TV_REND_EMISSION = {"1": "1", "2": "2", "3": "2", "32": "7"}
_TV_REND_DISTRIB_CH = {"1": "1", "2": "1", "3": "1", "32": "3"}
_TV_REND_REGULATION = {"1": "1", "2": "2", "3": "2", "32": "11"}
# (enum_type_chauffage, enum_type_regulation, enum_equipement_intermittence,
#  enum_type_installation, enum_classe_inertie) → tv_intermittence_id (v2.6)
_TV_INTERMITTENCE = {
    ("1", "2", "5", "1", "3"): "146",
    ("2", "1", "1", "2", "2"): "156",
}
_TV_INTERMITTENCE_DEFAUT = {"1": "146", "2": "156"}  # par type de chauffage
# enum_type_generateur_ch → tv_rendement_generation_id (générateurs effet
# joule ≥ 98 → 29 ; autres cas hors combustion : à compléter au fil du corpus)
_TV_REND_GENERATION = {"98": "29", "99": "29", "100": "29"}
# (enum_type_generateur_ecs, volume) → tv_pertes_stockage_id
_TV_PERTES_STOCKAGE = {("71", "65"): "4"}


def _mot(ctx: "Ctx", key: str, default: str = "") -> str:
    """
    Champ du blob d'entrée moteur BBS (Resultats_Calcul). Les balises à
    attributs (<APIFacade xmlns=…>) ne sont pas suivies par le flattener :
    le chemin effectif peut inclure ou non ce niveau.
    """
    base = "detail_calcul_Moteur_Moteur_Tribu_Class_en_XML"
    for prefix in (f"{base}/projet/", f"{base}/APIFacade/projet/", f"{base}/"):
        v = ctx.resultats.get(prefix + key)
        if v is not None:
            return v
    return default


def _vent(ctx: "Ctx", key: str, default: str = "") -> str:
    return ctx.resultats.get(f"detail_ventillation_XML/{key}", default)


def build_ventilation(ctx: "Ctx", row: dict) -> ET.Element:
    el = ET.Element("ventilation")
    de = ET.SubElement(el, "donnee_entree")
    add(de, "surface_ventile", dec(row.get("Surface_Actuel")))
    add(de, "description", row.get("Descriptions", ""))
    add(de, "reference", row.get("Clef", ""))
    add(de, "plusieurs_facade_exposee",
        "1" if _vent(ctx, "plusieurs_facade_exposee").lower() in ("true", "1") else "0")
    add(de, "tv_q4pa_conv_id", _vent(ctx, "tv_q4pa_conv_id"))
    add(de, "enum_methode_saisie_q4pa_conv_id", "1")
    add(de, "tv_debits_ventilation_id", _a(row, "Cmb_Ventillation_id_donnee"))
    add(de, "enum_type_ventilation_id", _vent(ctx, "enum_type_ventilation_id"))
    annee = num(_vent(ctx, "AnneeInstallation")) or 0
    add(de, "ventilation_post_2012", "1" if annee > 2012 else "0")
    add(de, "ref_produit_ventilation", "")
    di = ET.SubElement(el, "donnee_intermediaire")
    add(di, "q4pa_conv", dec(_vent(ctx, "Q4paconv")))
    add(di, "conso_auxiliaire_ventilation", dec(ctx.r("SM_Cvent")) or "0")
    add(di, "hperm", rnd(ctx.r("SM_Hperm"), 5))
    add(di, "hvent", rnd(ctx.r("SM_Hvent"), 5))
    return el


def _g1(row: dict, key: str, default: str = "") -> str:
    return row.get(f"XML_Actuel/detail_gen1/{key}", default)


def _dft(row: dict, key: str, default: str = "") -> str:
    return row.get(f"XML_Actuel/data_fiche_technique/{key}", default)


def _data_complementaires(parent: ET.Element, row: dict) -> None:
    """<data_complementaires xsi:nil> avec attributs data-* si connus."""
    el = add(parent, "data_complementaires", NIL)
    annee = _g1(row, "annee_installation")
    if annee and annee.lower() != "inconnue":
        el.set("data-annee-installation", annee)
    if f"XML_Actuel/detail_gen1/chaudiere_murale" in row:
        el.set("data-chaudiere-murale", _g1(row, "chaudiere_murale"))


def _periode_emetteur(annee: float | None) -> str:
    if annee is None:
        return "1"
    if annee < 1981:
        return "1"
    if annee <= 2000:
        return "2"
    return "3"


def build_chauffage(ctx: "Ctx", row: dict, ecs_row: dict | None) -> ET.Element:
    inst = ET.Element("installation_chauffage")
    de = ET.SubElement(inst, "donnee_entree")
    coll = _a(row, "Chk_collectif") == "1"
    add(de, "description", row.get("Descriptions", ""))
    add(de, "reference", row.get("Clef", ""))
    add(de, "surface_chauffee", dec(row.get("Surface_Actuel")))
    add(de, "rdim", dec(_mot(ctx, "Installation_collection/Installation/Rdim")) or "1")
    add(de, "nombre_niveau_installation_ch",
        _mot(ctx, "Installation_collection/Installation/Nb_niveau") or "1")
    add(de, "enum_cfg_installation_ch_id", _a(row, "enum_cfg_installation_ch_id") or "1")
    sh_imm = num(ctx.g("TxtSurfaceHabitableImmeubleComplet"))
    if coll and sh_imm:
        ratio = round((num(row.get("Surface_Actuel")) or 0) / sh_imm, 5)
        add(de, "ratio_virtualisation", f"{ratio:g}")
    add(de, "enum_type_installation_id", "2" if coll else "1")
    add(de, "enum_methode_calcul_conso_id", "2" if coll else "1")
    di = ET.SubElement(inst, "donnee_intermediaire")
    add(di, "besoin_ch", dec(_dft(row, "Bch")))
    add(di, "besoin_ch_depensier", dec(_dft(row, "BchDep")))
    add(di, "conso_ch", dec(_dft(row, "Cch")))
    add(di, "conso_ch_depensier", dec(_dft(row, "CchDep")))

    # Émetteur
    emc = ET.SubElement(inst, "emetteur_chauffage_collection")
    em = ET.SubElement(emc, "emetteur_chauffage")
    ede = ET.SubElement(em, "donnee_entree")
    e1 = lambda k, d="": row.get(f"XML_Actuel/detail_emeteur1/{k}", d)
    add(ede, "description", "")
    add(ede, "reference", f"Emetteur:{row.get('Clef', '')}#1")
    add(ede, "surface_chauffee", dec(row.get("Surface_Actuel")))
    ted = e1("enum_type_emission_ditribution_id") or "1"  # sic (faute LICIEL)
    add(ede, "tv_rendement_emission_id", _TV_REND_EMISSION.get(ted, "1"))
    add(ede, "tv_rendement_distribution_ch_id", _TV_REND_DISTRIB_CH.get(ted, "1"))
    add(ede, "tv_rendement_regulation_id", _TV_REND_REGULATION.get(ted, "1"))
    add(ede, "enum_type_emission_distribution_id", ted)
    regul = "2" if _mot(ctx, "Installation_collection/Installation/Emetteur_collection/emetteur/Is_regulation_par_piece") == "1" else "1"
    chauffage_type = str(int(num(e1("instalation_centrale", "0")) or 0) + 1)
    equip = _a(row, "enum_equipement_intermittence_id")
    inertie = ctx.r("DPE_Inertie_enum_classe_inertie_id")
    tv_int = _TV_INTERMITTENCE.get(
        (chauffage_type, regul, equip, "2" if coll else "1", inertie),
        _TV_INTERMITTENCE_DEFAUT.get(chauffage_type, "146"))
    add(ede, "tv_intermittence_id", tv_int)
    add(ede, "reseau_distribution_isole", e1("reseau_distribution_isole") or "0")
    add(ede, "enum_equipement_intermittence_id", equip)
    add(ede, "enum_type_regulation_id", regul)
    annee_em = num(_mot(ctx, "Installation_collection/Installation/Emetteur_collection/emetteur/annee_installation"))
    add(ede, "enum_periode_installation_emetteur_id", _periode_emetteur(annee_em))
    add(ede, "enum_type_chauffage_id", chauffage_type)
    add(ede, "enum_temp_distribution_ch_id", e1("enum_temp_distribution_ch_id") or "1")
    add(ede, "enum_lien_generateur_emetteur_id", "1")
    edi = ET.SubElement(em, "donnee_intermediaire")
    add(edi, "i0", dec(_dft(row, "I0_Em1")))
    add(edi, "rendement_emission", dec(_dft(row, "Re_Em1")))
    add(edi, "rendement_distribution", dec(_dft(row, "Rd_Em1")))
    add(edi, "rendement_regulation", dec(_dft(row, "Rr_Em1")))

    # Générateur
    gc = ET.SubElement(inst, "generateur_chauffage_collection")
    gen = ET.SubElement(gc, "generateur_chauffage")
    gde = ET.SubElement(gen, "donnee_entree")
    comb = _g1(row, "calcul_combustion") == "1"
    fonc = _mot(ctx, "Installation_collection/Installation/Generateur_collection/generateur/Fonctionnement_ecs") or "1"
    add(gde, "description", _g1(row, "bdd_nom_donnee"))
    add(gde, "reference", f"Generateur:{row.get('Clef', '')}#1")
    _data_complementaires(gde, row)
    if fonc == "3" and ecs_row is not None:
        add(gde, "reference_generateur_mixte", f"Generateur:{ecs_row.get('Clef', '')}")
    else:
        add(gde, "reference_generateur_mixte", NIL)
    add(gde, "ref_produit_generateur_ch", "Sans Objet")
    type_gen = _g1(row, "enum_type_generateur_ch_id")
    add(gde, "enum_type_generateur_ch_id", type_gen)
    add(gde, "enum_usage_generateur_id", fonc)
    add(gde, "enum_type_energie_id", _g1(row, "enum_type_energie_id"))
    add(gde, "position_volume_chauffe",
        str(1 - int(num(_g1(row, "generateur_hors_vol_hab", "0")) or 0)))
    if comb:
        add(gde, "tv_generateur_combustion_id", "1")
    else:
        # Générateurs à effet joule (types ≥ 98) : rendement 1, id TV 29.
        defaut = "29" if (num(type_gen) or 0) >= 98 else ""
        tv_gen = _TV_REND_GENERATION.get(type_gen, defaut)
        if tv_gen:
            add(gde, "tv_rendement_generation_id", tv_gen)
    add(gde, "identifiant_reseau_chaleur", NIL)
    if comb:
        add(gde, "presence_ventouse", _g1(row, "ventouse") or "0")
        add(gde, "presence_regulation_combustion",
            _g1(row, "regulation_T_fonctionnement") or "0")
    add(gde, "enum_methode_saisie_carac_sys_id", "1")
    add(gde, "enum_lien_generateur_emetteur_id", "1")
    gdi = ET.SubElement(gen, "donnee_intermediaire")
    if comb:
        add(gdi, "pn", x1000(_dft(row, "Pn_Gen1")))
        add(gdi, "qp0", x1000(_dft(row, "QP0_Gen1")))
        add(gdi, "temp_fonc_30", dec(_dft(row, "Tfonc30_Gen1")))
        add(gdi, "temp_fonc_100", dec(_dft(row, "Tfonc100_Gen1")))
        add(gdi, "rpn", rnd(str((num(_dft(row, "Rpn_Gen1")) or 0) / 100), 6))
        add(gdi, "rpint", rnd(str((num(_dft(row, "Rpint_Gen1")) or 0) / 100), 6))
    add(gdi, "rendement_generation", rnd(_dft(row, "Rg_Gen1"), 6))
    add(gdi, "conso_ch", dec(_dft(row, "Cch_Gen1")))
    add(gdi, "conso_ch_depensier", dec(_dft(row, "CchDep_Gen1")))
    return inst


def build_ecs(ctx: "Ctx", row: dict, ch_row: dict | None) -> ET.Element:
    inst = ET.Element("installation_ecs")
    de = ET.SubElement(inst, "donnee_entree")
    coll = _a(row, "Chk_collectif") == "1"
    add(de, "description", row.get("Descriptions", ""))
    add(de, "reference", row.get("Clef", ""))
    add(de, "enum_cfg_installation_ecs_id", "1")
    add(de, "enum_type_installation_id", "2" if coll else "1")
    add(de, "enum_methode_calcul_conso_id", "2" if coll else "1")
    sh_imm = num(ctx.g("TxtSurfaceHabitableImmeubleComplet"))
    if coll and sh_imm:
        ratio = round((num(row.get("Surface_Actuel")) or 0) / sh_imm, 5)
        add(de, "ratio_virtualisation", f"{ratio:g}")
    add(de, "surface_habitable", dec(row.get("Surface_Actuel")))
    add(de, "nombre_logement", _mot(ctx, "Nb_logement") or "1")
    add(de, "rdim", dec(_mot(ctx, "Installation_collection_ECS/Installation_ECS/Rdim")) or "1")
    add(de, "nombre_niveau_installation_ecs", _a(row, "Txt_nb_niveaux") or "1")
    add(de, "tv_rendement_distribution_ecs_id",
        _a(row, "tv_rendement_distribution_ecs_id") or "1")
    add(de, "enum_bouclage_reseau_ecs_id",
        "3" if _a(row, "Chk_Collectif_reseau_boucle") == "1" else "1")
    if coll:
        add(de, "reseau_distribution_isole",
            "1" if "isolé" in _dft(row, "Cmb_Distrubution_text") else "0")
    di = ET.SubElement(inst, "donnee_intermediaire")
    add(di, "rendement_distribution", dec(_dft(row, "Rd_Gen1")))
    add(di, "besoin_ecs", dec(ctx.r("SM_besoin_ecs")))
    add(di, "besoin_ecs_depensier", dec(ctx.r("SM_besoin_ecs_depensier")))
    add(di, "conso_ecs", dec(_dft(row, "Cecs")))
    add(di, "conso_ecs_depensier", dec(_dft(row, "CecsDep")))

    gc = ET.SubElement(inst, "generateur_ecs_collection")
    gen = ET.SubElement(gc, "generateur_ecs")
    gde = ET.SubElement(gen, "donnee_entree")
    comb = _g1(row, "calcul_combustion") == "1"
    fonc = _mot(ctx, "Installation_collection_ECS/Installation_ECS/Generateur_collection/generateur/Fonctionnement_ecs") or "2"
    vs = num(_dft(row, "Vs")) or 0
    add(gde, "description", _g1(row, "bdd_nom_donnee"))
    add(gde, "reference", f"Generateur:{row.get('Clef', '')}")
    _data_complementaires(gde, row)
    reprise = _a(row, "generateur_reprise_du_chauffage_clefCoposant")
    if fonc == "3" and reprise:
        add(gde, "reference_generateur_mixte", f"Generateur:{reprise}#1")
    else:
        add(gde, "reference_generateur_mixte", NIL)
    add(gde, "enum_type_generateur_ecs_id", _g1(row, "enum_type_generateur_ecs_id"))
    add(gde, "ref_produit_generateur_ecs", "")
    add(gde, "enum_usage_generateur_id", fonc)
    add(gde, "enum_type_energie_id", _g1(row, "enum_type_energie_id"))
    if comb:
        add(gde, "tv_generateur_combustion_id", "1")
    add(gde, "enum_methode_saisie_carac_sys_id", "1")
    type_ballon = _mot(ctx, "Installation_collection_ECS/Installation_ECS/Generateur_collection/generateur/type_ballon_elec")
    stockage = {"4": "3", "0": "1"}.get(type_ballon, "3" if vs > 0 else "1")
    if vs > 0:
        tv_st = _TV_PERTES_STOCKAGE.get(
            (_g1(row, "enum_type_generateur_ecs_id"), f"{vs:g}"))
        if tv_st:
            add(gde, "tv_pertes_stockage_id", tv_st)
    add(gde, "identifiant_reseau_chaleur", NIL)
    add(gde, "enum_type_stockage_ecs_id", stockage)
    pos = str(1 - int(num(_g1(row, "generateur_hors_vol_hab", "0")) or 0))
    add(gde, "position_volume_chauffe", pos)
    if comb:
        add(gde, "position_volume_chauffe_stockage", pos)
    add(gde, "volume_stockage", f"{vs:g}")
    if comb:
        add(gde, "presence_ventouse", _g1(row, "ventouse") or "0")
    gdi = ET.SubElement(gen, "donnee_intermediaire")
    if comb:
        add(gdi, "pn", x1000(_dft(row, "Pn_Gen1")))
        add(gdi, "qp0", x1000(_dft(row, "QP0_Gen1")))
        add(gdi, "rpn", rnd(str((num(_dft(row, "Rpn_Gen1")) or 0) / 100), 6))
    add(gdi, "ratio_besoin_ecs", dec(_a(row, "Valeur_Calculee_Ponderation_ECS")) or "1")
    add(gdi, "rendement_generation", rnd(_dft(row, "Rg_Gen1"), 6))
    add(gdi, "conso_ecs", dec(_dft(row, "Cecs")))
    add(gdi, "conso_ecs_depensier", dec(_dft(row, "CecsDep")))
    if vs > 0:
        add(gdi, "rendement_stockage", rnd(_dft(row, "Rs_Gen1"), 6))
    return inst


# ── Racine : administratif, caractéristiques, météo ─────────────────────────
_PERIODES = {
    "Avant 1948": "1", "1948 - 1974": "2", "1975 - 1977": "3",
    "1978 - 1982": "4", "1983 - 1988": "5", "1989 - 2000": "6",
    "2001 - 2005": "7", "2006 - 2012": "8", "2013 - 2021": "9",
    "Après 2021": "10",
}
_ZONES = {"H1a": "1", "H1b": "2", "H1c": "3", "H2a": "4",
          "H2b": "5", "H2c": "6", "H2d": "7", "H3": "8"}


def _split_nom(nom_complet: str) -> tuple[str, str]:
    parts = nom_complet.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]
    return nom_complet, ""


def _bool01(v: str) -> str:
    return "1" if (v or "").strip().lower() in ("true", "1", "vrai") else "0"


def _adresse_bloc(parent: ET.Element, tag: str, ctx: "Ctx", ref_projet: str,
                  adr: str, cp: str, ville: str, compl: str = "") -> None:
    el = ET.SubElement(parent, tag)
    add(el, "adresse_brut", adr)
    add(el, "code_postal_brut", cp or "00000")
    add(el, "nom_commune_brut", ville)
    label = " ".join(x for x in (adr, cp, ville) if x)
    add(el, "label_brut", label)
    add(el, "label_brut_avec_complement",
        f"{label} {compl}".strip() if label else "")
    add(el, "enum_statut_geocodage_ban_id", "2")  # non géocodée BAN
    add(el, "ban_date_appel", ctx.now.strftime("%Y-%m-%d"))
    for ban in ("ban_id", "ban_id_ban_adresse", "ban_label", "ban_housenumber",
                "ban_street", "ban_citycode", "ban_postcode", "ban_city"):
        add(el, ban, NIL)
    add(el, "compl_nom_residence", "")
    add(el, "compl_ref_batiment", ref_projet)
    add(el, "compl_etage_appartement", "0")
    add(el, "compl_ref_cage_escalier", "")
    add(el, "compl_ref_logement", compl)


def build_administratif(ctx: "Ctx", cfg: dict) -> ET.Element:
    admin = ET.Element("administratif")
    ref_projet = ctx.dossier.name.replace("_", "/")
    add(admin, "dpe_a_remplacer", NIL)
    add(admin, "reference_interne_projet", ref_projet)
    add(admin, "motif_remplacement", ctx.g("Txt_num_ademe_raison_remplacement"))
    add(admin, "dpe_immeuble_associe", NIL)
    add(admin, "enum_version_id", "2.6")
    visite = date_fr(ctx.g("DemandeHistorisation").split(" ")[0]) \
        or ctx.now.strftime("%Y-%m-%d")
    add(admin, "date_visite_diagnostiqueur", visite)
    add(admin, "nom_proprietaire",
        ctx.admin.get("proprietaire_nom") or ctx.admin.get("proprietaire_entete") or "")
    add(admin, "siren_proprietaire", NIL)
    add(admin, "nom_proprietaire_installation_commune", "")
    add(admin, "date_etablissement_dpe", ctx.now.strftime("%Y-%m-%d"))
    add(admin, "enum_modele_dpe_id", "1")

    diag = ET.SubElement(admin, "diagnostiqueur")
    add(diag, "usr_logiciel_id", str(cfg.get("ademe_usr_logiciel_id", "26615")))
    add(diag, "version_logiciel",
        str(cfg.get("ademe_version_logiciel", "[Version XML:320]")))
    add(diag, "version_moteur_calcul", "BBS_Slama_")
    # Table Opérateurs : colonnes décalées d'un cran par rapport à leurs noms.
    op = {k.replace("LiColonne_", ""): v for k, v in ctx.operateurs.items()}
    nom, prenom = _split_nom(op.get("Gen_certif_societe", ""))
    diag_cfg = cfg.get("diagnostiqueur") or {}
    add(diag, "nom_diagnostiqueur", diag_cfg.get("nom") or nom)
    add(diag, "prenom_diagnostiqueur", diag_cfg.get("prenom") or prenom)
    add(diag, "mail_diagnostiqueur", diag_cfg.get("mail") or "")
    add(diag, "telephone_diagnostiqueur",
        diag_cfg.get("telephone") or op.get("NumTelOperateur", ""))
    add(diag, "adresse_diagnostiqueur", diag_cfg.get("adresse") or "")
    add(diag, "entreprise_diagnostiqueur",
        diag_cfg.get("entreprise") or "Indéterminée")
    add(diag, "numero_certification_diagnostiqueur",
        diag_cfg.get("numero_certification") or op.get("Gen_certif_date", ""))
    add(diag, "organisme_certificateur",
        diag_cfg.get("organisme_certificateur") or op.get("Gen_num_certif", ""))

    geo = ET.SubElement(admin, "geolocalisation")
    for f in ("numero_fiscal_local", "id_batiment_rnb", "rpls_log_id",
              "rpls_org_id", "idpar", "immatriculation_copropriete"):
        add(geo, f, NIL)
    adresses = ET.SubElement(geo, "adresses")
    adr = ctx.admin.get("bien_adresse", "")
    cp = ctx.admin.get("bien_cp", "")
    ville = ctx.admin.get("bien_ville", "")
    _adresse_bloc(adresses, "adresse_bien", ctx, ref_projet, adr, cp, ville)
    p_adr = ctx.admin.get("proprietaire_adresse") or adr
    p_cp = ctx.admin.get("proprietaire_cp") or cp
    p_ville = ctx.admin.get("proprietaire_ville") or ville
    _adresse_bloc(adresses, "adresse_proprietaire", ctx, ref_projet,
                  p_adr, p_cp, p_ville)
    add(adresses, "adresse_proprietaire_installation_commune", NIL)

    add(admin, "enum_consentement_formulaire_id",
        str(cfg.get("ademe_consentement_formulaire", "0")))
    commanditaire = "1" if "propri" in (ctx.admin.get("type") or "").lower() else "2"
    add(admin, "enum_commanditaire_id", commanditaire)
    add(admin, "horodatage_historisation",
        ctx.now.astimezone().isoformat(timespec="seconds"))
    add(admin, "information_formulaire_consentement", NIL)
    return admin


def build_caracteristique_generale(ctx: "Ctx") -> ET.Element:
    cg = ET.Element("caracteristique_generale")
    annee = ctx.g("TXT_annee_construction").strip()
    if annee:
        add(cg, "annee_construction", annee)
    add(cg, "enum_periode_construction_id",
        _PERIODES.get(ctx.g("Cmb_annee_construction_nom_donnee").strip(), "1"))
    ch_coll = ctx.r("Bien_systeme_chauffage_collectif") in ("1", "True")
    ecs_coll = ctx.r("Bien_systeme_ECS_collectif") in ("1", "True")
    if ctx.g("Cmb_Type_Batiment_nom_donnee").strip().lower() == "maison":
        methode = "1"
    else:
        methode = {(False, False): "2", (True, False): "3",
                   (False, True): "4", (True, True): "5"}[(ch_coll, ecs_coll)]
    add(cg, "enum_methode_application_dpe_log_id", methode)
    add(cg, "surface_habitable_logement", dec(ctx.g("TxtSurfaceHabitable")))
    # Les dossiers en cours de saisie contiennent parfois « - » : défaut 1.
    niveau = ctx.g("Cmb_Nb_niveau").strip()
    add(cg, "nombre_niveau_logement", niveau if niveau.isdigit() else "1")
    add(cg, "hsp", dec(ctx.g("TxtHauteurMoyenSousPlafond")))
    sh_imm = ctx.g("TxtSurfaceHabitableImmeubleComplet").strip()
    if sh_imm and sh_imm != "0" and num(sh_imm):
        add(cg, "surface_habitable_immeuble", dec(sh_imm))
    nb_app = ctx.g("Cmb_Nb_appartement").strip()
    add(cg, "nombre_appartement", nb_app if nb_app.isdigit() else "1")
    return cg


def build_meteo(ctx: "Ctx") -> ET.Element:
    meteo = ET.Element("meteo")
    add(meteo, "enum_zone_climatique_id",
        _ZONES.get(ctx.r("Bien_Zone_Climatique").strip(), "1"))
    add(meteo, "enum_classe_altitude_id", ctx.g("CmbAltitude_index") or "1")
    add(meteo, "batiment_materiaux_anciens",
        _bool01(ctx.r("Bien_Paroies_Ancien_avec_inertie")))
    return meteo


def build_inertie(ctx: "Ctx") -> ET.Element:
    inertie = ET.Element("inertie")
    add(inertie, "inertie_plancher_bas_lourd", _bool01(ctx.r("Bien_Lourd_plancher")))
    add(inertie, "inertie_plancher_haut_lourd", _bool01(ctx.r("Bien_Lourd_plafond")))
    add(inertie, "inertie_paroi_verticale_lourd", _bool01(ctx.r("Bien_Lourd_mur")))
    add(inertie, "enum_classe_inertie_id",
        ctx.r("DPE_Inertie_enum_classe_inertie_id") or "3")
    return inertie


# ── Sortie ───────────────────────────────────────────────────────────────────
def _usages(suffix_dep: str):
    """Déclinaisons par usage d'un bloc de sortie : (nom_champ, type, sous, dep)."""
    return [
        ("ch", "Chauffage", None, True),
        ("ecs", "ECSanitaires", None, True),
        ("eclairage", "Eclairage", None, False),
        ("auxiliaire_generation_ch", "Auxiliaires", "1", True),
        ("auxiliaire_distribution_ch", "Auxiliaires", "2", False),
        ("auxiliaire_generation_ecs", "Auxiliaires", "3", True),
        ("auxiliaire_distribution_ecs", "Auxiliaires", "4", False),
        ("auxiliaire_ventilation", "Auxiliaires", "5", False),
    ]


def build_sortie(ctx: "Ctx") -> ET.Element:
    sortie = ET.Element("sortie")
    r = ctx.r

    dep = ET.SubElement(sortie, "deperdition")
    add(dep, "hvent", dec(r("SM_Hvent")))
    add(dep, "hperm", dec(r("SM_Hperm")))
    add(dep, "deperdition_renouvellement_air", dec(r("Deperdition_ventillation")))
    add(dep, "deperdition_mur", dec(r("Deperdition_mur")))
    add(dep, "deperdition_plancher_bas", dec(r("Deperdition_plancher")))
    add(dep, "deperdition_plancher_haut", dec(r("Deperdition_plafond")))
    add(dep, "deperdition_baie_vitree", dec(r("Deperdition_fenetres")))
    add(dep, "deperdition_porte", dec(r("Deperdition_portes")))
    add(dep, "deperdition_pont_thermique", dec(r("Deperdition_Pont_Thermiques")))
    add(dep, "deperdition_enveloppe", dec(r("DPE_GV")))

    ab = ET.SubElement(sortie, "apport_et_besoin")
    sse = sum(float(dec(x)) for x in r("Meteo_Sse").split(";") if x.strip())
    add(ab, "surface_sud_equivalente", repr(sse))
    add(ab, "apport_solaire_fr", div1000(r("SM_apport_solaire_fr")))
    add(ab, "apport_interne_fr", div1000(r("SM_apport_interne_fr")))
    add(ab, "apport_solaire_ch", div1000(r("SM_apport_solaire_ch")))
    add(ab, "apport_interne_ch", div1000(r("SM_apport_interne_ch")))
    add(ab, "fraction_apport_gratuit_ch", dec(r("SM_fraction_apport_gratuit_ch")))
    add(ab, "fraction_apport_gratuit_depensier_ch",
        dec(r("SM_fraction_apport_gratuit_depensier_ch")))
    add(ab, "pertes_distribution_ecs_recup", div1000(r("SM_pertes_distribution_ecs_recup")))
    add(ab, "pertes_distribution_ecs_recup_depensier",
        div1000(r("SM_pertes_distribution_ecs_recup_depensier")))
    add(ab, "pertes_stockage_ecs_recup", div1000(r("SM_pertes_stockage_ecs_recup")))
    add(ab, "pertes_generateur_ch_recup", div1000(r("SM_pertes_generateur_ch_recup")))
    add(ab, "pertes_generateur_ch_recup_depensier",
        div1000(r("SM_pertes_generateur_ch_recup_depensier")))
    add(ab, "nadeq", dec(r("SM_nadeq")))
    add(ab, "v40_ecs_journalier", dec(r("SM_v40_ecs_journalier")))
    add(ab, "v40_ecs_journalier_depensier", dec(r("SM_v40_ecs_journalier_depensier")))
    for f in ("besoin_ch", "besoin_ch_depensier", "besoin_ecs",
              "besoin_ecs_depensier", "besoin_fr", "besoin_fr_depensier"):
        add(ab, f, dec(r(f"SM_{f}")) or "0")

    # ef / ep / ges / cout : mêmes déclinaisons, colonnes différentes.
    def bloc(tag, prefix, col, col_dep, m2, classe=None):
        el = ET.SubElement(sortie, tag)
        for name, type_, sous, has_dep in _usages(col_dep):
            add(el, f"{prefix}_{name}", ctx.dc_sum(col, type_, sous))
            if has_dep:
                add(el, f"{prefix}_{name}_depensier", ctx.dc_sum(col_dep, type_, sous))
        add(el, f"{prefix}_totale_auxiliaire" if prefix != "cout" else "cout_total_auxiliaire",
            ctx.dc_sum(col, "Auxiliaires"))
        add(el, f"{prefix}_fr", ctx.dc_sum(col, "Climatisation"))
        add(el, f"{prefix}_fr_depensier", ctx.dc_sum(col_dep, "Climatisation"))
        add(el, f"{prefix}_5_usages", ctx.dc_sum(col))
        if m2:
            add(el, f"{prefix}_5_usages_m2", trunc(r(m2)))
        if classe:
            add(el, classe[0], r(classe[1]))
        return el

    # Quirk LICIEL : pas de scénario dépensier pour EP et CO2 (valeur recopiée).
    bloc("ef_conso", "conso", "EF_PCI", "EF_PCI_senario_Depensier",
         "Valeur_DPE_Energie_EF")
    bloc("ep_conso", "ep_conso", "EP", "EP", "Valeur_DPE_Energie",
         ("classe_bilan_dpe", "Valeur_DPE_Energie_Classe"))
    bloc("emission_ges", "emission_ges", "CO2", "CO2", "Valeur_DPE_Co2",
         ("classe_emission_ges", "Valeur_DPE_Co2_Classe"))
    bloc("cout", "cout", "Cout", "Cout_senario_Depensier", None)

    pe = ET.SubElement(sortie, "production_electricite")
    add(pe, "production_pv", dec(r("SM_production_pv")) or "0")
    add(pe, "conso_elec_ac", dec(r("SM_conso_elec_ac")) or "0")
    for f in ("ch", "ecs", "fr", "eclairage", "auxiliaire", "autre_usage"):
        add(pe, f"conso_elec_ac_{f}", "0")

    spe = ET.SubElement(sortie, "sortie_par_energie_collection")
    seen = []
    for row in ctx.details:
        e = row.get("enum_type_energie_id", "")
        if e and e not in seen:
            seen.append(e)
    for e in seen:
        entry = ET.SubElement(spe, "sortie_par_energie")
        add(entry, "enum_type_energie_id", e)
        add(entry, "conso_ch", ctx.dc_sum("EF_PCI", "Chauffage", energie=e))
        add(entry, "conso_ecs", ctx.dc_sum("EF_PCI", "ECSanitaires", energie=e))
        add(entry, "conso_5_usages", ctx.dc_sum("EF_PCI", energie=e))
        add(entry, "emission_ges_ch", ctx.dc_sum("CO2", "Chauffage", energie=e))
        add(entry, "emission_ges_ecs", ctx.dc_sum("CO2", "ECSanitaires", energie=e))
        add(entry, "emission_ges_5_usages", ctx.dc_sum("CO2", energie=e))
        add(entry, "cout_ch", ctx.dc_sum("Cout", "Chauffage", energie=e))
        add(entry, "cout_ecs", ctx.dc_sum("Cout", "ECSanitaires", energie=e))
        add(entry, "cout_5_usages", ctx.dc_sum("Cout", energie=e))

    ce = ET.SubElement(sortie, "confort_ete")
    isol_ph = num(r("Valeur_Performance_isolant_plafond")) or 0
    add(ce, "isolation_toiture", "1" if isol_ph > 1 else "0")
    add(ce, "protection_solaire_exterieure", "0")
    add(ce, "aspect_traversant", _bool01(r("Logement_traversant")))
    add(ce, "brasseur_air", "0")
    inertie_cl = r("DPE_Inertie_enum_classe_inertie_id")
    add(ce, "inertie_lourde", "1" if inertie_cl in ("1", "2") else "0")
    add(ce, "enum_indicateur_confort_ete_id", r("Valeur_Confort_ete") or "1")

    def _qualite(v: str) -> str:
        """Indicateur 1-4 (dossiers en cours de saisie : valeur absente → 1)."""
        return v if v in ("1", "2", "3", "4") else "1"

    qi = ET.SubElement(sortie, "qualite_isolation")
    add(qi, "ubat", rnd(r("Valeur_Performance_isolant_Ubat"), 3))
    add(qi, "qualite_isol_enveloppe", _qualite(r("Valeur_Performance_isolant")))
    add(qi, "qualite_isol_mur", _qualite(r("Valeur_Performance_isolant_mur")))
    ph_type = {"1": "toit_terrasse", "2": "comble_perdu", "3": "comble_amenage"}.get(
        r("Valeur_Performance_isolant_plafond_type"), "comble_perdu")
    add(qi, f"qualite_isol_plancher_haut_{ph_type}",
        _qualite(r("Valeur_Performance_isolant_plafond")))
    add(qi, "qualite_isol_plancher_bas",
        _qualite(r("Valeur_Performance_isolant_plancher")))
    add(qi, "qualite_isol_menuiserie",
        _qualite(r("Valeur_Performance_isolant_menuiserie")))
    return sortie


# ── Collections de fin de document ───────────────────────────────────────────
_CAT_SIMPLIFIE = {"Mur": "1", "Plancher": "2", "Plafond": "3", "Fenêtre": "4",
                  "Porte": "4", "Chauffage": "5", "ECSanitaires": "6",
                  "Climatisation": "7", "Ventilation": "8"}
_CAT_FICHE = {"10": "11", "11": "1", "12": "2", "13": "3", "14": "4",
              "15": "5", "16": "6", "17": "10", "18": "7", "19": "8"}
_JUSTIFICATIF_LABELS = {
    "1": "Plans du logement",
    "10": "Notices techniques des équipements",
}


def _liciel_texte(s: str) -> str:
    for token, sym in _SYMBOLS:
        s = (s or "").replace(token, sym)
    return s


def build_collections(dpe: ET.Element, ctx: "Ctx") -> None:
    """Collections de fin de document (celles que LICIEL ajoute au dépôt)."""
    ET.SubElement(dpe, "descriptif_enr_collection")

    simplifie = ET.SubElement(dpe, "descriptif_simplifie_collection")
    for row in ctx.composants:
        el_type = row.get("Elements", "")
        cat = _CAT_SIMPLIFIE.get(el_type)
        if not cat:
            continue
        desc = _liciel_texte(row.get("Descriptions", ""))
        cats = [cat, "9"] if el_type == "Chauffage" else [cat]
        for c in cats:
            item = ET.SubElement(simplifie, "descriptif_simplifie")
            add(item, "description", desc)
            add(item, "enum_categorie_descriptif_simplifie_id", c)

    fiches = ET.SubElement(dpe, "fiche_technique_collection")
    groupes: dict[tuple, ET.Element] = {}
    for row in ctx.justificatifs:
        if row.get("bdd_imprimer") != "1":
            continue
        if row.get("bdd_supp_si_vide") == "1" and not (row.get("valeur") or "").strip():
            continue
        cat = _CAT_FICHE.get(row.get("bdd_trie_composants", ""))
        if not cat:
            continue
        key = (row.get("bdd_trie_composants"), row.get("clef_Composant"))
        fiche = groupes.get(key)
        if fiche is None:
            fiche = ET.SubElement(fiches, "fiche_technique")
            add(fiche, "enum_categorie_fiche_technique_id", cat)
            ET.SubElement(fiche, "sous_fiche_technique_collection")
            groupes[key] = fiche
        sfc = fiche.find("sous_fiche_technique_collection")
        sf = ET.SubElement(sfc, "sous_fiche_technique")
        valeur = _liciel_texte((row.get("valeur") or "").strip()
                               + (row.get("bdd_suffix") or ""))
        add(sf, "description", f"{_liciel_texte(row.get('nom_client', ''))}: {valeur}")
        add(sf, "valeur", valeur)
        add(sf, "detail_origine_donnee", "")
        add(sf, "enum_origine_donnee_id", row.get("enum_origine_donnee_id") or "2")

    justifs = ET.SubElement(dpe, "justificatif_collection")
    seen_types = []
    for row in ctx.justificatifs:
        t = (row.get("enum_type_justificatif_id") or "").strip()
        if t and t not in seen_types:
            seen_types.append(t)
    for t in seen_types:
        item = ET.SubElement(justifs, "justificatif")
        add(item, "description", _JUSTIFICATIF_LABELS.get(t, ""))
        add(item, "enum_type_justificatif_id", t)

    gestes = ET.SubElement(dpe, "descriptif_geste_entretien_collection")
    for row in ctx.entretien:
        if "X" not in (row.get("AImprimerAuto", ""), row.get("AImprimerManuel", "")):
            continue
        item = ET.SubElement(gestes, "descriptif_geste_entretien")
        add(item, "description", _liciel_texte(row.get("Recommandation", "")))
        add(item, "enum_picto_geste_entretien_id",
            row.get("enum_picto_geste_entretien_id") or "1")
        add(item, "categorie_geste_entretien", row.get("Composant", ""))
    # descriptif_travaux : optionnel (minOccurs=0), omis en v1.


def build_dpe(dossier: Path, cfg: dict | None = None) -> ET.Element | None:
    """
    Reconstruit l'arbre <dpe> complet depuis les tables LICIEL.
    Retourne None si le dossier n'a pas de mission DPE exploitable.
    """
    if not liciel.has_dpe_mission(dossier):
        return None
    ctx = Ctx(dossier)
    if not ctx.composants or not ctx.general:
        return None

    cfg = cfg or {}
    dpe = ET.Element("dpe", {"version": "2"})
    dpe.append(build_administratif(ctx, cfg))
    logement = ET.SubElement(dpe, "logement")
    logement.append(build_caracteristique_generale(ctx))
    logement.append(build_meteo(ctx))
    enveloppe = ET.SubElement(logement, "enveloppe")
    enveloppe.append(build_inertie(ctx))

    murs = ET.SubElement(enveloppe, "mur_collection")
    for row in ctx.by_element("Mur"):
        murs.append(build_mur(ctx, row))
    pbs = ET.SubElement(enveloppe, "plancher_bas_collection")
    for row in ctx.by_element("Plancher"):
        pbs.append(build_plancher(ctx, row, "bas"))
    phs = ET.SubElement(enveloppe, "plancher_haut_collection")
    for row in ctx.by_element("Plafond"):
        phs.append(build_plancher(ctx, row, "haut"))
    baies = ET.SubElement(enveloppe, "baie_vitree_collection")
    for row in ctx.by_element("Fenêtre"):
        baies.append(build_baie(ctx, row))
    portes = ET.SubElement(enveloppe, "porte_collection")
    for row in ctx.by_element("Porte"):
        portes.append(build_porte(ctx, row))
    ET.SubElement(enveloppe, "ets_collection")
    pts = ET.SubElement(enveloppe, "pont_thermique_collection")
    for pt in build_ponts_thermiques(ctx):
        pts.append(pt)

    vents = ET.SubElement(logement, "ventilation_collection")
    for row in ctx.by_element("Ventilation"):
        vents.append(build_ventilation(ctx, row))
    ET.SubElement(logement, "climatisation_collection")
    add(logement, "production_elec_enr", NIL)
    ch_rows = ctx.by_element("Chauffage")
    ecs_rows = ctx.by_element("ECSanitaires")
    ecs_coll = ET.SubElement(logement, "installation_ecs_collection")
    for row in ecs_rows:
        ecs_coll.append(build_ecs(ctx, row, ch_rows[0] if ch_rows else None))
    ch_coll = ET.SubElement(logement, "installation_chauffage_collection")
    for row in ch_rows:
        ch_coll.append(build_chauffage(ctx, row, ecs_rows[0] if ecs_rows else None))
    logement.append(build_sortie(ctx))

    build_collections(dpe, ctx)
    return dpe
