"""
Lecture métier des DPE Analys'immo (ADN) pour transmission à Opticheck.

Ce module produit, pour une mission DPE donnée :
  - un **résumé** au même format que celui de LICIEL (`liciel.parse_dpe_summary`),
    afin que l'IHM de confirmation, le rapport e-mail et l'API reçoivent la
    même forme quelle que soit l'origine du DPE ;
  - une **charge utile complète** : toutes les lignes de saisie du DPE
    (enveloppe, installations, ventilation) et les sorties du moteur de calcul,
    extraites de `ADN_DIAG_DPE2012`.

L'extraction est pilotée par le schéma : on découvre à l'exécution les tables
rattachées à une mission (`idMission`) ou à un lot de calcul (`idSaisieLot`) et
on lit toutes leurs colonnes non binaires. Un mapping colonne par colonne
serait à réécrire à chaque version d'ADN ; cette approche suit le schéma.

Rappel important : la classe énergie n'est pas stockée par ADN, elle se déduit
des seuils de `XDPEclasseConsommationEnergie` selon le type de bâtiment.
"""
import re
from datetime import datetime

import adn_db
from adn_db import DB_DIAG, DB_DPE, DB_RG

# Catégories de mission correspondant à un DPE. C'est bien
# `idCategorieMission` qu'il faut filtrer : `idTypeMission` porte le type de
# bâtiment (MI, IC, ICPROJ, TT, ERPB…), pas la nature du diagnostic.
DPE_CATEGORIES = ("DPE", "DPE2012", "DPE2021")

# Audits énergétiques : même moteur, mais ce n'est pas un DPE.
AUDIT_CATEGORIES = ("AE2021",)

# Rôles d'interlocuteur (ADN_DIAG.RoleInterlocuteurDossier).
ROLE_PROPRIETAIRE = 1
ROLE_DONNEUR_ORDRE = 2

# Colonnes à ne JAMAIS extraire : identifiants de télétransmission ADEME et
# secrets de signature. Ils n'ont aucune utilité pour l'analyse Opticheck et
# ne doivent pas quitter le poste.
SECRET_COLUMNS = {
    "loginademe", "mdpademe", "loginademeaudit", "mdpademeaudit",
    "clesignature", "password", "motdepasse", "mdp",
}

# Tables rattachées à une mission ou à un lot mais sans intérêt pour l'analyse
# (ressources binaires, journal de navigation, lexique de rédaction).
SKIP_TABLES = {
    "XDPEressourcesSaisieLot", "XDPEparcoursDossier", "XDPEsaisieLexique",
}

_BINARY_TYPES = ("varbinary", "binary", "image", "timestamp")


class AdnError(RuntimeError):
    """Erreur de lecture d'un DPE Analys'immo, message destiné à l'utilisateur."""


# ── Introspection du schéma ──────────────────────────────────────────────────
_schema_cache: dict[tuple[int, str], dict] = {}


def _schema(src, database: str) -> dict:
    """
    Colonnes exploitables par table pour une base donnée :
    {nom_table: [colonnes]}, hors colonnes binaires et hors secrets.
    """
    key = (id(src), database)
    if key in _schema_cache:
        return _schema_cache[key]
    rows = src.query(
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
        "FROM INFORMATION_SCHEMA.COLUMNS",
        database=database)
    out: dict[str, list[str]] = {}
    for r in rows:
        if r["DATA_TYPE"] in _BINARY_TYPES:
            continue
        if (r["COLUMN_NAME"] or "").lower() in SECRET_COLUMNS:
            continue
        out.setdefault(r["TABLE_NAME"], []).append(r["COLUMN_NAME"])
    _schema_cache[key] = out
    return out


def _tables_with(src, database: str, column: str, prefix: str = "XDPE") -> list[str]:
    """Tables (préfixées `prefix`) possédant la colonne `column`."""
    schema = _schema(src, database)
    return sorted(t for t, cols in schema.items()
                  if t.startswith(prefix) and t not in SKIP_TABLES
                  and column in cols)


def _cols(src, database: str, table: str) -> list[str]:
    return _schema(src, database).get(table, [])


def _select(src, database: str, table: str, where: str, params: dict) -> list[dict]:
    """SELECT de toutes les colonnes exploitables d'une table."""
    cols = _cols(src, database, table)
    if not cols:
        return []
    fields = ", ".join(f"[{c}]" for c in cols)
    return src.query(f"SELECT {fields} FROM [{table}] WHERE {where}",
                     database=database, params=params)


# ── Dossiers et missions ─────────────────────────────────────────────────────
def _in_list(values) -> str:
    """Liste SQL littérale — les paramètres nommés ne couvrent pas un IN (...)."""
    safe = [str(v).replace("'", "''") for v in values]
    return ", ".join(f"N'{v}'" for v in safe)


def list_dossiers(src, limit: int = 30, categories=DPE_CATEGORIES) -> list[dict]:
    """
    Dossiers non supprimés portant au moins une mission DPE, du plus
    récemment modifié au plus ancien.
    """
    diag = src.resolve_db(DB_DIAG)
    rows = src.query(
        f"""
        SELECT TOP({int(limit)})
               d.idDossier, d.reference, d.referenceExterne,
               d.adresse, d.cptAdresse, d.codePostal, d.ville, d.departement,
               d.anneeConstruction, d.surface, d.usageBien, d.categorieBien,
               d.hspMoy, d.etage, d.numeroLot, d.nomBatiment,
               d.dateCommande, d.dateRapport, d.dateMaj, d.idStatut
        FROM Dossier d
        WHERE d.dateSup IS NULL
          AND EXISTS (SELECT 1 FROM Mission m
                      WHERE m.idDossier = d.idDossier
                        AND m.dateSup IS NULL
                        AND m.idCategorieMission IN ({_in_list(categories)}))
        ORDER BY d.dateMaj DESC, d.idDossier DESC
        """,
        database=diag)
    return rows


def find_latest_dossier(src, categories=DPE_CATEGORIES) -> dict | None:
    rows = list_dossiers(src, limit=1, categories=categories)
    return rows[0] if rows else None


def get_dpe_missions(src, id_dossier: int | None = None,
                     categories=DPE_CATEGORIES) -> list[dict]:
    """
    Missions DPE d'un dossier (ou de tous), triées par date de RDV
    décroissante. Le filtre porte sur `idCategorieMission`.
    """
    diag = src.resolve_db(DB_DIAG)
    where = "m.dateSup IS NULL AND m.idCategorieMission IN (" \
            f"{_in_list(categories)})"
    params = {}
    if id_dossier is not None:
        where += " AND m.idDossier = @idDossier"
        params["idDossier"] = int(id_dossier)
    return src.query(
        f"""
        SELECT m.idMission, m.idDossier, m.intitule, m.idTypeMission,
               m.idCategorieMission, m.dateRdv, m.dateDebut, m.dateFin,
               m.dateRedaction, m.dateValidite, m.statut, m.isFini,
               m.conclusion, m.idEmploye, m.idIntervenant, m.numRevision
        FROM Mission m
        WHERE {where}
        ORDER BY m.dateRdv DESC, m.idMission DESC
        """,
        database=diag, params=params)


def has_dpe_mission(src, id_dossier: int) -> bool:
    return bool(get_dpe_missions(src, id_dossier))


# ── Interlocuteurs ───────────────────────────────────────────────────────────
def interlocuteurs(src, id_dossier: int) -> dict[int, dict]:
    """
    Interlocuteurs du dossier, indexés par rôle. Le nom vit dans le
    référentiel `ADN_RG.Interlocuteur` ; `DossierInterlocuteur` peut en porter
    une copie (`useDataCopy`) qui prime alors.
    """
    diag = src.resolve_db(DB_DIAG)
    liens = src.query(
        "SELECT idInterlocuteur, idRole, titre, nom, prenom, adresse1, "
        "adresse2, codePostal, ville, departement, useDataCopy "
        "FROM DossierInterlocuteur WHERE idDossier = @idDossier",
        database=diag, params={"idDossier": int(id_dossier)})
    if not liens:
        return {}

    ids = [l["idInterlocuteur"] for l in liens if l.get("idInterlocuteur")]
    referentiel: dict[int, dict] = {}
    if ids:
        rg = src.resolve_db(DB_RG)
        try:
            rows = src.query(
                "SELECT idInterlocuteur, titre, nom, prenom, adresse1, "
                "adresse2, codePostal, ville, departement, telephoneFixe, "
                "telephoneMobile, email, typePersonne, siret "
                f"FROM Interlocuteur WHERE idInterlocuteur IN ({_in_list(ids)})",
                database=rg)
            referentiel = {r["idInterlocuteur"]: r for r in rows}
        except Exception:
            # Référentiel indisponible : on se rabat sur la copie locale.
            referentiel = {}

    out: dict[int, dict] = {}
    for l in liens:
        base = dict(referentiel.get(l.get("idInterlocuteur")) or {})
        if l.get("useDataCopy"):
            base.update({k: v for k, v in l.items()
                         if k not in ("idRole", "useDataCopy") and v})
        for k, v in l.items():
            base.setdefault(k, v)
        out[l["idRole"]] = base
    return out


def _nom_complet(inter: dict) -> str:
    parts = [(inter.get("nom") or "").strip(), (inter.get("prenom") or "").strip()]
    return " ".join(p for p in parts if p).strip()


# ── Diagnostiqueur ───────────────────────────────────────────────────────────
def diagnostiqueur(src, id_employe: int | None) -> dict:
    """
    Bloc diagnostiqueur, lu dans `ADN_RG` (employé + société). Les
    identifiants ADEME de l'employé sont volontairement exclus (voir
    SECRET_COLUMNS).
    """
    if not id_employe:
        return {}
    rg = src.resolve_db(DB_RG)
    try:
        emp = src.query(
            "SELECT idEmploye, idSociete, titre, nom, prenom, mail, mailPro, "
            "numeroFixe, numeroPortable, numeroProFixe, numeroProPort, "
            "adresse1, adresse2, codePostal, ville, matricule, "
            "numQualifBet, numSiretBet "
            "FROM Employe WHERE idEmploye = @id",
            database=rg, params={"id": int(id_employe)})
    except Exception:
        return {}
    if not emp:
        return {}
    e = emp[0]
    out = {
        "nom": (e.get("nom") or "").strip(),
        "prenom": (e.get("prenom") or "").strip(),
        "mail": (e.get("mailPro") or e.get("mail") or "").strip(),
        "telephone": (e.get("numeroProPort") or e.get("numeroPortable")
                      or e.get("numeroProFixe") or e.get("numeroFixe") or "").strip(),
        "adresse": " ".join(x for x in (
            (e.get("adresse1") or "").strip(), (e.get("codePostal") or "").strip(),
            (e.get("ville") or "").strip()) if x),
        "matricule": (e.get("matricule") or "").strip(),
    }
    try:
        soc = src.query(
            "SELECT raisonSociale, nom, siret, adresse1, codePostal, ville, "
            "numeroCertification, organismeCertificateur "
            "FROM Societe WHERE idSociete = @id",
            database=rg, params={"id": e.get("idSociete")})
    except Exception:
        soc = []
    if soc:
        s = soc[0]
        out["entreprise"] = (s.get("raisonSociale") or s.get("nom") or "").strip()
        out["numero_certification"] = (s.get("numeroCertification") or "").strip()
        out["organisme_certificateur"] = (s.get("organismeCertificateur") or "").strip()
        out["siret"] = (s.get("siret") or "").strip()
    return out


# ── Étiquettes énergie / GES ─────────────────────────────────────────────────
_LETTRES = ("A", "B", "C", "D", "E", "F", "G")


def _seuils(src, type_dossier: str) -> tuple[list[float], list[float]]:
    """
    Seuils supérieurs des classes énergie et GES pour un type de bâtiment
    (MI, IC, TT, ERPB…). Une borne à 0 signifie « pas de plafond ».
    """
    dpe = src.resolve_db(DB_DPE)
    rows = src.query(
        "SELECT c.classConso_A, c.classConso_B, c.classConso_C, c.classConso_D, "
        "c.classConso_E, c.classConso_F, c.classConso_G, "
        "g.classEmission_A, g.classEmission_B, g.classEmission_C, "
        "g.classEmission_D, g.classEmission_E, g.classEmission_F, "
        "g.classEmission_G "
        "FROM XDPEtypeDossierDPE t "
        "LEFT JOIN XDPEclasseConsommationEnergie c "
        "  ON c.idClasseConsommationEnergie = t.idClasseConsommationEnergie "
        "LEFT JOIN XDPEclasseEmissionGES g "
        "  ON g.idClasseEmissionGES = t.idClasseEmissionGES "
        "WHERE t.idTypeDossierDPE = @t",
        database=dpe, params={"t": type_dossier or ""})
    if not rows:
        return [], []
    r = rows[0]
    conso = [float(r[f"classConso_{l}"] or 0) for l in _LETTRES]
    ges = [float(r[f"classEmission_{l}"] or 0) for l in _LETTRES]
    return conso, ges


def _classe(valeur: float | None, seuils: list[float]) -> str:
    """
    Lettre correspondant à une valeur, selon des seuils supérieurs croissants.

    Une valeur nulle signifie que le moteur n'a pas tourné (saisie incomplète),
    pas un bâtiment exemplaire : on ne renvoie surtout pas « A », qui serait
    trompeur dans le rapport et dans l'IHM de confirmation.
    """
    if valeur is None or not valeur or not seuils:
        return "—"
    for lettre, borne in zip(_LETTRES, seuils):
        if borne and valeur <= borne:
            return lettre
    return _LETTRES[-1]


# ── Lot de calcul et sorties moteur ──────────────────────────────────────────
def _lots(src, id_mission: int) -> list[dict]:
    dpe = src.resolve_db(DB_DPE)
    return _select(src, dpe, "XDPEsaisieLot",
                   "idMission = @id", {"id": int(id_mission)})


def _lot_principal(lots: list[dict]) -> dict | None:
    """
    Lot représentant l'état actuel du bien. Les autres lots portent les
    scénarios de travaux (clés de bouquet ESS / PRV / PCK).
    """
    if not lots:
        return None
    sans_bouquet = [l for l in lots if not (l.get("keyBouquet") or "").strip()]
    candidats = sans_bouquet or lots
    for l in candidats:
        if "actuel" in (l.get("libelleSaisieLot") or "").lower():
            return l
    return candidats[0]


def _detail_calcul(src, id_saisie_lot: int) -> dict:
    """
    Grandeurs intermédiaires du moteur pour un lot (déperditions, GV,
    consommations ramenées au m²). Vide tant que le calcul n'a pas tourné.
    """
    rows = _select(src, src.resolve_db(DB_DPE), "XDPEdetailCalcul",
                   "idSaisieLot = @id", {"id": int(id_saisie_lot)})
    return rows[0] if rows else {}


def _sortie_moteur(src, id_saisie_lot: int) -> dict | None:
    """
    Dernière sortie du moteur de calcul pour un lot. ADN conserve
    l'historique ; `isDepensier` distingue le scénario « occupant dépensier »
    du calcul conventionnel, c'est ce dernier qui porte l'étiquette.
    """
    dpe = src.resolve_db(DB_DPE)
    rows = _select(src, dpe, "XDPEsortieMoteur",
                   "idSaisieLot = @id", {"id": int(id_saisie_lot)})
    if not rows:
        return None
    conventionnels = [r for r in rows if not r.get("isDepensier")]
    return (conventionnels or rows)[-1]


# ── Résumé (même contrat que liciel.parse_dpe_summary) ───────────────────────
def parse_dpe_summary(src, dossier: dict, mission: dict) -> dict:
    """
    Résumé d'une mission DPE, au format attendu par l'IHM de confirmation,
    le rapport e-mail et l'API Opticheck.
    """
    dpe = src.resolve_db(DB_DPE)
    entete = _select(src, dpe, "XDPEdossierDPE",
                     "idMission = @id", {"id": int(mission["idMission"])})
    entete = entete[0] if entete else {}
    logement = _select(src, dpe, "XDPEdetailInformationLogement",
                       "idMission = @id", {"id": int(mission["idMission"])})
    logement = logement[0] if logement else {}

    lots = _lots(src, mission["idMission"])
    lot = _lot_principal(lots)
    sortie = _sortie_moteur(src, lot["idSaisieLot"]) if lot else None

    surface = logement.get("surfaceHabitable") or dossier.get("surface")
    type_dossier = (entete.get("idTypeDossierDPE")
                    or mission.get("idTypeMission") or "")
    conso_seuils, ges_seuils = _seuils(src, type_dossier)

    # L'étiquette se lit au m². Attention : `XDPEsortieMoteur.Ctotal` est une
    # consommation annuelle totale en kWh_ep, alors que `carboneTotal` est
    # déjà ramené au m². La valeur au m² est portée par
    # `XDPEdetailCalcul.consommationAnnuelleEPParm2` ; à défaut, on divise.
    calcul = _detail_calcul(src, lot["idSaisieLot"]) if lot else {}
    conso = _num(calcul.get("consommationAnnuelleEPParm2")) if calcul else None
    if conso is None and sortie:
        total = _num(sortie.get("Ctotal"))
        s = _num(surface)
        conso = (total / s) if (total and s) else None
    co2 = _num(sortie.get("carboneTotal")) if sortie else None
    if co2 is None:
        co2 = _num(calcul.get("emissionsGESAnnuelleParm2")) if calcul else None
    cout = _num(sortie.get("coutTotal")) if sortie else None

    # Un DPE dont le moteur 3CL n'a pas tourné n'a pas d'étiquette : ni classe,
    # ni consommation. Analys'immo laisse alors ces colonnes à zéro plutôt qu'à
    # NULL, d'où le test sur la consommation totale et non sur leur présence.
    calcul_effectue = bool(_num(sortie.get("Ctotal")) if sortie else None)
    if not calcul_effectue:
        conso = co2 = cout = None

    inters = interlocuteurs(src, dossier["idDossier"])
    donneur = inters.get(ROLE_DONNEUR_ORDRE) or inters.get(ROLE_PROPRIETAIRE) or {}

    adresse = " ".join(x for x in (
        (dossier.get("adresse") or "").strip(),
        (dossier.get("codePostal") or "").strip(),
        (dossier.get("ville") or "").strip()) if x)

    numero = (entete.get("refADEME") or entete.get("refAdemeXml")
              or entete.get("refTempADEME") or "")

    return {
        "source": "Analysimmo",
        "dossier": (dossier.get("reference") or f"dossier-{dossier['idDossier']}"),
        "annee": _annee(mission.get("dateRdv") or dossier.get("dateCommande")
                        or dossier.get("dateMaj")),
        "has_dpe": True,
        "donneur_ordre": _nom_complet(donneur) or "—",
        "donneur_ordre_details": {
            k: v for k, v in donneur.items()
            if k in ("titre", "nom", "prenom", "adresse1", "adresse2",
                     "codePostal", "ville", "telephoneFixe", "telephoneMobile",
                     "email", "siret") and v
        },
        "adresse": adresse or "—",
        "classe_energie": _classe(conso, conso_seuils),
        "classe_co2": _classe(co2, ges_seuils),
        "consommation": _fmt(conso),
        "co2_valeur": _fmt(co2),
        "surface": _fmt(surface),
        "cout_annuel": _fmt(cout),
        "methode": entete.get("methode") or mission.get("idCategorieMission") or "—",
        "numero_ademe": numero or "—",
        "calcul_effectue": calcul_effectue,
        # Spécifique ADN : de quoi situer la mission sans ambiguïté.
        "adn": {
            "id_dossier": dossier["idDossier"],
            "id_mission": mission["idMission"],
            "type_mission": mission.get("idTypeMission"),
            "categorie_mission": mission.get("idCategorieMission"),
            "intitule": mission.get("intitule"),
            "statut_mission": mission.get("statut"),
            "mission_finie": bool(mission.get("isFini")),
            "type_dossier_dpe": type_dossier,
            "transmis_ademe": bool(numero),
            "calcul_disponible": sortie is not None and conso not in (None, 0),
        },
    }


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _fmt(v) -> str:
    n = _num(v)
    if n is None:
        return "—"
    return str(int(n)) if float(n).is_integer() else f"{n:.2f}".rstrip("0").rstrip(".")


def _annee(value) -> str:
    if not value:
        return "—"
    m = re.search(r"(\d{4})", str(value))
    return m.group(1) if m else "—"


# ── Charge utile complète ────────────────────────────────────────────────────
def read_dpe(src, dossier: dict, mission: dict) -> dict:
    """
    Extraction complète d'une mission DPE : toutes les lignes de saisie et les
    sorties de calcul, table par table. C'est cette charge utile qui permet à
    Opticheck d'analyser le DPE avant validation.
    """
    dpe = src.resolve_db(DB_DPE)
    id_mission = int(mission["idMission"])

    payload: dict = {
        "meta": {
            "source": "Analysimmo (ADN)",
            "extrait_le": datetime.now().astimezone().isoformat(timespec="seconds"),
            "moteur": getattr(src, "kind", "?"),
            "base": dpe,
        },
        "dossier": dossier,
        "mission": mission,
        "interlocuteurs": {
            str(role): inter for role, inter in
            interlocuteurs(src, dossier["idDossier"]).items()
        },
        "diagnostiqueur": diagnostiqueur(
            src, mission.get("idEmploye") or mission.get("idIntervenant")),
        "mission_tables": {},
        "lots": [],
    }

    # Niveau mission : entête DPE, informations logement, immeuble, niveaux…
    for table in _tables_with(src, dpe, "idMission"):
        if table == "XDPEsaisieLot":
            continue  # traité comme racine des lots
        rows = _select(src, dpe, table, "idMission = @id", {"id": id_mission})
        if rows:
            payload["mission_tables"][table] = rows

    # Niveau lot : enveloppe, installations, ventilation, sorties moteur…
    lot_tables = [t for t in _tables_with(src, dpe, "idSaisieLot")
                  if t != "XDPEsaisieLot"]
    principal = _lot_principal(_lots(src, id_mission))
    for lot in _lots(src, id_mission):
        bloc = {
            "lot": lot,
            "est_lot_principal": bool(principal
                                      and lot["idSaisieLot"] == principal["idSaisieLot"]),
            "tables": {},
        }
        for table in lot_tables:
            rows = _select(src, dpe, table, "idSaisieLot = @id",
                           {"id": int(lot["idSaisieLot"])})
            if rows:
                bloc["tables"][table] = rows
        # Émetteurs : rattachés au générateur, pas au lot.
        bloc["emetteurs"] = _emetteurs(src, dpe,
                                       bloc["tables"].get("XDPEdetailSaisieGenerateur", []))
        # Enveloppe : intitulé, surface et U de chaque paroi vivent dans une
        # table commune, référencée par `idDetailEnveloppe`. Sans elle, la
        # charge utile perdrait les libellés du diagnostiqueur et les surfaces.
        bloc["enveloppe_details"] = _details_enveloppe(src, dpe, bloc["tables"])
        payload["lots"].append(bloc)

    payload["meta"]["volumetrie"] = _volumetrie(payload)
    return payload


def _emetteurs(src, dpe: str, generateurs: list[dict]) -> list[dict]:
    """
    Émetteurs de chaleur, joints aux générateurs via `XDPEEmetteurGenerateur`.
    Ils ne portent pas d'`idSaisieLot` et échappent donc à l'extraction par lot.
    """
    ids = [g.get("idSaisieGenerateur") for g in generateurs
           if g.get("idSaisieGenerateur")]
    if not ids:
        return []
    liens = src.query(
        "SELECT idSaisieGenerateur, idSaisieEmetteur FROM XDPEEmetteurGenerateur "
        f"WHERE idSaisieGenerateur IN ({_in_list(ids)})", database=dpe)
    em_ids = [l["idSaisieEmetteur"] for l in liens if l.get("idSaisieEmetteur")]
    if not em_ids:
        return []
    rows = _select(src, dpe, "XDPEdetailSaisieEmetteur",
                   f"idSaisieEmetteur IN ({_in_list(em_ids)})", {})
    par_emetteur = {l["idSaisieEmetteur"]: l["idSaisieGenerateur"] for l in liens}
    for r in rows:
        r["_idSaisieGenerateur"] = par_emetteur.get(r.get("idSaisieEmetteur"))
    return rows


def _details_enveloppe(src, dpe: str, tables: dict) -> dict:
    """
    Lignes `XDPEdetailEnveloppe` des parois du lot, indexées par
    `idDetailEnveloppe`. Cette table porte le libellé saisi, la surface et le
    coefficient U de chaque mur, plafond, plancher, porte et fenêtre ; les
    tables `XDPEdetailSaisieEnv*` n'en gardent qu'une référence.
    """
    ids = set()
    for table, rows in tables.items():
        if not table.startswith("XDPEdetailSaisieEnv"):
            continue
        for r in rows:
            if r.get("idDetailEnveloppe"):
                ids.add(r["idDetailEnveloppe"])
    if not ids:
        return {}
    rows = _select(src, dpe, "XDPEdetailEnveloppe",
                   f"idDetailEnveloppe IN ({_in_list(sorted(ids))})", {})
    return {str(r["idDetailEnveloppe"]): r for r in rows}


def _volumetrie(payload: dict) -> dict:
    """Compte des éléments extraits — sert de contrôle de complétude."""
    def n(lot, table):
        return len(lot["tables"].get(table, []))

    total = {"lots": len(payload["lots"]), "murs": 0, "fenetres": 0,
             "plafonds": 0, "planchers": 0, "portes": 0, "ponts_thermiques": 0,
             "generateurs": 0, "emetteurs": 0, "ventilations": 0,
             "climatisations": 0, "sorties_moteur": 0}
    for lot in payload["lots"]:
        total["murs"] += n(lot, "XDPEdetailSaisieEnvMur")
        total["fenetres"] += n(lot, "XDPEdetailSaisieEnvFenetre")
        total["plafonds"] += n(lot, "XDPEdetailSaisieEnvPlafond")
        total["planchers"] += n(lot, "XDPEdetailSaisieEnvPlancher")
        total["portes"] += n(lot, "XDPEdetailSaisieEnvPorte")
        total["ponts_thermiques"] += n(lot, "XDPEdetailPontThermique")
        total["generateurs"] += n(lot, "XDPEdetailSaisieGenerateur")
        total["ventilations"] += n(lot, "XDPEdetailVentilation")
        total["climatisations"] += n(lot, "XDPEdetailClimatisation")
        total["sorties_moteur"] += n(lot, "XDPEsortieMoteur")
        total["emetteurs"] += len(lot.get("emetteurs") or [])
    return total


# ── Façade pratique ─────────────────────────────────────────────────────────
def open_source(cfg: dict | None = None):
    """Ouvre la source Analys'immo, en traduisant l'échec en `AdnError`."""
    try:
        return adn_db.connect(cfg or {})
    except adn_db.AdnUnavailable as e:
        raise AdnError(str(e)) from e


def dossiers_avec_resume(src, limit: int = 30) -> list[dict]:
    """
    Dossiers DPE récents, chacun accompagné de son résumé et de la mission
    retenue. Alimente la liste de sélection de l'IHM.
    """
    out = []
    for d in list_dossiers(src, limit=limit):
        missions = get_dpe_missions(src, d["idDossier"])
        if not missions:
            continue
        m = missions[0]
        summary = parse_dpe_summary(src, d, m)
        # `path` est la clé d'identification des lignes dans la fenêtre de
        # sélection (dialog.show_dossier_selection_dialog). Un DPE Analys'immo
        # n'a pas de dossier sur le disque : on fabrique un identifiant stable
        # à partir du couple dossier / mission.
        summary["path"] = f"ADN:{d['idDossier']}/{m['idMission']}"
        summary["_dossier_row"] = d
        summary["_mission_row"] = m
        out.append(summary)
    return out
