"""Boîte de dialogue de confirmation – Passerelle Optimmo."""
import threading
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import filedialog, ttk

import fonts_loader
from icon_gen import make_header_logo

fonts_loader.load_inter()

# ── Palette extraite du SVG Opticheck ────────────────────────────────────────
C_BG        = "#FFFFFF"
C_SURFACE   = "#F5F7FA"
C_BORDER    = "#E2E8F0"
C_HEADER_BG = "#0A1628"   # fond sombre Optimmo
C_ACCENT    = "#009B6C"   # vert coche
C_ACCENT_HO = "#007A56"
C_TEXT      = "#1A202C"
C_MUTED     = "#718096"
C_SUCCESS   = "#009B6C"
C_ERROR     = "#C53030"
C_BTN_SEC   = "#EDF2F7"
C_BTN_SEC_H = "#E2E8F0"

CLASSE_PALETTE = {
    "A": "#07a24d", "B": "#4caf50", "C": "#ffb706",
    "D": "#ff9100", "E": "#ff6c22", "F": "#e53935", "G": "#7b1fa2",
}

F_REG  = ("Inter", 9)
F_MED  = ("Inter", 9,  "bold")
F_SM   = ("Inter", 8)
F_LG   = ("Inter", 13, "bold")
F_ICON = ("Inter", 28, "bold")


# Frames braille d'un spinner (rendu par les polices Windows par défaut).
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _spin_button(root: tk.Misc, button: tk.Button,
                 label: str = "Envoi en cours") -> None:
    """
    Anime le texte d'un bouton (déjà désactivé) avec un spinner, pour signaler
    l'envoi en cours et décourager les clics répétés. L'animation s'arrête
    d'elle-même quand la fenêtre est détruite (TclError silencieuse).
    """
    state = {"i": 0}

    def tick() -> None:
        frame = _SPINNER_FRAMES[state["i"] % len(_SPINNER_FRAMES)]
        try:
            button.config(text=f"{frame}  {label}…")
        except tk.TclError:
            return
        state["i"] += 1
        root.after(80, tick)

    tick()


def _pil_to_tk(img) -> tk.PhotoImage:
    """Convertit une image PIL en PhotoImage tkinter via un PNG en mémoire."""
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return tk.PhotoImage(data=buf.getvalue())


def _is_empty(value) -> bool:
    """Une valeur LICIEL absente vaut '—' (ou vide)."""
    return value is None or str(value).strip() in ("", "—")


def _row(parent, label: str, value: str, value_color: str = C_TEXT):
    f = tk.Frame(parent, bg=C_SURFACE)
    f.pack(fill="x", pady=2)
    tk.Label(f, text=label, bg=C_SURFACE, fg=C_MUTED, font=F_SM,
             width=20, anchor="w").pack(side="left")
    tk.Label(f, text=value, bg=C_SURFACE, fg=value_color, font=F_MED,
             anchor="w").pack(side="left", fill="x", expand=True)


def _badge(parent, lettre: str, label: str):
    color = CLASSE_PALETTE.get(lettre.upper(), "#718096")
    tk.Label(parent, text=f"  {lettre}  {label}  ", bg=color, fg="white",
             font=F_SM, padx=4, pady=3).pack(side="left", padx=(0, 6))


def show_confirmation_dialog(summary: dict, nb_files: int, on_confirm) -> None:
    root = tk.Tk()
    root.title("Passerelle Optimmo")
    root.configure(bg=C_BG)
    root.resizable(False, False)

    w, h = 500, 480
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── En-tête avec logo ─────────────────────────────────────────────────────
    hdr = tk.Frame(root, bg=C_HEADER_BG, pady=0)
    hdr.pack(fill="x")

    logo_img = _pil_to_tk(make_header_logo())
    root._logo_ref = logo_img          # évite le garbage collect
    tk.Label(hdr, image=logo_img, bg=C_HEADER_BG, bd=0).pack(
        anchor="w", padx=4, pady=4)

    tk.Label(root, text="Soumettre ce DPE pour analyse avant validation",
             bg=C_BG, fg=C_MUTED, font=F_SM).pack(anchor="w", padx=20, pady=(8, 0))

    # ── Carte dossier ─────────────────────────────────────────────────────────
    card = tk.Frame(root, bg=C_SURFACE, padx=20, pady=14,
                    highlightbackground=C_BORDER, highlightthickness=1)
    card.pack(fill="x", padx=20, pady=(8, 0))

    # On n'affiche que les champs réellement présents dans le dossier LICIEL.
    _row(card, "Dossier", summary.get("dossier", "—"))
    if not _is_empty(summary.get("numero_ademe")):
        _row(card, "N° ADEME", summary["numero_ademe"], C_ACCENT)
    else:
        _row(card, "N° ADEME", "DPE en cours (non publié)", C_MUTED)
    if not _is_empty(summary.get("surface")):
        _row(card, "Surface", f"{summary['surface']} m²")
    if not _is_empty(summary.get("consommation")):
        _row(card, "Consommation", f"{summary['consommation']} kWh ep/m².an")
    if not _is_empty(summary.get("cout_annuel")):
        _row(card, "Coût annuel", summary["cout_annuel"])
    if not _is_empty(summary.get("methode")):
        _row(card, "Méthode", summary["methode"])

    if not (_is_empty(summary.get("classe_energie")) and _is_empty(summary.get("classe_co2"))):
        badge_row = tk.Frame(card, bg=C_SURFACE, pady=6)
        badge_row.pack(fill="x")
        tk.Label(badge_row, text="Classes", bg=C_SURFACE, fg=C_MUTED,
                 font=F_SM, width=20, anchor="w").pack(side="left")
        if not _is_empty(summary.get("classe_energie")):
            _badge(badge_row, summary["classe_energie"], "Énergie")
        if not _is_empty(summary.get("classe_co2")):
            _badge(badge_row, summary["classe_co2"], "CO₂")

    _row(card, "Fichiers XML", f"{nb_files} fichiers à transmettre")

    # ── Séparateur ────────────────────────────────────────────────────────────
    ttk.Separator(root).pack(fill="x", padx=20, pady=12)

    # ── Boutons ───────────────────────────────────────────────────────────────
    btn_row = tk.Frame(root, bg=C_BG, padx=20)
    btn_row.pack(fill="x")

    cancel_btn = tk.Button(
        btn_row, text="Annuler", bg=C_BTN_SEC, fg=C_TEXT,
        font=F_MED, relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_BTN_SEC_H, activeforeground=C_TEXT,
        command=root.destroy,
    )
    cancel_btn.pack(side="left")

    ok_btn = tk.Button(
        btn_row, text="Envoyer à Optimmo →", bg=C_ACCENT, fg="white",
        font=F_MED, relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_ACCENT_HO, activeforeground="white",
    )
    ok_btn.pack(side="right")

    def _on_ok():
        ok_btn.config(state="disabled", cursor="watch")
        cancel_btn.config(state="disabled")
        _spin_button(root, ok_btn)
        threading.Thread(
            target=lambda: root.after(0, lambda: _show_result(root, on_confirm())),
            daemon=True,
        ).start()

    ok_btn.config(command=_on_ok)
    root.mainloop()


def _show_result(parent: tk.Tk, message: str) -> None:
    parent.destroy()
    show_message(message)


def show_message(message: str) -> None:
    """Fenêtre de résultat autonome (succès/erreur détecté sur le texte)."""
    win = tk.Tk()
    win.title("Passerelle Optimmo – Résultat")
    win.configure(bg=C_BG)
    win.resizable(False, False)

    is_error = any(w in message.lower() for w in ("erreur", "error", "failed"))
    color = C_ERROR if is_error else C_SUCCESS
    icon  = "✕" if is_error else "✓"

    w2, h2 = 440, 210
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w2}x{h2}+{(sw - w2) // 2}+{(sh - h2) // 2}")

    tk.Frame(win, bg=color, height=4).pack(fill="x")
    tk.Label(win, text=icon, bg=C_BG, fg=color, font=F_ICON).pack(pady=(20, 6))
    tk.Label(win, text=message, bg=C_BG, fg=C_TEXT, font=F_REG,
             wraplength=400, justify="center").pack(padx=24)
    tk.Button(
        win, text="Fermer", bg=C_ACCENT, fg="white", font=F_MED,
        relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_ACCENT_HO, activeforeground="white",
        command=win.destroy,
    ).pack(pady=18)
    win.mainloop()


def show_reauth_dialog(message: str, on_reconnect) -> bool:
    """
    Prévient que la session Espace Pro a expiré et propose de se reconnecter.

    - message : texte affiché (contexte : session expirée / connexion requise).
    - on_reconnect() : lance la reconnexion navigateur (bloquant) et renvoie
      True/False. Exécuté dans un thread pour ne pas figer la fenêtre.

    Renvoie True si l'utilisateur s'est reconnecté avec succès.
    """
    result = {"ok": False}
    win = tk.Tk()
    win.title("Passerelle Optimmo — Connexion")
    win.configure(bg=C_BG)
    win.resizable(False, False)

    w2, h2 = 460, 300
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w2}x{h2}+{(sw - w2) // 2}+{(sh - h2) // 2}")

    hdr = tk.Frame(win, bg=C_HEADER_BG)
    hdr.pack(fill="x")
    logo_img = _pil_to_tk(make_header_logo())
    win._logo_ref = logo_img
    tk.Label(hdr, image=logo_img, bg=C_HEADER_BG, bd=0).pack(anchor="w", padx=4, pady=4)

    tk.Label(win, text="🔒", bg=C_BG, fg=C_ACCENT, font=("Inter", 24)).pack(pady=(16, 4))
    tk.Label(win, text=message, bg=C_BG, fg=C_TEXT, font=F_REG,
             wraplength=400, justify="center").pack(padx=24)
    status = tk.Label(win, text="", bg=C_BG, fg=C_MUTED, font=F_SM, wraplength=400)
    status.pack(pady=(8, 0))

    btn_row = tk.Frame(win, bg=C_BG)
    btn_row.pack(pady=18)
    later_btn = tk.Button(
        btn_row, text="Plus tard", bg=C_BTN_SEC, fg=C_TEXT, font=F_MED,
        relief="flat", padx=16, pady=9, cursor="hand2",
        activebackground=C_BTN_SEC_H, activeforeground=C_TEXT, command=win.destroy,
    )
    later_btn.pack(side="left", padx=(0, 8))
    reconnect_btn = tk.Button(
        btn_row, text="Se reconnecter", bg=C_ACCENT, fg="white", font=F_MED,
        relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_ACCENT_HO, activeforeground="white",
    )
    reconnect_btn.pack(side="left")

    def _finish(ok: bool) -> None:
        result["ok"] = ok
        if ok:
            win.destroy()
        else:
            status.config(text="Connexion impossible. Réessayez.", fg=C_ERROR)
            reconnect_btn.config(state="normal", text="Se reconnecter")
            later_btn.config(state="normal")

    def _do_reconnect() -> None:
        status.config(text="Une page de connexion va s'ouvrir dans votre navigateur…",
                      fg=C_MUTED)
        reconnect_btn.config(state="disabled", text="Reconnexion en cours…")
        later_btn.config(state="disabled")

        def worker() -> None:
            ok = False
            try:
                ok = bool(on_reconnect())
            except Exception:
                ok = False
            finally:
                win.after(0, lambda: _finish(ok))

        threading.Thread(target=worker, daemon=True).start()

    reconnect_btn.config(command=_do_reconnect)
    win.mainloop()
    return result["ok"]


# ── Sélection du logiciel de diagnostic ───────────────────────────────────────
def show_diag_setup_dialog(classify, source_labels: dict,
                           initial_dir: str = "",
                           heading: str = "Aucun logiciel de diagnostic détecté",
                           body: str | None = None) -> tuple[str, str] | None:
    """
    Explique qu'aucun logiciel de diagnostic n'est exploitable et propose de
    sélectionner le dossier d'installation du logiciel (LICIEL ou ADN
    Evaluation).

    - classify(path) -> (source, valeur) | None : identifie le logiciel à
      partir du dossier choisi (voir diag_setup.classify_dir).
    - source_labels : {code source → libellé lisible} pour le message de succès.
    - heading / body : textes adaptables (premier lancement vs reconfiguration).

    Renvoie (source, valeur) si un dossier valide a été retenu, sinon None.
    """
    if body is None:
        body = ("La passerelle n'a trouvé ni LICIEL Diagnostics ni ADN "
                "Evaluation sur ce poste.\n\nUn logiciel de diagnostic est "
                "nécessaire pour transmettre vos DPE. S'il est installé à un "
                "emplacement personnalisé, indiquez le dossier où il se trouve.")
    result: dict = {"value": None}
    win = tk.Tk()
    win.title("Passerelle Optimmo — Logiciel de diagnostic")
    win.configure(bg=C_BG)
    win.resizable(False, False)

    w2, h2 = 500, 360
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w2}x{h2}+{(sw - w2) // 2}+{(sh - h2) // 2}")

    hdr = tk.Frame(win, bg=C_HEADER_BG)
    hdr.pack(fill="x")
    logo_img = _pil_to_tk(make_header_logo())
    win._logo_ref = logo_img
    tk.Label(hdr, image=logo_img, bg=C_HEADER_BG, bd=0).pack(anchor="w", padx=4, pady=4)

    tk.Label(win, text=heading,
             bg=C_BG, fg=C_TEXT, font=F_LG).pack(anchor="w", padx=20, pady=(16, 4))
    tk.Label(
        win, text=body,
        bg=C_BG, fg=C_MUTED, font=F_REG, wraplength=460, justify="left",
    ).pack(anchor="w", padx=20)

    status = tk.Label(win, text="", bg=C_BG, fg=C_MUTED, font=F_SM,
                      wraplength=460, justify="left")
    status.pack(anchor="w", padx=20, pady=(12, 0))

    def _choose() -> None:
        path = filedialog.askdirectory(
            parent=win,
            title="Sélectionnez le dossier de votre logiciel de diagnostic",
            initialdir=initial_dir or None,
            mustexist=True,
        )
        if not path:
            return
        found = classify(path)
        if found is None:
            status.config(
                text=("Dossier non reconnu comme LICIEL ou ADN Evaluation.\n"
                      "Sélectionnez le dossier racine du logiciel "
                      "(ex. C:\\LICIEL_Diagnostics ou C:\\ADN_Evaluation)."),
                fg=C_ERROR,
            )
            return
        label = source_labels.get(found[0], found[0])
        status.config(text=f"{label} détecté. Configuration enregistrée.",
                      fg=C_SUCCESS)
        result["value"] = found
        win.after(700, win.destroy)

    btn_row = tk.Frame(win, bg=C_BG)
    btn_row.pack(side="bottom", fill="x", padx=20, pady=16)
    tk.Button(
        btn_row, text="Plus tard", bg=C_BTN_SEC, fg=C_TEXT, font=F_MED,
        relief="flat", padx=16, pady=9, cursor="hand2",
        activebackground=C_BTN_SEC_H, activeforeground=C_TEXT, command=win.destroy,
    ).pack(side="left")
    tk.Button(
        btn_row, text="Choisir le dossier…", bg=C_ACCENT, fg="white", font=F_MED,
        relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_ACCENT_HO, activeforeground="white", command=_choose,
    ).pack(side="right")

    win.mainloop()
    return result["value"]


# ── Sélection multi-dossiers ──────────────────────────────────────────────────
def _norm(s) -> str:
    """Minuscule + sans accents, pour une recherche tolérante."""
    s = str(s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# Champs de tri proposés : libellé affiché → clé de tri sur un dossier.
_SORT_KEYS = {
    "Date (récent)":    lambda d: -d.get("_order", 0),
    "Dossier":          lambda d: _norm(d.get("dossier")),
    "Donneur d'ordre":  lambda d: _norm(d.get("donneur_ordre")),
    "Adresse":          lambda d: _norm(d.get("adresse")),
    "Classe énergie":   lambda d: _norm(d.get("classe_energie")),
}


def show_dossier_selection_dialog(dossiers: list[dict], on_send) -> None:
    """
    Affiche la liste des dossiers LICIEL avec, par dossier : case à cocher,
    nom, donneur d'ordre, adresse, n° ADEME et classes. Recherche, tri et
    masquage des dossiers sans mission DPE sont disponibles.

    - dossiers : liste de dicts (résumé LICIEL enrichi des clés 'path' et '_order').
    - on_send(selection) : reçoit la liste des dossiers cochés et renvoie un
      message de statut affiché ensuite à l'utilisateur.
    """
    # On garde l'ordre d'origine (date décroissante) comme clé de tri "Date".
    for i, d in enumerate(dossiers):
        d.setdefault("_order", len(dossiers) - i)

    root = tk.Tk()
    root.title("Passerelle Optimmo — Dossiers")
    root.configure(bg=C_BG)
    root.resizable(False, False)

    w, h = 620, 680
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # État partagé (persiste à travers les re-rendus).
    sel_vars: dict[str, tk.BooleanVar] = {}
    search_var = tk.StringVar()
    sort_var = tk.StringVar(value="Date (récent)")
    hide_var = tk.BooleanVar(value=False)
    dir_desc = {"v": True}

    def _var(d: dict) -> tk.BooleanVar:
        key = str(d["path"])
        if key not in sel_vars:
            sel_vars[key] = tk.BooleanVar(value=False)
        return sel_vars[key]

    # En-tête
    hdr = tk.Frame(root, bg=C_HEADER_BG)
    hdr.pack(fill="x")
    logo_img = _pil_to_tk(make_header_logo())
    root._logo_ref = logo_img
    tk.Label(hdr, image=logo_img, bg=C_HEADER_BG, bd=0).pack(
        anchor="w", padx=4, pady=4)

    nb_dpe = sum(1 for d in dossiers if d.get("has_dpe"))
    tk.Label(
        root,
        text=f"{len(dossiers)} dossiers — {nb_dpe} avec une mission DPE transmissible",
        bg=C_BG, fg=C_MUTED, font=F_SM,
    ).pack(anchor="w", padx=20, pady=(8, 2))

    # ── Barre d'outils : recherche + tri + masquage ───────────────────────────
    tools = tk.Frame(root, bg=C_BG)
    tools.pack(fill="x", padx=20, pady=(4, 0))

    tk.Label(tools, text="Rechercher", bg=C_BG, fg=C_MUTED, font=F_SM).pack(
        side="left")
    search_entry = tk.Entry(tools, textvariable=search_var, font=F_REG,
                            relief="flat", bg=C_SURFACE,
                            highlightbackground=C_BORDER, highlightthickness=1)
    search_entry.pack(side="left", fill="x", expand=True, padx=(6, 10), ipady=3)

    tk.Label(tools, text="Trier", bg=C_BG, fg=C_MUTED, font=F_SM).pack(side="left")
    sort_combo = ttk.Combobox(tools, textvariable=sort_var, state="readonly",
                              values=list(_SORT_KEYS), width=14, font=F_SM)
    sort_combo.pack(side="left", padx=(6, 4))

    dir_btn = tk.Button(tools, text="▼", bg=C_BTN_SEC, fg=C_TEXT, font=F_SM,
                        relief="flat", width=3, cursor="hand2",
                        activebackground=C_BTN_SEC_H)
    dir_btn.pack(side="left")

    hide_row = tk.Frame(root, bg=C_BG)
    hide_row.pack(fill="x", padx=20, pady=(6, 4))
    tk.Checkbutton(
        hide_row, text="Masquer les dossiers sans mission DPE",
        variable=hide_var, bg=C_BG, activebackground=C_BG, fg=C_TEXT,
        font=F_SM, selectcolor=C_BG, cursor="hand2",
    ).pack(side="left")
    count_lbl = tk.Label(hide_row, text="", bg=C_BG, fg=C_MUTED, font=F_SM)
    count_lbl.pack(side="right")

    # ── Liste scrollable ──────────────────────────────────────────────────────
    list_wrap = tk.Frame(root, bg=C_BG)
    list_wrap.pack(fill="both", expand=True, padx=20, pady=(2, 0))
    canvas = tk.Canvas(list_wrap, bg=C_BG, highlightthickness=0)
    scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=C_BG)
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw", width=w - 56)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def _on_wheel(event):
        canvas.yview_scroll(int(-event.delta / 120), "units")
    canvas.bind_all("<MouseWheel>", _on_wheel)

    def _visible() -> list[dict]:
        items = dossiers
        if hide_var.get():
            items = [d for d in items if d.get("has_dpe")]
        q = _norm(search_var.get())
        if q:
            def match(d):
                hay = " ".join(_norm(d.get(k)) for k in
                               ("dossier", "donneur_ordre", "adresse", "numero_ademe"))
                return all(part in hay for part in q.split())
            items = [d for d in items if match(d)]
        keyf = _SORT_KEYS.get(sort_var.get(), _SORT_KEYS["Date (récent)"])
        return sorted(items, key=keyf, reverse=dir_desc["v"])

    def _render():
        for child in inner.winfo_children():
            child.destroy()
        items = _visible()
        count_lbl.config(text=f"{len(items)} affiché(s)")
        for d in items:
            eligible = d.get("has_dpe", False)
            rowf = tk.Frame(inner, bg=C_SURFACE if eligible else C_BG,
                            highlightbackground=C_BORDER, highlightthickness=1)
            rowf.pack(fill="x", pady=3)

            tk.Checkbutton(
                rowf, variable=_var(d), bg=rowf["bg"], activebackground=rowf["bg"],
                selectcolor=C_BG, state="normal" if eligible else "disabled",
            ).pack(side="left", padx=(6, 4), pady=8)

            info = tk.Frame(rowf, bg=rowf["bg"])
            info.pack(side="left", fill="x", expand=True, pady=4)

            tk.Label(info, text=d.get("dossier", "—"), bg=rowf["bg"],
                     fg=C_TEXT if eligible else C_MUTED, font=F_MED,
                     anchor="w").pack(fill="x")

            do = d.get("donneur_ordre", "")
            adr = d.get("adresse", "")
            line2_bits = []
            if do:
                line2_bits.append(f"DO : {do}")
            if adr:
                line2_bits.append(adr)
            if line2_bits:
                tk.Label(info, text="   ·   ".join(line2_bits), bg=rowf["bg"],
                         fg=C_TEXT if eligible else C_MUTED, font=F_SM,
                         anchor="w").pack(fill="x")

            if eligible:
                ademe = d.get("numero_ademe", "—")
                ademe_txt = ademe if not _is_empty(ademe) else "DPE en cours (non publié)"
                bits = [f"ADEME : {ademe_txt}"]
                if not _is_empty(d.get("classe_energie")):
                    bits.append(f"Énergie {d['classe_energie']}")
                if not _is_empty(d.get("classe_co2")):
                    bits.append(f"CO₂ {d['classe_co2']}")
                sub = "   ·   ".join(bits)
            else:
                sub = "Pas de mission DPE — rien à transmettre"
            tk.Label(info, text=sub, bg=rowf["bg"], fg=C_MUTED, font=F_SM,
                     anchor="w").pack(fill="x")
        canvas.yview_moveto(0)

    def _flip_dir():
        dir_desc["v"] = not dir_desc["v"]
        dir_btn.config(text="▼" if dir_desc["v"] else "▲")
        _render()

    dir_btn.config(command=_flip_dir)
    search_var.trace_add("write", lambda *_: _render())
    sort_combo.bind("<<ComboboxSelected>>", lambda *_: _render())
    hide_row.winfo_children()[0].config(command=_render)

    # ── Barre d'action ────────────────────────────────────────────────────────
    ttk.Separator(root).pack(fill="x", padx=20, pady=(8, 0))
    btn_row = tk.Frame(root, bg=C_BG, padx=20, pady=12)
    btn_row.pack(fill="x")

    status_lbl = tk.Label(root, text="", bg=C_BG, fg=C_MUTED, font=F_SM)
    status_lbl.pack(pady=(0, 8))

    def _toggle_all():
        shown = [d for d in _visible() if d.get("has_dpe")]
        target = not (shown and all(_var(d).get() for d in shown))
        for d in shown:
            _var(d).set(target)

    all_btn = tk.Button(
        btn_row, text="Tous (affichés)", bg=C_BTN_SEC, fg=C_TEXT, font=F_MED,
        relief="flat", padx=16, pady=9, cursor="hand2",
        activebackground=C_BTN_SEC_H, command=_toggle_all,
    )
    all_btn.pack(side="left")

    cancel_btn = tk.Button(
        btn_row, text="Fermer", bg=C_BTN_SEC, fg=C_TEXT, font=F_MED,
        relief="flat", padx=16, pady=9, cursor="hand2",
        activebackground=C_BTN_SEC_H, command=root.destroy,
    )
    cancel_btn.pack(side="left", padx=(8, 0))

    send_btn = tk.Button(
        btn_row, text="Envoyer la sélection →", bg=C_ACCENT, fg="white",
        font=F_MED, relief="flat", padx=18, pady=9, cursor="hand2",
        activebackground=C_ACCENT_HO, activeforeground="white",
    )
    send_btn.pack(side="right")

    def _do_send():
        selection = [d for d in dossiers
                     if str(d["path"]) in sel_vars and sel_vars[str(d["path"])].get()]
        if not selection:
            status_lbl.config(text="Sélectionnez au moins un dossier.", fg=C_ERROR)
            return
        send_btn.config(state="disabled", cursor="watch")
        all_btn.config(state="disabled")
        cancel_btn.config(state="disabled")
        canvas.unbind_all("<MouseWheel>")
        _spin_button(root, send_btn, "Envoi de la sélection")
        threading.Thread(
            target=lambda: root.after(0, lambda: _show_result(root, on_send(selection))),
            daemon=True,
        ).start()

    send_btn.config(command=_do_send)
    _render()
    root.mainloop()
