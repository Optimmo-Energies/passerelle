"""Rapport Opticheck envoyé par email après analyse d'un DPE soumis."""
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

_DEFAULT_DELAY = 60

CLASSE_COLORS = {
    "A": "#07a24d", "B": "#4caf50", "C": "#ffb706",
    "D": "#ff9100", "E": "#ff6c22", "F": "#e53935", "G": "#7b1fa2",
}


def schedule_report(summary: dict, cfg: dict) -> None:
    """Lance en arrière-plan l'envoi du rapport Opticheck après délai."""
    if summary.get("numero_ademe", "—") == "—":
        return
    threading.Thread(
        target=_delayed_report,
        args=(summary, cfg),
        daemon=True,
    ).start()


def _delayed_report(summary: dict, cfg: dict) -> None:
    delay = cfg.get("report_delay_seconds", _DEFAULT_DELAY)
    time.sleep(delay)
    try:
        ecarts_data = _fetch_ecarts(summary["numero_ademe"], cfg)
        html = _build_html(summary, ecarts_data)
        _send_email(cfg, html, summary["dossier"])
    except Exception:
        pass  # ne jamais faire crasher l'app pour un email


def _fetch_ecarts(numero_ademe: str, cfg: dict) -> dict:
    base_url = cfg.get("opticheck_api_url", "").rstrip("/")
    if not base_url:
        raise ValueError("opticheck_api_url non configuré")
    key = cfg.get("opticheck_api_key") or cfg.get("api_key", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = requests.get(
        f"{base_url}/recherche_ecarts_dpe/{numero_ademe}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


_CRITIQUE_CLASSIFICATIONS = {"false_improvement", "false_deterioration", "non_conforme"}


def _classify(ecarts: list) -> tuple[list, list]:
    critiques, non_critiques = [], []
    for e in ecarts:
        if e.get("type") == "AUCUNE_ANOMALIE":
            continue
        if e.get("classification") in _CRITIQUE_CLASSIFICATIONS:
            critiques.append(e)
        else:
            non_critiques.append(e)
    return critiques, non_critiques


def _ecarts_section(ecarts: list, is_critique: bool) -> str:
    if not ecarts:
        return ""
    accent = "#C53030" if is_critique else "#D97706"
    bg = "#FFF5F5" if is_critique else "#FFFBEB"
    border = "#FEB2B2" if is_critique else "#FDE68A"
    titre = "Écart(s) critique(s)" if is_critique else "Écart(s) non-critique(s)"
    emoji = "🔴" if is_critique else "🟡"
    n = len(ecarts)
    word = "écart" if n == 1 else "écarts"

    items = ""
    for e in ecarts:
        signification = e.get("signification", "")
        message = e.get("message", "")
        reaction = e.get("intended_reaction") or ""
        classif = e.get("classification", "")

        badge = ""
        if classif == "false_improvement":
            badge = ' <span style="background:#FEF3C7;color:#92400E;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Amélioration fictive</span>'
        elif classif == "false_deterioration":
            badge = ' <span style="background:#FEE2E2;color:#991B1B;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Dégradation fictive</span>'
        elif classif == "non_conforme":
            badge = ' <span style="background:#EBF4FF;color:#2B6CB0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Non conforme</span>'

        reaction_html = (
            f'<div style="margin-top:8px;padding:8px 10px;background:#F7FAFC;'
            f'border-radius:4px;color:#4A5568;font-size:12px;font-style:italic;">'
            f"→ {reaction}</div>"
            if reaction else ""
        )

        items += f"""
        <div style="border-left:3px solid {accent};padding:12px 16px;
                    margin-bottom:10px;background:{bg};border-radius:0 6px 6px 0;
                    border:1px solid {border};border-left-width:3px;">
          <div style="font-weight:600;color:#1A202C;margin-bottom:6px;font-size:14px;">
            {signification}{badge}
          </div>
          <div style="color:#4A5568;font-size:13px;line-height:1.55;">{message}</div>
          {reaction_html}
        </div>"""

    return f"""
    <div style="margin-bottom:28px;">
      <div style="font-weight:700;font-size:15px;color:{accent};margin-bottom:12px;
                  display:flex;align-items:center;gap:6px;">
        {emoji}&nbsp;{n} {word} — {titre}
      </div>
      {items}
    </div>"""


def _build_html(summary: dict, data: dict) -> str:
    numero_ademe = summary.get("numero_ademe", "—")
    dossier = summary.get("dossier", "—")
    dossier = summary.get("dossier", "—")
    annee = summary.get("annee", "")
    surface = summary.get("surface", "—")
    methode = summary.get("methode", "—")
    cout_annuel = summary.get("cout_annuel", "—")
    lettre_dpe = data.get("lettre_dpe") or summary.get("classe_energie", "—")
    lettre_ges = data.get("lettre_ges") or summary.get("classe_co2", "—")
    score_dpe = data.get("score_dpe") or summary.get("consommation", "—")
    score_ges = data.get("score_ges") or summary.get("co2_valeur", "—")
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")

    dossier_label = f"{dossier} — {annee}" if annee else dossier

    ecarts = data.get("ecarts", [])
    critiques, non_critiques = _classify(ecarts)
    score_fiab = data.get("score_global_reliability")

    color_e = CLASSE_COLORS.get(str(lettre_dpe).upper(), "#718096")
    color_g = CLASSE_COLORS.get(str(lettre_ges).upper(), "#718096")

    if not ecarts or all(e.get("type") == "AUCUNE_ANOMALIE" for e in ecarts):
        resultats_html = """
        <div style="text-align:center;padding:28px 20px;background:#F0FFF4;
                    border-radius:8px;border:1px solid #9AE6B4;">
          <div style="font-size:36px;margin-bottom:10px;">✅</div>
          <div style="font-weight:700;color:#276749;font-size:16px;">Aucun écart détecté</div>
          <div style="color:#48BB78;margin-top:6px;font-size:13px;">
            Ce DPE ne présente pas d'anomalie identifiée par Opticheck.
          </div>
        </div>"""
    else:
        resultats_html = _ecarts_section(critiques, True) + _ecarts_section(non_critiques, False)

    fiab_html = ""
    if score_fiab is not None:
        pct = round(float(score_fiab) * 100)
        fc = "#009B6C" if pct >= 80 else ("#D97706" if pct >= 60 else "#C53030")
        fiab_html = f"""
        <div style="margin-top:12px;">
          <span style="display:inline-block;padding:4px 14px;background:{fc}22;
                       border-radius:20px;font-size:12px;color:{fc};font-weight:700;">
            Score de fiabilité Opticheck : {pct} %
          </span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Rapport Opticheck — DPE n°{numero_ademe}</title>
</head>
<body style="margin:0;padding:0;background:#F5F7FA;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:620px;margin:32px auto;background:#FFFFFF;
              border-radius:12px;overflow:hidden;
              box-shadow:0 2px 16px rgba(0,0,0,0.09);">

    <!-- Header -->
    <div style="background:#0A1628;padding:22px 32px;">
      <div style="font-size:24px;font-weight:800;color:#FFFFFF;
                  letter-spacing:-0.5px;">Opticheck</div>
      <div style="color:#94A3B8;font-size:12px;margin-top:3px;">par Optimmo Énergies</div>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px 8px;">

      <p style="color:#1A202C;font-size:15px;line-height:1.65;margin:0 0 22px;">
        Opticheck a analysé le DPE
        <strong style="color:#009B6C;font-family:monospace;font-size:15px;">{numero_ademe}</strong>
        transmis le <strong>{date_str}</strong>. Voici ce qui en ressort.
      </p>

      <!-- DPE summary card -->
      <div style="background:#F5F7FA;border-radius:8px;padding:18px 22px;
                  margin-bottom:26px;border:1px solid #E2E8F0;">
        <div style="font-size:11px;color:#718096;font-weight:700;
                    text-transform:uppercase;letter-spacing:0.8px;
                    margin-bottom:12px;">Résumé du diagnostic</div>
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;width:42%;">Dossier</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{dossier_label}</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">N° ADEME</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;
                       font-family:monospace;">{numero_ademe}</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">Surface habitable</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{surface} m²</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">Méthode de calcul</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{methode}</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">Conso. énergie primaire</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{score_dpe} kWh ep/m²/an</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">Émissions GES</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{score_ges} kg CO₂eq/m²/an</td>
          </tr>
          <tr>
            <td style="color:#718096;font-size:13px;padding:4px 0;">Coût annuel estimé</td>
            <td style="color:#1A202C;font-size:13px;font-weight:600;">{cout_annuel} €</td>
          </tr>
        </table>
        <div style="margin-top:14px;">
          <span style="background:{color_e};color:white;padding:5px 14px;
                       border-radius:5px;font-size:14px;font-weight:700;
                       margin-right:8px;">{lettre_dpe} — Énergie</span>
          <span style="background:{color_g};color:white;padding:5px 14px;
                       border-radius:5px;font-size:14px;font-weight:700;">{lettre_ges} — CO₂</span>
        </div>
        {fiab_html}
      </div>

      <!-- Divider -->
      <div style="border-top:2px solid #E2E8F0;margin-bottom:26px;"></div>

      <div style="font-size:15px;font-weight:700;color:#1A202C;
                  margin-bottom:18px;">Résultats de l'analyse</div>

      {resultats_html}

    </div>

    <!-- Footer -->
    <div style="padding:18px 32px;background:#F5F7FA;
                border-top:1px solid #E2E8F0;margin-top:12px;">
      <p style="color:#A0AEC0;font-size:11px;margin:0;text-align:center;
                line-height:1.6;">
        Ce rapport a été généré automatiquement par
        <strong style="color:#718096;">Opticheck</strong> — Optimmo Énergies.<br>
        Pour toute question :
        <a href="mailto:contact@optimmo-energies.com"
           style="color:#009B6C;text-decoration:none;">contact@optimmo-energies.com</a>
      </p>
    </div>

  </div>
</body>
</html>"""


def _send_email(cfg: dict, html_body: str, dossier: str) -> None:
    smtp_host = cfg.get("smtp_host", "")
    smtp_port = int(cfg.get("smtp_port", 587))
    smtp_user = cfg.get("smtp_user", "")
    smtp_password = cfg.get("smtp_password", "")
    from_addr = cfg.get("smtp_from") or smtp_user
    to_addr = cfg.get("report_to", "")

    if not all([smtp_host, smtp_user, smtp_password, to_addr]):
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Opticheck — Rapport d'analyse dossier n°{dossier}"
    msg["From"] = f"Opticheck <{from_addr}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port) as srv:
        srv.ehlo()
        srv.starttls(context=context)
        srv.login(smtp_user, smtp_password)
        srv.sendmail(from_addr, to_addr, msg.as_string())
