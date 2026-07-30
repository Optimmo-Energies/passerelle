"""
Génère les icônes Opticheck à partir des coordonnées SVG originales (viewBox 352x232).
Aucune dépendance externe — PIL uniquement.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Couleurs extraites du SVG ─────────────────────────────────────────────────
BG       = (10,  22,  40)     # fond sombre (#0A1628)
CREAM    = (253, 251, 246)    # #FDFBF6 — lettre O
GREEN_CK = (0,   155, 108)    # #009B6C — coche
BAR_G    = (7,   162,  77)    # #07a24d — barre verte
BAR_Y    = (255, 183,   6)    # #ffb706 — barre jaune
BAR_O    = (255, 108,  34)    # #ff6c22 — barre orange

# SVG viewBox : 352 × 232
SVG_W, SVG_H = 352, 232


def _scale(x: float, y: float, w: int, h: int):
    """Convertit un point SVG en pixel dans un canvas (w×h)."""
    return x * w / SVG_W, y * h / SVG_H


def _ring(draw: ImageDraw.ImageDraw, cx: float, cy: float,
          r_out: float, r_in: float, color: tuple) -> None:
    """Dessine un anneau plein (donut) avec le fond BG comme trou."""
    draw.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=color)
    draw.ellipse([cx - r_in,  cy - r_in,  cx + r_in,  cy + r_in],  fill=BG)


# ── Icône tray 32×32 ── (uniquement le O + coche) ────────────────────────────
def make_tray_icon() -> Image.Image:
    W = H = 48   # on génère en 48, pystray scale correctement
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Le O dans le SVG : centre ≈ (221, 96), R_ext ≈ 88, R_int ≈ 48
    # On mappe sur un carré 48×48 centré
    # La région O occupe SVG x∈[134,309] y∈[5,191] → carré ~175×186
    # On normalise cx/cy/r par rapport à cette région
    roi_x0, roi_y0, roi_x1, roi_y1 = 134.0, 3.0, 309.0, 191.0
    roi_w = roi_x1 - roi_x0
    roi_h = roi_y1 - roi_y0

    def s(x, y):
        px = (x - roi_x0) / roi_w * W
        py = (y - roi_y0) / roi_h * H
        return px, py

    cx_svg, cy_svg = 221.27, 95.93
    r_ext_svg = 88.0
    r_int_svg = 48.5

    cx, cy = s(cx_svg, cy_svg)
    scale_x = W / roi_w
    scale_y = H / roi_h
    sc = (scale_x + scale_y) / 2

    r_out = r_ext_svg * sc
    r_in  = r_int_svg * sc
    _ring(draw, cx, cy, r_out, r_in, CREAM)

    # Coche : M205 96 L219 110 L247 74 en SVG
    pts = [(s(*p)) for p in [(205, 96), (219, 110), (247, 74)]]
    lw = max(2, int(3 * sc))
    draw.line([pts[0], pts[1]], fill=GREEN_CK, width=lw)
    draw.line([pts[1], pts[2]], fill=GREEN_CK, width=lw)

    return img.resize((32, 32), Image.LANCZOS)


# ── Logo header dialog (300×72) ── barres + O + coche ────────────────────────
def make_header_logo() -> Image.Image:
    W, H = 300, 72
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ── Barres gauche (3 lignes horizontales décoratives) ────────────────────
    bx = 8
    for i, (color, label) in enumerate([(BAR_G, "A"), (BAR_Y, "B–C"), (BAR_O, "D–E")]):
        y0 = 10 + i * 20
        width = 28 + i * 12
        draw.rounded_rectangle([bx, y0, bx + width, y0 + 12], radius=3, fill=color)

    # ── O + coche ────────────────────────────────────────────────────────────
    cx, cy = 190, 36
    r_out, r_in = 30, 16
    _ring(draw, cx, cy, r_out, r_in, CREAM)

    # coche (homothétie depuis SVG center 221,96 → notre cx,cy)
    def map_ck(sx, sy):
        return cx + (sx - 221.27) * (r_out / 88.0), cy + (sy - 95.93) * (r_out / 88.0)

    p0 = map_ck(205, 96)
    p1 = map_ck(219, 110)
    p2 = map_ck(247, 74)
    draw.line([p0, p1], fill=GREEN_CK, width=4)
    draw.line([p1, p2], fill=GREEN_CK, width=4)

    # ── Texte "Passerelle Optimmo" ────────────────────────────────────────────
    fnt_dir = Path(__file__).parent / "fonts"
    try:
        fnt_lg = ImageFont.truetype(str(fnt_dir / "Inter-SemiBold.ttf"), 16)
        fnt_sm = ImageFont.truetype(str(fnt_dir / "Inter-Regular.ttf"),  10)
    except Exception:
        fnt_lg = fnt_sm = ImageFont.load_default()

    draw.text((232, 22), "Passerelle", fill=CREAM,               font=fnt_lg)
    draw.text((232, 42), "Optimmo",    fill=(160, 180, 200),     font=fnt_sm)

    return img


# ── Icône d'alerte 32×32 ── fond orange, ! dans le O ────────────────────────
def make_tray_icon_alert() -> Image.Image:
    """Icône d'alerte — fond orange, signale un dossier en attente de transmission."""
    ALERT_BG = BAR_O  # #ff6c22 orange

    W = H = 48
    img = Image.new("RGB", (W, H), ALERT_BG)
    draw = ImageDraw.Draw(img)

    roi_x0, roi_y0, roi_x1, roi_y1 = 134.0, 3.0, 309.0, 191.0
    roi_w = roi_x1 - roi_x0
    roi_h = roi_y1 - roi_y0

    def s(x, y):
        return (x - roi_x0) / roi_w * W, (y - roi_y0) / roi_h * H

    cx_svg, cy_svg = 221.27, 95.93
    r_ext_svg, r_int_svg = 88.0, 48.5

    cx, cy = s(cx_svg, cy_svg)
    sc = ((W / roi_w) + (H / roi_h)) / 2
    r_out = r_ext_svg * sc
    r_in  = r_int_svg * sc

    # Anneau crème, trou orange (pas BG)
    draw.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=CREAM)
    draw.ellipse([cx - r_in,  cy - r_in,  cx + r_in,  cy + r_in],  fill=ALERT_BG)

    # Barre du !
    bw = max(2, int(2.5 * sc))
    bh = int(r_in * 0.65)
    draw.rectangle(
        [cx - bw // 2, cy - bh - 1, cx + bw // 2, cy + 1],
        fill=CREAM,
    )
    # Point du !
    dr = max(1, int(sc * 1.1))
    draw.ellipse([cx - dr, cy + 3, cx + dr, cy + 3 + dr * 2], fill=CREAM)

    return img.resize((32, 32), Image.LANCZOS)


if __name__ == "__main__":
    make_tray_icon().save("icon_tray.png")
    make_tray_icon_alert().save("icon_tray_alert.png")
    make_header_logo().save("icon_header.png")
    print("Icônes générées.")
