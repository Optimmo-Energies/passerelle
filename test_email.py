"""
Génère un aperçu HTML du rapport Opticheck avec des données mock.

  python test_email.py           → ouvre l'aperçu dans le navigateur
  python test_email.py --send    → envoie aussi l'email (SMTP requis dans config.json)
"""
import subprocess
import sys
from pathlib import Path

import config
import email_report

MOCK_SUMMARY = {
    "dossier": "26_IMO_0103",
    "annee": "2026",
    "numero_ademe": "—",
    "classe_energie": "D",
    "classe_co2": "C",
    "consommation": "157",
    "co2_valeur": "28",
    "surface": "49",
    "cout_annuel": "1 080",
    "methode": "3CL-DPE 2021",
}

MOCK_ECARTS_DATA = {
    "lettre_dpe": "C",
    "lettre_ges": "B",
    "score_dpe": 157,
    "score_ges": 9,
    "score_global_reliability": 0.22,
    "ecarts": [
        {
            "type": "ABSENCE_DEMANDE_RECUEILLEMENT",
            "signification": "Absence de demande de recueillement des données",
            "message": (
                "Le formulaire de consentement ADEME, obligatoire depuis le 1er juillet 2024 "
                "(arrêté du 25 mars 2024), ne figure pas dans les pièces transmises. "
                "Ce document permet au commanditaire d'accepter ou refuser que ses coordonnées "
                "personnelles soient transmises à l'ADEME lors du dépôt du DPE. "
                "Son absence peut bloquer la transmission et remettre en cause la validité du DPE."
            ),
            "classification": "non_conforme",
            "intended_reaction": (
                "Vérifiez que le formulaire de consentement ADEME a bien été signé par le "
                "commanditaire et joint au dossier avant toute transmission."
            ),
            "level": 3,
        },
        {
            "type": "ABSENCE_PORTE_ENTREE",
            "signification": "Absence de porte d'entrée",
            "message": (
                "Aucune porte d'entrée n'a été saisie dans ce DPE. "
                "La porte d'entrée est un élément de l'enveloppe contribuant aux déperditions "
                "thermiques. Son absence sous-estime les déperditions et améliore "
                "artificiellement la performance calculée."
            ),
            "classification": "false_improvement",
            "intended_reaction": (
                "Renseignez la porte d'entrée avec ses caractéristiques réelles "
                "(surface, type, valeur Uw)."
            ),
            "level": 3,
        },
        {
            "type": "ABSENCE_BAIES_VITREES",
            "signification": "Absence de baies vitrées",
            "message": (
                "Aucune baie vitrée (fenêtre, porte-fenêtre, velux) n'a été renseignée dans ce DPE. "
                "Les menuiseries participent aux déperditions par conduction et aux apports solaires. "
                "Leur absence fausse significativement le résultat et améliore artificiellement la note."
            ),
            "classification": "false_improvement",
            "intended_reaction": (
                "Saisissez l'ensemble des menuiseries (fenêtres, portes-fenêtres, velux) "
                "avec leurs caractéristiques : surface, orientation, Uw et facteur solaire Sw."
            ),
            "level": 3,
        },
    ],
}


def main():
    html = email_report._build_html(MOCK_SUMMARY, MOCK_ECARTS_DATA)

    out = Path.home() / "Desktop" / "opticheck_test_email.html"
    out.write_text(html, encoding="utf-8")
    print(f"Aperçu sauvegardé : {out}")
    subprocess.Popen(["cmd", "/c", "start", "", str(out)], shell=False)

    if "--send" in sys.argv:
        cfg = config.load()
        to = cfg.get("report_to", "—")
        print(f"Envoi à {to}…")
        email_report._send_email(cfg, html, MOCK_SUMMARY["dossier"])
        print("Email envoyé.")


if __name__ == "__main__":
    main()
