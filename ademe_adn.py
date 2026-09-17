"""
Reconstruction du XML DPE au format ADEME (modèle DPE_complet, schéma 2021)
depuis les bases Analys'immo — l'équivalent d'`ademe_rebuild.py`, côté ADN.

Objectif : qu'Opticheck reçoive d'un poste Analys'immo **le même type de
document** que d'un poste LICIEL, et n'ait donc rien à apprendre de nouveau.
La structure produite ici est celle de `ademe_rebuild.build_dpe()` ; seule la
source des données change.

── Comment les identifiants ADEME sont obtenus ──────────────────────────────
Analys'immo ne stocke pas les identifiants d'énumération ADEME dans les lignes
de saisie : il y met ses propres identifiants de référentiel
(`idEnumereTypeMur`, `idEnumereCORmur`…). La correspondance vit dans les tables
`XDPEenumere*`, qui portent deux colonnes clés sur leurs lignes `xDpe = 2021` :

  - `idLib` → l'identifiant d'énumération ADEME (`enum_*_id`) ;
  - `tvWB`  → l'identifiant de table de valeurs ADEME (`tv_*_id`).

Vérifié sur les murs : `idLib = 1` donne « Inconnu », `13` « Béton banché », et
la numérotation n'est ni contiguë ni dans l'ordre d'Analys'immo — c'est bien un
identifiant externe. Toutes les résolutions passent par `Ctx.enum()` /
`Ctx.tv()`, de sorte qu'un identifiant non mappé est **signalé** dans le
rapport de couverture au lieu d'être inventé.

── Ce qui n'est pas couvert ─────────────────────────────────────────────────
Le rapport renvoyé par `build_dpe()` liste les champs laissés vides et les
identifiants non résolus. C'est volontaire : mieux vaut un document
incomplet et mesuré qu'un document faux.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import adn
from adn_db import DB_DPE

_XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", _XSI)
ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")

NIL = object()  # sentinelle : émettre le champ avec xsi:nil="true"

# Version du modèle ADEME produit (bloc administratif/enum_version_id).
ENUM_VERSION_ID = "2"

# Colonne de saisie → (table de référentiel, clé primaire de ce référentiel).
# Les clés primaires sont celles réellement déclarées par Analys'immo : elles
# ne suivent pas de convention (idFermeture, idVitrage, idTypeEmetteur…).
REFERENTIELS = {
    "idEnumereTypeMur":       ("XDPEenumereTypeMur", "idEnumereTypeMur"),
    "idEnumereCORmur":        ("XDPEenumereCORmur", "idEnumereCORmur"),
    "idEnumerCORPlafond":     ("XDPEenumereCORPlafond", "idEnumerCORPlafond"),
    "idEnumereCORsol":        ("XDPEenumereCORsol", "idEnumereCORsol"),
    "idEnumereCorBaie":       ("XDPEenumereCorBaie", "idEnumereCorBaie"),
    "idEnumereCorMur":        ("XDPEenumereCORmur", "idEnumereCORmur"),
    "idEnumereUmur0":         ("XDPEenumereUmur0", "idEnumereUmur0"),
    "idEnumereUplafond0":     ("XDPEenumereUplafond0", "idEnumereUplafond0"),
    "idEnumereUplancher0":    ("XDPEenumereUplancher0", "idEnumereUplancher0"),
    "idEnumereUporte":        ("XDPEenumereUporte", "idEnumereUporte"),
    "idEnumereVentilation":   ("XDPEenumereVentilation", "idEnumereVentilation"),
    "idEnumereCombustible":   ("XDPEEnumereCombustible", "idEnumereCombustible"),
    "idVitrage":              ("XDPEenumereBaieVitrage", "idVitrage"),
    "idParoiVitree":          ("XDPEenumereBaieParoiVitree", "idParoiVitree"),
    "idMenuiserie":           ("XDPEenumereBaieMenuiserie", "idMenuiserie"),
    "idFermeture":            ("XDPEenumereBaieFermeture", "idFermeture"),
    "idUg":                   ("XDPEenumereTvwbBaieUg", "idtvWB"),
    "idUw":                   ("XDPEenumereBaieUw", "idEnumereBaieUw"),
    "idUjn":                  ("XDPEenumereBaieUjn", "idEnumereBaieUjn"),
    "idTypeEmetteur":         ("XDPEenumereTypeEmetteur", "idTypeEmetteur"),
    "idEnumereFicheTechnique": ("XDPEenumereFicheTechnique", "idEnumereFicheTechnique"),
    "idInclinaison":          ("XDPEenumereInclinaisonParoi", "idInclinaison"),
    "idInclPpv":              ("XDPEenumereInclinaisonCapteur", "idInclPpv"),
    "idEnumereUporte":        ("XDPEenumereUporte", "idEnumereUporte"),
    "idInter":                ("XDPEenumereEquipementIntermittence", "idInter"),
    "idEnumereReseauDistribution": ("XDPEenumereReseauDistribution",
                                    "idEnumereReseauDistribution"),
    "idRd":                   ("XDPEenumereRendementDistributionECS", "idRd"),
    "idInstall":              ("XDPEenumereInstalltionChauffage", "idInstallation"),
}

# Colonnes portant l'identifiant de table de valeurs, par ordre de préférence,
# quand ce n'est pas simplement `tvWB`.
TV_COLONNES = {
    "idEnumereVentilation": ("tvQ4paConv", "tvDebit", "tvWB"),
}

# Un même référentiel alimente parfois plusieurs champs ADEME, chacun par une
# colonne différente : la clé est alors le champ, non le référentiel.
TV_COLONNES_CHAMP = {
    "ventilation/tv_q4pa_conv_id": ("tvQ4paConv", "tvWB"),
    "ventilation/tv_debits_ventilation_id": ("tvDebit",),
    "emetteur_chauffage/tv_rendement_distribution_ch_id": ("tvIsole", "tv"),
    "emetteur_chauffage/tv_rendement_emission_id": ("tvWB",),
    "emetteur_chauffage/enum_type_emission_distribution_id": ("tvWB",),
}

# Énumérations qu'Analys'immo ne traduit pas dans ses référentiels : la
# correspondance est faite ici depuis le barème ADEME officiel, qui est public
# et stable. C'est plus robuste que de suivre des identifiants internes.

# enum_orientation_id : 1 Nord, 2 Est, 3 Ouest, 4 Sud, 5 Horizontal.
# enum_orientation_id du modèle ADEME : 1 sud, 2 nord, 3 est, 4 ouest,
# 5 horizontal. L'ordre n'est pas celui de la rose des vents, d'où l'écriture
# explicite — une correspondance décalée orienterait chaque paroi à l'envers et
# fausserait les apports solaires.
ORIENTATION_ADEME = {"sud": "1", "nord": "2", "est": "3", "ouest": "4",
                     "horizontal": "5"}

# enum_zone_climatique_id, depuis la zone en clair d'Analys'immo (colonne ZC
# de XDPEdptClimat : « H1a », « H2b »…).
ZONE_CLIMATIQUE = {"h1a": "1", "h1b": "2", "h1c": "3", "h2a": "4",
                   "h2b": "5", "h2c": "6", "h2d": "7", "h3": "8"}

# enum_classe_inertie_id : 1 Très lourde, 2 Lourde, 3 Moyenne, 4 Légère.
# Analys'immo code l'inertie par une clé littérale (TL / L / M / LE).
INERTIE_ADEME = {"tl": "1", "l": "2", "m": "3", "le": "4"}

# enum_periode_construction_id, dérivé de l'année de construction.
# Bornes du barème ADEME 2021 (borne supérieure incluse).
PERIODES_CONSTRUCTION = ((1947, "1"), (1974, "2"), (1977, "3"), (1982, "4"),
                         (1988, "5"), (2000, "6"), (2005, "7"), (2012, "8"),
                         (2021, "9"))
PERIODE_APRES = "10"

# enum_classe_altitude_id : 1 < 400 m, 2 de 400 à 800 m, 3 > 800 m.
def positif(v, n: int = 3):
    """
    Valeur physique facultative : émise seulement si elle est strictement
    positive. Un rendement ou une puissance à zéro n'est pas une mesure mais
    une absence — Analys'immo laisse ces colonnes à 0 quand il ne les calcule
    pas. Les transmettre telles quelles fait diviser par zéro le moteur
    d'Opticheck, qui renvoie alors des consommations infinies.
    """
    f = num(v)
    return rnd(f, n) if (f and f > 0) else None


def _wh_en_kwh(valeur):
    """Wh → kWh. Renvoie None si la valeur est absente ou nulle."""
    v = num(valeur)
    return (v / 1000.0) if v else None


def _annee_installation(row: dict):
    """Année d'installation d'un générateur, depuis ses colonnes de date."""
    for champ in ("anneeInstallation", "dateFabrication", "dateInstallation"):
        v = row.get(champ)
        if v in (None, ""):
            continue
        m = re.search(r"(19|20)\d{2}", str(v))
        if m:
            return int(m.group(0))
    return None


def _classe_altitude(altitude) -> str | None:
    a = num(altitude)
    if a is None:
        return None
    return "1" if a < 400 else ("2" if a <= 800 else "3")


# ── Ordre des éléments imposé par le modèle ADEME ────────────────────────────
# Le schéma décrit chaque bloc comme une *séquence* : l'ordre des éléments fait
# partie du contrat. Cette table a été extraite des classes générées depuis le
# XSD (namespace ImportAdemeDPEv22 des assemblys Analys'immo), puis figée ici
# pour que la passerelle n'ait aucune dépendance à ADN au moment de produire le
# document. Les clés « bloc.donnee_entree » distinguent les sous-blocs
# homonymes d'un élément à l'autre.
ORDRE_MODELE = {
    "dpe": (
        "administratif", "logement", "dpe_immeuble",
        "descriptif_enr_collection", "descriptif_simplifie_collection",
        "fiche_technique_collection", "justificatif_collection",
        "descriptif_geste_entretien_collection", "descriptif_travaux",
        "hashkey", "id", "version"
    ),
    "administratif": (
        "dpe_a_remplacer", "motif_remplacement", "dpe_immeuble_associe",
        "enum_version_id", "date_visite_diagnostiqueur",
        "nom_proprietaire", "siren_proprietaire",
        "nom_proprietaire_installation_commune",
        "date_etablissement_dpe", "enum_modele_dpe_id",
        "diagnostiqueur", "geolocalisation"
    ),
    "diagnostiqueur": (
        "usr_logiciel_id", "version_logiciel", "version_moteur_calcul",
        "nom_diagnostiqueur", "prenom_diagnostiqueur",
        "mail_diagnostiqueur", "telephone_diagnostiqueur",
        "adresse_diagnostiqueur", "entreprise_diagnostiqueur",
        "numero_certification_diagnostiqueur", "organisme_certificateur"
    ),
    "geolocalisation": (
        "invar_logement", "numero_fiscal_local", "rpls_log_id",
        "rpls_org_id", "idpar", "immatriculation_copropriete",
        "adresses"
    ),
    "adresses": (
        "adresse_bien", "adresse_proprietaire",
        "adresse_proprietaire_installation_commune"
    ),
    "t_adresse": (
        "adresse_brut", "code_postal_brut", "nom_commune_brut",
        "label_brut", "label_brut_avec_complement",
        "enum_statut_geocodage_ban_id", "ban_date_appel", "ban_id",
        "ban_label", "ban_housenumber", "ban_street", "ban_citycode",
        "ban_postcode", "ban_city", "ban_type", "ban_score", "ban_x",
        "ban_y", "compl_nom_residence", "compl_ref_batiment",
        "compl_etage_appartement", "compl_ref_cage_escalier",
        "compl_ref_logement"
    ),
    "logement": (
        "caracteristique_generale", "meteo", "enveloppe",
        "ventilation_collection", "climatisation_collection",
        "production_elec_enr", "installation_ecs_collection",
        "installation_chauffage_collection", "sortie"
    ),
    "caracteristique_generale": (
        "annee_construction", "enum_periode_construction_id",
        "enum_methode_application_dpe_log_id",
        "surface_habitable_logement", "nombre_niveau_immeuble",
        "nombre_niveau_logement", "hsp", "surface_habitable_immeuble",
        "surface_tertiaire_immeuble", "nombre_appartement",
        "appartement_non_visite"
    ),
    "meteo": (
        "enum_zone_climatique_id", "enum_classe_altitude_id",
        "batiment_materiaux_anciens"
    ),
    "enveloppe": (
        "inertie", "mur_collection", "plancher_bas_collection",
        "plancher_haut_collection", "baie_vitree_collection",
        "porte_collection", "ets_collection",
        "pont_thermique_collection"
    ),
    "inertie": (
        "inertie_plancher_bas_lourd", "inertie_plancher_haut_lourd",
        "inertie_paroi_verticale_lourd", "enum_classe_inertie_id"
    ),
    "mur.donnee_entree": (
        "description", "reference", "reference_lnc",
        "tv_coef_reduction_deperdition_id", "surface_aiu",
        "surface_aue", "enum_cfg_isolation_lnc_id",
        "enum_type_adjacence_id", "enum_orientation_id",
        "surface_paroi_totale", "surface_paroi_opaque", "paroi_lourde",
        "umur0_saisi", "tv_umur0_id", "epaisseur_structure",
        "enum_materiaux_structure_mur_id", "enum_methode_saisie_u0_id",
        "paroi_ancienne", "enduit_isolant_paroi_ancienne", "umur_saisi",
        "enum_type_doublage_id", "enum_type_isolation_id",
        "enum_periode_isolation_id", "resistance_isolation",
        "epaisseur_isolation", "tv_umur_id", "enum_methode_saisie_u_id"
    ),
    "mur.donnee_intermediaire": (
        "b", "umur", "umur0"
    ),
    "plancher_bas.donnee_entree": (
        "description", "reference", "reference_lnc",
        "tv_coef_reduction_deperdition_id", "surface_aiu",
        "surface_aue", "enum_cfg_isolation_lnc_id",
        "enum_type_adjacence_id", "surface_paroi_opaque", "upb0_saisi",
        "tv_upb0_id", "enum_type_plancher_bas_id",
        "enum_methode_saisie_u0_id", "upb_saisi",
        "enum_type_isolation_id", "enum_periode_isolation_id",
        "resistance_isolation", "epaisseur_isolation", "tv_upb_id",
        "enum_methode_saisie_u_id", "calcul_ue", "paroi_lourde",
        "perimetre_ue", "surface_ue", "ue"
    ),
    "plancher_bas.donnee_intermediaire": (
        "b", "upb", "upb_final", "upb0"
    ),
    "plancher_haut.donnee_entree": (
        "description", "reference", "reference_lnc",
        "tv_coef_reduction_deperdition_id", "surface_aiu",
        "surface_aue", "enum_cfg_isolation_lnc_id",
        "enum_type_adjacence_id", "surface_paroi_opaque",
        "paroi_lourde", "uph0_saisi", "tv_uph0_id",
        "enum_type_plancher_haut_id", "enum_methode_saisie_u0_id",
        "uph_saisi", "enum_type_isolation_id",
        "enum_periode_isolation_id", "resistance_isolation",
        "epaisseur_isolation", "tv_uph_id", "enum_methode_saisie_u_id"
    ),
    "plancher_haut.donnee_intermediaire": (
        "b", "uph", "uph0"
    ),
    "baie_vitree.donnee_entree": (
        "description", "reference", "reference_paroi", "reference_lnc",
        "tv_coef_reduction_deperdition_id", "surface_aiu",
        "surface_aue", "enum_cfg_isolation_lnc_id",
        "enum_type_adjacence_id", "surface_totale_baie", "nb_baie",
        "tv_ug_id", "enum_type_vitrage_id",
        "enum_inclinaison_vitrage_id", "enum_type_gaz_lame_id",
        "epaisseur_lame", "presence_protection_solaire_hors_fermeture",
        "vitrage_vir", "presence_joint",
        "enum_methode_saisie_perf_vitrage_id", "ug_saisi", "tv_uw_id",
        "enum_type_materiaux_menuiserie_id", "enum_type_baie_id",
        "uw_saisi", "double_fenetre", "uw_1", "sw_1", "uw_2", "sw_2",
        "tv_deltar_id", "tv_ujn_id", "enum_type_fermeture_id",
        "ujn_saisi", "presence_retour_isolation", "largeur_dormant",
        "tv_sw_id", "sw_saisi", "enum_type_pose_id",
        "enum_orientation_id", "tv_coef_masque_proche_id",
        "tv_coef_masque_lointain_homogene_id",
        "masque_lointain_non_homogene_collection"
    ),
    "baie_vitree.donnee_intermediaire": (
        "b", "ug", "uw", "ujn", "u_menuiserie", "sw", "fe1", "fe2"
    ),
    "porte.donnee_entree": (
        "description", "reference", "reference_paroi", "reference_lnc",
        "enum_cfg_isolation_lnc_id", "enum_type_adjacence_id",
        "tv_coef_reduction_deperdition_id", "surface_aiu",
        "surface_aue", "surface_porte", "tv_uporte_id",
        "enum_methode_saisie_uporte_id", "enum_type_porte_id",
        "uporte_saisi", "nb_porte", "largeur_dormant",
        "presence_retour_isolation", "enum_type_pose_id"
    ),
    "porte.donnee_intermediaire": (
        "uporte", "b"
    ),
    "pont_thermique.donnee_entree": (
        "description", "reference", "reference_1", "reference_2",
        "tv_pont_thermique_id", "pourcentage_valeur_pont_thermique",
        "l", "enum_methode_saisie_pont_thermique_id",
        "enum_type_liaison_id", "k_saisi"
    ),
    "pont_thermique.donnee_intermediaire": (
        "k"
    ),
    "ventilation.donnee_entree": (
        "surface_ventile", "description", "reference",
        "plusieurs_facade_exposee", "tv_q4pa_conv_id",
        "q4pa_conv_saisi", "enum_methode_saisie_q4pa_conv_id",
        "tv_debits_ventilation_id", "enum_type_ventilation_id",
        "ventilation_post_2012", "ref_produit_ventilation",
        "cle_repartition_ventilation"
    ),
    "ventilation.donnee_intermediaire": (
        "pvent_moy", "q4pa_conv", "conso_auxiliaire_ventilation",
        "hperm", "hvent"
    ),
    "climatisation.donnee_entree": (
        "description", "reference", "surface_clim",
        "nombre_logement_echantillon", "cle_repartition_clim",
        "tv_seer_id", "seer_saisi", "enum_type_energie_id",
        "enum_periode_installation_fr_id", "enum_type_generateur_fr_id",
        "enum_methode_calcul_conso_id", "enum_methode_saisie_carac_sys_id"
    ),
    "climatisation.donnee_intermediaire": (
        "eer", "besoin_fr", "besoin_fr_depensier", "conso_fr",
        "conso_fr_depensier"
    ),
    "production_elec_enr": (
        "donnee_entree", "donnee_intermediaire", "panneaux_pv_collection"
    ),
    "production_elec_enr.donnee_entree": (
        "presence_production_pv", "enum_type_enr_id"
    ),
    "production_elec_enr.donnee_intermediaire": (
        "production_pv", "taux_autoproduction", "conso_elec_ac"
    ),
    "panneaux_pv": (
        "surface_totale_capteurs", "ratio_virtualisation", "nombre_module",
        "tv_coef_orientation_pv_id", "enum_orientation_pv_id",
        "enum_inclinaison_pv_id"
    ),
    "installation_chauffage.donnee_entree": (
        "description", "reference", "surface_chauffee",
        "nombre_logement_echantillon", "rdim",
        "nombre_niveau_installation_ch", "enum_cfg_installation_ch_id",
        "ratio_virtualisation", "coef_ifc", "cle_repartition_ch",
        "enum_type_installation_id", "enum_methode_calcul_conso_id",
        "enum_methode_saisie_fact_couv_sol_id",
        "tv_facteur_couverture_solaire_id", "fch_saisi"
    ),
    "installation_chauffage.donnee_intermediaire": (
        "besoin_ch", "besoin_ch_depensier", "production_ch_solaire",
        "fch", "conso_ch", "conso_ch_depensier"
    ),
    "generateur_chauffage.donnee_entree": (
        "description", "reference", "reference_generateur_mixte",
        "ref_produit_generateur_ch", "enum_type_generateur_ch_id",
        "enum_usage_generateur_id", "enum_type_energie_id",
        "position_volume_chauffe", "tv_rendement_generation_id",
        "tv_scop_id", "tv_temp_fonc_100_id", "tv_temp_fonc_30_id",
        "tv_generateur_combustion_id", "tv_reseau_chaleur_id",
        "identifiant_reseau_chaleur", "n_radiateurs_gaz",
        "priorite_generateur_cascade", "presence_ventouse",
        "presence_regulation_combustion",
        "enum_methode_saisie_carac_sys_id",
        "enum_lien_generateur_emetteur_id", "date_arrete_reseau_chaleur"
    ),
    "generateur_chauffage.donnee_intermediaire": (
        "scop", "pn", "qp0", "pveilleuse", "temp_fonc_30",
        "temp_fonc_100", "rpn", "rpint", "rendement_generation",
        "conso_ch", "conso_ch_depensier"
    ),
    "emetteur_chauffage.donnee_entree": (
        "description", "reference", "surface_chauffee",
        "tv_rendement_emission_id", "tv_rendement_distribution_ch_id",
        "tv_rendement_regulation_id",
        "enum_type_emission_distribution_id", "tv_intermittence_id",
        "reseau_distribution_isole", "enum_equipement_intermittence_id",
        "enum_type_regulation_id",
        "enum_periode_installation_emetteur_id",
        "enum_type_chauffage_id", "enum_temp_distribution_ch_id",
        "enum_lien_generateur_emetteur_id"
    ),
    "emetteur_chauffage.donnee_intermediaire": (
        "i0", "rendement_emission", "rendement_distribution",
        "rendement_regulation"
    ),
    "installation_ecs.donnee_entree": (
        "description", "reference", "enum_cfg_installation_ecs_id",
        "enum_type_installation_id", "enum_methode_calcul_conso_id",
        "ratio_virtualisation", "cle_repartition_ecs",
        "surface_habitable", "nombre_logement", "rdim",
        "nombre_niveau_installation_ecs", "fecs_saisi",
        "tv_facteur_couverture_solaire_id",
        "enum_methode_saisie_fact_couv_sol_id",
        "enum_type_installation_solaire_id",
        "tv_rendement_distribution_ecs_id",
        "enum_bouclage_reseau_ecs_id", "reseau_distribution_isole",
        "date_arrete_reseau_chaleur"
    ),
    "installation_ecs.donnee_intermediaire": (
        "rendement_distribution", "besoin_ecs", "besoin_ecs_depensier",
        "fecs", "production_ecs_solaire", "conso_ecs",
        "conso_ecs_depensier"
    ),
    "generateur_ecs.donnee_entree": (
        "description", "reference", "reference_generateur_mixte",
        "enum_type_generateur_ecs_id", "ref_produit_generateur_ecs",
        "enum_usage_generateur_id", "enum_type_energie_id",
        "tv_generateur_combustion_id",
        "enum_methode_saisie_carac_sys_id", "tv_pertes_stockage_id",
        "tv_scop_id", "enum_periode_installation_ecs_thermo_id",
        "identifiant_reseau_chaleur", "tv_reseau_chaleur_id",
        "enum_type_stockage_ecs_id", "position_volume_chauffe",
        "position_volume_chauffe_stockage", "volume_stockage",
        "presence_ventouse"
    ),
    "generateur_ecs.donnee_intermediaire": (
        "pn", "qp0", "pveilleuse", "rpn", "cop", "ratio_besoin_ecs",
        "rendement_generation", "rendement_generation_stockage",
        "conso_ecs", "conso_ecs_depensier", "rendement_stockage"
    ),
    "installation_chauffage": (
        "donnee_entree", "donnee_intermediaire",
        "emetteur_chauffage_collection", "generateur_chauffage_collection"
    ),
    "installation_ecs": (
        "donnee_entree", "donnee_intermediaire", "generateur_ecs_collection"
    ),
    "confort_ete": (
        "isolation_toiture", "protection_solaire_exterieure",
        "aspect_traversant", "brasseur_air", "inertie_lourde",
        "enum_indicateur_confort_ete_id"
    ),
    "fiche_technique": (
        "enum_categorie_fiche_technique_id", "sous_fiche_technique_collection"
    ),
    "sous_fiche_technique": (
        "description", "valeur", "detail_origine_donnee",
        "enum_origine_donnee_id"
    ),
    "sortie": (
        "deperdition", "apport_et_besoin", "ef_conso", "ep_conso",
        "emission_ges", "cout", "production_electricite",
        "sortie_par_energie_collection", "confort_ete",
        "qualite_isolation"
    ),
    "deperdition": (
        "hvent", "hperm", "deperdition_renouvellement_air",
        "deperdition_mur", "deperdition_plancher_bas",
        "deperdition_plancher_haut", "deperdition_baie_vitree",
        "deperdition_porte", "deperdition_pont_thermique",
        "deperdition_enveloppe"
    ),
    "apport_et_besoin": (
        "surface_sud_equivalente", "apport_solaire_fr",
        "apport_interne_fr", "apport_solaire_ch", "apport_interne_ch",
        "fraction_apport_gratuit_ch",
        "fraction_apport_gratuit_depensier_ch",
        "pertes_distribution_ecs_recup",
        "pertes_distribution_ecs_recup_depensier",
        "pertes_stockage_ecs_recup", "pertes_generateur_ch_recup",
        "pertes_generateur_ch_recup_depensier", "nadeq",
        "v40_ecs_journalier", "v40_ecs_journalier_depensier",
        "besoin_ch", "besoin_ch_depensier", "besoin_ecs",
        "besoin_ecs_depensier", "besoin_fr", "besoin_fr_depensier"
    ),
    "ef_conso": (
        "conso_ch", "conso_ch_depensier", "conso_ecs",
        "conso_ecs_depensier", "conso_eclairage",
        "conso_auxiliaire_generation_ch",
        "conso_auxiliaire_generation_ch_depensier",
        "conso_auxiliaire_distribution_ch",
        "conso_auxiliaire_generation_ecs",
        "conso_auxiliaire_generation_ecs_depensier",
        "conso_auxiliaire_distribution_ecs",
        "conso_auxiliaire_distribution_fr",
        "conso_auxiliaire_ventilation", "conso_totale_auxiliaire",
        "conso_fr", "conso_fr_depensier", "conso_5_usages",
        "conso_5_usages_m2"
    ),
    "ep_conso": (
        "ep_conso_ch", "ep_conso_ch_depensier", "ep_conso_ecs",
        "ep_conso_ecs_depensier", "ep_conso_eclairage",
        "ep_conso_auxiliaire_generation_ch",
        "ep_conso_auxiliaire_generation_ch_depensier",
        "ep_conso_auxiliaire_distribution_ch",
        "ep_conso_auxiliaire_generation_ecs",
        "ep_conso_auxiliaire_generation_ecs_depensier",
        "ep_conso_auxiliaire_distribution_ecs",
        "ep_conso_auxiliaire_distribution_fr",
        "ep_conso_auxiliaire_ventilation", "ep_conso_totale_auxiliaire",
        "ep_conso_fr", "ep_conso_fr_depensier", "ep_conso_5_usages",
        "ep_conso_5_usages_m2", "classe_bilan_dpe"
    ),
    "emission_ges": (
        "emission_ges_ch", "emission_ges_ch_depensier",
        "emission_ges_ecs", "emission_ges_ecs_depensier",
        "emission_ges_eclairage",
        "emission_ges_auxiliaire_generation_ch",
        "emission_ges_auxiliaire_generation_ch_depensier",
        "emission_ges_auxiliaire_distribution_ch",
        "emission_ges_auxiliaire_generation_ecs",
        "emission_ges_auxiliaire_generation_ecs_depensier",
        "emission_ges_auxiliaire_distribution_ecs",
        "emission_ges_auxiliaire_distribution_fr",
        "emission_ges_auxiliaire_ventilation",
        "emission_ges_totale_auxiliaire", "emission_ges_fr",
        "emission_ges_fr_depensier", "emission_ges_5_usages",
        "emission_ges_5_usages_m2", "classe_emission_ges"
    ),
    "cout": (
        "cout_ch", "cout_ch_depensier", "cout_ecs",
        "cout_ecs_depensier", "cout_eclairage",
        "cout_auxiliaire_generation_ch",
        "cout_auxiliaire_generation_ch_depensier",
        "cout_auxiliaire_distribution_ch",
        "cout_auxiliaire_generation_ecs",
        "cout_auxiliaire_generation_ecs_depensier",
        "cout_auxiliaire_distribution_ecs",
        "cout_auxiliaire_distribution_fr",
        "cout_auxiliaire_ventilation", "cout_total_auxiliaire",
        "cout_fr", "cout_fr_depensier", "cout_5_usages"
    ),
    "qualite_isolation": (
        "ubat", "qualite_isol_enveloppe", "qualite_isol_mur",
        "qualite_isol_plancher_haut_toit_terrasse",
        "qualite_isol_plancher_haut_comble_perdu",
        "qualite_isol_plancher_haut_comble_amenage",
        "qualite_isol_plancher_bas", "qualite_isol_menuiserie"
    ),
}


# ── Aides valeurs ────────────────────────────────────────────────────────────
def dec(v) -> str:
    """Décimal ADEME : point décimal, pas d'espace."""
    if v is None:
        return ""
    return str(v).replace(",", ".").replace(" ", "").strip()


def num(v):
    try:
        return float(dec(v))
    except (TypeError, ValueError):
        return None


def rnd(v, n: int) -> str:
    """Arrondi à n décimales, zéros finaux supprimés."""
    f = num(v)
    if f is None:
        return ""
    s = f"{round(f, n):.{n}f}".rstrip("0").rstrip(".")
    return s or "0"


def trunc(v) -> str:
    """Entier tronqué (les surfaces *_m2 ADEME sont tronquées)."""
    f = num(v)
    return "" if f is None else str(int(f))


def bool01(v) -> str:
    return "1" if v in (True, 1, "1", "True", "true") else "0"


def iso_date(v) -> str:
    """Horodatage ADN (ISO ou jj/mm/aaaa) → aaaa-mm-jj."""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def add(parent: ET.Element, tag: str, value=None) -> ET.Element:
    """
    Ajoute <tag> à parent. `None` → élément non émis (retourne un élément
    détaché), NIL → xsi:nil="true", autre → texte.
    """
    if value is None:
        return ET.Element(tag)
    el = ET.SubElement(parent, tag)
    if value is NIL:
        el.set(f"{{{_XSI}}}nil", "true")
    elif value != "":
        el.text = str(value)
    return el


# ── Energies, postes de consommation et appreciations ────────────────────────
# Contenu CO2 par energie, en kgCO2/kWh (arrete du 31 mars 2021). Analys'immo
# ne stocke que l'emission totale : ces facteurs servent a la repartir par
# poste, la somme restant celle calculee par le moteur.
CO2_PAR_ENERGIE = {
    "ELEC": 0.079, "GAZ": 0.227, "GPL": 0.272, "FIOUL": 0.324,
    "BOIS": 0.030, "CHARBON": 0.384, "RESCHA": 0.190,
}

# Colonnes de `XDPEsortieMoteur` donnant la consommation finale d'un poste,
# ventilee par energie. Les colonnes agregees (CchBois) sont retenues plutot
# que leurs details (CchBoisBuches...) pour ne rien compter deux fois.
CONSO_PAR_ENERGIE = {
    "ch": {"CchElectricite": "ELEC", "CchAppointElec": "ELEC",
           "CchGaz": "GAZ", "CchPropane": "GPL", "Cchbutane": "GPL",
           "CchFioul": "FIOUL", "CchBois": "BOIS", "CchCharbon": "CHARBON",
           "CchRCU": "RESCHA"},
    "ecs": {"CecsElectricite": "ELEC", "CecsGaz": "GAZ",
            "CecsPropane": "GPL", "Cecsbutane": "GPL", "CecsFioul": "FIOUL",
            "CecsBois": "BOIS", "CecsCharbon": "CHARBON",
            "CecsRCU": "RESCHA"},
    "fr": {"CfrElectricite": "ELEC"},
}

# Postes toujours electriques, et leur colonne dans `XDPEsortieMoteur`.
POSTES_ELECTRIQUES = {
    "eclairage": "Ceclairage", "ventilation": "Cvent",
    "aux_gen_ch": "QauxCh", "aux_dist_ch": "CauxDistCh",
    "aux_gen_ecs": "Qauxecs", "aux_dist_ecs": "CauxDistEcs",
}

# Colonnes de cout, par poste.
COUT_PAR_POSTE = {
    "ch": ("coutGazCollCh", "coutGazIndivCh", "coutElecCh", "coutFioulCh",
           "coutPropaneCh", "coutButaneCh", "coutCharbonCh", "coutRcuCh",
           "coutBoisAutresCh", "coutBoisGranulesBriquettesCh"),
    "ecs": ("coutGazCollEcs", "coutGazIndivEcs", "coutElecEcs",
            "coutFioulEcs", "coutPropaneEcs", "coutButaneEcs",
            "coutCharbonEcs", "coutRcuECs", "coutBoisAutresEcs",
            "coutBoisGranulesBriquettesEcs"),
    "eclairage": ("coutElecEcl",),
    "ventilation": ("coutElecVent",),
    "fr": ("coutElecFr", "coutElecAuxFr"),
    "aux_ch": ("coutElecAuxCh",),
    "aux_ecs": ("coutElecAuxEcs",),
    "aux_total": ("coutElecAux", "coutElecVent"),
}

# Pompe à chaleur assurant aussi l'ECS : famille « pac double service »,
# distinguée par l'année d'installation (avant 2010, 2010-2014, après 2014).
PAC_DOUBLE_SERVICE = ((2009, 2014), ("10", "11", "12"))

# Réseaux de chaleur urbains. Analys'immo ne leur donne aucune correspondance
# ADEME : ni `XDPEdataAdeme`, ni `tvWB` exploitable — le `tvWB` du
# « combustible » porte en réalité le code du réseau au registre national
# (« 0106C »), que le modèle attend dans `identifiant_reseau_chaleur`.
# Côté énumérations, l'énergie 8 désigne le réseau urbain, et le générateur se
# décline selon que le réseau est isolé ou non : 107/108 pour le chauffage,
# 72/73 pour l'ECS. Les identifiants voisins (109-112, 142, 171 et 74-77)
# servent aux réseaux dont on ne connaît que l'énergie dominante ; ici le
# réseau est nommé, on dispose donc mieux.
# Climatisation. `XDPEenumereSystemeClimatisation.idLib` donne le premier
# identifiant ADEME de la famille ; l'énumération décline ensuite chaque
# famille par période d'installation, et c'est ce rang qu'il faut ajouter.
# Les familles thermodynamiques distinguent quatre périodes, sauf l'air/air
# qui n'en a que trois ; les systèmes « autres » n'en ont aucune.
PERIODES_CLIMATISATION = (2007, 2014, 2016)   # bornes hautes
FAMILLES_CLIMATISATION = {
    "1": 3,    # pac air/air
    "4": 4,    # pac air/eau
    "8": 4,    # pac eau/eau
    "12": 4,   # pac eau glycolée/eau
    "16": 4,   # pac géothermique
}
# enum_periode_installation_fr_id : 1 avant 2008, 2 de 2008 à 2014, 3 ensuite.
PERIODE_INSTALLATION_FR = (2007, 2014)

# Production d'électricité renouvelable : l'énumération combine les trois
# sources comme un masque de bits (photovoltaïque 1, éolien 2, cogénération 4).
ENR_PHOTOVOLTAIQUE, ENR_EOLIEN, ENR_COGENERATION = 1, 2, 4
ORIENTATION_PV_ADEME = {"E": "1", "SE": "2", "S": "3", "SW": "4", "W": "5"}

# Chaudières PAC hybrides. Analys'immo n'en donne aucune correspondance ADEME
# (`XDPEdataAdeme` est muette sur les générateurs 126, 127 et 128) alors que
# l'énumération en tient une par combustible et par tranche d'installation.
#
# L'ADEME décrit un appareil hybride comme deux générateurs — une partie pompe
# à chaleur et une partie chaudière. Analys'immo n'en tient qu'un, avec une
# seule consommation et une seule énergie : c'est la partie chaudière. On émet
# donc celle-là, la seule dont on ait les données ; la partie pompe à chaleur
# reste implicite, comme dans la saisie d'origine.
#
# Par combustible : (identifiant jusqu'en 2015, identifiant ensuite), pour le
# chauffage puis pour l'ECS.
HYBRIDE_ADEME = {
    "2": (("148", "149"), ("120", "121")),   # gaz naturel
    "3": (("160", "161"), ("132", "133")),   # GPL, propane, butane
    "4": (("150", "151"), ("122", "123")),   # fioul domestique
}
HYBRIDE_BASCULE = 2015

# Planchers et plafonds rayonnants électriques. Analys'immo en fait deux
# générateurs sans `tvWB`, là où l'ADEME n'en connaît qu'un, décliné selon la
# présence d'une régulation terminale — une information que la saisie porte sur
# l'émetteur, pas sur le générateur. C'est pour cela que le référentiel reste
# muet : la distinction ne s'y trouve pas.
RAYONNANT_ADEME = {True: "102", False: "103"}

# Installations collectives desservant plusieurs bâtiments. Analys'immo n'en
# donne aucune correspondance ADEME (générateurs 132 à 135, 143 et 144) ;
# l'ADEME les modélise comme des réseaux de chaleur dont on ne connaît que
# l'énergie dominante — c'est exactement ce que décrit le libellé
# « chaudière(s) X multi bâtiment modélisée comme un réseau de chaleur ».
#
# Par type d'énergie : (identifiant chauffage, identifiant ECS).
MULTI_BATIMENT_ADEME = {
    "1": ("112", "77"),    # pompe(s) à chaleur
    "2": ("111", "76"),    # gaz
    "4": ("110", "75"),    # fioul
    "5": ("109", "74"),    # bois
    "6": ("171", "134"),   # charbon
}

# Combustibles bois de la table TV044 : bûches, granulés, plaquettes.
COMBUSTIBLES_BOIS = {"4", "5", "6", "7"}

RESEAU_CHALEUR = re.compile(r"^\d{3,4}[CF]$")
ENERGIE_RESEAU_CHALEUR = "8"
GENERATEUR_RESEAU_CHALEUR_CH = {False: "107", True: "108"}
GENERATEUR_RESEAU_CHALEUR_ECS = {False: "72", True: "73"}

# Générateurs d'ECS : `idGenerateur` d'Analys'immo → `enum_type_generateur_ecs_id`.
# Aucun de ces neuf générateurs ne porte d'identifiant ADEME dans les
# référentiels d'Analys'immo — ni `XDPEdataAdeme`, ni `tvWB`. Les libellés se
# correspondent un pour un, la table est donc écrite ici.
# Une valeur simple = correspondance directe ; un couple (bornes, codes) = choix
# selon l'année d'installation, la dernière borne valant « après ».
GENERATEURS_ECS_ADEME = {
    "72": "68",   # chauffe-eau électrique horizontal
    "73": "69",   # chauffe-eau électrique vertical (catégorie inconnue)
    "74": ((1980, 1989, 2000, 2015), ("63", "64", "65", "66", "67")),
    "75": ((1980, 1989, 2000, 2015), ("110", "111", "112", "113", "114")),
    "76": ((1989, 2000), ("58", "59", "60")),      # accumulateur gaz classique
    "77": ((1989, 2000), ("105", "106", "107")),   # idem GPL/propane/butane
    "78": ((2000,), ("61", "62")),                 # accumulateur gaz condensation
    "79": ((2000,), ("61", "62")),
    "101": "118",  # chaudière électrique
    # 71 « chauffe-eau thermodynamique » : l'identifiant ADEME depend en plus de
    # la source d'air (ambiant, exterieur, extrait), qu'Analys'immo porte sur la
    # saisie et non sur le referentiel. Laisse non resolu et consigne.
}

# Second niveau d'adjacence d'Analys'immo (`XDPEenumereDetailCORmur.keyDetail`)
# vers `enum_type_adjacence_id`. Les libellés d'Analys'immo reprennent mot pour
# mot ceux de l'énumération ADEME, la correspondance est donc directe.
DETAIL_ADJACENCE_ADEME = {
    "CISE": "14",    # circulation sans ouverture directe sur l'extérieur
    "CIAE": "15",    # circulation avec ouverture directe sur l'extérieur
    "CIB": "16",     # circulation avec bouche ou gaine de désenfumage
    "HAF": "17",     # hall d'entrée avec fermeture automatique
    "HSF": "18",     # hall d'entrée sans fermeture automatique
    "GARPC": "19",   # garage privé collectif
    "GAR": "8",      # garage
    "CEL": "9",      # cellier
    "ADEP": "21",    # autres dépendances
    "CFOV": "11",    # comble fortement ventilé
    "CFAV": "12",    # comble faiblement ventilé
    "CTFV": "13",    # comble très faiblement ventilé
}

# Table TV040 de l'arrêté du 31 mars 2021 : rendement de distribution d'ECS.
# (identifiant ADEME, installation collective ?, rendement).
TV_RENDEMENT_DISTRIBUTION_ECS = (
    (1, False, 0.93),   # individuelle, production en volume habitable,
    (2, False, 0.87),   #   pièces alimentées contiguës puis non contiguës,
    (3, False, 0.83),   #   puis production hors volume habitable
    (4, True, 0.28),    # collective, réseau non isolé, pièces contiguës
    (5, True, 0.26),    # collective, réseau non isolé, pièces non contiguës
    (6, True, 0.55),    # collective, réseau isolé bouclé, pièces contiguës
    (7, True, 0.52),    # collective, réseau isolé bouclé, non contiguës
    (8, True, 0.83),    # collective, réseau isolé tracé ou sans bouclage
)

# Seuils d'appreciation de l'isolation (W/m2.K) : au-dela, l'isolation est
# jugee insuffisante ; en deca du dernier seuil, tres bonne.
APPRECIATIONS = ("insuffisante", "moyenne", "bonne", "tres bonne")
SEUILS_UBAT = (1.60, 1.05, 0.65)
SEUILS_UMUR = (1.50, 0.80, 0.45)
SEUILS_UPH = (1.50, 0.60, 0.30)
SEUILS_UPB = (1.50, 0.80, 0.45)
SEUILS_UMEN = (3.50, 2.50, 1.80)


def req(v, n: int = 3) -> str:
    """
    Valeur numerique obligatoire. L'ingestion refuse `xsi:nil` sur ces
    champs : quand Analys'immo n'a rien calcule, la seule ecriture recevable
    est 0. Le document reste alors refuse sur les contraintes de minimum, ce
    qui est le comportement voulu : un DPE non calcule ne doit pas passer.
    """
    return rnd(v, n) or "0"


def _appreciation(u, seuils) -> str:
    """Appreciation textuelle d'une isolation a partir de son U moyen."""
    x = num(u)
    if x is None or x <= 0:
        return APPRECIATIONS[0]
    for i, seuil in enumerate(seuils):
        if x > seuil:
            return APPRECIATIONS[i]
    return APPRECIATIONS[-1]


def _confort_ete(inertie_lourde, protection, traversant, isolation_toiture) -> str:
    """
    enum_indicateur_confort_ete_id : 1 insuffisant, 2 moyen, 3 bon. L'indicateur
    ADEME agrege inertie, protection solaire, traversant et isolation de
    toiture ; on compte les criteres favorables.
    """
    favorables = sum(bool(x) for x in
                     (inertie_lourde, protection, traversant, isolation_toiture))
    return "1" if favorables <= 1 else ("2" if favorables <= 2 else "3")


# ── Contexte : données de la mission + résolution des énumérations ───────────
# Référentiels de détail d'adjacence, par famille de paroi. Chacun a sa propre
# numérotation — l'identifiant 22 désigne une circulation commune côté mur mais
# tout autre chose côté plafond — et l'orthographe de leur clé primaire varie
# (« idEnumer… » sans « e » pour le sol et le plafond).
DETAIL_COR_FAMILLES = {
    "mur":      ("XDPEenumereDetailCORmur", "idEnumereDetailCORmur"),
    "baie":     ("XDPEenumereDetailCorBaie", "idEnumereDetailCORbaie"),
    "sol":      ("XDPEenumereDetailCORsol", "idEnumerDetailCORsol"),
    "plafond":  ("XDPEenumereDetailCORPlafond", "idEnumerDetailCORPlafond"),
}


def _detail_cor(row: dict):
    """
    Détail d'adjacence d'une paroi : (famille, identifiant).

    Chaque famille range ce détail sous un nom qui lui est propre —
    `idEnumereDetailCORmur`, `idEnumereDetailCorMur` pour les portes,
    `idEnumereDetailCorBaie`, `idEnumereDetailCORsol` — avec une casse qui
    varie d'une table à l'autre. On reconnaît la colonne à son motif plutôt
    qu'à son orthographe exacte, et son suffixe donne le référentiel à
    interroger.
    """
    for cle, valeur in row.items():
        nom = cle.lower()
        if not nom.startswith("idenumeredetailcor") or valeur in (None, ""):
            continue
        suffixe = nom[len("idenumeredetailcor"):]
        famille = "mur" if suffixe.startswith("mur") else suffixe
        return (famille if famille in DETAIL_COR_FAMILLES else "mur"), valeur
    return None, None


class Ctx:
    """
    Données Analys'immo d'une mission DPE, plus la résolution des
    identifiants ADEME. Tient le rapport de couverture.
    """

    def __init__(self, src, dossier: dict, mission: dict, cfg: dict | None = None):
        self.src = src
        self.cfg = cfg or {}
        self.dossier = dossier
        self.mission = mission
        self.dpe_db = src.resolve_db(DB_DPE)
        self.now = datetime.now()

        payload = adn.read_dpe(src, dossier, mission)
        self.payload = payload
        mt = payload["mission_tables"]
        self.entete = (mt.get("XDPEdossierDPE") or [{}])[0]
        self.logement = (mt.get("XDPEdetailInformationLogement") or [{}])[0]
        self.immeuble = (mt.get("XDPEdetailImmeuble") or [{}])[0]

        lots = payload["lots"]
        principal = next((l for l in lots if l["est_lot_principal"]), None)
        self.lot = principal or (lots[0] if lots else {"lot": {}, "tables": {}})
        self.tables = self.lot.get("tables", {})
        self.details_env = self.lot.get("enveloppe_details", {})
        self.emetteurs = self.lot.get("emetteurs", [])
        self.calcul = (self.tables.get("XDPEdetailCalcul") or [{}])[0]
        # Le moteur produit deux jeux de résultats : le scénario conventionnel,
        # qui porte l'étiquette, et le scénario « occupant dépensier ». Le
        # modèle ADEME attend les deux, champ à champ.
        sorties = self.tables.get("XDPEsortieMoteur") or []
        conv = [s for s in sorties if not s.get("isDepensier")]
        dep = [s for s in sorties if s.get("isDepensier")]
        self.sortie = (conv or sorties or [{}])[0]
        self.sortie_dep = (dep or [{}])[0]
        self.calcul_effectue = bool(num(self.sortie.get("Ctotal")))
        self.diagnostiqueur = payload.get("diagnostiqueur") or {}
        self.interlocuteurs = payload.get("interlocuteurs") or {}

        self._ref_cache: dict[tuple[str, str], dict] = {}
        self._coefs_ep: dict[str, float] | None = None
        self._energies: dict[str, str] | None = None
        self._dormants: dict[str, float] | None = None
        self._details_cor: dict[str, str] | None = None
        self._generateurs: dict[str, dict] | None = None
        self._capteurs_pv: list[dict] | None = None
        self._verandas: dict[str, dict] | None = None
        self.manquants: list[str] = []   # identifiants ADEME non résolus
        self.vides: list[str] = []       # champs laissés vides
        self.incidents: list[str] = []   # éléments abandonnés en cours de route

    # ── référentiels ────────────────────────────────────────────────────────
    def _referentiel(self, table: str, pk: str) -> dict:
        """Lignes 2021 d'un référentiel, indexées par clé primaire."""
        key = (table, pk)
        if key in self._ref_cache:
            return self._ref_cache[key]
        cols = adn._cols(self.src, self.dpe_db, table)
        wanted = [c for c in (pk, "idLib", "idLLib", "tvWB", "xDpe",
                              "idTypeEnergie", "tvQ4paConv", "tvDebit",
                              "tv", "tvIsole")
                  if c in cols]
        rows: dict = {}
        if pk in wanted:
            champs = ", ".join(f"[{c}]" for c in wanted)
            try:
                res = self.src.query(
                    f"SELECT {champs} FROM [{table}]", database=self.dpe_db)
                for r in res:
                    # Les référentiels contiennent les deux millésimes ; seules
                    # les lignes 2021 portent les identifiants du modèle actuel.
                    if "xDpe" in r and r["xDpe"] not in (None, 2021, "2021"):
                        continue
                    rows[str(r[pk])] = r
            except Exception:
                rows = {}
        self._ref_cache[key] = rows
        return rows

    def enum(self, colonne: str, valeur, champ_ademe: str = "") -> str | None:
        """
        Identifiant d'énumération ADEME (`enum_*_id`) correspondant à une
        valeur de référentiel Analys'immo. None si non résolu — l'appelant
        émet alors xsi:nil, et le manque est consigné.
        """
        if valeur in (None, ""):
            return None
        spec = REFERENTIELS.get(colonne)
        if spec is None:
            self.manquants.append(f"{champ_ademe or colonne} "
                                  f"(référentiel inconnu pour {colonne})")
            return None
        table, pk = spec
        row = self._referentiel(table, pk).get(str(valeur))
        if row is None:
            self.manquants.append(f"{champ_ademe or colonne} "
                                  f"({table}#{valeur} absent des lignes 2021)")
            return None
        for c in ("idLib", "idLLib"):
            if row.get(c) not in (None, ""):
                return str(row[c])
        # Quelques référentiels (types d'énergie) portent l'identifiant ADEME
        # dans `tvWB` plutôt que dans `idLib`.
        tvwb = str(row.get("tvWB") or "").strip()
        if tvwb.isdigit():
            return tvwb
        self.manquants.append(f"{champ_ademe or colonne} "
                              f"({table}#{valeur} sans idLib)")
        return None

    def tv(self, colonne: str, valeur, champ_ademe: str = "") -> str | None:
        """Identifiant de table de valeurs ADEME (`tv_*_id`)."""
        if valeur in (None, ""):
            return None
        spec = REFERENTIELS.get(colonne)
        if spec is None:
            self.manquants.append(f"{champ_ademe or colonne} (référentiel inconnu)")
            return None
        table, pk = spec
        row = self._referentiel(table, pk).get(str(valeur))
        cols = (TV_COLONNES_CHAMP.get(champ_ademe)
                or TV_COLONNES.get(colonne, ("tvWB",)))
        trouve = next((c for c in cols
                       if row is not None and row.get(c) not in (None, "")), None)
        if trouve is None:
            self.manquants.append(f"{champ_ademe or colonne} "
                                  f"({table}#{valeur} sans {'/'.join(cols)})")
            return None
        tvwb = str(row[trouve]).strip()
        # Les lignes 2012 portent l'ancien format « TV001_001 » : inexploitable
        # pour le modèle 2021, qui attend un entier.
        if tvwb.isdigit():
            return tvwb
        self.manquants.append(f"{champ_ademe or colonne} "
                              f"({table}#{valeur} : {trouve}={tvwb!r} au format 2012)")
        return None

    def _zone_hiver(self) -> str:
        """Zone climatique d'hiver du département (« H1 », « H2 », « H3 »)."""
        dpt = str(self.logement.get("dpt") or self.entete.get("dpt") or "").strip()
        if not dpt:
            return ""
        try:
            rows = self.src.query(
                "SELECT ZC FROM XDPEdptClimat WHERE dpt = @d",
                database=self.dpe_db, params={"d": dpt})
        except Exception:
            return ""
        return str((rows[0].get("ZC") if rows else "") or "")[:2].upper()

    def veranda(self, row: dict) -> dict:
        """Véranda associée à une paroi, ou {} si la paroi ne donne pas dessus."""
        if row.get("idVeranda") in (None, ""):
            return {}
        if self._verandas is None:
            self._verandas = {}
            try:
                for v in self.src.query(
                        "SELECT * FROM XDPESaisieVeranda WHERE idSaisieLot = @l",
                        database=self.dpe_db,
                        params={"l": int(self.lot["lot"]["idSaisieLot"])}):
                    self._verandas[str(v["idVeranda"])] = v
            except Exception:
                self._verandas = {}
        return self._verandas.get(str(row.get("idVeranda"))) or {}

    def bveranda(self, row: dict) -> dict:
        """
        Ligne du barème `XDPEenumereBveranda` pour une paroi sur véranda.

        Analys'immo ne conserve pas le coefficient b qu'il calcule ; le barème
        le redonne à partir du triplet (zone d'hiver, orientation de la véranda,
        paroi isolée ou non) et porte en prime le `Tv` ADEME de la table de
        réduction des déperditions.
        """
        v = self.veranda(row)
        if not v:
            return {}
        cle = {"1": "VERS", "2": "VEREO", "3": "VEREO", "4": "VERN"}.get(
            str(v.get("idPosition")))
        zone = self._zone_hiver()
        if not cle or not zone:
            return {}
        isole = bool(row.get("isParoiIso") or row.get("isLncISo")
                     or row.get("isIsoLnc"))
        try:
            lignes = self.src.query(
                "SELECT b, Tv, idLibIsoLnc FROM XDPEenumereBveranda "
                "WHERE ZC = @z AND keyCor = @k AND isIso = @i",
                database=self.dpe_db,
                params={"z": zone, "k": cle, "i": 1 if isole else 0})
        except Exception:
            lignes = []
        return lignes[0] if lignes else {}

    def generateur_rayonnant(self, row: dict) -> str | None:
        """Plancher ou plafond rayonnant électrique, selon sa régulation."""
        ligne = self._referentiel_generateurs().get(
            str(row.get("idGenerateur"))) or {}
        libelle = (ligne.get("libelle") or "").lower()
        if "rayonnant" not in libelle:
            return None
        if not ("plancher" in libelle or "plafond" in libelle):
            return None   # les panneaux rayonnants ont, eux, leur propre tvWB
        regule = any(
            e.get("hasRegTherm")
            for e in self.emetteurs
            if e.get("_idSaisieGenerateur") in (None,
                                                row.get("idSaisieGenerateur")))
        return RAYONNANT_ADEME[bool(regule)]

    def generateur_multi_batiment(self, row: dict) -> tuple[str | None, str | None]:
        """(chauffage, ECS) d'une installation collective multi bâtiment."""
        ligne = self._referentiel_generateurs().get(
            str(row.get("idGenerateur"))) or {}
        libelle = (ligne.get("libelle") or "").lower()
        if "multi" not in libelle or "bâtiment" not in libelle:
            return None, None
        famille = MULTI_BATIMENT_ADEME.get(str(ligne.get("idTypeEnergie")))
        if not famille:
            self.manquants.append(
                "generateur/multi bâtiment : énergie %r sans équivalent ADEME"
                % ligne.get("idTypeEnergie"))
            return None, None
        return famille

    def generateur_hybride(self, row: dict) -> tuple[str | None, str | None]:
        """(chauffage, ECS) d'une chaudière PAC hybride, ou (None, None)."""
        ligne = self._referentiel_generateurs().get(
            str(row.get("idGenerateur"))) or {}
        if "hybride" not in (ligne.get("libelle") or "").lower():
            return None, None
        famille = HYBRIDE_ADEME.get(str(ligne.get("idTypeEnergie")))
        if not famille:
            return None, None
        annee = _annee_installation(row)
        rang = 0 if (annee is not None and annee <= HYBRIDE_BASCULE) else 1
        return famille[0][rang], famille[1][rang]

    def identifiant_reseau(self, row: dict) -> str | None:
        """Code du réseau de chaleur au registre national, ou None."""
        ref = self._referentiel("XDPEEnumereCombustible", "idEnumereCombustible")
        ligne = ref.get(str(row.get("idEnumereCombustible"))) or {}
        code = str(ligne.get("tvWB") or "").strip().upper()
        return code if RESEAU_CHALEUR.match(code) else None

    def reseau_isole(self, row: dict) -> bool:
        """Isolation du réseau, portée par les émetteurs du générateur."""
        for e in self.emetteurs:
            if e.get("_idSaisieGenerateur") in (None,
                                                row.get("idSaisieGenerateur")):
                if e.get("isResIsole"):
                    return True
        return False

    def combustible_tvwb(self, row: dict) -> str | None:
        """Identifiant ADEME du combustible d'un générateur (table TV044)."""
        ref = self._referentiel("XDPEEnumereCombustible", "idEnumereCombustible")
        ligne = ref.get(str(row.get("idEnumereCombustible"))) or {}
        tvwb = str(ligne.get("tvWB") or "").strip()
        return tvwb if tvwb.isdigit() else None

    def data_ademe(self, key: str, id_adn, champ_ademe: str,
                   anciennete=None, type_energie=None,
                   combustible=None, condensation=None) -> str | None:
        """
        Identifiant ADEME via `XDPEdataAdeme`, la table de correspondance
        qu'Analys'immo maintient pour les énumérations sans `idLib` (types de
        générateur notamment).

        Un même générateur Analys'immo couvre plusieurs valeurs ADEME, que le
        barème distingue par l'ancienneté de l'appareil et son énergie. Les
        trois colonnes de la table s'alignent donc sur ce triplet :
        `idLib1` = générateur, `idLib2` = ancienneté, `idLib3` = type d'énergie.
        Si le triplet ne suffit pas à trancher, on ne devine pas : on signale.
        """
        if id_adn in (None, ""):
            return None
        if not hasattr(self, "_data_ademe"):
            try:
                self._data_ademe = self.src.query(
                    "SELECT keyData, tv, idLib1, idLib2, idLib3 "
                    "FROM XDPEdataAdeme", database=self.dpe_db)
            except Exception:
                self._data_ademe = []

        lignes = [r for r in self._data_ademe
                  if r.get("keyData") == key
                  and str(r.get("idLib1")) == str(id_adn)]
        if not lignes:
            self.manquants.append(f"{champ_ademe} "
                                  f"(XDPEdataAdeme sans entrée pour {key}#{id_adn})")
            return None

        for col, val in (("idLib2", anciennete), ("idLib3", type_energie)):
            if len(lignes) > 1 and val not in (None, ""):
                filtre = [r for r in lignes if str(r.get(col)) == str(val)]
                if filtre:
                    lignes = filtre

        cands = sorted({str(r["tv"]) for r in lignes}, key=int)
        if len(cands) == 1:
            return cands[0]
        if len(cands) == 2 and str(combustible) in COMBUSTIBLES_BOIS:
            # Chaudières bois : l'ADEME tient deux séries d'identifiants
            # consécutives, bûche puis plaquette, là où Analys'immo n'a qu'un
            # générateur « Chaudière bois ». La distinction est portée par le
            # combustible (TV044 : 4 bûches, 5 granulés, 6 et 7 plaquettes).
            #
            # Ce départage ne vaut que pour le bois. D'autres familles sont
            # ambiguës pour une tout autre raison — un générateur d'air chaud
            # se décline en standard ou à condensation — et le combustible n'y
            # apprend rien : on continue de signaler plutôt que de deviner.
            return cands[0] if str(combustible) == "4" else cands[1]
        if len(cands) == 2 and condensation is not None:
            # Générateurs d'air chaud : l'ADEME distingue le modèle standard de
            # celui à condensation, dans cet ordre, et Analys'immo porte le
            # drapeau sur la saisie.
            return cands[1] if condensation else cands[0]
        self.manquants.append(
            f"{champ_ademe} ({key}#{id_adn} ambigu : "
            f"{len(cands)} valeurs ADEME possibles)")
        return None

    def data_ademe_or_nil(self, key: str, id_adn, champ_ademe: str,
                          anciennete=None, type_energie=None,
                          combustible=None, condensation=None):
        v = self.data_ademe(key, id_adn, champ_ademe, anciennete, type_energie,
                            combustible, condensation)
        if v is None:
            # `XDPEdataAdeme` ne couvre qu'une partie des générateurs (les
            # chaudières, pour l'essentiel). Les autres portent directement
            # leur identifiant ADEME dans `XDPEenumereGenerateur.tvWB` — c'est
            # le cas des convecteurs électriques.
            v = self.generateur_tvwb(id_adn)
            if v is not None and self.manquants:
                # `data_ademe` a consigné un échec que ce repli vient de
                # rattraper : le rapport ne doit pas signaler un manque résolu.
                self.manquants.pop()
        return v if v is not None else NIL

    def _referentiel_generateurs(self) -> dict:
        if self._generateurs is None:
            self._generateurs = {}
            try:
                for r in self.src.query(
                        "SELECT idGenerateur, libelle, tvWB, idTypeEnergie, "
                        "isBallonElec, isChauffeEauThermo, isAcuGaz, "
                        "isChauffeBain, isPAC, isECS, isChauffage "
                        "FROM XDPEenumereGenerateur WHERE xDpe = 2021",
                        database=self.dpe_db):
                    self._generateurs[str(r["idGenerateur"])] = r
            except Exception:
                self._generateurs = {}
        return self._generateurs

    def generateur_tvwb(self, id_adn) -> str | None:
        """Identifiant ADEME porté directement par le référentiel générateur."""
        if id_adn in (None, ""):
            return None
        ligne = self._referentiel_generateurs().get(str(id_adn))
        tvwb = str((ligne or {}).get("tvWB") or "").strip()
        return tvwb if tvwb.isdigit() else None

    def generateur_ecs(self, row: dict, champ_ademe: str) -> str | None:
        """
        enum_type_generateur_ecs_id.

        Aucun des générateurs d'ECS du référentiel 2021 d'Analys'immo ne porte
        d'identifiant ADEME : ni `XDPEdataAdeme`, ni `tvWB`. Le jeu est
        heureusement clos — neuf entrées — et les libellés correspondent un
        pour un à l'énumération ADEME. Les familles datées se distinguent par
        l'année d'installation du générateur.
        """
        id_adn = row.get("idGenerateur")
        ligne = self._referentiel_generateurs().get(str(id_adn)) or {}
        # Une pompe à chaleur qui produit aussi l'ECS relève de la famille
        # « pac double service », quelle que soit sa source (air, eau,
        # géothermie) : on la reconnaît au drapeau du référentiel plutôt que
        # d'énumérer ses identifiants.
        code = GENERATEURS_ECS_ADEME.get(str(id_adn))
        if code is None and ligne.get("isPAC"):
            code = PAC_DOUBLE_SERVICE
        if isinstance(code, str):
            return code
        if isinstance(code, tuple):
            annee = num(row.get("dateFabrication") or "")
            if annee is None:
                annee = _annee_installation(row)
            bornes, codes = code
            if annee is not None:
                for borne, valeur in zip(bornes, codes):
                    if annee <= borne:
                        return valeur
                return codes[-1]
        self.manquants.append(
            "%s (générateur Analys'immo #%s « %s » sans correspondance ADEME)"
            % (champ_ademe, id_adn, (ligne.get("libelle") or "")[:40]))
        return None

    def type_energie(self, row: dict) -> str | None:
        """idTypeEnergie du combustible d'un générateur (discriminant ADEME)."""
        combustible = row.get("idEnumereCombustible")
        if combustible in (None, ""):
            return None
        ref = self._referentiel("XDPEEnumereCombustible", "idEnumereCombustible")
        return (ref.get(str(combustible)) or {}).get("idTypeEnergie")

    def enum_or_nil(self, colonne: str, valeur, champ_ademe: str):
        v = self.enum(colonne, valeur, champ_ademe)
        return v if v is not None else NIL

    def tv_or_nil(self, colonne: str, valeur, champ_ademe: str):
        v = self.tv(colonne, valeur, champ_ademe)
        return v if v is not None else NIL

    # ── accès pratique ──────────────────────────────────────────────────────
    def env(self, row: dict, champ: str):
        """Champ de XDPEdetailEnveloppe (libellé, surface, U) d'une paroi."""
        det = self.details_env.get(str(row.get("idDetailEnveloppe"))) or {}
        return det.get(champ)

    def libelle(self, row: dict) -> str:
        return (self.env(row, "libelleDetailEnveloppe")
                or row.get("descriptif") or row.get("descriptionPorte")
                or row.get("descriptionFenetre") or "")

    def reference(self, row: dict) -> str:
        """`reference` ADEME d'un élément : Analys'immo la conserve déjà."""
        return row.get("referenceAdm") or row.get("newRef") or ""

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table) or []

    def capteurs_pv(self) -> list[dict]:
        """
        Capteurs photovoltaïques du lot.

        `XDPEdetailSaisiePhotovoltaique` ne porte pas d'`idSaisieLot` : elle
        pend à la « source d'énergie », qui elle est rattachée au lot. Elle
        échappe donc à l'extraction par lot et se lit à part.
        """
        if self._capteurs_pv is None:
            ids = [str(s.get("idDetailSourceEnergie"))
                   for s in self.rows("XDPEdetailSourceEnergie")
                   if s.get("idDetailSourceEnergie") not in (None, "")]
            if not ids:
                self._capteurs_pv = []
            else:
                liste = ", ".join(i for i in ids if i.isdigit())
                try:
                    self._capteurs_pv = self.src.query(
                        "SELECT * FROM XDPEdetailSaisiePhotovoltaique "
                        f"WHERE idDetailSourceEnergie IN ({liste})",
                        database=self.dpe_db) if liste else []
                except Exception:
                    self._capteurs_pv = []
        return self._capteurs_pv

    def note_vide(self, champ: str) -> None:
        self.vides.append(champ)


    # ── postes de consommation ──────────────────────────────────────────────
    def coef_ep(self, key_energie: str) -> float:
        """
        Coefficient de conversion en energie primaire, lu dans le referentiel
        d'Analys'immo (`XDPEtypeEnergie.coeffEP2020`) plutot que code en dur :
        c'est celui que son moteur a lui-meme applique.
        """
        if self._coefs_ep is None:
            self._coefs_ep = {}
            try:
                for r in self.src.query(
                        "SELECT KEYenergie, coeffEP2020, coeffEP, tvWB "
                        "FROM XDPEtypeEnergie", database=self.dpe_db):
                    self._coefs_ep[str(r["KEYenergie"]).strip().upper()] = (
                        num(r.get("coeffEP2020")) or num(r.get("coeffEP")) or 1.0)
            except Exception:
                self._coefs_ep = {}
        return self._coefs_ep.get(key_energie.upper(), 1.0)

    def _conso_par_energie(self, s: dict, poste: str) -> dict:
        """Consommation finale d'un poste, ventilee par energie."""
        out: dict[str, float] = {}
        for colonne, energie in CONSO_PAR_ENERGIE.get(poste, {}).items():
            v = num(s.get(colonne))
            if v:
                out[energie] = out.get(energie, 0.0) + v
        return out

    def postes_ef(self, s: dict) -> dict:
        """Consommations en energie finale, par poste du modele ADEME."""
        out = {p: sum(self._conso_par_energie(s, p).values())
               for p in ("ch", "ecs", "fr")}
        for poste, colonne in POSTES_ELECTRIQUES.items():
            out[poste] = num(s.get(colonne)) or 0.0
        out["aux_total"] = (out["aux_gen_ch"] + out["aux_dist_ch"]
                            + out["aux_gen_ecs"] + out["aux_dist_ecs"]
                            + out["ventilation"])
        out["total"] = (out["ch"] + out["ecs"] + out["fr"]
                        + out["eclairage"] + out["aux_total"])
        return out

    def postes_ep(self, s: dict) -> dict:
        """Idem en energie primaire, chaque energie avec son coefficient."""
        out = {}
        for poste in ("ch", "ecs", "fr"):
            out[poste] = sum(v * self.coef_ep(e) for e, v
                             in self._conso_par_energie(s, poste).items())
        coef_elec = self.coef_ep("ELEC")
        for poste, colonne in POSTES_ELECTRIQUES.items():
            out[poste] = (num(s.get(colonne)) or 0.0) * coef_elec
        out["aux_total"] = (out["aux_gen_ch"] + out["aux_dist_ch"]
                            + out["aux_gen_ecs"] + out["aux_dist_ecs"]
                            + out["ventilation"])
        out["eclairage"] = (num(s.get("Ceclairage")) or 0.0) * coef_elec
        out["total"] = (out["ch"] + out["ecs"] + out["fr"]
                        + out["eclairage"] + out["aux_total"])
        return out

    def postes_ges(self, s: dict) -> dict:
        """
        Emissions par poste. Analys'immo ne stocke que le total : on le
        repartit au prorata du contenu CO2 de chaque energie consommee, de
        sorte que la somme reste exactement celle qu'il a calculee.
        """
        brut = {}
        for poste in ("ch", "ecs", "fr"):
            brut[poste] = sum(v * CO2_PAR_ENERGIE.get(e, 0.0) for e, v
                              in self._conso_par_energie(s, poste).items())
        co2_elec = CO2_PAR_ENERGIE["ELEC"]
        for poste, colonne in POSTES_ELECTRIQUES.items():
            brut[poste] = (num(s.get(colonne)) or 0.0) * co2_elec
        brut["eclairage"] = (num(s.get("Ceclairage")) or 0.0) * co2_elec

        somme = sum(brut.values())
        total = num(self.calcul.get("emissionsGESAnnuelle"))
        if total is None:
            co2_m2 = num(s.get("carboneTotal"))
            surface = num(self.logement.get("surfaceHabitable"))
            total = (co2_m2 * surface) if (co2_m2 and surface) else None
        facteur = (total / somme) if (total and somme) else 1.0

        out = {k: v * facteur for k, v in brut.items()}
        out["aux_total"] = (out["aux_gen_ch"] + out["aux_dist_ch"]
                            + out["aux_gen_ecs"] + out["aux_dist_ecs"]
                            + out["ventilation"])
        out["total"] = total or somme
        return out

    def postes_cout(self, s: dict) -> dict:
        """Couts annuels par poste, additionnes depuis les colonnes d'energie."""
        def somme(poste):
            return sum(num(s.get(c)) or 0.0 for c in COUT_PAR_POSTE[poste])

        out = {p: somme(p) for p in ("ch", "ecs", "eclairage",
                                     "ventilation", "fr")}
        # Analys'immo ne separe pas generation et distribution cote cout : on
        # repartit le cout des auxiliaires au prorata de leur consommation.
        ef = self.postes_ef(s)
        for usage, colonne in (("ch", "aux_ch"), ("ecs", "aux_ecs")):
            total = somme(colonne)
            gen, dist = ef["aux_gen_" + usage], ef["aux_dist_" + usage]
            base = gen + dist
            out["aux_gen_" + usage] = total * (gen / base) if base else 0.0
            out["aux_dist_" + usage] = total * (dist / base) if base else 0.0
        out["aux_total"] = somme("aux_total") or (
            out["aux_gen_ch"] + out["aux_dist_ch"] + out["aux_gen_ecs"]
            + out["aux_dist_ecs"] + out["ventilation"])
        out["total"] = num(s.get("coutTotal")) or 0.0
        return out

    def sortie_par_energie(self, s: dict) -> list[dict]:
        """
        Repartition des consommations, emissions et couts par energie, telle
        que le modele ADEME l'attend dans `sortie_par_energie_collection`.
        """
        ch = self._conso_par_energie(s, "ch")
        ecs = self._conso_par_energie(s, "ecs")
        fr = self._conso_par_energie(s, "fr")
        elec = (num(s.get("Ceclairage")) or 0.0)
        for colonne in POSTES_ELECTRIQUES.values():
            elec += num(s.get(colonne)) or 0.0
        autres = {"ELEC": elec} if elec else {}

        cout = self.postes_cout(s)
        ef = self.postes_ef(s)
        energies = set(ch) | set(ecs) | set(fr) | set(autres)
        out = []
        for energie in sorted(energies):
            c_ch, c_ecs = ch.get(energie, 0.0), ecs.get(energie, 0.0)
            total = c_ch + c_ecs + fr.get(energie, 0.0) + autres.get(energie, 0.0)
            co2 = CO2_PAR_ENERGIE.get(energie, 0.0)
            # Le cout se repartit au prorata de la consommation du poste.
            part_ch = (c_ch / ef["ch"]) if ef["ch"] else 0.0
            part_ecs = (c_ecs / ef["ecs"]) if ef["ecs"] else 0.0
            out.append({
                "enum_type_energie_id": self.enum_energie(energie) or NIL,
                "conso_ch": c_ch, "conso_ecs": c_ecs, "conso_5_usages": total,
                "emission_ges_ch": c_ch * co2, "emission_ges_ecs": c_ecs * co2,
                "emission_ges_5_usages": total * co2,
                "cout_ch": cout["ch"] * part_ch,
                "cout_ecs": cout["ecs"] * part_ecs,
                "cout_5_usages": cout["ch"] * part_ch + cout["ecs"] * part_ecs,
            })
        return out

    def enum_energie(self, key_energie: str) -> str | None:
        """`enum_type_energie_id` ADEME depuis la cle d'energie Analys'immo."""
        if self._energies is None:
            self._energies = {}
            try:
                for r in self.src.query(
                        "SELECT KEYenergie, tvWB FROM XDPEtypeEnergie",
                        database=self.dpe_db):
                    suffixe = str(r.get("tvWB") or "").rsplit("_", 1)[-1]
                    if suffixe.isdigit():
                        self._energies[str(r["KEYenergie"]).strip().upper()] = \
                            str(int(suffixe))
            except Exception:
                self._energies = {}
        code = self._energies.get(key_energie.upper())
        if code is None:
            self.manquants.append(
                "sortie_par_energie/enum_type_energie_id (%s)" % key_energie)
        return code

    def adjacence(self, row: dict, colonne: str, champ: str) -> str | None:
        """
        enum_type_adjacence_id d'une paroi.

        Analys'immo pose parfois une seconde question : « Circulations
        communes », « Local non chauffé » et « Comble » n'ont pas d'identifiant
        ADEME propre, ils sont précisés dans `XDPEenumereDetailCORmur`
        (circulation avec ou sans ouverture directe, comble plus ou moins
        ventilé…). C'est ce détail qui porte la correspondance, un pour un avec
        l'énumération ADEME.
        """
        famille, detail = _detail_cor(row)
        if detail is not None:
            cle = self._detail_adjacence().get(famille, {}).get(str(detail))
            code = DETAIL_ADJACENCE_ADEME.get(str(cle or "").strip().upper())
            if code:
                return code
        code = self.enum(colonne, row.get(colonne), champ)
        if code:
            return code
        self.manquants.append(
            "%s (COR %s, détail %s)" % (champ, row.get(colonne), detail))
        return None

    def _detail_adjacence(self) -> dict:
        """Détails d'adjacence, par famille de paroi puis par identifiant."""
        if self._details_cor is None:
            self._details_cor = {}
            for famille, (table, pk) in DETAIL_COR_FAMILLES.items():
                lignes = {}
                try:
                    for r in self.src.query(
                            "SELECT [%s] AS id, keyDetail FROM [%s]" % (pk, table),
                            database=self.dpe_db):
                        lignes[str(r["id"])] = r.get("keyDetail")
                except Exception:
                    pass
                self._details_cor[famille] = lignes
        return self._details_cor

    def tv_rendement_ecs(self, row: dict) -> str | None:
        """
        tv_rendement_distribution_ecs_id, d'après la table TV040 de l'arrêté.

        Analys'immo ne conserve pas l'identifiant ADEME de la ligne retenue —
        ses propres `tvWB` sont restés au format 2012 (« TV040_010 »), dont les
        numéros débordent la table 2021. Mais il conserve le rendement
        (`RdEcs`), qu'il a lui-même pris dans cette table : on retrouve donc la
        ligne par son rendement, dans la bonne famille d'installation.
        """
        rendement = num(row.get("RdEcs"))
        collectif = bool(row.get("isCollectifECS") or row.get("isCollectif"))
        famille = [t for t in TV_RENDEMENT_DISTRIBUTION_ECS
                   if t[1] == collectif]
        if rendement and famille:
            ident, _, rd = min(famille, key=lambda t: abs(t[2] - rendement))
            if abs(rd - rendement) <= 0.02:
                return str(ident)
        self.manquants.append(
            "installation_ecs/tv_rendement_distribution_ecs_id "
            f"(RdEcs={row.get('RdEcs')!r}, collectif={collectif})")
        return None

    def type_porte(self, row: dict) -> str | None:
        """
        enum_type_porte_id. Analys'immo ne renseigne `idEnumereUporte` que
        lorsque le type est choisi explicitement ; sinon le libellé de la porte
        reprend mot pour mot celui du référentiel, ce qui permet de le
        retrouver.
        """
        code = self.enum("idEnumereUporte", row.get("idEnumereUporte"))
        if code:
            return code
        libelle = str(row.get("descriptionPorte") or "").strip().lower()
        if libelle:
            try:
                for r in self.src.query(
                        "SELECT typePorte, idLib FROM XDPEenumereUporte "
                        "WHERE xDpe = 2021", database=self.dpe_db):
                    if str(r.get("typePorte") or "").strip().lower() == libelle:
                        return str(r["idLib"])
            except Exception:
                pass
        self.manquants.append(
            "porte/enum_type_porte_id (libellé %r)" % libelle)
        return None

    def paroi_lourde(self, table: str) -> bool:
        """Vrai si la famille de parois est majoritairement en matériau lourd."""
        rows = self.rows(table)
        if not rows:
            return False
        lourds = sum(1 for r in rows
                     if r.get("isLourd") or r.get("materiauLourd")
                     or r.get("paroiLourde") or r.get("isMateriauLourd"))
        return lourds * 2 >= len(rows)

    def _cle_orientation(self, row: dict) -> str:
        """Orientation d'une baie au format des barèmes d'ombrage : N, S ou WE."""
        pos = (row.get("positionParoi") or "").strip().lower()
        if "nord" in pos:
            return "N"
        if "sud" in pos:
            return "S"
        if "est" in pos or "ouest" in pos:
            return "WE"
        # A défaut du libellé, l'identifiant de position (1 sud, 2 est,
        # 3 ouest, 4 nord dans le sélecteur d'Analys'immo).
        return {"1": "S", "2": "WE", "3": "WE", "4": "N"}.get(
            str(row.get("idOrientation") or row.get("idPosition") or ""), "")

    def _bareme_ombrage(self, table: str) -> list[dict]:
        cle = "_bareme_" + table
        if not hasattr(self, cle):
            try:
                lignes = self.src.query(
                    "SELECT * FROM [%s] WHERE xDpe = 2021" % table,
                    database=self.dpe_db)
            except Exception:
                lignes = []
            setattr(self, cle, lignes)
        return getattr(self, cle)

    def facteurs_ombrage(self, row: dict) -> tuple[str | None, str | None]:
        """
        Facteurs d'ombrage (fe1 lointain, fe2 proche) d'une baie.

        Analys'immo ne persiste pas ces deux coefficients — il les recalcule à
        chaque lancement — mais il embarque les barèmes dont il les tire :
        `XDPEenumereFe1` pour le masque proche (loggia, auvent, paroi
        latérale), `XDPEenumereFe2` pour le masque lointain (angle alpha). On
        les relit de la même façon. Attention, les deux noms sont inversés par
        rapport au modèle ADEME, où fe1 désigne le masque lointain.
        """
        def dans(valeur, mini, maxi):
            v, a, b = num(valeur) or 0.0, num(mini), num(maxi)
            if a is None or b is None:
                return False
            # Les tranches sont ouvertes à gauche, sauf la première.
            return (v > a or a == 0) and v <= b

        orientation = self._cle_orientation(row)

        # Masque proche : loggia, auvent ou paroi latérale.
        proche = None
        l1l2 = max(num(row.get("L1")) or 0.0, num(row.get("L2")) or 0.0)
        for r in self._bareme_ombrage("XDPEenumereFe1"):
            if (bool(r.get("isFondLoggia")) != bool(row.get("isFondLoggia"))
                    or bool(r.get("isAuvent")) != bool(row.get("isAuvent"))
                    or bool(r.get("isLateral")) != bool(row.get("isLateral"))
                    or bool(r.get("isRetourSud")) != bool(row.get("isRetourSud"))):
                continue
            cle_r = (r.get("keyOrientation") or "").strip()
            if cle_r and cle_r != orientation:
                continue
            # Les lignes « paroi latérale » ne portent pas de géométrie.
            bornes = (num(r.get("maxAvancee")) or 0) > 0
            if bornes and not (dans(row.get("avancee"), r.get("minAvancee"),
                                    r.get("maxAvancee"))
                               and dans(l1l2, r.get("minL1L2"), r.get("maxL1L2"))):
                continue
            proche = r.get("Fe1")
            break

        # Masque lointain : angle alpha de l'obstacle homogène.
        lointain = None
        alpha = num(row.get("alphaHomogene"))
        if alpha is not None:
            for r in self._bareme_ombrage("XDPEenumereFe2"):
                cle_r = (r.get("keyOrientation") or "").strip()
                if cle_r and cle_r != orientation:
                    continue
                if dans(alpha, r.get("minAlpha"), r.get("maxAlpha")):
                    lointain = r.get("Fe2")
                    break

        return (rnd(lointain, 3) if lointain is not None else None,
                rnd(proche, 3) if proche is not None else None)

    def masques_lointains(self, row: dict) -> list[dict]:
        """Masques lointains rattachés à une baie, s'il y en a."""
        ref = row.get("idDetailEnveloppe")
        if ref is None:
            return []
        return [m for m in self.rows("XDPEdetailMasqueLointain")
                if m.get("idDetailEnveloppe") == ref]

    def largeur_dormant(self, row: dict) -> str | None:
        """Largeur du dormant (cm), via le référentiel `XDPEenumereLargeurDormant`."""
        id_lp = row.get("idLp")
        if id_lp is None:
            return None
        if self._dormants is None:
            self._dormants = {}
            try:
                for r in self.src.query(
                        "SELECT idLp, lp FROM XDPEenumereLargeurDormant",
                        database=self.dpe_db):
                    self._dormants[str(r["idLp"])] = num(r.get("lp"))
            except Exception:
                self._dormants = {}
        return rnd(self._dormants.get(str(id_lp)), 1) or None

    def surface_deperditive(self) -> float:
        """Somme des surfaces de parois deperditives, pour ramener le GV au m2."""
        total = 0.0
        for table, champ in (("XDPEdetailSaisieEnvMur", None),
                             ("XDPEdetailSaisieEnvPlafond", None),
                             ("XDPEdetailSaisieEnvPlancher", None),
                             ("XDPEdetailSaisieEnvFenetre", None),
                             ("XDPEdetailSaisieEnvPorte", None)):
            for row in self.rows(table):
                total += num(self.env(row, "surface")) or 0.0
        return total

    def u_moyen(self, table: str) -> float | None:
        """U moyen d'une famille de parois, pondere par les surfaces."""
        num_, den = 0.0, 0.0
        for row in self.rows(table):
            s = num(self.env(row, "surface")) or 0.0
            u = num(self.env(row, "U"))
            if u is None:
                u = num(row.get("Ujn")) or num(row.get("Uw")) or num(row.get("U"))
            if s and u:
                num_ += s * u
                den += s
        return (num_ / den) if den else None


# ── Bloc administratif ───────────────────────────────────────────────────────
def _adresse(parent: ET.Element, tag: str, adr: str, cp: str, ville: str,
             ctx: Ctx) -> None:
    """Bloc t_adresse : champs *_brut, sans géocodage BAN."""
    el = ET.SubElement(parent, tag)
    add(el, "adresse_brut", adr or "")
    add(el, "code_postal_brut", cp or "")
    add(el, "nom_commune_brut", ville or "")
    label = " ".join(x for x in (adr, cp, ville) if x)
    add(el, "label_brut", label)
    add(el, "label_brut_avec_complement", label)
    # 2 = adresse non géocodée par la BAN. Analys'immo stocke un `infoBAN`
    # mais son format n'est pas celui attendu ici : on ne l'invente pas.
    add(el, "enum_statut_geocodage_ban_id", "2")
    add(el, "ban_date_appel", ctx.now.strftime("%Y-%m-%d"))


def build_administratif(ctx: Ctx) -> ET.Element:
    admin = ET.Element("administratif")
    ent, mis, dos = ctx.entete, ctx.mission, ctx.dossier

    add(admin, "dpe_a_remplacer", NIL)
    add(admin, "motif_remplacement", NIL)
    add(admin, "enum_version_id", ENUM_VERSION_ID)
    add(admin, "enum_modele_dpe_id", str(ctx.cfg.get("ademe_modele_dpe_id", "1")))
    visite = iso_date(ent.get("dateVisite") or mis.get("dateRdv")
                      or mis.get("dateDebut"))
    etabli = iso_date(ent.get("dateEtablissementDiagnostic")
                      or mis.get("dateRedaction") or dos.get("dateRapport"))
    if not visite:
        ctx.note_vide("administratif/date_visite_diagnostiqueur")
    if not etabli:
        ctx.note_vide("administratif/date_etablissement_dpe")
    add(admin, "date_visite_diagnostiqueur", visite or NIL)
    add(admin, "date_etablissement_dpe", etabli or NIL)

    proprio = ctx.interlocuteurs.get(str(adn.ROLE_PROPRIETAIRE)) or {}
    donneur = ctx.interlocuteurs.get(str(adn.ROLE_DONNEUR_ORDRE)) or {}
    nom_proprio = adn._nom_complet(proprio) or adn._nom_complet(donneur)
    add(admin, "nom_proprietaire", nom_proprio or NIL)
    if not nom_proprio:
        ctx.note_vide("administratif/nom_proprietaire")

    diag = ET.SubElement(admin, "diagnostiqueur")
    d = ctx.diagnostiqueur
    cfg_diag = {k: v for k, v in (ctx.cfg.get("diagnostiqueur") or {}).items() if v}
    add(diag, "version_logiciel", str(ent.get("versionMethode") or "") or NIL)
    add(diag, "version_moteur_calcul", str(ent.get("methode") or "") or NIL)
    for tag, cle in (("nom_diagnostiqueur", "nom"),
                     ("prenom_diagnostiqueur", "prenom"),
                     ("mail_diagnostiqueur", "mail"),
                     ("telephone_diagnostiqueur", "telephone"),
                     ("adresse_diagnostiqueur", "adresse"),
                     ("entreprise_diagnostiqueur", "entreprise"),
                     ("numero_certification_diagnostiqueur", "numero_certification"),
                     ("organisme_certificateur", "organisme_certificateur")):
        val = cfg_diag.get(cle) or d.get(cle) or ""
        add(diag, tag, val or NIL)
        if not val:
            ctx.note_vide(f"administratif/diagnostiqueur/{tag}")

    geo = ET.SubElement(admin, "geolocalisation")
    add(geo, "idpar", dos.get("parcelle") or NIL)
    add(geo, "immatriculation_copropriete", ent.get("numCopro") or NIL)
    adresses = ET.SubElement(geo, "adresses")
    _adresse(adresses, "adresse_bien",
             " ".join(x for x in ((dos.get("numVoie") or "").strip(),
                                  (dos.get("typeVoie") or "").strip(),
                                  (dos.get("adresse") or "").strip()) if x),
             dos.get("codePostal") or "", dos.get("ville") or "", ctx)
    adr_p = proprio.get("adresse1") or donneur.get("adresse1") or dos.get("adresse")
    cp_p = proprio.get("codePostal") or donneur.get("codePostal") or dos.get("codePostal")
    v_p = proprio.get("ville") or donneur.get("ville") or dos.get("ville")
    _adresse(adresses, "adresse_proprietaire", adr_p or "", cp_p or "", v_p or "", ctx)

    # Consentement RGPD : champ obligatoire du modèle, non porté par ADN.
    add(admin, "enum_consentement_formulaire_id",
        str(ctx.cfg.get("ademe_consentement_formulaire", "0")))
    add(admin, "horodatage_historisation",
        ctx.now.astimezone().isoformat(timespec="seconds"))
    return admin


# ── Caractéristique générale, météo, inertie ────────────────────────────────
# Méthodes d'application « collectives » du modèle ADEME : l'analyse d'écarts
# d'Opticheck y ramène les déperditions à l'échelle de l'immeuble, donc
# `surface_habitable_immeuble` y est indispensable même si le XSD la dit
# optionnelle.
METHODES_COLLECTIVES = {"3", "5", "31", "32", "35", "37"}


def build_caracteristique_generale(ctx: Ctx) -> ET.Element:
    cg = ET.Element("caracteristique_generale")
    log, imm, dos = ctx.logement, ctx.immeuble, ctx.dossier

    annee = log.get("anneeConstruction") or dos.get("anneeConstruction")
    add(cg, "annee_construction", trunc(annee) or NIL)
    add(cg, "enum_periode_construction_id", _periode_construction(ctx, annee))
    add(cg, "enum_methode_application_dpe_log_id",
        _methode_application(ctx) or NIL)
    surface = log.get("surfaceHabitable") or dos.get("surface")
    add(cg, "surface_habitable_logement", rnd(surface, 2) or NIL)
    add(cg, "hsp", rnd(log.get("hauteurSsPlafond")
                       or dos.get("hspMoy"), 2) or NIL)
    add(cg, "nombre_niveau_logement", trunc(log.get("nombreNiveaux")) or NIL)

    # Bloc immeuble : présent uniquement pour un DPE d'immeuble collectif.
    # Les noms de colonnes de XDPEdetailImmeuble sont `SHbat` / `nbLogements` /
    # `nbNiveaux` ; les variantes tentées auparavant n'existent pas et le champ
    # était omis en silence. `surface_habitable_immeuble` n'est pas cosmétique :
    # l'analyse d'écarts d'Opticheck divise par elle dès que la méthode
    # d'application est collective (3 ou 5), et son absence y levait un
    # TypeError qui bloquait toute transmission.
    if imm:
        surface_imm = (imm.get("SHbat") or imm.get("surfaceReference")
                       or imm.get("surfaceHabitable"))
        if not surface_imm and _methode_application(ctx) in METHODES_COLLECTIVES:
            ctx.note_vide("caracteristique_generale/surface_habitable_immeuble")
        add(cg, "surface_habitable_immeuble", rnd(surface_imm, 2) or None)
        add(cg, "nombre_appartement",
            trunc(imm.get("nbLogements") or imm.get("nombreLogement")
                  or imm.get("nbLogement")) or None)
        add(cg, "nombre_niveau_immeuble",
            trunc(imm.get("nbNiveaux") or imm.get("nombreNiveaux")) or None)
    return cg


def _periode_construction(ctx: Ctx, annee) -> str:
    """enum_periode_construction_id depuis l'année, selon le barème ADEME."""
    a = num(annee)
    if a is None:
        ctx.manquants.append(
            "caracteristique_generale/enum_periode_construction_id "
            "(année de construction absente)")
        return NIL
    for borne, code in PERIODES_CONSTRUCTION:
        if a <= borne:
            return code
    return PERIODE_APRES


def build_meteo(ctx: Ctx) -> ET.Element:
    meteo = ET.Element("meteo")
    log, dos = ctx.logement, ctx.dossier
    dpt = str(log.get("dpt") or dos.get("departement") or "").strip()
    add(meteo, "enum_zone_climatique_id", _zone_climatique(ctx, dpt))
    altitude = log.get("altitudeSaisie") or dos.get("atlitude")
    classe = _classe_altitude(altitude)
    if classe is None:
        ctx.manquants.append("meteo/enum_classe_altitude_id (altitude absente)")
    add(meteo, "enum_classe_altitude_id", classe or NIL)
    add(meteo, "batiment_materiaux_anciens", bool01(log.get("isAncien")))
    return meteo


def _zone_climatique(ctx: Ctx, dpt: str):
    """
    enum_zone_climatique_id. Analys'immo porte la zone en clair par
    département (`XDPEdptClimat.ZC`, ex. « H1a ») : on la traduit selon le
    barème ADEME plutôt que de deviner depuis le numéro de département, dont
    certains sont à cheval sur deux zones.
    """
    if not dpt:
        ctx.manquants.append("meteo/enum_zone_climatique_id (département absent)")
        return NIL
    try:
        rows = ctx.src.query(
            "SELECT ZC, idZoneHiver FROM XDPEdptClimat WHERE dpt = @d",
            database=ctx.dpe_db, params={"d": dpt})
    except Exception:
        rows = []
    zc = (rows[0].get("ZC") or rows[0].get("idZoneHiver") or "") if rows else ""
    code = ZONE_CLIMATIQUE.get(str(zc).strip().lower())
    if code:
        return code
    ctx.manquants.append(f"meteo/enum_zone_climatique_id (dpt {dpt}, ZC={zc!r})")
    return NIL


def _methode_application(ctx: Ctx) -> str | None:
    """
    enum_methode_application_dpe_log_id. Analys'immo range la méthode dans
    `XDPEtypeDossierDPE.tvWB`, sous la forme « TR002_00n » où n est
    l'identifiant ADEME (maison individuelle 1, immeuble collectif 3).
    """
    type_dossier = _type_dossier(ctx)
    try:
        rows = ctx.src.query(
            "SELECT tvWB FROM XDPEtypeDossierDPE WHERE idTypeDossierDPE = @t",
            database=ctx.dpe_db, params={"t": str(type_dossier)})
    except Exception:
        rows = []
    suffixe = str(rows[0]["tvWB"] if rows else "").rsplit("_", 1)[-1]
    if suffixe.isdigit():
        return str(int(suffixe))
    ctx.manquants.append(
        "caracteristique_generale/enum_methode_application_dpe_log_id "
        f"(type de dossier {type_dossier!r})")
    return None


def build_inertie(ctx: Ctx) -> ET.Element:
    inertie = ET.Element("inertie")
    # Le modèle attend le caractère lourd de chaque famille de parois, que
    # Analys'immo porte paroi par paroi (« matériau lourd »).
    add(inertie, "inertie_plancher_bas_lourd",
        bool01(ctx.paroi_lourde("XDPEdetailSaisieEnvPlancher")))
    add(inertie, "inertie_plancher_haut_lourd",
        bool01(ctx.paroi_lourde("XDPEdetailSaisieEnvPlafond")))
    add(inertie, "inertie_paroi_verticale_lourd",
        bool01(ctx.paroi_lourde("XDPEdetailSaisieEnvMur")))
    key = str(ctx.logement.get("keyInertie") or "").strip().lower()
    code = INERTIE_ADEME.get(key)
    if code is None:
        ctx.manquants.append(
            f"inertie/enum_classe_inertie_id (clé Analys'immo {key!r})")
    add(inertie, "enum_classe_inertie_id", code or NIL)
    return inertie


# ── Parois opaques ───────────────────────────────────────────────────────────
def _type_isolation(row: dict) -> str:
    """
    enum_type_isolation_id : 1 inconnu, 2 non isolé, 3 ITI, 4 ITE,
    5 répartie, 6 ITI+ITE.
    """
    iti, ite, itr = row.get("ITI"), row.get("ITE"), row.get("ITR")
    if itr:
        return "5"
    if iti and ite:
        return "6"
    if iti:
        return "3"
    if ite:
        return "4"
    if row.get("isIsoConnue") in (False, 0) or row.get("isIsoInconnue"):
        return "1"
    return "2"


def _de_paroi(de: ET.Element, ctx: Ctx, row: dict, col_cor: str,
              champ: str) -> None:
    """Tête commune de donnee_entree des parois opaques."""
    add(de, "description", ctx.libelle(row) or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    lnc = bool(row.get("idLnc"))
    add(de, "reference_lnc", (f"LNC{ctx.reference(row)}") if lnc else NIL)
    bver = ctx.bveranda(row)
    if bver.get("Tv"):
        add(de, "tv_coef_reduction_deperdition_id", rnd(bver["Tv"], 0))
    else:
        add(de, "tv_coef_reduction_deperdition_id",
            ctx.tv_or_nil(col_cor, row.get(col_cor),
                          f"{champ}/tv_coef_reduction_deperdition_id"))
    # Analys'immo ne conserve pas le coefficient b qu'il affiche : la colonne
    # reste à zéro. Le moteur le recalcule à partir des deux surfaces, encore
    # faut-il les lui donner — et elles sont renseignées même quand le local
    # non chauffé n'a pas été déclaré comme objet (`idLnc` vide).
    if num(row.get("Aiu")) or num(row.get("Aue")):
        add(de, "surface_aiu", rnd(row.get("Aiu"), 2) or NIL)
        add(de, "surface_aue", rnd(row.get("Aue"), 2) or NIL)
    add(de, "enum_type_adjacence_id",
        ctx.adjacence(row, col_cor, f"{champ}/enum_type_adjacence_id") or NIL)


def build_mur(ctx: Ctx, row: dict) -> ET.Element:
    mur = ET.Element("mur")
    de = ET.SubElement(mur, "donnee_entree")
    _de_paroi(de, ctx, row, "idEnumereCORmur", "mur")
    add(de, "enum_orientation_id", _orientation(ctx, row, "mur"))
    surface = ctx.env(row, "surface")
    add(de, "surface_paroi_totale", rnd(surface, 2) or NIL)
    add(de, "surface_paroi_opaque", rnd(surface, 2) or NIL)
    add(de, "paroi_lourde", bool01(row.get("isMatLourd") or row.get("isLourd")))
    add(de, "tv_umur0_id", ctx.tv_or_nil("idEnumereUmur0",
                                         row.get("idEnumereUmur0"),
                                         "mur/tv_umur0_id"))
    add(de, "epaisseur_structure", rnd(row.get("epaisseurParoiSaisie"), 1) or NIL)
    add(de, "enum_materiaux_structure_mur_id",
        ctx.enum_or_nil("idEnumereTypeMur", row.get("idEnumereTypeMur"),
                        "mur/enum_materiaux_structure_mur_id"))
    # 2 = U0 issu d'une table de valeurs (le cas d'Analys'immo).
    add(de, "enum_methode_saisie_u0_id", "2")
    add(de, "enduit_isolant_paroi_ancienne", bool01(row.get("isParoiAncienne")))
    # Analys'immo ne décrit pas le doublage des murs : aucune table ne le porte.
    # 1 = « inconnu », la seule réponse honnête — le score de complétude
    # d'Opticheck la comptera comme une donnée manquante, ce qui est exact.
    add(de, "enum_type_doublage_id", "1")
    add(de, "enum_type_isolation_id", _type_isolation(row))
    add(de, "enum_methode_saisie_u_id", _methode_u(row))
    di = ET.SubElement(mur, "donnee_intermediaire")
    add(di, "b", _b_paroi(ctx, row))
    add(di, "umur", rnd(ctx.env(row, "U") or row.get("U0calcul"), 3) or NIL)
    add(di, "umur0", rnd(row.get("U0calcul"), 3) or NIL)
    return mur


def _methode_u(row: dict) -> str:
    """
    enum_methode_saisie_u_id : 1 inconnu (isolation inconnue),
    6 saisie directe de la résistance, 2 table de valeurs sinon.
    """
    if row.get("UsaisieContainsR"):
        return "6"
    if row.get("isIsoInconnue") or row.get("isIsoConnue") in (False, 0):
        return "1"
    return "2"


def _orientation(ctx: Ctx, row: dict, champ: str):
    """enum_orientation_id depuis la position de paroi d'Analys'immo."""
    pos = (row.get("positionParoi") or "").strip().lower()
    for cle, val in ORIENTATION_ADEME.items():
        if cle in pos:
            return val
    idp = row.get("idOrientation") or row.get("idPosition")
    # idPosition d'Analys'immo suit l'ordre Sud, Est, Ouest, Nord de son
    # sélecteur ; on le traduit vers l'énumération ADEME (1 sud, 2 nord,
    # 3 est, 4 ouest).
    ordre = {"1": "1", "2": "3", "3": "4", "4": "2"}
    if str(idp) in ordre:
        return ordre[str(idp)]
    ctx.manquants.append(f"{champ}/enum_orientation_id")
    return NIL


def build_plancher(ctx: Ctx, row: dict, kind: str) -> ET.Element:
    """kind = 'bas' ou 'haut'."""
    el = ET.Element(f"plancher_{kind}")
    de = ET.SubElement(el, "donnee_entree")
    col_cor = "idEnumereCORsol" if kind == "bas" else "idEnumerCORPlafond"
    _de_paroi(de, ctx, row, col_cor, f"plancher_{kind}")
    add(de, "surface_paroi_opaque", rnd(ctx.env(row, "surface"), 2) or NIL)
    add(de, "paroi_lourde", bool01(row.get("isMatLourd") or row.get("isLourd")))
    col_u0 = "idEnumereUplancher0" if kind == "bas" else "idEnumereUplafond0"
    tag_u0 = "tv_upb0_id" if kind == "bas" else "tv_uph0_id"
    add(de, tag_u0, ctx.tv_or_nil(col_u0, row.get(col_u0),
                                  f"plancher_{kind}/{tag_u0}"))
    add(de, f"enum_type_plancher_{kind}_id",
        ctx.enum_or_nil(col_u0, row.get(col_u0),
                        f"plancher_{kind}/enum_type_plancher_{kind}_id"))
    add(de, "enum_methode_saisie_u0_id", "2")
    add(de, "enum_type_isolation_id", _type_isolation(row))
    add(de, "enum_methode_saisie_u_id", _methode_u(row))
    if kind == "bas":
        # Drapeau « Ue calculé à partir de 2S/P » : il vaut 1 dès que le
        # périmètre et la surface sont renseignés, ce qui est le cas des
        # planchers sur terre-plein et sur vide sanitaire.
        add(de, "calcul_ue",
            "1" if (num(row.get("perimetreTP")) and num(row.get("surfaceTP")))
            else "0")
        if row.get("perimetreTP"):
            add(de, "perimetre_ue", rnd(row.get("perimetreTP"), 2))
        if row.get("surfaceTP"):
            add(de, "surface_ue", rnd(row.get("surfaceTP"), 2))
    di = ET.SubElement(el, "donnee_intermediaire")
    # Une paroi donnant sur une véranda n'a ni `tv_coef_reduction_deperdition_id`
    # ni surfaces Aiu/Aue : son coefficient b vient du barème des vérandas, sans
    # quoi le moteur d'Opticheck n'a rien pour le calculer.
    add(di, "b", _b_paroi(ctx, row))
    u = rnd(ctx.env(row, "U") or row.get("U0calcul"), 3)
    if kind == "bas":
        add(di, "upb", u or NIL)
        add(di, "upb_final", u or NIL)
        add(di, "upb0", rnd(row.get("U0calcul"), 3) or NIL)
    else:
        add(di, "uph", u or NIL)
        add(di, "uph0", rnd(row.get("U0calcul"), 3) or NIL)
    return el


# ── Menuiseries ──────────────────────────────────────────────────────────────
def build_baie(ctx: Ctx, row: dict) -> ET.Element:
    baie = ET.Element("baie_vitree")
    de = ET.SubElement(baie, "donnee_entree")
    add(de, "description", ctx.libelle(row) or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "reference_paroi", row.get("keyParoi") or NIL)
    add(de, "tv_coef_reduction_deperdition_id",
        ctx.tv_or_nil("idEnumereCorBaie", row.get("idEnumereCorBaie"),
                      "baie_vitree/tv_coef_reduction_deperdition_id"))
    add(de, "enum_type_adjacence_id",
        ctx.adjacence(row, "idEnumereCorBaie",
                      "baie_vitree/enum_type_adjacence_id") or NIL)
    add(de, "enum_orientation_id", _orientation(ctx, row, "baie_vitree"))
    # Analys'immo stocke la surface unitaire et le nombre de motifs.
    unitaire = num(ctx.env(row, "surface"))
    nb = num(row.get("Nbmotif")) or 1
    add(de, "surface_totale_baie",
        rnd((unitaire or 0) * nb, 2) if unitaire else NIL)
    add(de, "nb_baie", trunc(nb))
    add(de, "enum_type_vitrage_id",
        ctx.enum_or_nil("idVitrage", row.get("idVitrage"),
                        "baie_vitree/enum_type_vitrage_id"))
    if row.get("epaisseur"):
        add(de, "epaisseur_lame", trunc(row.get("epaisseur")))
    add(de, "enum_type_fermeture_id",
        ctx.enum_or_nil("idFermeture", row.get("idFermeture"),
                        "baie_vitree/enum_type_fermeture_id"))
    add(de, "tv_ug_id", ctx.tv_or_nil("idUg", row.get("idUg"),
                                      "baie_vitree/tv_ug_id"))
    add(de, "tv_uw_id", ctx.tv_or_nil("idUw", row.get("idUw"),
                                      "baie_vitree/tv_uw_id"))
    add(de, "tv_ujn_id", ctx.tv_or_nil("idUjn", row.get("idUjn"),
                                       "baie_vitree/tv_ujn_id"))
    # Type de baie et materiau de menuiserie. `enum_type_baie_id` est lu sans
    # garde par l'analyse d'ecarts d'Opticheck (`int(...)` direct) : l'omettre
    # y provoquait un 500, et c'est ce champ qui bloquait toute transmission
    # d'un DPE Analys'immo.
    add(de, "enum_type_baie_id",
        ctx.enum_or_nil("idParoiVitree", row.get("idParoiVitree"),
                        "baie_vitree/enum_type_baie_id"))
    add(de, "enum_type_materiaux_menuiserie_id",
        ctx.enum_or_nil("idMenuiserie", row.get("idMenuiserie"),
                        "baie_vitree/enum_type_materiaux_menuiserie_id"))
    # 1 = performances issues des tables forfaitaires et des relevés observés.
    # C'est bien ce que fait Analys'immo : il choisit Ug/Uw/Ujn dans ses
    # référentiels d'après le vitrage et la menuiserie relevés, et non d'après
    # un document justificatif du fabricant. Déclarer une saisie justifiée
    # (2 à 6) ferait passer le moteur d'Opticheck à côté de sa vérification.
    add(de, "enum_methode_saisie_perf_vitrage_id", "1")
    # 1 = air, 2 = argon ou krypton, 3 = inconnu.
    add(de, "enum_type_gaz_lame_id", "2" if row.get("isKrypton") else "1")
    add(de, "vitrage_vir", bool01(row.get("isVir")))
    add(de, "enum_inclinaison_vitrage_id",
        ctx.enum_or_nil("idInclinaison", row.get("idInclinaison"),
                        "baie_vitree/enum_inclinaison_vitrage_id"))
    add(de, "largeur_dormant", ctx.largeur_dormant(row) or NIL)
    di = ET.SubElement(baie, "donnee_intermediaire")
    add(di, "b", _b_paroi(ctx, row))
    add(di, "ug", rnd(row.get("Ug"), 3) or NIL)
    add(di, "uw", rnd(row.get("Uw"), 3) or NIL)
    add(di, "ujn", rnd(row.get("Ujn"), 3) or NIL)
    # U retenu pour la baie : celui de la menuiserie fermeture comprise quand
    # il y a une fermeture, sinon celui de la fenêtre nue.
    add(di, "u_menuiserie",
        req(row.get("Ujn") if row.get("idFermeture") else row.get("Uw")))
    # Facteurs d'ombrage : fe1 masques lointains, fe2 masques proches.
    # Analys'immo ne les persiste pas, il les recalcule à chaque calcul depuis
    # la géométrie des masques. Sans masque déclaré, l'ombrage est neutre (1).
    fe1, fe2 = ctx.facteurs_ombrage(row)
    masque = bool(row.get("isMasqueFacade")) or bool(ctx.masques_lointains(row))
    if masque and fe1 is None and fe2 is None:
        ctx.manquants.append(
            "baie_vitree/fe1-fe2 (masque déclaré, géométrie non reprise)")
    # Sans masque, l'ombrage est neutre : le moteur multiplie les deux
    # facteurs, un 1 laisse donc l'apport solaire intact.
    add(di, "fe1", fe1 or "1")
    add(di, "fe2", fe2 or "1")
    add(di, "sw", rnd(row.get("Fts") or row.get("FtsSaisie"), 3) or NIL)
    return baie


def build_porte(ctx: Ctx, row: dict) -> ET.Element:
    porte = ET.Element("porte")
    de = ET.SubElement(porte, "donnee_entree")
    add(de, "description", ctx.libelle(row) or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "reference_paroi", row.get("idMur") or NIL)
    add(de, "tv_coef_reduction_deperdition_id",
        ctx.tv_or_nil("idEnumereCorMur", row.get("idEnumereCorMur"),
                      "porte/tv_coef_reduction_deperdition_id"))
    if num(row.get("Aiu")) or num(row.get("Aue")):
        add(de, "surface_aiu", rnd(row.get("Aiu"), 2) or NIL)
        add(de, "surface_aue", rnd(row.get("Aue"), 2) or NIL)
    add(de, "enum_type_adjacence_id",
        ctx.adjacence(row, "idEnumereCorMur",
                      "porte/enum_type_adjacence_id") or NIL)
    add(de, "surface_porte", rnd(ctx.env(row, "surface"), 2) or NIL)
    add(de, "tv_uporte_id", ctx.tv_or_nil("idEnumereUporte",
                                          row.get("idEnumereUporte"),
                                          "porte/tv_uporte_id"))
    # 1 = valeur forfaitaire : Analys'immo lit Uporte dans son référentiel
    # (`XDPEenumereUporte.Uporte`), il ne s'appuie pas sur un justificatif.
    add(de, "enum_methode_saisie_uporte_id", "1")
    add(de, "enum_type_porte_id", ctx.type_porte(row) or NIL)
    add(de, "largeur_dormant", ctx.largeur_dormant(row) or NIL)
    di = ET.SubElement(porte, "donnee_intermediaire")
    add(di, "b", _b_paroi(ctx, row))
    add(di, "uporte", rnd(ctx.env(row, "U"), 3) or NIL)
    return porte


# Liaisons du référentiel Analys'immo (`keyLineique`) vers l'énumération
# ADEME des types de liaison.
LIAISONS_ADEME = {
    "plbmur": "1",   # plancher bas / mur
    "plimur": "2",   # plancher intermédiaire / mur
    "plhmur": "3",   # plancher haut / mur
    "refmur": "4",   # refend / mur
    "menmur": "5",   # menuiserie / mur
}


def build_pont_thermique(ctx: Ctx, row: dict) -> ET.Element:
    """
    Un pont thermique du modèle ADEME. Analys'immo ne conserve que le type de
    liaison : pour un immeuble collectif, son moteur applique des constantes
    plutôt que des linéaires saisis. Quand aucune longueur n'est renseignée,
    sa déperdition de ponts thermiques est nulle — c'est ce que l'on transcrit,
    plutôt que d'inventer un métré.
    """
    pt = ET.Element("pont_thermique")
    key = str(row.get("keyLineique") or "").strip().lower()
    de = ET.SubElement(pt, "donnee_entree")
    add(de, "description", row.get("descriptif") or row.get("keyLineique") or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "reference_1", row.get("keyParoi1") or row.get("ref1") or NIL)
    add(de, "reference_2", row.get("keyParoi2") or row.get("ref2") or NIL)
    add(de, "l", req(row.get("longueur") or row.get("lineaire"), 2))
    code = LIAISONS_ADEME.get(key)
    if code is None:
        ctx.manquants.append(
            f"pont_thermique/enum_type_liaison_id (liaison {key!r})")
    add(de, "enum_type_liaison_id", code or NIL)
    add(de, "pourcentage_valeur_pont_thermique",
        rnd(row.get("pourcentage") or 1, 2))
    di = ET.SubElement(pt, "donnee_intermediaire")
    add(di, "k", req(row.get("k") or row.get("psi")))
    return pt


def _ajouter(ctx: Ctx, parent: ET.Element, fabrique, row: dict,
             quoi: str) -> None:
    """
    Ajoute un element a une collection, en isolant son echec.

    Un DPE peut contenir n'importe quoi : Analys'immo laisse saisir des
    configurations que le generateur n'a jamais rencontrees. Si l'une d'elles
    fait lever, on perd cet element et on le dit dans le rapport — mais le DPE
    part, et surtout les autres DPE de la meme transmission partent aussi.
    """
    try:
        parent.append(fabrique(ctx, row))
    except Exception as e:
        ctx.incidents.append(
            "%s ignore (%s : %s)" % (quoi, type(e).__name__, str(e)[:120]))


def par_m2(valeur, surface):
    """Valeur ramenee au metre carre, ou None si l'un des deux manque."""
    v, sf = num(valeur), num(surface)
    return (v / sf) if (v is not None and sf) else None


def _b_paroi(ctx: Ctx, row: dict) -> str:
    """
    Coefficient de réduction des déperditions d'une paroi.

    Analys'immo ne persiste pas celui qu'il affiche — la colonne reste à zéro.
    Pour une paroi donnant sur une véranda, il n'y a par ailleurs ni table de
    réduction ni surfaces Aiu/Aue à donner au moteur : c'est le barème des
    vérandas qui fournit la valeur.
    """
    return rnd(ctx.bveranda(row).get("b") or row.get("b"), 3) or NIL


# ── Climatisation ────────────────────────────────────────────────────────────
def _rang_periode(annee, bornes) -> int:
    """Rang (0 pour la première) de l'année dans une suite de bornes hautes."""
    if annee is None:
        return 0
    for i, borne in enumerate(bornes):
        if annee <= borne:
            return i
    return len(bornes)


def _generateur_froid(ctx: Ctx, row: dict) -> str | None:
    """
    enum_type_generateur_fr_id.

    Analys'immo ne décrit que la famille de système (`idLib` du référentiel) ;
    l'ADEME la décline par période d'installation, et les rangs se suivent.
    """
    ref = ctx._referentiel("XDPEenumereSystemeClimatisation",
                           "idEnumereSystemeClimatisation")
    ligne = ref.get(str(row.get("idEnumereSystemeClimatisation"))) or {}
    base = str(ligne.get("idLib") or "").strip()
    if not base.isdigit():
        ctx.manquants.append(
            "climatisation/enum_type_generateur_fr_id "
            f"(système {row.get('idEnumereSystemeClimatisation')!r} sans idLib)")
        return None
    nb_periodes = FAMILLES_CLIMATISATION.get(base)
    if not nb_periodes:
        return base           # « autre système » : pas de déclinaison
    annee = num(row.get("periodeInstallation")) or _annee_installation(row)
    rang = min(_rang_periode(annee, PERIODES_CLIMATISATION[:nb_periodes - 1]),
               nb_periodes - 1)
    return str(int(base) + rang)


def build_climatisation(ctx: Ctx, row: dict) -> ET.Element:
    clim = ET.Element("climatisation")
    de = ET.SubElement(clim, "donnee_entree")
    add(de, "description",
        row.get("descriptionClimatisation") or row.get("libelleSysteme") or NIL)
    add(de, "reference", row.get("referenceAdm") or NIL)
    add(de, "surface_clim", req(row.get("surfaceClimatisee"), 2))
    annee = num(row.get("periodeInstallation")) or _annee_installation(row)
    add(de, "enum_periode_installation_fr_id",
        str(_rang_periode(annee, PERIODE_INSTALLATION_FR) + 1))
    add(de, "enum_type_generateur_fr_id", _generateur_froid(ctx, row) or NIL)
    # 1 = calcul simple ; 3 = installation collective rapportée à un logement,
    # ce que fait Analys'immo pour une climatisation d'immeuble.
    add(de, "enum_methode_calcul_conso_id", "3" if row.get("isCollectif") else "1")
    if row.get("idEnumereCombustible"):
        add(de, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "climatisation/enum_type_energie_id"))
    else:
        # Le référentiel des systèmes porte l'énergie ; l'électricité est le
        # défaut du modèle et le cas de tous les systèmes thermodynamiques.
        add(de, "enum_type_energie_id",
            "2" if str(row.get("idTypeEnergie")) == "2" else "1")
    # Le SEER n'est renseigné que s'il a été relevé sur la plaque ; sinon le
    # moteur reprend la valeur forfaitaire de la période.
    if row.get("isSEERconnu"):
        add(de, "tv_seer_id", rnd(row.get("SEER"), 0))
    # 8 = SEER relevé sur l'appareil ; 1 = valeurs forfaitaires, ce
    # qu'Analys'immo applique dès que le SEER n'a pas été saisi.
    add(de, "enum_methode_saisie_carac_sys_id",
        "8" if row.get("isSEERconnu") else "1")
    di = ET.SubElement(clim, "donnee_intermediaire")
    add(di, "eer", req(row.get("EER")))
    add(di, "besoin_fr", req(row.get("Bfr")))
    add(di, "conso_fr", req(row.get("Cfr")))
    add(di, "conso_fr_depensier", req(row.get("CfrDepensier") or row.get("Cfr")))
    return clim


# ── Production d'électricité renouvelable ────────────────────────────────────
def build_production_enr(ctx: Ctx) -> ET.Element | None:
    """
    production_elec_enr : panneaux photovoltaïques, éolien, cogénération.

    Analys'immo range les capteurs dans `XDPEdetailSaisiePhotovoltaique`, qui
    n'est pas rattachée au lot mais à la « source d'énergie » de celui-ci.
    """
    sources = ctx.rows("XDPEdetailSourceEnergie")
    capteurs = ctx.capteurs_pv()
    source = sources[0] if sources else {}
    presence = 0
    if capteurs:
        presence |= ENR_PHOTOVOLTAIQUE
    if source.get("presenceEolienne"):
        presence |= ENR_EOLIEN
    if source.get("presenceCogeneration"):
        presence |= ENR_COGENERATION
    if not presence:
        return None

    enr = ET.Element("production_elec_enr")
    de = ET.SubElement(enr, "donnee_entree")
    add(de, "presence_production_pv", "1" if capteurs else "0")
    add(de, "enum_type_enr_id", str(presence))
    di = ET.SubElement(enr, "donnee_intermediaire")
    add(di, "production_pv", req(ctx.sortie.get("Ppv")))
    add(di, "conso_elec_ac", req(ctx.sortie.get("CelecAc")))
    coll = ET.SubElement(enr, "panneaux_pv_collection")
    for c in capteurs:
        pv = ET.SubElement(coll, "panneaux_pv")
        add(pv, "surface_totale_capteurs", rnd(c.get("Surface"), 2) or NIL)
        nb = num(c.get("Nm"))
        if nb:
            add(pv, "nombre_module", rnd(nb, 0))
        code = ORIENTATION_PV_ADEME.get(str(c.get("keyOri") or "").strip().upper())
        if code is None:
            ctx.manquants.append("panneaux_pv/enum_orientation_pv_id "
                                 f"(orientation {c.get('keyOri')!r})")
        add(pv, "enum_orientation_pv_id", code or NIL)
        add(pv, "enum_inclinaison_pv_id",
            ctx.enum_or_nil("idInclPpv", c.get("idInclPpv"),
                            "panneaux_pv/enum_inclinaison_pv_id"))
    return enr


# ── Ventilation ──────────────────────────────────────────────────────────────
def build_ventilation(ctx: Ctx, row: dict) -> ET.Element:
    v = ET.Element("ventilation")
    de = ET.SubElement(v, "donnee_entree")
    add(de, "description",
        row.get("descriptionVentilation") or row.get("descriptif") or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "surface_ventile", rnd(ctx.logement.get("surfaceHabitable"), 2) or NIL)
    add(de, "tv_q4pa_conv_id", ctx.tv_or_nil("idEnumereVentilation",
                                             row.get("idEnumereVentilation"),
                                             "ventilation/tv_q4pa_conv_id"))
    add(de, "enum_type_ventilation_id",
        ctx.enum_or_nil("idEnumereVentilation", row.get("idEnumereVentilation"),
                        "ventilation/enum_type_ventilation_id"))
    add(de, "plusieurs_facade_exposee",
        bool01(ctx.logement.get("perimetreSurExterieurNiv1")))
    add(de, "tv_debits_ventilation_id",
        ctx.tv_or_nil("idEnumereVentilation", row.get("idEnumereVentilation"),
                      "ventilation/tv_debits_ventilation_id"))
    # Le seuil réglementaire porte sur l'installation, pas sur le bâti.
    annee = num(row.get("anneeConstruction") or row.get("anneeInstallation"))
    add(de, "ventilation_post_2012", bool01(annee is not None and annee > 2012))
    if row.get("Q4paSaisi"):
        add(de, "q4pa_saisi", rnd(row.get("Q4paSaisi"), 3))
    di = ET.SubElement(v, "donnee_intermediaire")
    add(di, "hperm", req(row.get("Hperm")))
    add(di, "hvent", req(row.get("Hvent")))
    add(di, "q4pa_conv", req(row.get("Q4Paenv") or row.get("Q4pa")))
    add(di, "conso_auxiliaire_ventilation", req(row.get("CauxVent")))
    return v


# ── Installations ────────────────────────────────────────────────────────────
def build_chauffage(ctx: Ctx, row: dict) -> ET.Element:
    inst = ET.Element("installation_chauffage")
    de = ET.SubElement(inst, "donnee_entree")
    add(de, "description", row.get("descriptionChauffage")
        or row.get("nomInstall") or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "surface_chauffee", rnd(row.get("Shi")
                                    or ctx.logement.get("surfaceHabitable"), 2) or NIL)
    add(de, "enum_cfg_installation_ch_id",
        "1" if row.get("isIndividuel") else "2")
    add(de, "nombre_niveau_installation_ch",
        trunc(row.get("nbNivCha")) or NIL)
    add(de, "enum_type_installation_id",
        ctx.tv_or_nil("idInstall", row.get("idInstall") or row.get("keyInstall"),
                      "installation_chauffage/enum_type_installation_id"))
    # 1 = consommations issues du calcul conventionnel 3CL, le seul mode que
    # produit Analys'immo.
    add(de, "enum_methode_calcul_conso_id", "1")
    s, sd = ctx.sortie, ctx.sortie_dep
    di = ET.SubElement(inst, "donnee_intermediaire")
    add(di, "besoin_ch", req(row.get("Bch") or s.get("Bch")))
    add(di, "besoin_ch_depensier", req(row.get("BchDepensier") or sd.get("Bch")))
    add(di, "conso_ch", req(row.get("CchPCI") or s.get("Cch")))
    add(di, "conso_ch_depensier",
        req(row.get("CchDepensier") or sd.get("Cch")))

    gen_coll = ET.SubElement(inst, "generateur_chauffage_collection")
    gen = ET.SubElement(gen_coll, "generateur_chauffage")
    gde = ET.SubElement(gen, "donnee_entree")
    add(gde, "description", row.get("descriptionChauffage") or NIL)
    add(gde, "reference", ctx.reference(row) or NIL)
    add(gde, "reference_generateur_mixte",
        ctx.reference(row) if row.get("isECS") else NIL)
    hybride_ch, _ = ctx.generateur_hybride(row)
    multi_ch, _ = ctx.generateur_multi_batiment(row)
    rayonnant = ctx.generateur_rayonnant(row)
    reseau = ctx.identifiant_reseau(row)
    if rayonnant:
        add(gde, "enum_type_generateur_ch_id", rayonnant)
        add(gde, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "generateur_chauffage/enum_type_energie_id"))
    elif multi_ch:
        add(gde, "enum_type_generateur_ch_id", multi_ch)
        add(gde, "enum_type_energie_id", ENERGIE_RESEAU_CHALEUR)
    elif hybride_ch:
        add(gde, "enum_type_generateur_ch_id", hybride_ch)
        add(gde, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "generateur_chauffage/enum_type_energie_id"))
    elif reseau:
        add(gde, "enum_type_generateur_ch_id",
            GENERATEUR_RESEAU_CHALEUR_CH[ctx.reseau_isole(row)])
        add(gde, "enum_type_energie_id", ENERGIE_RESEAU_CHALEUR)
        add(gde, "identifiant_reseau_chaleur", reseau)
    else:
        add(gde, "enum_type_generateur_ch_id",
            ctx.data_ademe_or_nil(
                "enum_type_generateur_ch_id", row.get("idGenerateur"),
                "generateur_chauffage/enum_type_generateur_ch_id",
                anciennete=row.get("idAnciennete"),
                type_energie=ctx.type_energie(row),
                combustible=ctx.combustible_tvwb(row),
                condensation=row.get("isAirChaudCondens")))
        add(gde, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "generateur_chauffage/enum_type_energie_id"))
    add(gde, "position_volume_chauffe", bool01(row.get("InVolChauf")))
    add(gde, "enum_usage_generateur_id", _usage_generateur(row))
    # 2 = caractéristiques issues des valeurs par défaut du référentiel, ce que
    # fait Analys'immo dès lors qu'aucun rapport de chaudière n'est joint.
    add(gde, "enum_methode_saisie_carac_sys_id",
        "1" if row.get("hasRapportChaudiere") else "2")
    add(gde, "enum_lien_generateur_emetteur_id", _lien_generateur(row))
    gdi = ET.SubElement(gen, "donnee_intermediaire")
    add(gdi, "pn", positif(row.get("Pnom")))
    add(gdi, "rpn", positif(row.get("Rpn")))
    add(gdi, "rpint", positif(row.get("Rpint")))
    add(gdi, "qp0", positif(row.get("QP0")))
    add(gdi, "pveilleuse", positif(row.get("Pveil")))
    add(gdi, "rendement_generation", positif(row.get("Rg")) or NIL)
    add(gdi, "conso_ch", req(row.get("CchPCI") or ctx.sortie.get("Cch")))
    add(gdi, "conso_ch_depensier",
        req(row.get("CchDepensier") or ctx.sortie_dep.get("Cch")))

    # Émetteurs rattachés à ce générateur.
    em_coll = ET.SubElement(inst, "emetteur_chauffage_collection")
    for e in ctx.emetteurs:
        if e.get("_idSaisieGenerateur") not in (None, row.get("idSaisieGenerateur")):
            continue
        em = ET.SubElement(em_coll, "emetteur_chauffage")
        ede = ET.SubElement(em, "donnee_entree")
        add(ede, "description", e.get("descriptif") or e.get("emetteur") or NIL)
        add(ede, "reference", e.get("referenceAdm") or NIL)
        add(ede, "surface_chauffee", rnd(e.get("surfaceChauffee"), 2) or NIL)
        add(ede, "enum_type_emission_distribution_id",
            ctx.tv_or_nil("idTypeEmetteur", e.get("idTypeEmetteur"),
                          "emetteur_chauffage/enum_type_emission_distribution_id"))
        add(ede, "enum_equipement_intermittence_id",
            ctx.enum_or_nil("idInter", e.get("idInter"),
                            "emetteur_chauffage/enum_equipement_intermittence_id"))
        add(ede, "tv_rendement_emission_id",
            ctx.tv_or_nil("idTypeEmetteur", e.get("idTypeEmetteur"),
                          "emetteur_chauffage/tv_rendement_emission_id"))
        add(ede, "reseau_distribution_isole", bool01(e.get("isResIsole")))
        add(ede, "tv_rendement_distribution_ch_id",
            ctx.tv_or_nil("idEnumereReseauDistribution",
                          e.get("idEnumereReseauDistribution"),
                          "emetteur_chauffage/tv_rendement_distribution_ch_id"))
        add(ede, "tv_rendement_regulation_id", req(e.get("Tv"), 0))
        add(ede, "tv_intermittence_id",
            ctx.enum_or_nil("idInter", e.get("idInter"),
                            "emetteur_chauffage/tv_intermittence_id"))
        add(ede, "enum_type_chauffage_id",
            "1" if e.get("hasIntRegul") and not e.get("isDivise") else "2")
        add(ede, "enum_type_regulation_id",
            "1" if e.get("hasRobinetThermo") or e.get("hasRegTherm") else "2")
        add(ede, "enum_temp_distribution_ch_id", _temp_distribution(e))
        add(ede, "enum_lien_generateur_emetteur_id", _lien_generateur(row))
        edi = ET.SubElement(em, "donnee_intermediaire")
        add(edi, "rendement_emission", positif(e.get("Re")) or NIL)
        add(edi, "rendement_distribution",
            req(e.get("Rd") or e.get("Rd0")))
        add(edi, "rendement_regulation", positif(e.get("Rr")) or NIL)
        add(edi, "i0", rnd(e.get("I0"), 3) or NIL)
    return inst


def _usage_generateur(row: dict) -> str:
    """enum_usage_generateur_id : 1 chauffage, 2 ECS, 3 mixte."""
    ch, ecs = bool(row.get("isChauffage")), bool(row.get("isECS"))
    return "3" if (ch and ecs) else ("2" if ecs else "1")


def _lien_generateur(row: dict) -> str:
    """
    enum_lien_generateur_emetteur_id : 1 générateur unique desservant tous les
    émetteurs, 2 générateur en cascade ou en appoint.
    """
    return "2" if (row.get("FctCascade") or row.get("FctAppoint")
                   or row.get("FctRelevePAC")) else "1"


def _temp_distribution(emetteur: dict) -> str:
    """
    enum_temp_distribution_ch_id : 1 basse température, 2 moyenne,
    3 haute. Analys'immo porte le régime dans la clé du réseau de
    distribution (EAUBT / EAUMT / EAUHT).
    """
    if emetteur.get("isBasseT"):
        return "1"
    key = str(emetteur.get("KEY_reseauDistribution") or "").upper()
    if key.startswith("EAUBT"):
        return "1"
    if key.startswith("EAUHT"):
        return "3"
    return "2"


def build_ecs(ctx: Ctx, row: dict) -> ET.Element:
    inst = ET.Element("installation_ecs")
    de = ET.SubElement(inst, "donnee_entree")
    add(de, "description", row.get("descriptionECS") or NIL)
    add(de, "reference", ctx.reference(row) or NIL)
    add(de, "enum_cfg_installation_ecs_id",
        "1" if row.get("isIndividuelECS") else "2")
    add(de, "enum_type_installation_id",
        "1" if row.get("isIndividuelECS") else "2")
    add(de, "surface_habitable",
        rnd(row.get("surfaceECS") or ctx.logement.get("surfaceHabitable"), 2) or NIL)
    add(de, "nombre_niveau_installation_ecs", trunc(row.get("nbNivEcs")) or NIL)
    add(de, "enum_methode_calcul_conso_id", "1")
    add(de, "tv_rendement_distribution_ecs_id", ctx.tv_rendement_ecs(row) or NIL)
    # 1 = sans bouclage, 2 = avec. Analys'immo note le bouclage du réseau ECS
    # dans `keyBouclage`.
    add(de, "enum_bouclage_reseau_ecs_id",
        "2" if row.get("keyBouclage") else "1")
    s, sd = ctx.sortie, ctx.sortie_dep
    di = ET.SubElement(inst, "donnee_intermediaire")
    # Attention aux unités : sur la ligne générateur d'Analys'immo, `Bch` est en
    # kWh mais `Becs` est en **Wh** — mille fois plus. La sortie du moteur, elle,
    # est en kWh pour les deux. On convertit donc la valeur par générateur.
    add(di, "besoin_ecs", req(_wh_en_kwh(row.get("Becs")) or s.get("Becs")))
    add(di, "besoin_ecs_depensier",
        req(_wh_en_kwh(row.get("BecsDepensier")) or sd.get("Becs")))
    add(di, "conso_ecs", req(row.get("CecsPCI") or s.get("Cecs")))
    add(di, "conso_ecs_depensier",
        req(row.get("CecsDepensier") or sd.get("Cecs")))
    add(di, "rendement_distribution", req(row.get("RdEcs")))

    gen_coll = ET.SubElement(inst, "generateur_ecs_collection")
    gen = ET.SubElement(gen_coll, "generateur_ecs")
    gde = ET.SubElement(gen, "donnee_entree")
    add(gde, "description", row.get("descriptionECS") or NIL)
    add(gde, "reference", ctx.reference(row) or NIL)
    add(gde, "reference_generateur_mixte",
        ctx.reference(row) if row.get("isChauffage") else NIL)
    # Analys'immo ne maintient de correspondance ADEME que pour les
    # generateurs de chauffage. Pour un generateur mixte (chauffage + ECS),
    # c'est le meme appareil : on reutilise sa valeur, ce que le modele ADEME
    # admet puisque `reference_generateur_mixte` les relie explicitement.
    # Générateur mixte (chauffage + ECS) : c'est le même appareil, on réutilise
    # sa correspondance côté chauffage. Générateur dédié à l'ECS : Analys'immo
    # n'en donne aucune, d'où la table `GENERATEURS_ECS_ADEME`.
    _, hybride_ecs = ctx.generateur_hybride(row)
    _, multi_ecs = ctx.generateur_multi_batiment(row)
    reseau = ctx.identifiant_reseau(row)
    if multi_ecs:
        add(gde, "enum_type_generateur_ecs_id", multi_ecs)
        add(gde, "enum_type_energie_id", ENERGIE_RESEAU_CHALEUR)
    elif hybride_ecs:
        add(gde, "enum_type_generateur_ecs_id", hybride_ecs)
        add(gde, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "generateur_ecs/enum_type_energie_id"))
    elif reseau:
        add(gde, "enum_type_generateur_ecs_id",
            GENERATEUR_RESEAU_CHALEUR_ECS[ctx.reseau_isole(row)])
        add(gde, "enum_type_energie_id", ENERGIE_RESEAU_CHALEUR)
        add(gde, "identifiant_reseau_chaleur", reseau)
    else:
        code_ecs = None
        if row.get("isChauffage"):
            code_ecs = ctx.data_ademe(
                "enum_type_generateur_ch_id", row.get("idGenerateur"),
                "generateur_ecs/enum_type_generateur_ecs_id",
                anciennete=(row.get("idAncienneteECS")
                            or row.get("idAnciennete")),
                type_energie=ctx.type_energie(row),
                combustible=ctx.combustible_tvwb(row),
                condensation=row.get("isAirChaudCondens"))
        if code_ecs is None:
            code_ecs = ctx.generateur_ecs(
                row, "generateur_ecs/enum_type_generateur_ecs_id")
        add(gde, "enum_type_generateur_ecs_id", code_ecs or NIL)
        add(gde, "enum_type_energie_id",
            ctx.enum_or_nil("idEnumereCombustible",
                            row.get("idEnumereCombustible"),
                            "generateur_ecs/enum_type_energie_id"))
    add(gde, "position_volume_chauffe", bool01(row.get("InVolChauf")))
    add(gde, "enum_methode_saisie_carac_sys_id", "2")
    add(gde, "enum_usage_generateur_id", _usage_generateur(row))
    # 1 = sans stockage, 2 = avec ballon. Analys'immo donne le volume dans `Vs`.
    volume = num(row.get("Vs"))
    add(gde, "enum_type_stockage_ecs_id",
        "2" if (volume or row.get("hasBallonAccu")) else "1")
    add(gde, "volume_stockage", req(volume, 1))
    gdi = ET.SubElement(gen, "donnee_intermediaire")
    add(gdi, "rendement_generation", positif(row.get("RgEcs")) or NIL)
    add(gdi, "rendement_stockage", positif(row.get("RsEcs")))
    add(gdi, "pn", positif(row.get("Pnom")))
    add(gdi, "conso_ecs", req(row.get("CecsPCI") or ctx.sortie.get("Cecs")))
    add(gdi, "conso_ecs_depensier",
        req(row.get("CecsDepensier") or ctx.sortie_dep.get("Cecs")))
    return inst


# ── Sortie (résultats du moteur) ─────────────────────────────────────────────
def build_sortie(ctx: Ctx) -> ET.Element:
    """
    Bloc `sortie` : les resultats du moteur 3CL d'Analys'immo.

    Analys'immo produit deux jeux de resultats, conventionnel et « occupant
    depensier », que le modele ADEME attend cote a cote (`besoin_ch` /
    `besoin_ch_depensier`). Les consommations sont stockees en energie finale
    par usage et par energie ; l'energie primaire et les emissions se
    reconstituent avec les coefficients du referentiel.
    """
    sortie = ET.Element("sortie")
    c, s, sd = ctx.calcul, ctx.sortie, ctx.sortie_dep
    surface = num(ctx.logement.get("surfaceHabitable")) or 0.0

    ef = ctx.postes_ef(s)
    efd = ctx.postes_ef(sd)
    ep = ctx.postes_ep(s)
    epd = ctx.postes_ep(sd)
    ges = ctx.postes_ges(s)
    gesd = ctx.postes_ges(sd)
    cout = ctx.postes_cout(s)
    coutd = ctx.postes_cout(sd)

    # ── deperditions ────────────────────────────────────────────────────────
    vent = (ctx.rows("XDPEdetailVentilation") or [{}])[0]
    dep = ET.SubElement(sortie, "deperdition")
    add(dep, "hvent", req(vent.get("Hvent")))
    add(dep, "hperm", req(vent.get("Hperm")))
    add(dep, "deperdition_renouvellement_air", req(c.get("aRA")))
    add(dep, "deperdition_mur", req(c.get("DPmurs")))
    add(dep, "deperdition_plancher_bas", req(c.get("DPplancher")))
    add(dep, "deperdition_plancher_haut", req(c.get("DPplafond")))
    add(dep, "deperdition_baie_vitree", req(c.get("DPvitrage")))
    add(dep, "deperdition_porte", req(c.get("DPportes")))
    # Analys'immo n'isole pas la part des ponts thermiques : elle se deduit du
    # GV, qui est la somme de toutes les deperditions.
    postes = sum(num(c.get(k)) or 0.0 for k in
                 ("DPmurs", "DPplancher", "DPplafond", "DPvitrage",
                  "DPportes", "aRA"))
    gv = num(c.get("GV"))
    add(dep, "deperdition_pont_thermique",
        req(num(c.get("PT")) if c.get("PT") else
            (gv - postes if gv else None)))
    add(dep, "deperdition_enveloppe", req(gv))

    # ── apports et besoins ──────────────────────────────────────────────────
    ab = ET.SubElement(sortie, "apport_et_besoin")
    add(ab, "surface_sud_equivalente", req(c.get("Sse")))
    add(ab, "apport_solaire_fr", req(s.get("apportsSolairesFr")))
    add(ab, "apport_interne_fr", req(s.get("apportsInternesFr")))
    add(ab, "apport_solaire_ch", req(s.get("apportsSolairesCh")))
    add(ab, "apport_interne_ch", req(s.get("apportsInternesCh")))
    add(ab, "fraction_apport_gratuit_ch", req(s.get("fractionApportsGratuit")))
    add(ab, "fraction_apport_gratuit_depensier_ch",
        req(sd.get("fractionApportsGratuit")))
    add(ab, "pertes_distribution_ecs_recup", req(s.get("pertesDistEcsRecup")))
    add(ab, "pertes_distribution_ecs_recup_depensier",
        req(sd.get("pertesDistEcsRecup")))
    add(ab, "pertes_stockage_ecs_recup", req(s.get("pertesStockageEcsRecup")))
    add(ab, "pertes_generateur_ch_recup", req(s.get("pertesGenChRecup")))
    add(ab, "pertes_generateur_ch_recup_depensier",
        req(sd.get("pertesGenChRecup")))
    add(ab, "nadeq", req(s.get("Nadeq")))
    add(ab, "v40_ecs_journalier", req(s.get("V40ecsJournalier")))
    add(ab, "v40_ecs_journalier_depensier", req(sd.get("V40ecsJournalier")))
    add(ab, "besoin_ch", req(s.get("Bch")))
    add(ab, "besoin_ch_depensier", req(sd.get("Bch")))
    add(ab, "besoin_ecs", req(s.get("Becs")))
    add(ab, "besoin_ecs_depensier", req(sd.get("Becs")))
    add(ab, "besoin_fr", req(s.get("Bfr")))
    add(ab, "besoin_fr_depensier", req(sd.get("Bfr")))

    # ── consommations en energie finale, puis primaire ──────────────────────
    def bloc(nom_bloc, prefixe, conv, depensier, aux="totale_auxiliaire"):
        # Le bloc « cout » écrit `cout_total_auxiliaire` là où les autres
        # écrivent `..._totale_auxiliaire` : le modèle n'est pas homogène.
        el = ET.SubElement(sortie, nom_bloc)
        p = prefixe
        add(el, p + "ch", req(conv["ch"]))
        add(el, p + "ch_depensier", req(depensier["ch"]))
        add(el, p + "ecs", req(conv["ecs"]))
        add(el, p + "ecs_depensier", req(depensier["ecs"]))
        add(el, p + "eclairage", req(conv["eclairage"]))
        add(el, p + "auxiliaire_generation_ch", req(conv["aux_gen_ch"]))
        add(el, p + "auxiliaire_generation_ch_depensier",
            req(depensier["aux_gen_ch"]))
        add(el, p + "auxiliaire_distribution_ch", req(conv["aux_dist_ch"]))
        add(el, p + "auxiliaire_generation_ecs", req(conv["aux_gen_ecs"]))
        add(el, p + "auxiliaire_generation_ecs_depensier",
            req(depensier["aux_gen_ecs"]))
        add(el, p + "auxiliaire_distribution_ecs", req(conv["aux_dist_ecs"]))
        add(el, p + "auxiliaire_ventilation", req(conv["ventilation"]))
        add(el, p + aux, req(conv["aux_total"]))
        add(el, p + "fr", req(conv["fr"]))
        add(el, p + "fr_depensier", req(depensier["fr"]))
        return el

    bef = bloc("ef_conso", "conso_", ef, efd)
    add(bef, "conso_5_usages", req(s.get("CtotalEf")))
    add(bef, "conso_5_usages_m2",
        req(par_m2(s.get("CtotalEf"), surface)))

    bep = bloc("ep_conso", "ep_conso_", ep, epd)
    add(bep, "ep_conso_5_usages", req(s.get("Ctotal")))
    add(bep, "ep_conso_5_usages_m2",
        req(c.get("consommationAnnuelleEPParm2")
            or par_m2(s.get("Ctotal"), surface)))
    add(bep, "classe_bilan_dpe", _classe_energie(ctx) or NIL)

    bges = bloc("emission_ges", "emission_ges_", ges, gesd)
    total_ges = num(c.get("emissionsGESAnnuelle"))
    add(bges, "emission_ges_5_usages", req(total_ges or ges["total"]))
    add(bges, "emission_ges_5_usages_m2",
        req(c.get("emissionsGESAnnuelleParm2")
            or ((total_ges / surface) if (total_ges and surface) else None)))
    add(bges, "classe_emission_ges", _classe_ges(ctx) or NIL)

    bcout = bloc("cout", "cout_", cout, coutd, aux="total_auxiliaire")
    add(bcout, "cout_5_usages", req(s.get("coutTotal") or c.get("Dtotal")))

    # ── production d'electricite ────────────────────────────────────────────
    pv = ET.SubElement(sortie, "production_electricite")
    add(pv, "production_pv", req(s.get("Ppv")))
    add(pv, "conso_elec_ac", req(s.get("CelecAc")))
    add(pv, "conso_elec_ac_ch", req(s.get("CelecAcCh")))
    add(pv, "conso_elec_ac_ecs", req(s.get("CelecAcEcs")))
    add(pv, "conso_elec_ac_fr", req(s.get("CelecAcFr")))
    add(pv, "conso_elec_ac_eclairage", req(s.get("CelecAcEcl")))
    add(pv, "conso_elec_ac_auxiliaire", req(s.get("CelecAcAux")))
    add(pv, "conso_elec_ac_autre_usage", req(s.get("CelecAcAu")))

    # ── repartition par energie ─────────────────────────────────────────────
    coll = ET.SubElement(sortie, "sortie_par_energie_collection")
    for entree in ctx.sortie_par_energie(s):
        spe = ET.SubElement(coll, "sortie_par_energie")
        add(spe, "enum_type_energie_id", entree["enum_type_energie_id"])
        for champ in ("conso_ch", "conso_ecs", "conso_5_usages",
                      "emission_ges_ch", "emission_ges_ecs",
                      "emission_ges_5_usages", "cout_ch", "cout_ecs",
                      "cout_5_usages"):
            add(spe, champ, req(entree[champ]))

    # ── confort d'ete ───────────────────────────────────────────────────────
    ce = ET.SubElement(sortie, "confort_ete")
    toits = ctx.rows("XDPEdetailSaisieEnvPlafond")
    isolation_toiture = any(t.get("ITI") or t.get("ITE") for t in toits)
    add(ce, "isolation_toiture", bool01(isolation_toiture))
    baies = ctx.rows("XDPEdetailSaisieEnvFenetre")
    protection = any(b.get("idFermeture") for b in baies)
    add(ce, "protection_solaire_exterieure", bool01(protection))
    traversant = bool(ctx.logement.get("perimetreSurExterieurNiv1"))
    add(ce, "aspect_traversant", bool01(traversant))
    brasseur = bool((ctx.rows("XDPEdetailVentilation") or [{}])[0].get("hasVmr"))
    add(ce, "brasseur_air", bool01(brasseur))
    inertie_lourde = str(ctx.logement.get("keyInertie") or "").upper() in ("TL", "L")
    add(ce, "inertie_lourde", bool01(inertie_lourde))
    add(ce, "enum_indicateur_confort_ete_id",
        _confort_ete(inertie_lourde, protection, traversant, isolation_toiture))

    # ── qualite de l'isolation ──────────────────────────────────────────────
    # L'ingestion attend ici des appreciations, pas les grandeurs physiques.
    qi = ET.SubElement(sortie, "qualite_isolation")
    sdep = ctx.surface_deperditive()
    ubat = num(ctx.lot.get("lot", {}).get("uBatCalcule"))
    if not ubat and gv and sdep:
        ubat = gv / sdep
    add(qi, "ubat", req(ubat))
    add(qi, "qualite_isol_enveloppe", _appreciation(ubat, SEUILS_UBAT))
    add(qi, "qualite_isol_mur",
        _appreciation(ctx.u_moyen("XDPEdetailSaisieEnvMur"), SEUILS_UMUR))
    add(qi, "qualite_isol_plancher_haut_toit_terrasse",
        _appreciation(ctx.u_moyen("XDPEdetailSaisieEnvPlafond"), SEUILS_UPH))
    add(qi, "qualite_isol_plancher_bas",
        _appreciation(ctx.u_moyen("XDPEdetailSaisieEnvPlancher"), SEUILS_UPB))
    add(qi, "qualite_isol_menuiserie",
        _appreciation(ctx.u_moyen("XDPEdetailSaisieEnvFenetre"), SEUILS_UMEN))
    return sortie


def build_fiches_techniques(ctx: Ctx) -> list[ET.Element]:
    """
    fiche_technique_collection : Analys'immo tient ces lignes dans
    `XDPEficheTechnique` (catégorie, entrée, valeur), avec la même intention
    que le modèle ADEME — justifier l'origine de chaque donnée saisie.
    """
    lignes = ctx.rows("XDPEficheTechnique")
    if not lignes:
        return []
    par_categorie: dict[str, list[dict]] = {}
    for r in lignes:
        par_categorie.setdefault(str(r.get("categorie") or ""), []).append(r)

    out = []
    for categorie, rs in par_categorie.items():
        ft = ET.Element("fiche_technique")
        code = ctx.tv("idEnumereFicheTechnique", categorie,
                      "fiche_technique/enum_categorie_fiche_technique_id")
        add(ft, "enum_categorie_fiche_technique_id", code or NIL)
        coll = ET.SubElement(ft, "sous_fiche_technique_collection")
        for r in sorted(rs, key=lambda x: (x.get("ordre") or 0)):
            sft = ET.SubElement(coll, "sous_fiche_technique")
            add(sft, "description", r.get("entree") or NIL)
            add(sft, "valeur", r.get("valeur") or NIL)
            # 1 = valeur observée sur site (cas par défaut d'une saisie
            # terrain) ; Analys'immo ne qualifie pas l'origine plus finement.
            add(sft, "enum_origine_donnee_id", "1")
        out.append(ft)
    return out


def _classe_energie(ctx: Ctx) -> str:
    """
    Étiquette énergie. Les seuils s'appliquent à la consommation **au m²** :
    `XDPEsortieMoteur.Ctotal` est un total annuel en kWh_ep, c'est
    `XDPEdetailCalcul.consommationAnnuelleEPParm2` qui porte le ratio.
    """
    conso = num(ctx.calcul.get("consommationAnnuelleEPParm2"))
    if conso is None:
        total = num(ctx.sortie.get("Ctotal"))
        surface = num(ctx.logement.get("surfaceHabitable"))
        conso = (total / surface) if (total and surface) else None
    seuils, _ = adn._seuils(ctx.src, _type_dossier(ctx))
    return adn._classe(conso, seuils) if conso else ""


def _classe_ges(ctx: Ctx) -> str:
    """Étiquette GES. `carboneTotal` est déjà ramené au m², lui."""
    co2 = num(ctx.sortie.get("carboneTotal"))
    if co2 is None:
        co2 = num(ctx.calcul.get("emissionsGESAnnuelleParm2"))
    _, seuils = adn._seuils(ctx.src, _type_dossier(ctx))
    return adn._classe(co2, seuils) if co2 else ""


def _type_dossier(ctx: Ctx) -> str:
    return (ctx.entete.get("idTypeDossierDPE")
            or ctx.mission.get("idTypeMission") or "")


# ── Assemblage ───────────────────────────────────────────────────────────────
def build_dpe(src, dossier: dict, mission: dict,
              cfg: dict | None = None) -> tuple[ET.Element, dict]:
    """
    Construit l'arbre <dpe> au format ADEME depuis Analys'immo.

    Retourne (élément racine, rapport de couverture). Le rapport liste les
    identifiants ADEME non résolus et les champs laissés vides : c'est lui qui
    dit ce que vaut le document, plutôt que de laisser croire à une couverture
    complète.
    """
    ctx = Ctx(src, dossier, mission, cfg)

    dpe = ET.Element("dpe", {"version": ENUM_VERSION_ID})
    dpe.append(build_administratif(ctx))

    logement = ET.SubElement(dpe, "logement")
    logement.append(build_caracteristique_generale(ctx))
    logement.append(build_meteo(ctx))

    enveloppe = ET.SubElement(logement, "enveloppe")
    enveloppe.append(build_inertie(ctx))

    murs = ET.SubElement(enveloppe, "mur_collection")
    for row in ctx.rows("XDPEdetailSaisieEnvMur"):
        _ajouter(ctx, murs, build_mur, row, "mur")
    pbs = ET.SubElement(enveloppe, "plancher_bas_collection")
    for row in ctx.rows("XDPEdetailSaisieEnvPlancher"):
        _ajouter(ctx, pbs, lambda c, r: build_plancher(c, r, "bas"), row,
                 "plancher bas")
    phs = ET.SubElement(enveloppe, "plancher_haut_collection")
    for row in ctx.rows("XDPEdetailSaisieEnvPlafond"):
        _ajouter(ctx, phs, lambda c, r: build_plancher(c, r, "haut"), row,
                 "plancher haut")
    baies = ET.SubElement(enveloppe, "baie_vitree_collection")
    for row in ctx.rows("XDPEdetailSaisieEnvFenetre"):
        _ajouter(ctx, baies, build_baie, row, "baie vitree")
    portes = ET.SubElement(enveloppe, "porte_collection")
    for row in ctx.rows("XDPEdetailSaisieEnvPorte"):
        _ajouter(ctx, portes, build_porte, row, "porte")
    ET.SubElement(enveloppe, "ets_collection")
    pts = ET.SubElement(enveloppe, "pont_thermique_collection")
    for row in ctx.rows("XDPEdetailPontThermique"):
        _ajouter(ctx, pts, build_pont_thermique, row, "pont thermique")

    vents = ET.SubElement(logement, "ventilation_collection")
    for row in ctx.rows("XDPEdetailVentilation"):
        _ajouter(ctx, vents, build_ventilation, row, "ventilation")
    clims = ET.SubElement(logement, "climatisation_collection")
    for row in ctx.rows("XDPEdetailClimatisation"):
        _ajouter(ctx, clims, build_climatisation, row, "climatisation")
    try:
        enr = build_production_enr(ctx)
    except Exception as e:
        enr = None
        ctx.incidents.append("production photovoltaique ignoree (%s : %s)"
                             % (type(e).__name__, str(e)[:120]))
    if enr is not None:
        logement.append(enr)

    generateurs = ctx.rows("XDPEdetailSaisieGenerateur")
    ecs_coll = ET.SubElement(logement, "installation_ecs_collection")
    for row in generateurs:
        if row.get("isECS"):
            _ajouter(ctx, ecs_coll, build_ecs, row, "installation ECS")
    ch_coll = ET.SubElement(logement, "installation_chauffage_collection")
    for row in generateurs:
        if row.get("isChauffage"):
            _ajouter(ctx, ch_coll, build_chauffage, row,
                     "installation de chauffage")

    logement.append(build_sortie(ctx))

    try:
        fiches = build_fiches_techniques(ctx)
    except Exception as e:
        fiches = []
        ctx.incidents.append("fiches techniques ignorees (%s : %s)"
                             % (type(e).__name__, str(e)[:120]))
    if fiches:
        coll = ET.SubElement(dpe, "fiche_technique_collection")
        for ft in fiches:
            coll.append(ft)

    rapport = {
        "source": "Analys'immo (ADN)",
        "reference_dossier": dossier.get("reference"),
        "id_mission": mission.get("idMission"),
        "elements": {
            "murs": len(ctx.rows("XDPEdetailSaisieEnvMur")),
            "planchers_bas": len(ctx.rows("XDPEdetailSaisieEnvPlancher")),
            "planchers_hauts": len(ctx.rows("XDPEdetailSaisieEnvPlafond")),
            "baies": len(ctx.rows("XDPEdetailSaisieEnvFenetre")),
            "portes": len(ctx.rows("XDPEdetailSaisieEnvPorte")),
            "ponts_thermiques": len(ctx.rows("XDPEdetailPontThermique")),
            "ventilations": len(ctx.rows("XDPEdetailVentilation")),
            "generateurs": len(generateurs),
            "emetteurs": len(ctx.emetteurs),
        },
        "calcul_disponible": bool(num(ctx.sortie.get("Ctotal"))),
        "identifiants_non_resolus": sorted(set(ctx.manquants)),
        "champs_vides": sorted(set(ctx.vides)),
        "incidents": sorted(set(ctx.incidents)),
    }
    rapport["nb_identifiants_non_resolus"] = len(rapport["identifiants_non_resolus"])
    return dpe, rapport


def _cle_ordre(el: ET.Element, parent_tag: str) -> str | None:
    """Clé de `ORDRE_MODELE` pour un élément, selon son parent."""
    tag = el.tag
    if tag in ("donnee_entree", "donnee_intermediaire"):
        return f"{parent_tag}.{tag}"
    if tag in ("adresse_bien", "adresse_proprietaire"):
        return "t_adresse"
    return tag if tag in ORDRE_MODELE else None


def conformer(root: ET.Element, rapport: dict) -> None:
    """
    Met le document en conformité avec la séquence du modèle : réordonne les
    enfants de chaque bloc connu et écarte les éléments absents du modèle.

    Un élément écarté est consigné dans le rapport : c'est un défaut du
    générateur, pas une donnée manquante, et il doit se voir.
    """
    # Champs introduits par le XSD 2024, absents du modele v2.2 embarque par
    # Analys'immo, mais que la passerelle joint deja aux envois LICIEL
    # (ademe_xml._enrich_administratif) : on les conserve pour que les deux
    # sources produisent le meme document.
    TOLERES = {"enum_consentement_formulaire_id", "horodatage_historisation"}
    ecartes: list[str] = []

    def descendre(el: ET.Element, chemin: str, parent_tag: str = ""):
        cle = _cle_ordre(el, parent_tag)
        if cle:
            ordre = {nom: i for i, nom in enumerate(ORDRE_MODELE[cle])}
            connus, toleres, inconnus = [], [], []
            for enf in list(el):
                if enf.tag in ordre:
                    connus.append(enf)
                elif enf.tag in TOLERES:
                    toleres.append(enf)
                else:
                    inconnus.append(enf)
            for enf in inconnus:
                ecartes.append(f"{chemin}/{enf.tag}")
                el.remove(enf)
            connus.sort(key=lambda e: ordre[e.tag])
            # Les champs tolérés (schéma 2024) passent après la séquence
            # connue, comme dans le document produit depuis LICIEL.
            for enf in connus + toleres:
                el.remove(enf)
            for enf in connus + toleres:
                el.append(enf)
        for enf in list(el):
            descendre(enf, f"{chemin}/{enf.tag}", el.tag)

    descendre(root, "/dpe")
    if ecartes:
        rapport["elements_hors_modele"] = sorted(set(ecartes))


def build(src, dossier: dict, mission: dict,
          cfg: dict | None = None) -> tuple[str, bytes, dict]:
    """
    XML ADEME prêt à joindre : (nom de fichier, octets UTF-8, rapport).

    Le nom reprend la convention du chemin LICIEL pour un document
    reconstruit — Opticheck sait déjà qu'un « reconstruit_ » n'est pas un XML
    publié par l'ADEME.
    """
    root, rapport = build_dpe(src, dossier, mission, cfg)
    conformer(root, rapport)
    body = ET.tostring(root, encoding="unicode")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    ref = re.sub(r"[^A-Za-z0-9._-]+", "-", str(dossier.get("reference") or "dpe"))
    return f"reconstruit_adn_{ref}.xml", xml.encode("utf-8"), rapport
