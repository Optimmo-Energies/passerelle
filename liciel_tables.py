"""
Parseur générique des tables LICIEL (XML/Table_*.xml).

Format : cp1252 sans déclaration, racine <LiTable_X>, lignes <LiItem_table_X>,
colonnes <LiColonne_Y>. Certaines colonnes contiennent des blobs pseudo-XML
échappés (ex : Calcul_Composant_xml dans la table Composants) qui portent les
identifiants ADEME (enum_*, tv_*) : on les aplatit en dictionnaires.
"""
import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_TAG = re.compile(r"<(/?)([A-Za-z_][\w.-]*)\s*(/?)>")


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


_ITEM = re.compile(r"<LiItem_[\w]+>")
_COL = re.compile(r"<LiColonne_([\w]+)>(.*?)</LiColonne_\1>", re.S)


def parse_table(path: Path) -> list[dict]:
    """
    Lit une table LICIEL et retourne une liste de lignes, chaque ligne étant
    un dict {nom_colonne (sans préfixe LiColonne_): contenu brut str}.

    Parcours par regex (pas ElementTree) : les blobs pseudo-XML imbriqués
    par LICIEL ne sont pas toujours bien formés (balises orphelines) et
    feraient échouer un parseur strict.
    """
    if not path.exists():
        return []
    text = _decode(path)
    # Certaines tables (Resultats_Calcul…) n'ont pas de lignes LiItem :
    # les colonnes sont directement sous la racine.
    chunks = _ITEM.split(text)[1:] or [text]
    rows = []
    for chunk in chunks:
        row = {name: value.strip() for name, value in _COL.findall(chunk)}
        rows.append(row)
    return rows


def flatten_blob(text: str) -> dict:
    """
    Aplatit un blob pseudo-XML LICIEL (éventuellement échappé) en dict
    {chemin/feuille: valeur}. Tolérant : parcours par regex, pas de
    validation. En cas de balises répétées, suffixe #2, #3…
    """
    if not text:
        return {}
    if "<" not in text and "&lt;" in text:
        text = html.unescape(text)
    result: dict[str, str] = {}
    stack: list[str] = []
    pos = 0
    for m in _TAG.finditer(text):
        closing, tag, selfclose = m.group(1), m.group(2), m.group(3)
        if closing:
            # texte accumulé depuis la dernière balise = valeur de la feuille
            if stack and stack[-1].rsplit("/", 1)[-1] == tag:
                value = text[pos:m.start()].strip()
                if value:
                    base = stack[-1]
                    key, n = base, 2
                    while key in result:
                        key = f"{base}#{n}"
                        n += 1
                    result[key] = value
                stack.pop()
        elif not selfclose:
            stack.append((stack[-1] + "/" if stack else "") + tag)
        pos = m.end()
    return result


def row_fields(row: dict) -> dict:
    """
    Aplatit une ligne de table : colonnes simples + feuilles des blobs
    imbriqués (clés préfixées par le nom de colonne).
    """
    flat: dict[str, str] = {}
    for name, value in row.items():
        if "&lt;" in value and "<" not in value:
            value = html.unescape(value)
        if "<" in value:
            flat.update({f"{name}/{k}": v
                         for k, v in flatten_blob(value).items()})
        else:
            flat[name] = html.unescape(value)
    return flat


def load_composants(dossier: Path) -> list[dict]:
    """Lignes aplaties de la table Composants d'un dossier."""
    rows = parse_table(dossier / "XML" / "Table_Z_DPE_2020_Composants.xml")
    return [row_fields(r) for r in rows]


def load_single_row(dossier: Path, table: str) -> dict:
    """Première ligne aplatie d'une table (General, Resultats_Calcul…)."""
    rows = parse_table(dossier / "XML" / f"Table_Z_DPE_2020_{table}.xml")
    return row_fields(rows[0]) if rows else {}
