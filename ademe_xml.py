"""
Construction du XML DPE au format ADEME (modèle DPE_complet.xsd de
l'observatoire DPE) à partir d'un dossier LICIEL.

LICIEL écrit le XML ADEME (<numero_ademe>.xml à la racine du dossier) avec
une déclaration `encoding='UTF-8'` mais un contenu réellement encodé en
cp1252 : le fichier est invalide pour un parseur strict. Ce module :

1. relit ce XML en corrigeant l'encodage (sortie : UTF-8 véritable) ;
2. l'enrichit des informations absentes du XML publié par l'ADEME
   (bloc `administratif` du modèle complet) : propriétaire / donneur
   d'ordre, adresse du propriétaire, diagnostiqueur, horodatage ;
3. le sérialise avec une déclaration XML UTF-8 propre.

Les champs sans équivalent dans le XSD (téléphone / mail du donneur
d'ordre, notaire…) sont retournés séparément pour être joints au résumé
JSON transmis à Opticheck.
"""
import copy
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import liciel

# Conserve les préfixes xsi/xsd du XML LICIEL lors de la re-sérialisation.
ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")

_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

# ── Décodage / réparation d'encodage ─────────────────────────────────────────
def _fix_mojibake(text: str) -> str:
    """
    Répare un texte UTF-8 qui a été relu en cp1252 (double encodage :
    « Ã© » pour « é »). Le « Ã » (A tilde) n'existe pas en français : sa
    présence signale le double encodage ; la réparation n'est conservée
    que si elle le fait disparaître.
    """
    if "Ã" not in text:
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if repaired.count("Ã") < text.count("Ã"):
        return repaired
    return text


def decode_xml_bytes(raw: bytes) -> str:
    """
    Décode un XML LICIEL/ADEME en texte, quelle que soit la réalité de
    l'encodage (UTF-8 correct, cp1252 déclaré UTF-8, ou double encodage).
    La déclaration XML d'origine est retirée (une propre est réécrite à
    la sérialisation).
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    text = _fix_mojibake(text)
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text)


# ── Aides ElementTree ────────────────────────────────────────────────────────
def _set(parent: ET.Element, tag: str, value: str) -> ET.Element:
    """
    Renseigne <tag> sous `parent` (créé si absent). Un élément existant
    vide ou xsi:nil est rempli ; une valeur déjà saisie n'est pas écrasée
    par du vide.
    """
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    if value and not (el.text and el.text.strip()):
        el.text = value
        el.attrib.pop(_XSI_NIL, None)
    return el


def _ensure(parent: ET.Element, tag: str) -> ET.Element:
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    return el


def _fill_adresse(el: ET.Element, adr: str, cp: str, ville: str) -> None:
    """
    Complète les champs *_brut d'un bloc t_adresse. Ne remplit que le vide ;
    le CP « 00000 » (placeholder des XML de dépôt LICIEL) est traité comme
    vide.
    """
    if adr:
        _set(el, "adresse_brut", adr)
    if cp:
        cpel = _ensure(el, "code_postal_brut")
        if not cpel.text or cpel.text.strip() in ("", "00000"):
            cpel.text = cp
            cpel.attrib.pop(_XSI_NIL, None)
    if ville:
        _set(el, "nom_commune_brut", ville)
    label = " ".join(x for x in (adr, cp, ville) if x)
    if label:
        _set(el, "label_brut", label)
        _set(el, "label_brut_avec_complement", label)


# ── Données LICIEL ───────────────────────────────────────────────────────────
def _split_nom_prenom(nom_complet: str) -> tuple[str, str]:
    """« DURANT Jean » → (« DURANT », « Jean »)."""
    parts = nom_complet.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return nom_complet, ""


def _diagnostiqueur_info(dossier: Path, cfg: dict) -> dict:
    """
    Informations diagnostiqueur : la configuration (`cfg["diagnostiqueur"]`)
    prime, complétée par les données société LICIEL (Donnees_Entreprises.xml).
    """
    info = {
        "nom": "", "prenom": "", "mail": "", "telephone": "", "adresse": "",
        "entreprise": "", "numero_certification": "", "organisme_certificateur": "",
    }

    societe = liciel.read_entreprise(cfg.get("liciel_root", ""))
    if societe:
        nom, prenom = _split_nom_prenom(societe.get("operateur", ""))
        info.update({
            "nom": nom,
            "prenom": prenom,
            "entreprise": societe.get("entreprise", ""),
            "numero_certification": societe.get("certif_num", ""),
            "organisme_certificateur": societe.get("certif_societe", ""),
        })

    for key, value in (cfg.get("diagnostiqueur") or {}).items():
        if value:
            info[key] = str(value)
    return info


# ── Enrichissement du bloc administratif ─────────────────────────────────────
def _enrich_administratif(root: ET.Element, dossier: Path, cfg: dict) -> None:
    admin = root.find("administratif")
    if admin is None:
        return

    do = liciel.parse_donneur_ordre(dossier)

    # Propriétaire : champ officiel du modèle complet. Le propriétaire prime,
    # à défaut le donneur d'ordre (cas le plus courant : c'est le même).
    nom_proprietaire = (do.get("proprietaire_nom") or do.get("proprietaire_entete")
                        or do.get("nom") or do.get("entete"))
    if nom_proprietaire:
        _set(admin, "nom_proprietaire", nom_proprietaire)

    # Adresses (t_adresse, champs *_brut). Les XML de dépôt LICIEL laissent
    # l'adresse du bien vide (CP « 00000 ») jusqu'à la publication : on la
    # complète depuis la table admin quand elle est lisible.
    geoloc = admin.find("geolocalisation")
    adresses = geoloc.find("adresses") if geoloc is not None else None
    bien = adresses.find("adresse_bien") if adresses is not None else None
    if bien is not None:
        _fill_adresse(bien, do.get("bien_adresse"), do.get("bien_cp"),
                      do.get("bien_ville"))

    # Adresse du propriétaire — obligatoire dans le modèle complet. Si le
    # donneur d'ordre n'a pas d'adresse renseignée, on duplique l'adresse
    # du bien (propriétaire occupant).
    if adresses is not None:
        adr = do.get("proprietaire_adresse") or do.get("adresse")
        cp = do.get("proprietaire_cp") or do.get("cp")
        ville = do.get("proprietaire_ville") or do.get("ville")
        ap = adresses.find("adresse_proprietaire")
        if ap is not None:
            _fill_adresse(ap, adr, cp, ville)
        elif adr and cp and ville:
            ap = ET.SubElement(adresses, "adresse_proprietaire")
            _fill_adresse(ap, adr, cp, ville)
            _set(ap, "enum_statut_geocodage_ban_id", "2")  # non géocodée BAN
            _set(ap, "ban_date_appel", datetime.now().strftime("%Y-%m-%d"))
        elif bien is not None:
            ap = copy.deepcopy(bien)
            ap.tag = "adresse_proprietaire"
            adresses.append(ap)

    # Diagnostiqueur : champs du modèle complet retirés du XML publié.
    diag = admin.find("diagnostiqueur")
    if diag is not None:
        info = _diagnostiqueur_info(dossier, cfg)
        _set(diag, "nom_diagnostiqueur", info["nom"])
        _set(diag, "prenom_diagnostiqueur", info["prenom"])
        _set(diag, "mail_diagnostiqueur", info["mail"])
        _set(diag, "telephone_diagnostiqueur", info["telephone"])
        _set(diag, "adresse_diagnostiqueur", info["adresse"])
        _set(diag, "entreprise_diagnostiqueur", info["entreprise"])
        _set(diag, "numero_certification_diagnostiqueur",
             info["numero_certification"])
        _set(diag, "organisme_certificateur", info["organisme_certificateur"])

    # Consentement RGPD (obligatoire depuis le XSD 2024, absent des XML
    # LICIEL) : 0 = absence, 1 = fourni, 2 = non requis.
    _set(admin, "enum_consentement_formulaire_id",
         str(cfg.get("ademe_consentement_formulaire", "0")))

    # Horodatage de génération (fuseau Europe/Paris imposé par le XSD).
    if admin.find("horodatage_historisation") is None:
        _set(admin, "horodatage_historisation",
             datetime.now().astimezone().isoformat(timespec="seconds"))


def _apply_extra_fields(root: ET.Element, cfg: dict) -> None:
    """
    Point d'extension : `cfg["ademe_champs_supplementaires"]` associe un
    chemin relatif à la racine <dpe> à une valeur, en créant les éléments
    intermédiaires manquants. Ex. : {"administratif/siren_proprietaire":
    "123456789"}.
    """
    for path, value in (cfg.get("ademe_champs_supplementaires") or {}).items():
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            continue
        parent = root
        for part in parts[:-1]:
            parent = _ensure(parent, part)
        _set(parent, parts[-1], str(value))


# ── Point d'entrée ───────────────────────────────────────────────────────────
def build(dossier: Path, cfg: dict) -> tuple[str, bytes] | None:
    """
    Construit le XML ADEME enrichi et correctement encodé pour un dossier
    LICIEL, par ordre de préférence :
    1. XML publié (nommé par le n° ADEME) ;
    2. dernier XML de dépôt (télétransmission avant attribution du numéro) ;
    3. reconstruction complète depuis les tables LICIEL (ademe_rebuild) pour
       les DPE jamais télétransmis.
    Retourne (nom_de_fichier, octets UTF-8), ou None si le dossier n'a pas
    de mission DPE exploitable.
    """
    source = liciel.find_ademe_xml(dossier)
    root = None
    name = None
    if source is not None:
        try:
            root = ET.fromstring(decode_xml_bytes(source.read_bytes()))
            name = source.name
        except ET.ParseError:
            root = None
    if root is None:
        import ademe_rebuild
        try:
            root = ademe_rebuild.build_dpe(dossier, cfg)
        except Exception:
            root = None
        if root is None:
            return None
        name = f"reconstruit_{dossier.name}.xml"

    _enrich_administratif(root, dossier, cfg)
    _apply_extra_fields(root, cfg)

    body = ET.tostring(root, encoding="unicode")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    return name, xml.encode("utf-8")
