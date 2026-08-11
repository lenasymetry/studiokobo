"""
Export vues 3D cotées – PDF style KOBO.

Mise en forme :
    - 1 vue isométrique par caisson (azim=225, fond blanc, ligne-art)
  - Toutes les cotations nécessaires à la fabrication
  - Barre orange KOBO en bas avec vrai logo
  - Nom du projet en haut à droite (gras)
"""

from __future__ import annotations

import io
import os
from typing import List, Dict, Optional

import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.transforms import Bbox
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import to_rgba
from PIL import Image, ImageDraw
from machining_logic import calculate_all_zones_2d

# Résolution du rendu 3D matriciel (pixels par pouce de la zone de vue).
# Un vrai z-buffer pixel par pixel : chaque pixel de la vue garde la face la
# plus proche de la caméra, ce qui élimine par construction toute erreur
# d'ordre d'affichage (étagère cachée par un montant, un fond, etc.) — quelle
# que soit la géométrie du meuble, contrairement à un tri de polygones
# (peintre) qui reste une approximation.
_RASTER_DPI = 260

# ─────────────────────────────────────────────────────────────────────────────
# Constantes visuelles KOBO
# ─────────────────────────────────────────────────────────────────────────────

_ORANGE  = "#C85529"   # barre du bas
_DARK    = "#1E1E1E"   # texte principal et arêtes
_GREY_TXT = "#5A5A5A"  # texte secondaire
_FACE    = "#EBEBEB"   # remplissage faces (gris clair, vue frontale)
_FACE_TOP = "#D8D8D8"  # face supérieure
_FACE_SIDE = "#E0E0E0" # face latérale
_FACE_SHELF = "#B8B8B8" # face avant des étagères (plus foncée pour bien les distinguer)
_DIM     = "#1A1A1A"   # lignes et textes de cotation
_DIM_LW  = 0.55
_CAB_LW  = 0.9
_DIM_FS  = 5.5
_DESC_FS = 7.5

# ─────────────────────────────────────────────────────────────────────────────
# Chargement du logo KOBO
# ─────────────────────────────────────────────────────────────────────────────

def _load_kobo_logo() -> Optional[np.ndarray]:
    """Charge kobo_logo.png et inverse les couleurs (blanc → noir) si besoin."""
    here = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(here, "kobo_logo.png")
    if not os.path.exists(logo_path):
        return None
    try:
        from PIL import Image
        img = Image.open(logo_path).convert("RGBA")
        arr = np.array(img, dtype=np.float32)
        # Détecte si le logo est blanc (canaux RGB majoritairement > 200)
        rgb_mean = arr[:, :, :3].mean()
        if rgb_mean > 180:
            # Inverse RGB pour passer blanc → noir ; garde l'alpha intact
            arr[:, :, :3] = 255.0 - arr[:, :, :3]
        return arr.astype(np.uint8)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Géométrie 3D
# ─────────────────────────────────────────────────────────────────────────────

def _rotate_points(points, angle_deg: float, axis: str, pivot: tuple[float, float, float]):
    if abs(float(angle_deg)) <= 1e-9:
        return points
    px, py, pz = pivot
    a = math.radians(float(angle_deg))
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y, z in points:
        xr, yr, zr = x - px, y - py, z - pz
        if axis == "x":
            yr, zr = yr * ca - zr * sa, yr * sa + zr * ca
        elif axis == "y":
            xr, zr = xr * ca + zr * sa, -xr * sa + zr * ca
        else:
            xr, yr = xr * ca - yr * sa, xr * sa + yr * ca
        out.append((xr + px, yr + py, zr + pz))
    return out


def _box_faces(ox, oy, oz, w, d, h, rotation_angle=0.0, rotation_axis="z", rotation_pivot=None):
    x0, y0, z0 = ox, oy, oz
    x1, y1, z1 = ox + w, oy + d, oz + h

    vertices = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    if rotation_pivot is not None and abs(float(rotation_angle)) > 1e-9:
        vertices = _rotate_points(vertices, rotation_angle, rotation_axis, rotation_pivot)

    return {
        "haut":    [vertices[4], vertices[5], vertices[6], vertices[7]],
        "avant":   [vertices[0], vertices[1], vertices[5], vertices[4]],
        "droite":  [vertices[1], vertices[2], vertices[6], vertices[5]],
        "gauche":  [vertices[0], vertices[3], vertices[7], vertices[4]],
        "arriere": [vertices[3], vertices[2], vertices[6], vertices[7]],
        "bas":     [vertices[0], vertices[1], vertices[2], vertices[3]],
    }


# Normales des 6 faces d'un box (outward normals)
_BOX_NORMALS = {
    "haut":    ( 0,  0, +1),
    "bas":     ( 0,  0, -1),
    "avant":   ( 0, -1,  0),
    "arriere": ( 0, +1,  0),
    "droite":  (+1,  0,  0),
    "gauche":  (-1,  0,  0),
}

def _view_vector(azim_deg: float, elev_deg: float):
    """Vecteur scène → caméra (unitaire) : direction de l'œil vue depuis le
    centre du meuble, pour l'angle de vue donné. Une face est visible si sa
    normale sortante pointe globalement vers ce vecteur (`_backface_set`),
    et c'est ce même vecteur qui sert d'axe de profondeur caméra dans
    `_camera_basis` : le culling de faces cachées et la projection utilisent
    donc toujours la même notion de caméra."""
    az = math.radians(azim_deg)
    el = math.radians(elev_deg)
    vx = math.cos(el) * math.cos(az)
    vy = math.cos(el) * math.sin(az)
    vz = math.sin(el)
    return vx, vy, vz


def _backface_set(azim_deg: float, elev_deg: float) -> frozenset:
    """Retourne les noms des faces cachées (backfaces) pour l'angle de vue donné.
    Une face est cachée si sa normale pointe dans la direction opposée à la caméra."""
    vx, vy, vz = _view_vector(azim_deg, elev_deg)
    return frozenset(
        n for n, (nx, ny, nz) in _BOX_NORMALS.items()
        if nx * vx + ny * vy + nz * vz <= 0
    )


def _add_box(collector: list, ox, oy, oz, w, d, h, alpha=0.95, shelf=False,
             skip: frozenset = frozenset(), rotation_angle=0.0,
             rotation_axis="z", rotation_pivot=None):
    """Calcule les faces visibles d'une boîte et les empile dans `collector`
    (de simples données géométriques, rien n'est dessiné ici). `_rasterize_scene`
    se charge ensuite de composer toutes les faces de la scène dans une image
    matricielle par un vrai test de profondeur pixel par pixel (z-buffer)."""
    if w <= 0 or d <= 0 or h <= 0:
        return
    faces = _box_faces(
        ox, oy, oz, w, d, h,
        rotation_angle=rotation_angle,
        rotation_axis=rotation_axis,
        rotation_pivot=rotation_pivot,
    )
    fc_avant = _FACE_SHELF if shelf else _FACE
    colors = {
        "haut": _FACE_TOP, "avant": fc_avant, "droite": _FACE_SIDE,
        "gauche": _FACE_SIDE, "arriere": _FACE_TOP, "bas": _FACE_TOP,
    }
    lw = _CAB_LW * 1.4 if shelf else _CAB_LW
    _alpha = 1.0 if shelf else alpha
    local_skip = skip if abs(float(rotation_angle)) <= 1e-9 else frozenset()
    for name, pts in faces.items():
        if name in local_skip:
            continue  # backface culling : face cachée pour cet angle de vue
        collector.append({
            "pts": pts,
            "facecolor": colors[name], "lw": lw, "alpha": _alpha,
        })


def _plane_from_points(xs, ys, zs):
    """Ajuste un plan z = a·x + b·y + c (moindres carrés) à des points d'une
    face plane, déjà projetés en coordonnées écran (px, py) avec leur
    profondeur caméra. Comme la projection est orthographique (affine), la
    profondeur varie de façon exactement linéaire sur l'écran : ce plan donne
    donc la vraie profondeur en tout point intérieur de la face, pas
    seulement à ses sommets."""
    A = np.column_stack([xs, ys, np.ones_like(xs)])
    coeffs, *_ = np.linalg.lstsq(A, zs, rcond=None)
    return coeffs  # a, b, c


def _fit_affine(corners2d, rect):
    """Calcule l'affine (échelle uniforme + décalage) qui inscrit ('contain',
    proportions conservées) le rectangle englobant de `corners2d` dans `rect`
    (x0,y0,w,h en coordonnées de `ax_img`). Renvoie une fonction
    (sx,sy) -> (ax_x, ax_y). Calcul entièrement autonome, déterministe, qui ne
    dépend que des valeurs passées en argument.

    L'échelle est uniforme sur les deux axes, ce qui est correct parce que
    `ax_img` est rendu carré par `imshow` (aspect 'equal') : une unité de
    données y mesure la même longueur physique en x et en y."""
    xs = [c[0] for c in corners2d]
    ys = [c[1] for c in corners2d]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    span_x = (x1 - x0) or 1e-9
    span_y = (y1 - y0) or 1e-9
    rx0, ry0, rw, rh = rect
    scale = min(rw / span_x, rh / span_y)
    off_x = rx0 + (rw - span_x * scale) / 2 - x0 * scale
    off_y = ry0 + (rh - span_y * scale) / 2 - y0 * scale

    def to_fig(sx, sy):
        return sx * scale + off_x, sy * scale + off_y

    return to_fig


def _camera_basis(azim_deg: float, elev_deg: float):
    """Base orthonormée (droite écran, haut écran, profondeur caméra) pour
    l'angle de vue donné, dérivée du MÊME vecteur de regard que
    `_backface_set` (`_view_vector`) : le culling de faces cachées et la
    projection partagent donc, par construction, exactement la même notion de
    caméra — aucune divergence possible entre "quelle face est visible" et
    "où elle est dessinée"."""
    gaze = np.array(_view_vector(azim_deg, elev_deg))  # caméra → scène
    world_up = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(gaze, world_up))) > 0.999:
        world_up = np.array([0.0, 1.0, 0.0])   # vue au zénith : évite un produit vectoriel nul
    right = np.cross(gaze, world_up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, gaze)
    up = up / np.linalg.norm(up)
    return right, up, gaze


def _make_projector(azim_deg, elev_deg, view_bounds, rect):
    """LA fonction de projection unique 3D → 2D (fraction figure) du module :
    projection orthographique isométrique CALCULÉE À LA MAIN (algèbre
    vectorielle pure, `numpy`/`math` seulement) — aucune dépendance à
    matplotlib/mplot3d (pas d'Axes3D, pas de get_proj/transData/apply_aspect).
    C'est précisément ce qui manquait avant : ces objets matplotlib ont un
    état interne mutable (recadrage de la position réelle des axes au premier
    `fig.canvas.draw()`) qui pouvait diverger entre le moment où le meuble
    était rastérisé et celui où les cotations étaient posées, provoquant un
    décalage systématique. Ici, `project` est une fonction PURE : mêmes
    arguments ⇒ même résultat, à tout instant, sans effet du moment où elle
    est appelée. Le rendu matriciel du meuble (`_rasterize_scene`) ET toutes
    les cotations (`_draw_dims_overlay`) appellent cette MÊME closure — par
    construction, ils ne peuvent donc plus diverger.

    `view_bounds` = (xlim, ylim, zlim), les mêmes limites que celles utilisées
    pour cadrer la vue (marges comprises) : ses 8 coins définissent l'échelle/
    le centrage dans `rect` via `_fit_affine`."""
    right, up, gaze = _camera_basis(azim_deg, elev_deg)

    def view_xyz(x, y, z):
        p = np.array([x, y, z], dtype=float)
        sx = float(np.dot(p, right))
        sy = float(np.dot(p, up))
        depth = float(np.dot(p, gaze))   # plus GRAND = plus proche de la caméra
        return sx, sy, depth

    xlim, ylim, zlim = view_bounds
    corners2d = [
        view_xyz(x, y, z)[:2]
        for x in xlim for y in ylim for z in zlim
    ]
    to_fig = _fit_affine(corners2d, rect)

    def project_with_depth(x, y, z):
        sx, sy, depth = view_xyz(x, y, z)
        fx, fy = to_fig(sx, sy)
        return fx, fy, depth

    def project(x, y, z):
        """3D → 2D (fraction figure). Fonction unique utilisée par le rendu du
        meuble ET par toutes les cotations — aucun offset manuel indépendant
        de cette projection n'est autorisé ailleurs dans le module."""
        fx, fy, _ = project_with_depth(x, y, z)
        return fx, fy

    project.with_depth = project_with_depth
    return project


def _refit_projector(project, faces: list, fit_box):
    """Recale la projection pour que la SILHOUETTE RÉELLEMENT DESSINÉE (toutes
    les faces empilées, porte ouverte comprise) s'inscrive exactement dans
    `fit_box`, centrée, proportions conservées.

    Sans ce recalage, le cadrage était calculé sur les LIMITES DE VUE
    (`xl0..zl1`) et non sur le dessin : ces limites ajoutent des marges
    proportionnelles asymétriques (jusqu'à 2·m d'un côté contre 0.2·m de
    l'autre) et, dès qu'il y a une porte, un dégagement de 1.05·L de chaque
    côté en X. Résultat : un caisson à porte occupait une fraction bien plus
    petite de la zone qu'un caisson nu (« trop petit / trop grand » d'une page
    à l'autre), et le dessin se retrouvait décalé dans sa zone au lieu d'être
    centré. Ici la boîte englobante mesurée EST celle du dessin, donc la
    taille apparente et le centrage sont les mêmes pour tous les caissons.

    L'affine appliquée est une similitude (échelle uniforme + translation) :
    elle ne déforme rien et laisse la profondeur caméra inchangée, donc le
    z-buffer et le tri des faces restent valides."""
    pts = [p for f in faces for p in f["pts"]]
    if not pts:
        return project
    xy = [project(*p) for p in pts]
    x0 = min(p[0] for p in xy); x1 = max(p[0] for p in xy)
    y0 = min(p[1] for p in xy); y1 = max(p[1] for p in xy)
    span_x = (x1 - x0) or 1e-9
    span_y = (y1 - y0) or 1e-9
    bx, by, bw, bh = fit_box
    s = min(bw / span_x, bh / span_y)
    off_x = bx + (bw - span_x * s) / 2 - x0 * s
    off_y = by + (bh - span_y * s) / 2 - y0 * s

    base = project.with_depth

    def project_with_depth(x, y, z):
        fx, fy, depth = base(x, y, z)
        return fx * s + off_x, fy * s + off_y, depth

    def refitted(x, y, z):
        fx, fy, _ = project_with_depth(x, y, z)
        return fx, fy

    refitted.with_depth = project_with_depth
    return refitted


def _project_face_to_raster(project, rect, pts, res_x, res_y):
    """Projette les 4 sommets 3D d'une face vers des coordonnées pixels du
    raster, via `project` (voir `_make_projector`) — la même fonction que
    celle utilisée pour poser les cotations — et renvoie aussi la profondeur
    caméra brute (zs) de chaque sommet, pour le z-buffer."""
    rx0, ry0, rw, rh = rect
    px_list, py_list, zs_list = [], [], []
    for x, y, z in pts:
        fx, fy, zs = project.with_depth(x, y, z)
        ax_fx, ax_fy = (fx - rx0) / rw, (fy - ry0) / rh
        px_list.append(ax_fx * res_x)
        py_list.append((1.0 - ax_fy) * res_y)
        zs_list.append(zs)
    return px_list, py_list, zs_list


def _rasterize_scene(project, rect, faces: list, res_x: int, res_y: int) -> np.ndarray:
    """Compose toutes les faces de la scène dans une image RGBA (res_y, res_x, 4)
    au moyen d'un vrai z-buffer : pour chaque pixel, seule la face la plus
    proche de la caméra à cet endroit précis est visible. Contrairement à un
    tri de polygones (peintre, même "correct"), ce test se fait POINT PAR
    POINT à l'intérieur de chaque face — il ne peut donc pas se tromper sur
    des faces qui se chevauchent partiellement à l'écran (le cas exact des
    étagères/montants/traverses adjacents qui provoquait les artefacts).

    Les arêtes (traits noirs) sont rastérisées avec la face à laquelle elles
    appartiennent : une arête n'est donc visible que là où sa propre face
    l'est, ce qui donne un rendu « ligne cachée » correct sans calcul séparé.
    """
    # `_view_vector` (donc `gaze`) pointe de la SCÈNE VERS LA CAMÉRA — c'est ce
    # qui rend le backface culling correct (`n · v > 0` ⇒ face visible). La
    # profondeur `p · gaze` est donc d'autant PLUS GRANDE que le point est
    # PROCHE de la caméra. Le z-buffer doit par conséquent conserver le MAXIMUM
    # et être initialisé à -inf. Il gardait le minimum : il peignait la face la
    # plus LOINTAINE de chaque pixel — d'où le fond qui passait devant les
    # étagères et les éléments avalés par les faces situées derrière eux.
    zbuf = np.full((res_y, res_x), -np.inf, dtype=np.float64)
    img = np.zeros((res_y, res_x, 4), dtype=np.float64)

    opaque = [f for f in faces if f["alpha"] >= 0.9]
    transparent = [f for f in faces if f["alpha"] < 0.9]

    def face_depth_avg(f):
        _, _, zs = _project_face_to_raster(project, rect, f["pts"], res_x, res_y)
        return sum(zs) / len(zs)

    def rasterize(f, blend: bool):
        px, py, zs = _project_face_to_raster(project, rect, f["pts"], res_x, res_y)
        edge_px = max(1, int(round(f["lw"] * _RASTER_DPI / 72.0)))
        pad = edge_px + 1
        x0 = max(0, int(np.floor(min(px))) - pad)
        x1 = min(res_x, int(np.ceil(max(px))) + pad + 1)
        y0 = max(0, int(np.floor(min(py))) - pad)
        y1 = min(res_y, int(np.ceil(max(py))) + pad + 1)
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        poly = [(px_ - x0, py_ - y0) for px_, py_ in zip(px, py)]

        fill_im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(fill_im).polygon(poly, fill=255)
        edge_im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(edge_im).line(poly + [poly[0]], fill=255, width=edge_px, joint="curve")

        fill_mask = np.asarray(fill_im, dtype=bool)
        edge_mask = np.asarray(edge_im, dtype=bool)
        combined = fill_mask | edge_mask
        if not combined.any():
            return

        a, b, c = _plane_from_points(np.array(px), np.array(py), np.array(zs))
        yy, xx = np.nonzero(combined)
        gx, gy = xx + x0, yy + y0
        depth = a * gx + b * gy + c
        nearer = depth > zbuf[gy, gx]
        if not nearer.any():
            return
        sy, sx, sd = gy[nearer], gx[nearer], depth[nearer]
        is_edge = edge_mask[yy[nearer], xx[nearer]]

        face_rgba = np.array(to_rgba(f["facecolor"], alpha=f["alpha"]))
        edge_rgba = np.array(to_rgba(_DARK, alpha=1.0))
        colors = np.where(is_edge[:, None], edge_rgba, face_rgba)

        if not blend:
            zbuf[sy, sx] = sd
            img[sy, sx, :] = colors
        else:
            src_a = colors[:, 3]
            dst = img[sy, sx, :]
            img[sy, sx, :3] = colors[:, :3] * src_a[:, None] + dst[:, :3] * (1 - src_a[:, None])
            img[sy, sx, 3] = src_a + dst[:, 3] * (1 - src_a)

    for f in opaque:
        rasterize(f, blend=False)
    # Éléments semi-transparents (portes, tiroirs) : peu nombreux et ne se
    # chevauchant pas entre eux en pratique, un tri peintre arrière→avant
    # suffit ici, mélangé par-dessus le résultat opaque (jamais devant lui).
    # Arrière → avant = profondeur CROISSANTE (voir la note sur `zbuf`) : le
    # `reverse=True` d'origine peignait l'avant en premier, donc à l'envers.
    for f in sorted(transparent, key=face_depth_avg):
        rasterize(f, blend=True)

    return img


# (cotations posées en 2D par _draw_dims_overlay, via la même fonction `project`)


# ─────────────────────────────────────────────────────────────────────────────
# Sommets de référence du meuble (source unique pour le rendu ET les cotations)
# ─────────────────────────────────────────────────────────────────────────────

def _drawer_face_x_span(drp: Dict, zones: list, rox: float, render_L: float,
                        t_montant: float) -> tuple:
    """Emprise horizontale (x0, x1) de la façade d'un tiroir.

    RÈGLE UNIQUE, partagée par le rendu 3D (`_render_cabinet`) et par les
    cotations (`_cabinet_vertices`) : deux tiroirs posés côte à côte n'ont pas
    la même largeur qu'un tiroir pleine largeur, et cette largeur dépend de la
    zone (montants secondaires) ainsi que du système de coulisse. Dupliquer ce
    calcul des deux côtés laisserait passer une cote qui ne correspond pas au
    panneau dessiné."""
    gap = float(drp.get("drawer_gap", 2.0))
    system = str(drp.get("drawer_system", "TANDEMBOX"))
    is_applique = bool(drp.get("_applique_mode", False))
    zone_id = drp.get("zone_id", None)

    if zone_id is not None and zone_id < len(zones):
        zone = zones[zone_id]
        zx0 = rox + float(zone["x_min"])
        zx1 = rox + float(zone["x_max"])
        zw = zx1 - zx0
        if system == "ANGLAISE":
            side_gap = 2.0
            return zx0 + side_gap, zx0 + side_gap + max(0.0, zw - 2.0 * side_gap)
        if is_applique:
            side_clearance = 2.0
            w = max(0.0, zw + 2.0 * t_montant - 2.0 * side_clearance)
            x = zx0 - t_montant + side_clearance
            return x, x + w
        return zx0 + gap, zx0 + gap + (zw - 2.0 * gap)

    if system == "ANGLAISE":
        side_gap = 2.0
        return rox + side_gap, rox + side_gap + max(0.0, render_L - 2.0 * side_gap)
    if is_applique:
        x = rox - t_montant - 1.0
        return x, x + (render_L + 2.0 * t_montant + 2.0)
    return rox + gap, rox + gap + (render_L - 2.0 * gap)


def _cabinet_vertices(cab: Dict, origin=(0.0, 0.0, 0.0)) -> Dict[str, tuple]:
    """Calcule les sommets/arêtes de référence du meuble EXCLUSIVEMENT à partir
    des dimensions paramétriques (L, W, H, épaisseurs, hauteurs de tablettes) —
    aucune valeur codée en dur. C'est le registre unique dans lequel les
    cotations doivent obligatoirement puiser leurs points d'ancrage 3D, et
    contre lequel `_assert_anchored` vérifie chaque ligne d'attache."""
    dims = cab["dims"]
    L  = float(dims["L_raw"])
    W  = float(dims["W_raw"])
    H  = float(dims["H_raw"])
    tt = float(dims["t_tb_raw"])
    tl = float(dims.get("t_lr_raw", 19.0))
    ox, oy, oz = origin

    # Aligner les ancrages de cote avec la meme geometrie que la vue 3D:
    # quand un fileur est actif, le volume du caisson est reduit cote ouverture.
    dp = cab.get("door_props", {})
    fileur_w = float(dp.get("fileur_width", 0.0) or 0.0)
    if fileur_w > 0.0:
        L = max(0.0, L - fileur_w)
        if dp.get("door_opening", "right") == "left":
            ox += fileur_w

    v: Dict[str, tuple] = {
        "coin_bas_avant_gauche":    (ox,     oy,     oz),
        "coin_bas_avant_droit":     (ox + L, oy,     oz),
        "coin_bas_arriere_droit":   (ox + L, oy + W, oz),
        "coin_bas_arriere_gauche":  (ox,     oy + W, oz),
        "coin_haut_avant_gauche":   (ox,     oy,     oz + H),
        "coin_haut_avant_droit":    (ox + L, oy,     oz + H),
        "coin_haut_arriere_droit":  (ox + L, oy + W, oz + H),
        "coin_haut_arriere_gauche": (ox,     oy + W, oz + H),
    }

    # Limites des zones (tablette / vide) sur les arêtes avant droite et
    # avant gauche, calculées en parcourant les tablettes triées par hauteur.
    shelves_sorted = sorted(cab.get("shelves", []), key=lambda s: float(s["height"]))
    v["zone_bas_z"] = (ox + L, oy, oz + tt)
    v["zone_bas_z_gauche"] = (ox, oy, oz + tt)
    for i, s in enumerate(shelves_sorted, 1):
        sh_z  = oz + tt + float(s["height"])
        sh_th = float(s.get("thickness", 19.0))
        v[f"tablette_{i}_dessous"] = (ox + L, oy, sh_z)
        v[f"tablette_{i}_dessus"]  = (ox + L, oy, sh_z + sh_th)
        v[f"tablette_{i}_dessous_gauche"] = (ox, oy, sh_z)
        v[f"tablette_{i}_dessus_gauche"]  = (ox, oy, sh_z + sh_th)
    v["zone_haut_z"] = (ox + L, oy, oz + H - tt)
    v["zone_haut_z_gauche"] = (ox, oy, oz + H - tt)

    # Largeur ET profondeur d'une tablette (règle de la feuille de débit :
    # largeur de traverse, profondeur = profondeur du caisson - 19mm) :
    # arêtes de la première tablette seulement, identiques pour toutes.
    if shelves_sorted:
        sh_z0 = oz + tt + float(shelves_sorted[0]["height"])
        v["tablette_1_avant_gauche"] = (ox + tl, oy, sh_z0)
        v["tablette_1_avant_droit"]  = (ox + L - tl, oy, sh_z0)
        v["tablette_1_arriere_gauche"] = (ox + tl, oy + max(0.0, W - 19.0), sh_z0)

    # Porte : coins RÉELLEMENT dessinés (position ouverte, après rotation —
    # mêmes formules/angles que le rendu 3D), pas la position fermée : une
    # cote doit longer la porte telle qu'elle apparaît dans la vue, et
    # dimensionner ces points fait aussi que le dégagement local de `dim_edge`
    # trouve la porte elle-même juste à côté (donc un petit décalage propre),
    # au lieu de la capter au loin et de partir hors de la page.
    if dp.get("has_door"):
        gap = float(dp.get("door_gap", 2.0))
        if dp.get("door_model") == "floor_length":
            door_h = H + 80.0 - gap
            door_z_bot = oz - 80.0
        else:
            door_h = H - 2.0 * gap
            door_z_bot = oz + gap
        dtype = dp.get("door_type", "single")

        if dtype == "single":
            opening = dp.get("door_opening", "right")
            rot_angle = 120.0 if opening == "right" else -90.0
            x_left, x_right = ox + gap, ox + L - gap
            pivot_x = x_right if opening == "right" else x_left
            rotated = _rotate_points(
                [(x_left, oy, door_z_bot), (x_right, oy, door_z_bot), (x_left, oy, door_z_bot + door_h)],
                rot_angle, "z", (pivot_x, oy, door_z_bot),
            )
            v["porte_bas_gauche"], v["porte_bas_droit"], v["porte_haut_gauche"] = rotated
        else:
            dl_half = (L - 2.0 * gap) / 2.0
            pivot_g, pivot_d = ox + gap, ox + L - gap
            rotated_g = _rotate_points(
                [(pivot_g, oy, door_z_bot), (pivot_g + dl_half, oy, door_z_bot), (pivot_g, oy, door_z_bot + door_h)],
                -90.0, "z", (pivot_g, oy, door_z_bot),
            )
            rotated_d = _rotate_points(
                [(pivot_d - dl_half, oy, door_z_bot), (pivot_d, oy, door_z_bot), (pivot_d, oy, door_z_bot + door_h)],
                120.0, "z", (pivot_d, oy, door_z_bot),
            )
            v["porte_g_bas_gauche"], v["porte_g_bas_droit"], v["porte_g_haut_gauche"] = rotated_g
            v["porte_d_bas_gauche"], v["porte_d_bas_droit"], v["porte_d_haut_droit"] = rotated_d

    # Tiroirs : largeur de façade (règle de la feuille de débit, identique
    # pour toutes) sur la première façade, + chaîne verticale des façades et
    # des jeux entre elles (même principe que la chaîne des tablettes
    # ci-dessus : bas du caisson → jeu → tiroir 1 → jeu → tiroir 2 → … → haut).
    drawers_all = cab.get("drawers", []) or []
    if drawers_all:
        try:
            _zones = calculate_all_zones_2d(cab, include_all_elements=False)
        except Exception:
            _zones = []
        # Emprise RÉELLE de chaque façade (X depuis la règle partagée avec le
        # rendu, Z depuis l'offset et la hauteur de façade). Sans elle, toutes
        # les cotes de tiroir supposaient une façade pleine largeur empilée
        # verticalement : deux tiroirs côte à côte se retrouvaient cotés au
        # même endroit, avec des segments de hauteur nulle.
        faces = []
        for d in drawers_all:
            x0, x1 = _drawer_face_x_span(d, _zones, ox, L, tl)
            z0 = oz + float(d.get("drawer_bottom_offset", 0.0))
            z1 = z0 + float(d.get("drawer_face_H_raw", 150.0))
            faces.append((x0, x1, z0, z1))
        # Tri lecture : de bas en haut, puis de gauche à droite.
        order = sorted(range(len(faces)), key=lambda k: (faces[k][2], faces[k][0]))
        for n, k in enumerate(order, 1):
            x0, x1, z0, z1 = faces[k]
            v[f"tiroir_{n}_face_bas_gauche"]  = (x0, oy, z0)
            v[f"tiroir_{n}_face_bas_droit"]   = (x1, oy, z0)
            v[f"tiroir_{n}_face_haut_gauche"] = (x0, oy, z1)
            v[f"tiroir_{n}_face_haut_droit"]  = (x1, oy, z1)

        # Chaîne VERTICALE (hauteurs + jeux) : conservée telle quelle, mais
        # elle ne doit porter que sur UNE colonne — enchaîner des façades
        # posées côte à côte reviendrait à empiler des cotes identiques au
        # même endroit. On retient la colonne la plus à gauche.
        col_x0 = min(f[0] for f in faces)
        column = sorted((f for f in faces if abs(f[0] - col_x0) <= 1.0),
                        key=lambda f: f[2])
        d0 = column[0]
        v["tiroir_1_bas_gauche"] = (d0[0], oy, d0[2])
        v["tiroir_1_bas_droit"]  = (d0[1], oy, d0[2])

        v["tiroir_zone_bas_z"] = (ox + L, oy, oz + tt)
        v["tiroir_zone_bas_z_gauche"] = (ox, oy, oz + tt)
        for i, (_x0, _x1, d_bot, d_top) in enumerate(column, 1):
            v[f"tiroir_{i}_dessous"] = (ox + L, oy, d_bot)
            v[f"tiroir_{i}_dessus"]  = (ox + L, oy, d_top)
            v[f"tiroir_{i}_dessous_gauche"] = (ox, oy, d_bot)
            v[f"tiroir_{i}_dessus_gauche"]  = (ox, oy, d_top)
        v["tiroir_zone_haut_z"] = (ox + L, oy, oz + H - tt)
        v["tiroir_zone_haut_z_gauche"] = (ox, oy, oz + H - tt)

    return v


# ─────────────────────────────────────────────────────────────────────────────
# Rendu 3D d'un caisson
# ─────────────────────────────────────────────────────────────────────────────

def _render_cabinet(ax_img, fig, rect, cab: Dict, origin=(0.0, 0.0, 0.0),
                    show_all_dims=True, azim=315, elev=22, fit_box=None):
    """`ax_img` : axes 2D (pleine page, coordonnées 0..1 = fraction figure)
    sur lesquels est affichée l'image matricielle rendue par `_rasterize_scene`.
    `rect` : (x0, y0, w, h) de la toile matricielle, en fraction de figure.
    `fit_box` : (x0, y0, w, h) de la zone où le dessin lui-même doit s'inscrire,
    centré (voir `_refit_projector`). Doit être contenu dans `rect`, sans quoi
    le dessin serait rogné par les bords de la toile.

    Aucun axe 3D matplotlib n'est utilisé ici : la projection (voir
    `_make_projector`) est un calcul autonome, pas une machinerie mplot3d."""
    dims = cab["dims"]
    L  = float(dims["L_raw"])
    W  = float(dims["W_raw"])
    H  = float(dims["H_raw"])
    tl = float(dims["t_lr_raw"])
    tb = float(dims["t_fb_raw"])
    tt = float(dims["t_tb_raw"])
    ox, oy, oz = origin
    dp       = cab.get("door_props", {})

    # ── Cadrage de la vue (limites 3D + marges) ──────────────────────────────
    pad = max(L, W, H) * 0.10
    m   = pad * 1.4
    # Porte sol : peut descendre de 80 mm sous le bas du caisson
    z_floor_ext = 90.0 if (dp.get("has_door") and dp.get("door_model") == "floor_length") else 0
    xl0, xl1 = ox - m*0.2,  ox + L + m*2.0
    yl0, yl1 = oy - m*0.35, oy + W + m*0.2   # marge avant (portes/tiroirs)
    zl0, zl1 = oz - max(m*0.1, z_floor_ext),  oz + H + m*0.7

    # Porte ouverte à 90°/120° : elle pivote depuis un coin du caisson et
    # balaie un rayon jusqu'à sa propre largeur (≤ L), aussi bien en X qu'en Y
    # — les marges ci-dessus (dimensionnées pour l'ancien angle de 45°) sont
    # trop courtes côté avant et la coupaient. On élargit le cadrage d'un
    # rayon L autour du caisson pour qu'elle reste toujours entièrement visible,
    # quel que soit le sens d'ouverture ou le nombre de vantaux.
    if dp.get("has_door"):
        door_room = L * 1.05
        xl0 = min(xl0, ox - door_room)
        xl1 = max(xl1, ox + L + door_room)
        yl0 = min(yl0, oy - door_room)

    # Fonction de projection UNIQUE (voir `_make_projector`) : calcul
    # vectoriel pur, entièrement autonome (aucune dépendance matplotlib/
    # mplot3d). `_rasterize_scene` et `_draw_dims_overlay` utilisent tous les
    # deux CETTE closure — ils ne peuvent donc plus structurellement diverger.
    project = _make_projector(azim, elev, ((xl0, xl1), (yl0, yl1), (zl0, zl1)), rect)

    # ── Backface culling : on ne dessine que les 3 faces visibles de chaque boîte ─
    bf_skip = _backface_set(azim, elev)

    base_el = cab.get("base_elements", {
        "has_back_panel": True,
        "has_left_upright": True,
        "has_right_upright": True,
        "has_bottom_traverse": True,
        "has_top_traverse": True,
    })
    has_back = bool(base_el.get("has_back_panel", True))

    # Meme adaptation que la vue Streamlit: fileur => reduction de largeur
    # du caisson cote ouverture, avec decalage eventuel de l'origine.
    rox, roy, roz = ox, oy, oz
    render_L = L
    fileur_w = float(dp.get("fileur_width", 0.0) or 0.0)
    if fileur_w > 0.0:
        render_L = max(0.0, L - fileur_w)
        if dp.get("door_opening", "right") == "left":
            rox += fileur_w

    # Zones identiques a Streamlit: portes sur zones complètes, tiroirs sur
    # zones calculees sans les tiroirs eux-memes.
    try:
        zones_render_all = calculate_all_zones_2d(cab, include_all_elements=True)
    except Exception:
        zones_render_all = []
    try:
        zones_render_without_elements = calculate_all_zones_2d(cab, include_all_elements=False)
    except Exception:
        zones_render_without_elements = []

    # Toutes les faces de toutes les boîtes sont empilées ici, puis triées et
    # affichées en une seule passe (tri peintre global) par _flush_boxes,
    # au lieu d'être ajoutées aux axes boîte par boîte : cela évite les
    # artefacts d'ordre d'affichage de matplotlib entre boîtes voisines
    # (étagère cachée par un montant, un fond ou une traverse, etc.).
    boxes: list = []

    # 1) Structure principale (ordre identique au rendu Streamlit)
    if base_el.get("has_bottom_traverse", True):
        _add_box(boxes, rox + tl, roy, roz, render_L - 2 * tl, W, tt, skip=bf_skip)
    if base_el.get("has_top_traverse", True):
        _add_box(boxes, rox + tl, roy, roz + H - tt, render_L - 2 * tl, W, tt, skip=bf_skip)
    if base_el.get("has_left_upright", True):
        _add_box(boxes, rox, roy, roz, tl, W, H, skip=bf_skip)
    if base_el.get("has_right_upright", True):
        _add_box(boxes, rox + render_L - tl, roy, roz, tl, W, H, skip=bf_skip)
    if has_back:
        _add_box(boxes, rox + tl, roy + W - tb, roz + tt, render_L - 2 * tl, tb, H - 2 * tt, skip=bf_skip)

    # 2) Montants secondaires
    for div in cab.get("vertical_dividers", []) or []:
        div_x = float(div.get("position_x", 0.0))
        div_th = float(div.get("thickness", 19.0))
        _add_box(
            boxes,
            rox + div_x - div_th / 2.0,
            roy,
            roz + tt,
            div_th,
            W - tb,
            H - 2 * tt,
            skip=bf_skip,
        )

    # 3) Étagères verticales
    for vs in cab.get("vertical_shelves", []) or []:
        vs_th = float(vs.get("thickness", 19.0))
        vs_x = float(vs.get("position_x", 300.0))
        vs_bottom = float(vs.get("bottom_y", 0.0))
        vs_top = float(vs.get("top_y", 100.0))
        vs_h = max(0.0, vs_top - vs_bottom)
        if vs_h <= 0.0:
            continue
        _add_box(
            boxes,
            rox + vs_x - vs_th / 2.0,
            roy,
            roz + tt + vs_bottom,
            vs_th,
            W,
            vs_h,
            skip=bf_skip,
        )

    # 4) Portes
    if dp.get("has_door"):
        gap = float(dp.get("door_gap", 2.0))
        dth_d = float(dp.get("door_thickness", 19.0))
        dy = roy - dth_d
        if dp.get("door_model") == "floor_length":
            dH = H + 80.0 - gap
            dz = roz - 80.0
        else:
            dH = H - 2.0 * gap
            dz = roz + gap
        # Ouverture "droite" à 120° pour bien dégager l'intérieur du caisson
        # (demande métier) ; "gauche" à 90°. À 45° la porte côté "droite"
        # devenait exactement de profil pour cette caméra (azim=225°), donc
        # quasi invisible sur le rendu — voir _view_vector/_backface_set.
        rot_angle = 120.0 if dp.get("door_opening") == "right" else -90.0

        zone_id = dp.get("zone_id", None)
        if zone_id is not None and zone_id < len(zones_render_all):
            zone = zones_render_all[zone_id]
            zone_x_min = rox + float(zone["x_min"])
            zone_x_max = rox + float(zone["x_max"])
            zone_w = zone_x_max - zone_x_min
            safety = 2.0
            dW_zone = zone_w - 2.0 * gap - 2.0 * safety
            door_x_start = zone_x_min + gap + safety
            pivot_x = zone_x_max - gap - safety if dp.get("door_opening") == "right" else zone_x_min + gap + safety
            if dW_zone > 0.0:
                _add_box(
                    boxes,
                    door_x_start,
                    dy,
                    dz,
                    dW_zone,
                    dth_d,
                    dH,
                    alpha=0.50,
                    skip=bf_skip,
                    rotation_angle=rot_angle,
                    rotation_axis="z",
                    rotation_pivot=(pivot_x, dy, dz),
                )
        else:
            if dp.get("door_type") == "single":
                pivot_x = rox + render_L - gap if dp.get("door_opening") == "right" else rox + gap
                _add_box(
                    boxes,
                    rox + gap,
                    dy,
                    dz,
                    render_L - 2.0 * gap,
                    dth_d,
                    dH,
                    alpha=0.50,
                    skip=bf_skip,
                    rotation_angle=rot_angle,
                    rotation_axis="z",
                    rotation_pivot=(pivot_x, dy, dz),
                )
            else:
                dl_half = (render_L - 2.0 * gap) / 2.0
                pivot_g = rox + gap
                pivot_d = rox + render_L - gap
                _add_box(
                    boxes,
                    rox + gap,
                    dy,
                    dz,
                    dl_half,
                    dth_d,
                    dH,
                    alpha=0.50,
                    skip=bf_skip,
                    rotation_angle=-90.0,
                    rotation_axis="z",
                    rotation_pivot=(pivot_g, dy, dz),
                )
                _add_box(
                    boxes,
                    rox + render_L - gap - dl_half,
                    dy,
                    dz,
                    dl_half,
                    dth_d,
                    dH,
                    alpha=0.50,
                    skip=bf_skip,
                    rotation_angle=120.0,
                    rotation_axis="z",
                    rotation_pivot=(pivot_d, dy, dz),
                )

        # Fileur en facade
        if fileur_w > 0.0:
            fil_w = fileur_w
            fil_th = 19.0
            fil_h = dH
            fil_y = dy
            fil_z = dz
            if dp.get("door_opening", "right") == "right":
                fil_x = rox + render_L
            else:
                fil_x = rox - fil_w
            _add_box(boxes, fil_x, fil_y, fil_z, fil_w, fil_th, fil_h, alpha=0.95, skip=bf_skip)

    # 5) Tiroirs
    drawers = cab.get("drawers", []) or []
    anglaise_zone_ids = {
        dr.get("zone_id")
        for dr in drawers
        if dr.get("drawer_system") == "ANGLAISE" and dr.get("zone_id") is not None
    }
    interior_depth = W - 2.0 * tb
    t_montant = float(cab.get("dims", {}).get("t_lr_raw", 19.0))

    for drp in drawers:
        gap = float(drp.get("drawer_gap", 2.0))
        drawer_system = str(drp.get("drawer_system", "TANDEMBOX"))
        drawer_height = float(drp.get("drawer_face_H_raw", 150.0))
        drawer_th = float(drp.get("drawer_face_thickness", 19.0))
        is_applique = bool(drp.get("_applique_mode", False))
        zone_id = drp.get("zone_id", None)

        # Emprise horizontale : règle partagée avec les cotations
        # (voir `_drawer_face_x_span`), jamais recalculée séparément.
        dx, dx1 = _drawer_face_x_span(
            drp, zones_render_without_elements, rox, render_L, t_montant)
        dW = dx1 - dx

        if drawer_system == "ANGLAISE":
            dy = roy + 30.0
            dd = drawer_th
        elif is_applique:
            dy = roy - drawer_th
            dd = drawer_th
        else:
            dy = roy + tb
            dd = interior_depth - 19.0

        dz = roz + float(drp.get("drawer_bottom_offset", 0.0))
        same_zone_as_anglaise = (zone_id in anglaise_zone_ids) and drawer_system != "ANGLAISE"
        alpha = 0.25 if same_zone_as_anglaise else 0.60

        if dW > 0.0 and dd > 0.0 and drawer_height > 0.0:
            _add_box(boxes, dx, dy, dz, dW, dd, drawer_height, alpha=alpha, skip=bf_skip)

    # 6) Étagères horizontales (règle de la feuille de débit : largeur de
    # traverse, profondeur = profondeur du caisson - 19mm pour ne pas
    # transpercer le Fond — même règle que le fichier SketchUp).
    for s in cab.get("shelves", []) or []:
        sh_z = roz + tt + float(s.get("height", 0.0))
        sh_th = float(s.get("thickness", 19.0))
        _add_box(
            boxes,
            rox + tl,
            roy,
            sh_z,
            max(0.0, render_L - 2.0 * tl),
            max(0.0, W - 19.0),
            sh_th,
            shelf=True,
            skip=bf_skip,
        )

    # 7) Joues
    joues = cab.get("joues", {}) or {}
    for key, side in (("gauche", "left"), ("droite", "right"), ("dessus", "top"), ("dessous", "bottom")):
        joue = joues.get(key, {}) or {}
        if not bool(joue.get("enabled", False)):
            continue
        jw = float(joue.get("width", 0.0) or 0.0)
        jl = float(joue.get("length", 0.0) or 0.0)
        jt = float(joue.get("thickness", 0.0) or 0.0)
        if jw <= 0.0 or jl <= 0.0 or jt <= 0.0:
            continue
        if side == "left":
            _add_box(boxes, rox - jt, roy, roz, jt, jw, jl, alpha=0.75, skip=bf_skip)
        elif side == "right":
            _add_box(boxes, rox + render_L, roy, roz, jt, jw, jl, alpha=0.75, skip=bf_skip)
        elif side == "top":
            _add_box(boxes, rox, roy, roz + H, jw, jl, jt, alpha=0.75, skip=bf_skip)
        elif side == "bottom":
            _add_box(boxes, rox, roy, roz - jt, jw, jl, jt, alpha=0.75, skip=bf_skip)

    # Recadrage sur la silhouette réelle : à faire ICI, une fois toutes les
    # boîtes empilées (y compris la porte ouverte, qui est ce qui faisait le
    # plus varier l'encombrement d'une page à l'autre) et avant la
    # rastérisation, pour que le raster ET les cotations partagent la même
    # projection définitive.
    if fit_box is not None:
        project = _refit_projector(project, boxes, fit_box)

    res_x = max(1, int(round(rect[2] * fig.get_figwidth() * _RASTER_DPI)))
    res_y = max(1, int(round(rect[3] * fig.get_figheight() * _RASTER_DPI)))
    img = _rasterize_scene(project, rect, boxes, res_x, res_y)
    rx0, ry0, rw, rh = rect
    # zorder élevé (au-dessus des lignes de cote ET de leurs étiquettes,
    # voir `_draw_dims_overlay`) : le fond du raster est transparent hors de
    # la silhouette du meuble, donc seuls les pixels du meuble recouvrent
    # une cote qui passerait derrière lui — le dessin reste toujours au
    # premier plan (demande métier), sans que cela ne dépende d'un calcul de
    # dégagement.
    im = ax_img.imshow(img, extent=(rx0, rx0 + rw, ry0, ry0 + rh),
                       origin="upper", interpolation="bilinear", zorder=35)
    # `clip_on=False` : `ax_img` a des limites fixes 0..1 (le repère dans lequel
    # `project` travaille), mais l'axe rendu carré par l'aspect 'equal' ne
    # couvre qu'une partie de la page — la feuille s'étend en réalité de
    # -0.21 à 1.21 en X. Sans ça, matplotlib rogne l'image sur la boîte de
    # l'axe : tout ce qui dépasse x=1 était tranché net, en plein milieu du
    # meuble, alors que les cotations (déjà en `clip_on=False`) continuaient,
    # elles, à s'afficher. Le dessin doit toujours être ENTIER.
    im.set_clip_on(False)

    # Masque de silhouette (alpha du rendu) + géométrie du rect, pour que
    # _draw_dims_overlay puisse vérifier qu'aucune étiquette ne touche
    # réellement le dessin (et pas seulement une estimation approximative).
    # `project` et `vertices` sont renvoyés pour que _draw_dims_overlay
    # réutilise EXACTEMENT la même fonction de projection et le même registre
    # de sommets que ceux qui ont servi à rendre le meuble.
    # `ax` est renvoyé parce que `project` produit des coordonnées de DONNÉES
    # de cet axe (celles passées à `imshow`), et non de la fraction figure :
    # les cotations doivent donc être tracées dans `ax.transData`, le seul
    # repère où elles coïncident avec le dessin (voir `_draw_dims_overlay`).
    return {
        "alpha": img[..., 3], "rect": rect, "res_x": res_x, "res_y": res_y,
        "project": project, "vertices": _cabinet_vertices(cab, origin),
        "ax": ax_img,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Texte descriptif
# ─────────────────────────────────────────────────────────────────────────────

def _hinge_count_per_leaf(cab: Dict) -> int:
    """Nombre de charnières PAR VANTAIL, d'après la hauteur totale du caisson :
    2 en dessous de 1500 mm, 3 de 1500 à 2000 mm, 4 au-delà.

    Un réglage manuel des charnières dans l'application (mode « Personnalisé »)
    l'emporte : c'est un choix explicite de l'utilisateur, pas une valeur par
    défaut à recalculer."""
    dp = cab.get("door_props", {}) or {}
    if dp.get("hinge_mode") == "custom":
        custom = dp.get("custom_hinge_positions") or []
        if custom:
            return len(custom)
    H = float(cab.get("dims", {}).get("H_raw", 0.0))
    if H > 2000.0:
        return 4
    if H >= 1500.0:
        return 3
    return 2


def _description_lines(cab: Dict, label: str) -> List[str]:
    dims = cab["dims"]
    L   = int(round(float(dims["L_raw"])))
    W   = int(round(float(dims["W_raw"])))
    H   = int(round(float(dims["H_raw"])))
    tl  = int(round(float(dims.get("t_lr_raw", 19))))
    tt  = int(round(float(dims.get("t_tb_raw", 19))))
    tb  = int(round(float(dims.get("t_fb_raw", 19))))

    base     = cab.get("base_elements", {})
    has_back = base.get("has_back_panel", True)
    dp       = cab.get("door_props", {})
    shelves_sorted = sorted(cab.get("shelves", []), key=lambda s: float(s["height"]))
    nb_dividers = len(cab.get("vertical_dividers", []))
    drawers = cab.get("drawers", []) or []
    nb_drawers  = len(drawers)

    int_L = L - 2 * tl
    int_H = H - 2 * tt
    int_P = W - tb if has_back else W

    # Largeur effective (réduite si un fileur est posé) : sert de référence
    # aux cotes de porte/tiroir pleine largeur, comme dans la feuille de débit.
    fileur_w = float(dp.get("fileur_width", 0.0) or 0.0)
    L_eff = max(0.0, float(L) - fileur_w)
    try:
        zones_all = calculate_all_zones_2d(cab, include_all_elements=True)
    except Exception:
        zones_all = []
    try:
        zones_no_el = calculate_all_zones_2d(cab, include_all_elements=False)
    except Exception:
        zones_no_el = []

    lines: List[str] = []

    # Dimensions extérieures
    lines += ["DIMENSIONS EXTÉRIEURES",
              f"  H : {H} mm",
              f"  L : {L} mm",
              f"  P : {W} mm",
              ""]

    # Panneaux
    if tl == tt == tb:
        lines += ["PANNEAUX",
                  f"  Épaisseur : {tl} mm",
                  ""]
    else:
        lines += ["PANNEAUX",
                  f"  Montants (G/D) : {tl} mm",
                  f"  Traverses (H/B) : {tt} mm",
                  f"  Fond : {tb} mm" if has_back else "  Sans fond",
                  ""]

    # Dimensions intérieures
    lines += ["DIMENSIONS INTÉRIEURES",
              f"  L int : {int_L} mm",
              f"  H int : {int_H} mm",
              f"  P int : {int_P} mm",
              ""]

    # Étagères : cotes réelles (largeur × profondeur), épaisseur et position.
    # Même règle que la feuille de débit : largeur = largeur de traverse,
    # profondeur = profondeur du caisson - 19 mm.
    if shelves_sorted:
        lines.append("ÉTAGÈRES (depuis fond int.)")
        shelf_w = max(0.0, L - 2 * tl)
        shelf_d = max(0.0, W - 19.0)
        for i, s in enumerate(shelves_sorted, 1):
            h_pos = int(round(float(s["height"])))
            s_th  = int(round(float(s.get("thickness", 19.0))))
            lines.append(
                f"  N°{i} : {int(round(shelf_w))}×{int(round(shelf_d))}×{s_th} mm (h={h_pos})"
            )
        lines.append("")

    # Porte(s) : cotes réelles de chaque vantail.
    if dp.get("has_door"):
        dtype = dp.get("door_type", "single")
        gap = float(dp.get("door_gap", 2.0))
        dth_d = float(dp.get("door_thickness", 19.0))
        if dp.get("door_model") == "floor_length":
            dH = H + 80.0 - gap
        else:
            dH = H - 2.0 * gap

        zone_id = dp.get("zone_id", None)
        if zone_id is not None and zone_id < len(zones_all):
            zone = zones_all[zone_id]
            zone_w = float(zone["x_max"]) - float(zone["x_min"])
            avail_w = zone_w - 2.0 * gap - 4.0  # même marge de sécurité que le rendu
        else:
            avail_w = L_eff - 2.0 * gap
        avail_w = max(0.0, avail_w)

        lines.append("PORTE" if dtype == "single" else "PORTES")
        if dtype == "single":
            lines.append(f"  {int(round(avail_w))}×{int(round(dH))}×{int(round(dth_d))} mm")
        else:
            leaf_w = avail_w / 2.0
            lines.append(f"  2 vantaux : {int(round(leaf_w))}×{int(round(dH))}×{int(round(dth_d))} mm")
        lines.append("")

    # Tiroirs : cotes réelles de chaque façade (largeur = largeur du caisson,
    # ou de la zone, - 4 mm — même règle que la feuille de débit).
    if drawers:
        lines.append("TIROIR(S)")
        for i, drp in enumerate(drawers, 1):
            face_h  = float(drp.get("drawer_face_H_raw", 150.0))
            face_th = float(drp.get("drawer_face_thickness", 19.0))
            zone_id = drp.get("zone_id", None)
            if zone_id is not None and zone_id < len(zones_no_el):
                zone = zones_no_el[zone_id]
                zone_w = float(zone["x_max"]) - float(zone["x_min"])
                face_w = max(0.0, zone_w - 4.0)
            else:
                face_w = max(0.0, L_eff - 4.0)
            lines.append(f"  N°{i} : {int(round(face_w))}×{int(round(face_h))}×{int(round(face_th))} mm")
        lines.append("")

    # Accessoires
    acc: List[str] = []
    if has_back:
        acc.append("fond encastré")
    if nb_dividers:
        acc.append(f"{nb_dividers} séparateur(s)")
    if dp.get("has_door"):
        dtype = dp.get("door_type", "single")
        per_leaf = _hinge_count_per_leaf(cab)
        if dtype == "single":
            acc.append(f"1 porte ({per_leaf} charnières BLUMOTION)")
        else:
            acc.append(f"2 portes (2 × {per_leaf} charnières BLUMOTION, "
                       f"soit {2 * per_leaf} au total)")
    if nb_drawers:
        acc.append(f"{nb_drawers} tiroir(s)")
    fileur = float(dp.get("fileur_width", 0))
    if fileur > 0:
        acc.append(f"fileur {int(fileur)} mm")
    if acc:
        lines.append("ACCESSOIRES")
        for a in acc:
            lines.append(f"  {a}")

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Barre orange KOBO (bas de page)
# ─────────────────────────────────────────────────────────────────────────────

# A4 paysage : 297 × 210 mm  (fig coords : largeur=1, hauteur=1)
_MARGIN_B    = 5  / 210   # 5 mm espace blanc en bas
_LOGO_H      = 8  / 210   # hauteur du logo
_BAND_SHRINK = 3  / 210   # les bandes sont 3 mm moins hautes que le logo
_BAR_H       = _LOGO_H    # hauteur totale de la zone (= logo)
_GAP_MM      = 2  / 297   # 2 mm de jeu logo↔bande (fraction largeur fig)
_FIG_W_IN, _FIG_H_IN = 11.69, 8.27


def _crop_logo(arr: np.ndarray) -> np.ndarray:
    """Supprime les bordures blanches/transparentes d'une image logo."""
    if arr.shape[2] == 4:
        mask = arr[:, :, 3] > 10          # pixels non-transparents
    else:
        mask = ~np.all(arr[:, :, :3] > 240, axis=2)   # pixels non-blancs
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return arr
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]
    return arr[r0:r1+1, c0:c1+1]


def _draw_kobo_bar(fig, logo_img: Optional[np.ndarray] = None):
    """2 bandes orange gauche/droite + logo centré fond blanc, 5 mm du bas."""
    bar_y = _MARGIN_B

    # Bandes 3 mm moins hautes que le logo, centrées verticalement
    band_h = _LOGO_H - _BAND_SHRINK
    band_y = bar_y + _BAND_SHRINK / 2   # centrage vertical

    def _orange_rect(x, w):
        r = mpatches.Rectangle(
            (x, band_y), w, band_h,
            transform=fig.transFigure,
            facecolor=_ORANGE, edgecolor="none",
            clip_on=False, zorder=50,
        )
        fig.add_artist(r)

    if logo_img is not None:
        logo = _crop_logo(logo_img)
        h_px, w_px = logo.shape[:2]

        # Hauteur logo = hauteur bande ; largeur proportionnelle
        logo_h_frac = _BAR_H
        logo_w_frac = logo_h_frac * (w_px / h_px) * (_FIG_H_IN / _FIG_W_IN)

        logo_x = 0.5 - logo_w_frac / 2
        logo_end = 0.5 + logo_w_frac / 2

        # Bande gauche (de x=0 jusqu'à 2 mm avant le logo)
        _orange_rect(0, logo_x - _GAP_MM)
        # Bande droite (2 mm après le logo jusqu'à x=1)
        _orange_rect(logo_end + _GAP_MM, 1 - logo_end - _GAP_MM)

        # Logo fond blanc centré
        logo_ax = fig.add_axes(
            [logo_x, bar_y, logo_w_frac, logo_h_frac],
            zorder=51,
        )
        logo_ax.imshow(logo, interpolation="bilinear", aspect="auto")
        logo_ax.set_facecolor("white")
        logo_ax.axis("off")
    else:
        # Repli : bande pleine + texte
        _orange_rect(0, 1)
        fig.text(0.5, bar_y + _BAR_H * 0.5,
                 "kobo",
                 ha="center", va="center",
                 fontsize=11, fontweight="bold",
                 color="#1A1A1A", fontfamily="DejaVu Sans",
                 transform=fig.transFigure,
                 zorder=51)

    return bar_y + _BAR_H


# ─────────────────────────────────────────────────────────────────────────────
# Ligne de séparation horizontale
# ─────────────────────────────────────────────────────────────────────────────

def _separator(fig, y: float, color="#CCCCCC", lw=0.6):
    fig.add_artist(plt.Line2D([0.0, 1.0], [y, y],
                              transform=fig.transFigure,
                              color=color, linewidth=lw, zorder=20))


# ─────────────────────────────────────────────────────────────────────────────
# Cotations 2D overlay (projetées par-dessus la vue 3D)
# ─────────────────────────────────────────────────────────────────────────────

def _draw_dims_overlay(fig, cab: Dict, origin=(0.0, 0.0, 0.0), raster=None,
                       azim=315, elev=22):
    """
    Pose les cotations en 2D sur la figure, par-dessus la vue 3D, en n'utilisant
    QUE deux sources de vérité, toutes deux produites par `_render_cabinet` :

      - `project` = raster["project"] : LA fonction de projection 3D → 2D
        (voir `_make_projector`), STRICTEMENT LA MÊME que celle qui a servi à
        rastériser le meuble. Aucune cotation ne calcule sa propre projection
        ni n'applique un offset manuel indépendant de cette fonction.
      - `vertices` = raster["vertices"] (voir `_cabinet_vertices`) : le
        registre des sommets réels du meuble, dérivé uniquement des
        dimensions paramétriques. Chaque ligne d'attache DOIT partir d'un
        point de ce registre.

    `_assert_anchored` vérifie après coup, pour chaque ligne d'attache, que
    son point de départ projeté coïncide (< 1 pt PDF) avec la projection
    indépendante du sommet enregistré correspondant ; sinon elle lève une
    erreur plutôt que de laisser passer une cotation mal ancrée.

    Les cotes L/P (dim_edge) sont dessinées PARALLÈLES à l'arête qu'elles
    mesurent (ligne de cote décalée le long de la normale à l'arête, comme en
    dessin technique), jamais sur une horizontale arbitraire. Les cotes de
    hauteur (dim_right) restent sur une voie verticale unique par groupe
    (lane_idx) — toutes les cotes partielles sur lane 0, la cote totale sur
    lane 1, strictement plus à droite.
    """
    if raster is None or "project" not in raster or "vertices" not in raster:
        raise ValueError(
            "_draw_dims_overlay nécessite le raster de _render_cabinet "
            "(fonction 'project' + registre 'vertices')")

    project  = raster["project"]
    vertices = raster["vertices"]
    ax       = raster["ax"]

    dims = cab["dims"]
    L = float(dims["L_raw"])
    W = float(dims["W_raw"])
    H = float(dims["H_raw"])
    # Exigence metier: ancrer les cotes verticales sur UNE arete explicite du
    # meuble en vue de face. Mettre "left" ou "right" selon besoin.
    FRONT_FACE_DIM_EDGE = "right"
    use_right_edge = (FRONT_FACE_DIM_EDGE == "right")

    # ── Repère de tracé : les COORDONNÉES DE DONNÉES de `ax` ────────────────
    # `project` renvoie des coordonnées dans le repère de données de `ax`
    # (ce sont exactement celles passées à `imshow` via `extent`), PAS de la
    # fraction figure. Ces deux repères ne coïncident pas : `imshow` impose
    # `aspect='equal'`, donc matplotlib rétrécit la position réelle de l'axe
    # pour la rendre carrée ([0,0,1,1] devient [0.146, 0, 0.707, 1] sur une
    # page 11.69×8.27 in). Tracer les cotations sur `fig.transFigure` — ce
    # que faisait le code — les plaçait donc dans un repère étranger au
    # dessin : d'où le décalage horizontal (jusqu'à ~29 mm au bord droit,
    # proportionnel à l'abscisse) entre la vue 3D et ses cotations.
    # En traçant tout dans `ax.transData`, cotations et dessin partagent le
    # même repère par construction, et la vue 3D n'est pas modifiée d'un pixel.
    TR = ax.transData
    # Taille physique de l'axe, pour convertir une longueur de ce repère en
    # points PDF (contrôle d'ancrage ci-dessous).
    _pos = ax.get_position()
    ax_w_pt = _pos.width  * fig.get_figwidth()  * 72.0
    ax_h_pt = _pos.height * fig.get_figheight() * 72.0

    def _assert_anchored(pt_3d, screen_xy, what):
        """`pt_3d` DOIT être l'un des sommets de `vertices` (comparaison
        exacte, ce sont littéralement les mêmes tuples) ; on reprojette alors
        ce sommet indépendamment et on vérifie que l'écart avec `screen_xy`
        (le point réellement utilisé pour tracer la ligne d'attache) est
        inférieur à 1 pt PDF. Toute cotation ancrée sur un point qui n'est
        pas dans le registre — ou dont la projection dérive — lève une
        erreur au lieu de générer un PDF silencieusement faux."""
        match = next((name for name, v in vertices.items()
                      if all(abs(a - b) < 1e-6 for a, b in zip(v, pt_3d))), None)
        if match is None:
            raise ValueError(
                f"Cotation '{what}' : le point 3D {pt_3d} n'est pas un sommet "
                f"enregistré du meuble (sommets connus : {sorted(vertices)})")
        ref_x, ref_y = project(*vertices[match])
        d_pt = (((ref_x - screen_xy[0]) * ax_w_pt) ** 2 +
                ((ref_y - screen_xy[1]) * ax_h_pt) ** 2) ** 0.5
        if d_pt >= 1.0:
            raise ValueError(
                f"Cotation '{what}' : ligne d'attache décalée de {d_pt:.2f} pt "
                f"par rapport au sommet '{match}' — dessin et cotation "
                f"utilisent des transformations différentes.")

    # ── Boîte englobante réelle du dessin (fraction figure), depuis le masque
    #    alpha du rendu matriciel : caisson + portes/tiroirs inclus ──────────
    alpha = raster["alpha"]
    rx0, ry0, rw, rh = raster["rect"]
    res_x, res_y = raster["res_x"], raster["res_y"]
    rows = np.nonzero(np.any(alpha > 0.5, axis=1))[0]
    cols = np.nonzero(np.any(alpha > 0.5, axis=0))[0]
    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1
    bbox_x0 = rx0 + (c0 / res_x) * rw
    bbox_x1 = rx0 + (c1 / res_x) * rw
    bbox_y0 = ry0 + (1 - r1 / res_y) * rh
    bbox_y1 = ry0 + (1 - r0 / res_y) * rh
    bbox_cx, bbox_cy = (bbox_x0 + bbox_x1) / 2, (bbox_y0 + bbox_y1) / 2

    # Position projetee de l'arete choisie : la voie de cote se place du meme
    # cote ecran que cette arete, pour que le changement soit visuellement net.
    if use_right_edge:
        edge_bot = vertices["coin_bas_avant_droit"]
        edge_top = vertices["coin_haut_avant_droit"]
    else:
        edge_bot = vertices["coin_bas_avant_gauche"]
        edge_top = vertices["coin_haut_avant_gauche"]
    edge_bot_x, _ = project(*edge_bot)
    edge_top_x, _ = project(*edge_top)
    place_dims_right = ((edge_bot_x + edge_top_x) * 0.5) >= bbox_cx

    # Position (fraction figure) de CHAQUE pixel dessiné (silhouette complète,
    # pas seulement sa boîte englobante). `dim_edge` s'en sert pour calculer
    # le dégagement réellement nécessaire LOCALEMENT le long de chaque arête —
    # utiliser la boîte englobante globale surestimait le décalage (tiré vers
    # le coin le plus éloigné du dessin, même hors du passage de l'arête
    # mesurée), ce qui envoyait la ligne de cote hors de la page.
    _sil_row, _sil_col = np.nonzero(alpha > 0.5)
    _sil_x = rx0 + (_sil_col / res_x) * rw
    _sil_y = ry0 + (1 - _sil_row / res_y) * rh

    GAP  = 0.028     # écart mini entre le dessin et la voie de cote la plus proche
    LANE = 0.072     # écart de dégagement supplémentaire des cotes obliques (dim_edge)
    TICK = 0.007     # demi-longueur des petits tirets d'extrémité
    LBL_GAP = 0.014  # écart entre la ligne de cote et l'étiquette

    # ── Bornes de la page, converties FRACTION FIGURE → repère de tracé ──────
    # La mise en page (`_cabinet_page`) raisonne en fraction figure : titre à
    # 0.935, séparateur texte/dessin à 0.31, bord de feuille à 0/1. Le tracé,
    # lui, se fait dans `ax.transData`, et les DEUX repères ne coïncident pas
    # (l'aspect 'equal' d'imshow rétrécit l'axe à [0.146, 0.854] de la page).
    # Les bornes étaient écrites en clair comme si c'était le même repère :
    # "0.335" pour le plancher gauche valait en réalité 0.383 de la page, et
    # "0.90" pour le plafond droit ne valait que 0.783 — soit ~7 % de largeur
    # perdue à gauche et ~22 % à droite. C'est CE gaspillage qui écrasait les
    # voies de cote les unes contre les autres et faisait finir un chiffre sur
    # la ligne du voisin. On convertit donc explicitement.
    _inv_tr = TR.inverted()
    def _fig_x(f):
        return float(_inv_tr.transform(fig.transFigure.transform((f, 0.5)))[0])
    def _fig_y(f):
        return float(_inv_tr.transform(fig.transFigure.transform((0.5, f)))[1])

    PAGE_L = _fig_x(0.325)   # juste à droite du séparateur texte/dessin (0.31)
    PAGE_R = _fig_x(0.985)
    PAGE_B = _fig_y(0.105)   # au-dessus de la barre KOBO
    PAGE_T = _fig_y(0.915)   # sous le trait de titre
    X_FLOOR = PAGE_L
    Y_CEIL  = PAGE_T
    Y_FLOOR = PAGE_B

    renderer = fig.canvas.get_renderer()

    def _text_size_data(s, rotation=0):
        t = ax.text(0.0, 0.0, s, fontsize=_DIM_FS, rotation=rotation, transform=TR)
        bb = t.get_window_extent(renderer)
        t.remove()
        ox, oy = _inv_tr.transform((0.0, 0.0))
        return (_inv_tr.transform((bb.width, 0.0))[0] - ox,
                _inv_tr.transform((0.0, bb.height))[1] - oy)

    # Les étiquettes des voies VERTICALES sont écrites tournées à 90° (usage du
    # dessin technique pour une cote verticale). Ce n'est pas cosmétique :
    # posée à plat, une étiquette occupe ~0.055 de largeur, soit plus que
    # l'espace disponible entre deux voies quand une porte ouverte mange le
    # côté du dessin — le chiffre débordait alors forcément sur la voie
    # voisine. Tournée, elle n'occupe que sa hauteur de texte (~0.009), donc
    # elle tient toujours dans son propre couloir.
    _lbl_probe_w, LANE_LBL_THICK = _text_size_data("ép. 199 mm")
    # Marge de non-contact : les tests de chevauchement gonflent les boîtes
    # (7 px pour une ligne, ~5.5 px pour une étiquette). Deux éléments
    # calculés « juste à touche » sont donc considérés en collision et
    # déclenchent une dérive en cascade — il faut réserver cette marge dans
    # le budget de largeur, pas seulement l'encombrement géométrique.
    PAD_D = _inv_tr.transform((14.0, 0.0))[0] - _inv_tr.transform((0.0, 0.0))[0]
    # Seconde colonne d'étiquettes DANS le couloir, réservée aux cotes des
    # éléments très minces (épaisseur de tablette, jeu entre tiroirs). Sans
    # elle, ces étiquettes — aussi hautes que celles des grandes zones alors
    # que le segment mesuré ne fait que quelques pixels — poussaient de proche
    # en proche toute la chaîne vers le haut, jusqu'à décrocher les derniers
    # chiffres du meuble qu'ils cotent.
    # Décalage minimal : juste de quoi que les deux colonnes ne se touchent
    # pas (une épaisseur de texte + la marge de non-contact). Tout millimètre
    # de plus éloigne inutilement le chiffre de sa ligne.
    THIN_LBL_COL = LANE_LBL_THICK + PAD_D

    # Largeur utile d'un couloir de voie : il doit contenir la colonne
    # principale ET la colonne décalée des cotes minces, sans toucher la voie
    # suivante.
    LANE_MIN = LBL_GAP + THIN_LBL_COL + LANE_LBL_THICK + PAD_D
    LANE_MAX = LANE_MIN * 1.5

    # Plancher / plafond des voies verticales : la voie la plus éloignée doit
    # encore laisser tenir SON étiquette entre elle et le bord de la zone.
    X_FLOOR_LEFT_LANE = PAGE_L + LANE_LBL_THICK + LBL_GAP
    X_CEIL            = PAGE_R - LANE_LBL_THICK - LBL_GAP

    # Nombre de voies réellement utilisées sur cette page : 0 = zones/tablettes,
    # 1 = hauteur totale, 2 = chaîne des tiroirs (seulement s'il y en a). On
    # répartit l'espace disponible entre ces voies-là uniquement, au lieu de
    # réserver systématiquement trois couloirs dont un reste vide.
    _n_lanes = 2 + (1 if (cab.get("drawers") or []) else 0)
    _lane_gaps = max(_n_lanes - 1, 1)
    # Départ de la pile de voies. Les cotes obliques (`dim_edge` — hauteur de
    # porte, largeur de tablette…) se posent à `GAP` de la silhouette qu'elles
    # longent, ET leur étiquette se pose encore au-delà. La pile doit donc
    # laisser passer les deux : sinon la cote de hauteur d'une porte ouverte
    # tombait exactement sur la voie des zones (deux lignes confondues) et son
    # chiffre n'avait plus nulle part où se poser.
    LANE_GAP = GAP + TICK + LBL_GAP + LANE_LBL_THICK + PAD_D
    _left_lane_span  = max((bbox_x0 - LANE_GAP) - X_FLOOR_LEFT_LANE, 0.0)
    _right_lane_span = max(X_CEIL - (bbox_x1 + LANE_GAP), 0.0)
    LEFT_LANE  = min(max(_left_lane_span  / _lane_gaps, LANE_MIN), LANE_MAX)
    RIGHT_LANE = min(max(_right_lane_span / _lane_gaps, LANE_MIN), LANE_MAX)

    def _ln(x1, y1, x2, y2, col=_DIM, lw=0.75, ls="-", alpha=1.0, zorder=30):
        ax.add_artist(plt.Line2D(
            [x1, x2], [y1, y2], transform=TR,
            color=col, lw=lw, ls=ls, alpha=alpha, zorder=zorder, clip_on=False))

    # Seules les LIGNES DE COTE (voie, ligne parallèle à l'arête, tirets
    # d'extrémité, trait de rappel) interdisent la pose d'une étiquette. Les
    # lignes d'attache en pointillés, elles, balaient forcément tout l'espace
    # entre le meuble et sa voie : les traiter comme bloquantes ne laissait
    # aucune place libre à la cote d'une porte ouverte, qui partait alors se
    # coincer dans le coin de la feuille. En dessin technique, un chiffre qui
    # croise une ligne d'attache reste parfaitement lisible.
    dim_line_boxes: list[Bbox] = []
    def _register_line_bbox(x1, y1, x2, y2, pad_px=5.0, blocking=True):
        p1 = TR.transform((x1, y1))
        p2 = TR.transform((x2, y2))
        x_min = min(p1[0], p2[0])
        x_max = max(p1[0], p2[0])
        y_min = min(p1[1], p2[1])
        y_max = max(p1[1], p2[1])
        bb = Bbox.from_extents(x_min, y_min, x_max, y_max).padded(pad_px)
        if blocking:
            dim_line_boxes.append(bb)
        return bb

    # ── Couloirs des voies verticales, RÉSERVÉS AVANT tout tracé ─────────────
    # Cause directe du défaut signalé : les voies étaient dessinées dans
    # l'ordre des appels (zones, puis hauteur totale, puis tiroirs). Quand une
    # étiquette de la voie 0 cherchait sa place, les voies 1 et 2 n'existaient
    # pas encore — donc `_label_touches_dim_lines` ne pouvait pas les voir, et
    # le chiffre finissait posé pile sur une ligne tracée juste après. On
    # réserve donc la position de TOUTES les voies possibles dès maintenant.
    # L'écart ENTRE voies est garanti constant : si la place manque, c'est la
    # pile entière qui glisse vers le dessin, jamais l'écartement qui
    # s'effondre. Brider chaque voie séparément (comme avant) ramenait deux
    # voies sur la même abscisse dès que le plancher était atteint.
    def _lane_x(lane_idx):
        if place_dims_right:
            base = min(bbox_x1 + LANE_GAP, X_CEIL - _lane_gaps * RIGHT_LANE)
            return base + lane_idx * RIGHT_LANE
        base = max(bbox_x0 - LANE_GAP, X_FLOOR_LEFT_LANE + _lane_gaps * LEFT_LANE)
        return base - lane_idx * LEFT_LANE

    lane_strip_boxes: dict = {}
    for _li in range(_n_lanes):
        _lx = _lane_x(_li)
        lane_strip_boxes[_li] = _register_line_bbox(_lx, bbox_y0, _lx, bbox_y1, pad_px=3.0)

    placed_boxes: list = []
    _lbl_pad_px = 4.0 * fig.dpi / 72.0

    def _label_overlaps_existing(text_artist) -> bool:
        bbox = text_artist.get_window_extent(renderer).padded(_lbl_pad_px)
        return any(bbox.overlaps(b) for b in placed_boxes)

    def _label_touches_dim_lines(text_artist, ignore=()) -> bool:
        bbox = text_artist.get_window_extent(renderer).padded(_lbl_pad_px)
        return any(bbox.overlaps(line_bb) for line_bb in dim_line_boxes
                   if not any(line_bb is ig for ig in ignore))

    def _segment_bbox(x1, y1, x2, y2, pad_px=3.0):
        p1 = TR.transform((x1, y1))
        p2 = TR.transform((x2, y2))
        return Bbox.from_extents(
            min(p1[0], p2[0]), min(p1[1], p2[1]),
            max(p1[0], p2[0]), max(p1[1], p2[1]),
        ).padded(pad_px)

    def _connector_segments(anchor, px, py, ideal=None):
        """Trajet du trait de rappel entre la ligne de cote et l'étiquette.

        AUCUN trait quand l'étiquette est restée à sa place idéale : elle est
        alors déjà collée à sa ligne, écrite parallèlement à elle, et un
        raccord n'apprendrait rien — il ne ferait qu'ajouter du trait. Le
        rappel n'apparaît que si l'étiquette a dû être écartée.

        Quand il apparaît, c'est un trait DROIT unique. L'équerre précédente
        doublait la longueur visible et ajoutait un virage pour un décalage
        qui, la plupart du temps, était déjà exactement porté par la normale
        à la ligne — donc parfaitement lisible en droit."""
        if anchor is None:
            return []
        ax_, ay_ = anchor
        if ideal is not None:
            if (px - ideal[0]) ** 2 + (py - ideal[1]) ** 2 <= (0.004) ** 2:
                return []
        elif (ax_ - px) ** 2 + (ay_ - py) ** 2 <= (0.006) ** 2:
            return []
        return [(ax_, ay_, px, py)]

    def _connector_overlaps_existing(anchor, px, py, ideal=None, ignore=()) -> bool:
        """Le chevauchement d'ÉTIQUETTES est vérifié (ci-dessus), mais pas
        celui du petit trait qui la relie à sa ligne de cote (voir
        `_place_label`, paramètre `anchor`) : une étiquette pouvait donc être
        posée dans un emplacement libre alors que son TRAIT DE RAPPEL,
        lui, traversait une étiquette déjà posée ailleurs — donnant
        l'impression qu'un chiffre "appartient" à la ligne qu'il chevauche.
        Ici on vérifie le trajet réel du connecteur (le même que celui qui
        sera tracé) contre toutes les étiquettes et lignes déjà posées."""
        if anchor is None:
            return False
        segments = _connector_segments(anchor, px, py, ideal)
        for x1, y1, x2, y2 in segments:
            seg_bb = _segment_bbox(x1, y1, x2, y2)
            if any(seg_bb.overlaps(b) for b in placed_boxes):
                return True
            if any(seg_bb.overlaps(b) for b in dim_line_boxes
                   if not any(b is ig for ig in ignore)):
                return True
        return False

    # Bornes dures de la page imprimable (indépendantes des voies/lanes) :
    # quoi qu'il arrive — même si une étiquette n'a jamais trouvé de place
    # libre dans son budget de recherche — elle ne doit JAMAIS finir hors de
    # la feuille ni sous le panneau de texte. `clip_on=False` (nécessaire pour
    # ne pas rogner les cotes en bord de dessin) désactive tout garde-fou
    # matplotlib.
    SAFE_X_LO, SAFE_X_HI = PAGE_L, PAGE_R
    SAFE_Y_LO, SAFE_Y_HI = PAGE_B, PAGE_T

    def _place_label(x, y, text, col, ha, va, step, anchor=None, zorder=31,
                     rotation=0, own_lines=()):
        """Pose `text` en (x,y) ; si ça chevauche une étiquette déjà posée,
        la décale de petits pas successifs le long de `step` (dx,dy), sans
        jamais sortir de la page (voir `SAFE_X_*`/`SAFE_Y_*` ci-dessus).
        `anchor` (point sur la ligne de cote elle-même, si fourni) est relié à
        l'étiquette par un petit trait fin : avec plusieurs cotes voisines et
        à peu près parallèles, la seule proximité ne suffit pas toujours à
        dire quel nombre va avec quelle ligne — ce trait lève l'ambiguïté."""
        # Les bornes s'appliquaient au POINT D'ANCRAGE du texte, pas à son
        # encombrement : une étiquette alignée à droite pouvait donc être
        # « dans la page » tout en débordant de sa propre largeur sur le
        # panneau de texte. On rétrécit les bornes de l'encombrement réel.
        _w, _h = _text_size_data(text, rotation=rotation)
        _lo_x = SAFE_X_LO + (_w if ha == "right" else _w / 2 if ha == "center" else 0.0)
        _hi_x = SAFE_X_HI - (_w if ha == "left" else _w / 2 if ha == "center" else 0.0)
        _lo_y = SAFE_Y_LO + (_h if va == "top" else _h / 2 if va == "center" else 0.0)
        _hi_y = SAFE_Y_HI - (_h if va == "bottom" else _h / 2 if va == "center" else 0.0)
        if _lo_x > _hi_x:
            _lo_x = _hi_x = (SAFE_X_LO + SAFE_X_HI) / 2
        if _lo_y > _hi_y:
            _lo_y = _hi_y = (SAFE_Y_LO + SAFE_Y_HI) / 2
        SAFE_X_LO_, SAFE_X_HI_ = _lo_x, _hi_x
        SAFE_Y_LO_, SAFE_Y_HI_ = _lo_y, _hi_y

        txt = None
        px = min(max(x, SAFE_X_LO_), SAFE_X_HI_)
        py = min(max(y, SAFE_Y_LO_), SAFE_Y_HI_)
        # Repli à 3 niveaux, du plus exigeant au moins exigeant :
        #   1. rien touché du tout (étiquette + trait de rappel libres),
        #   2. étiquette libre de toute autre étiquette ET de toute ligne,
        #   3. étiquette libre des autres ÉTIQUETTES seulement.
        # Le niveau 3 existe parce qu'un chiffre posé sur un TRAIT reste
        # lisible, alors que deux chiffres empilés donnent une bouillie
        # illisible ("796 mm" et "800 mm" superposés en "796 mm nm"). Sans lui,
        # une recherche insatisfaisable gardait le tout dernier essai, c'est à
        # dire la position bloquée contre le bord de page où toutes les
        # étiquettes en échec finissaient au même endroit.
        # Position IDÉALE : collée à sa ligne, sur la normale. Toute la
        # recherche part de là et doit y revenir si elle échoue.
        ideal = (px, py)
        # Rayon de dérive borné. La recherche prenait le premier emplacement
        # libre le long du pas, sans limite de distance : une étiquette
        # coincée dans une zone chargée partait jusqu'à 0.35 (≈ 7 cm sur la
        # feuille) de sa propre ligne, avec un trait de rappel interminable.
        # Passé ce rayon, un chiffre légèrement en contact avec un trait est
        # préférable à un chiffre qu'on ne rattache plus à rien.
        MAX_SHIFT = 6.0 * LBL_GAP
        _step_len = max((step[0] ** 2 + step[1] ** 2) ** 0.5, 1e-9)
        _n_steps = max(1, min(64, int(MAX_SHIFT / _step_len) + 1))

        best = None
        fb_lines = None
        fb_labels = None
        for _ in range(_n_steps):
            if txt is not None:
                txt.remove()
            txt = ax.text(px, py, text, ha=ha, va=va, fontsize=_DIM_FS,
                            color=col, transform=TR, zorder=zorder,
                            rotation=rotation, rotation_mode="default",
                            clip_on=False,
                            bbox=dict(facecolor="white", edgecolor="none", pad=2.5, alpha=0.95))
            free_of_labels = not _label_overlaps_existing(txt)
            label_ok = free_of_labels and not _label_touches_dim_lines(txt, ignore=own_lines)
            if free_of_labels and fb_labels is None:
                fb_labels = (px, py)
            if label_ok and fb_lines is None:
                fb_lines = (px, py)
            if label_ok and not _connector_overlaps_existing(
                    anchor, px, py, ideal=ideal, ignore=own_lines):
                best = (px, py)
                break
            px = min(max(px + step[0], SAFE_X_LO_), SAFE_X_HI_)
            py = min(max(py + step[1], SAFE_Y_LO_), SAFE_Y_HI_)
        if best is None:
            # `ideal` en dernier recours (et non le dernier essai) : mieux vaut
            # un chiffre au contact d'un trait, mais à sa place, qu'un chiffre
            # propre relégué à l'autre bout de la zone.
            chosen = fb_lines or fb_labels or ideal
            if chosen != (px, py):
                px, py = chosen
                txt.remove()
                txt = ax.text(px, py, text, ha=ha, va=va, fontsize=_DIM_FS,
                                color=col, transform=TR, zorder=zorder,
                                rotation=rotation, rotation_mode="default",
                                clip_on=False,
                                bbox=dict(facecolor="white", edgecolor="none", pad=2.5, alpha=0.95))
        placed_boxes.append(txt.get_window_extent(renderer).padded(_lbl_pad_px))
        for _sx1, _sy1, _sx2, _sy2 in _connector_segments(anchor, px, py, ideal):
            _ln(_sx1, _sy1, _sx2, _sy2, col=col, lw=0.4, ls=":", alpha=0.6, zorder=zorder)
            _register_line_bbox(_sx1, _sy1, _sx2, _sy2)

    # ── Cote verticale (voie fixe à droite du dessin) : zones + H globale ───
    # Ligne d'attache horizontale depuis le sommet réel, jusqu'à une voie de
    # cote verticale unique par `lane_idx` (0 = cotes partielles, 1 = cote
    # totale, strictement plus à droite) : flèches/tirets posés exactement
    # sur cette même voie, donc toujours en contact avec la ligne de cote.
    def dim_right(pt1_3d, pt2_3d, label, lane_idx, col=_DIM, label_extra_gap=0.0,
                  foreground=False):
        # `foreground` : voir `dim_edge` plus bas — même principe, pour une
        # voie dont le tracé (tirets + voie + tirets d'extrémité) risquerait
        # de passer sous le rendu du caisson (silhouette qui la recouvrirait
        # alors entièrement) au lieu d'être toujours visible.
        z_line = 40 if foreground else 30
        z_lbl = 41 if foreground else 31
        xa1, ya1 = project(*pt1_3d)
        xa2, ya2 = project(*pt2_3d)
        _assert_anchored(pt1_3d, (xa1, ya1), label)
        _assert_anchored(pt2_3d, (xa2, ya2), label)
        # Position de la voie : STRICTEMENT celle réservée en amont
        # (`_lane_x`), pour que la ligne tombe pile dans le couloir déjà
        # interdit aux étiquettes des autres voies. Un décalage local
        # (`lane_extra_gap`) rendrait cette réservation fausse.
        xd = _lane_x(lane_idx)
        own_lines = [lane_strip_boxes[lane_idx]]
        yd1, yd2 = ya1, ya2
        _ln(xa1, ya1, xd, yd1, col=col, lw=0.5, ls="--", alpha=0.6, zorder=z_line)
        own_lines.append(_register_line_bbox(xa1, ya1, xd, yd1, blocking=False))
        _ln(xa2, ya2, xd, yd2, col=col, lw=0.5, ls="--", alpha=0.6, zorder=z_line)
        own_lines.append(_register_line_bbox(xa2, ya2, xd, yd2, blocking=False))
        _ln(xd, yd1, xd, yd2, col=col, zorder=z_line)
        own_lines.append(_register_line_bbox(xd, yd1, xd, yd2))
        for yp in (yd1, yd2):
            _ln(xd - TICK, yp, xd + TICK, yp, col=col, zorder=z_line)
            own_lines.append(_register_line_bbox(xd - TICK, yp, xd + TICK, yp))
        # Étiquette TOURNÉE À 90°, plaquée contre sa propre voie : elle
        # n'occupe alors que l'épaisseur d'une ligne de texte en largeur, donc
        # elle tient dans son couloir sans jamais mordre sur la voie voisine
        # (voir `LANE_LBL_THICK`). Le pas de dégagement est essentiellement
        # vertical — c'est la seule direction où la voie a de la place — avec
        # une petite composante vers l'extérieur pour ne pas empiler deux
        # étiquettes au même endroit quand la chaîne est très serrée.
        if place_dims_right:
            _place_label(xd + LBL_GAP + label_extra_gap, (yd1 + yd2) / 2, label, col,
                        ha="left", va="center", step=(0.004, 0.012),
                        anchor=(xd, (yd1 + yd2) / 2),
                        zorder=z_lbl, rotation=90, own_lines=own_lines)
        else:
            _place_label(xd - LBL_GAP - label_extra_gap, (yd1 + yd2) / 2, label, col,
                        ha="right", va="center", step=(-0.004, 0.012),
                        anchor=(xd, (yd1 + yd2) / 2),
                        zorder=z_lbl, rotation=90, own_lines=own_lines)

    # ── Cote parallèle à l'arête mesurée (L, P) ──────────────────────────────
    # Convention de dessin technique : lignes d'attache PERPENDICULAIRES à
    # l'arête, partant exactement des sommets projetés ; ligne de cote
    # PARALLÈLE à l'arête (jamais une horizontale arbitraire indépendante de
    # l'angle isométrique). Le décalage le long de la normale est la distance
    # minimale nécessaire pour dégager, LOCALEMENT sous l'arête mesurée, les
    # pixels réellement dessinés (silhouette) — pas la boîte englobante
    # globale du dessin, qui tire le décalage vers le coin le plus éloigné du
    # meuble même quand il n'est pas sur le passage de cette arête, et envoie
    # la cote hors de la page.
    def dim_edge(pt1_3d, pt2_3d, label, col=_DIM, label_on_line=False, extra_offset=0.0,
                 fixed_offset=None, foreground=False, label_extra_gap=0.0):
        # `foreground` : passe la cote AU-DESSUS du rendu du caisson (au lieu
        # d'en dessous, cf. `ax_img.imshow(..., zorder=35)` plus haut). Utile
        # pour une cote dont les points mesurés sont ENTOURÉS de matière du
        # meuble (tablette à l'intérieur du caisson) : sinon les lignes
        # d'attache partent en grande partie masquées, et on ne comprend plus
        # ce que la cote pointe.
        z_line = 40 if foreground else 30
        z_lbl = 41 if foreground else 31
        xa1, ya1 = project(*pt1_3d)
        xa2, ya2 = project(*pt2_3d)
        _assert_anchored(pt1_3d, (xa1, ya1), label)
        _assert_anchored(pt2_3d, (xa2, ya2), label)

        dx, dy = xa2 - xa1, ya2 - ya1
        ln = (dx ** 2 + dy ** 2) ** 0.5 + 1e-9
        ux, uy = dx / ln, dy / ln      # direction unitaire de l'arête (= de la cote)
        nx, ny = -uy, ux               # normale unitaire
        # Sens de la normale déterminé géométriquement (jamais un vecteur
        # deviné à la main) : celui qui éloigne la cote du CENTRE de la boîte
        # englobante réelle du dessin. Un simple vecteur "outward" fixe serait
        # ambigu pour une arête proche de 45° (produit scalaire proche de 0 →
        # sens instable), ce qui provoquait un décalage énorme et une ligne
        # d'attache partant dans la mauvaise direction, à travers le dessin.
        mx0, my0 = (xa1 + xa2) / 2, (ya1 + ya2) / 2
        if nx * (mx0 - bbox_cx) + ny * (my0 - bbox_cy) < 0:
            nx, ny = -nx, -ny

        if fixed_offset is not None:
            # Dégagement FIXE (n'interroge pas la silhouette) : utilisé pour
            # les cotes d'éléments dessinés ailleurs dans une position ouverte/
            # pivotée (porte, tiroir) — la recherche de dégagement local sous
            # l'arête y capterait la silhouette de l'élément ouvert lui-même
            # (sans lien avec sa position de référence mesurée ici) et
            # pousserait la cote hors de la page.
            offset = float(fixed_offset) + float(extra_offset)
        else:
            # Coordonnée tangentielle (le long de l'arête) de chaque pixel de la
            # silhouette, relative à pt1 : on ne garde que ceux qui tombent sous
            # le segment mesuré (+ petite marge), puis on prend leur extension
            # maximale le long de la normale — c'est ÇA le dégagement local requis.
            u = (_sil_x - xa1) * ux + (_sil_y - ya1) * uy
            margin = 0.05 * ln
            under_edge = (u >= -margin) & (u <= ln + margin)
            if under_edge.any():
                v = (_sil_x[under_edge] - xa1) * nx + (_sil_y[under_edge] - ya1) * ny
                local_clearance = float(v.max())
            else:
                local_clearance = 0.0
            offset = max(local_clearance, 0.0) + GAP + float(extra_offset)

        # Le MÊME décalage doit s'appliquer aux deux extrémités : c'est ce qui
        # garantit que la ligne de cote reste rigoureusement PARALLÈLE à
        # l'arête mesurée (une simple translation). Brider xd1/yd1 et xd2/yd2
        # indépendamment (comme avant) pouvait arrêter une extrémité avant
        # l'autre sur une arête très longue (ex. largeur d'un grand caisson),
        # ce qui inclinait la ligne au lieu de la garder parallèle. On calcule
        # donc le plus grand décalage commun qui garde LES DEUX extrémités
        # dans la page, puis on l'applique identiquement aux deux.
        def _max_offset_within_bounds(xa, ya):
            limit = offset
            if nx > 1e-9:
                limit = min(limit, (X_CEIL - xa) / nx)
            elif nx < -1e-9:
                limit = min(limit, (X_FLOOR - xa) / nx)
            if ny > 1e-9:
                limit = min(limit, (Y_CEIL - ya) / ny)
            elif ny < -1e-9:
                # Plancher manquant jusqu'ici : une cote descendante (largeur
                # de façade de tiroir, par exemple) n'était bridée par rien
                # vers le bas et pouvait aller mordre la barre KOBO.
                limit = min(limit, (Y_FLOOR - ya) / ny)
            return limit

        safe_offset = max(0.0, min(
            _max_offset_within_bounds(xa1, ya1),
            _max_offset_within_bounds(xa2, ya2),
        ))
        xd1, yd1 = xa1 + nx * safe_offset, ya1 + ny * safe_offset
        xd2, yd2 = xa2 + nx * safe_offset, ya2 + ny * safe_offset

        edge_helper_lw = 0.35
        edge_dim_lw = 0.60
        edge_tick = TICK * 0.70

        own_lines = []
        _ln(xa1, ya1, xd1, yd1, col=col, lw=edge_helper_lw, ls="--", alpha=0.45, zorder=z_line)
        own_lines.append(_register_line_bbox(xa1, ya1, xd1, yd1, blocking=False))
        _ln(xa2, ya2, xd2, yd2, col=col, lw=edge_helper_lw, ls="--", alpha=0.45, zorder=z_line)
        own_lines.append(_register_line_bbox(xa2, ya2, xd2, yd2, blocking=False))
        _ln(xd1, yd1, xd2, yd2, col=col, lw=edge_dim_lw, zorder=z_line)   # ligne de cote : parallèle à l'arête par construction
        own_lines.append(_register_line_bbox(xd1, yd1, xd2, yd2))
        for xp, yp in ((xd1, yd1), (xd2, yd2)):
            _ln(xp - nx * edge_tick, yp - ny * edge_tick, xp + nx * edge_tick, yp + ny * edge_tick, col=col, lw=edge_dim_lw, zorder=z_line)
            own_lines.append(_register_line_bbox(xp - nx * edge_tick, yp - ny * edge_tick, xp + nx * edge_tick, yp + ny * edge_tick))
        mx, my = (xd1 + xd2) / 2, (yd1 + yd2) / 2
        # Le nombre est écrit PARALLÈLE à sa propre ligne de cote (l'axe des
        # données est isotrope — l'axe fait 8.27×8.27 in — donc l'angle calculé
        # ici est bien l'angle à l'écran). Deux effets : l'association
        # chiffre ↔ ligne devient immédiate, et surtout l'étiquette n'occupe
        # plus que son épaisseur de texte PERPENDICULAIREMENT à la ligne. À
        # plat, un « 796 mm » posé le long d'une arête verticale barrait toute
        # la largeur des voies de cote voisines et devait fuir à l'autre bout
        # de la page pour trouver une place libre.
        _ang = math.degrees(math.atan2(yd2 - yd1, xd2 - xd1))
        if _ang > 90.0:
            _ang -= 180.0
        elif _ang < -90.0:
            _ang += 180.0
        # Dégagement en cas de collision : d'abord LE LONG de sa propre ligne
        # (le chiffre glisse en restant collé à la cote qu'il désigne), avec
        # une petite composante vers l'extérieur. Un pas purement normal
        # faisait traverser à l'étiquette toute la largeur de la page, voie de
        # cote après voie de cote, jusqu'à se coincer contre le bord.
        _step = (ux * 0.020 + nx * 0.005, uy * 0.020 + ny * 0.005)
        if label_on_line:
            # Texte centre sur la ligne de cote (masque blanc pour rester lisible).
            _place_label(mx, my, label, col,
                         ha="center", va="center",
                         step=_step, anchor=(mx, my), zorder=z_lbl,
                         rotation=_ang, own_lines=own_lines)
        else:
            # `label_extra_gap` : seconde rangée d'étiquettes dans le même
            # couloir, pour les segments trop courts pour contenir leur propre
            # nombre (jeu entre deux façades voisines). Sans elle, ces
            # étiquettes se battent avec celles des segments voisins et
            # finissent posées sur une ligne qui n'est pas la leur.
            _lbl_off = LBL_GAP + _text_size_data(label)[1] / 2 + float(label_extra_gap)
            _place_label(mx + nx * _lbl_off, my + ny * _lbl_off, label, col,
                         ha="center", va="center",
                         step=_step,
                         anchor=(mx, my), zorder=z_lbl,
                         rotation=_ang, own_lines=own_lines)

    # L (largeur) : arête supérieure ARRIERE (demande metier)
    dim_edge(vertices["coin_haut_arriere_gauche"], vertices["coin_haut_arriere_droit"],
              f"{int(L)} mm", label_on_line=False, extra_offset=LANE * 0.6)
    # P (profondeur) : arête supérieure du côté visible (droite ou gauche)
    if use_right_edge:
        p1 = vertices["coin_haut_avant_droit"]
        p2 = vertices["coin_haut_arriere_droit"]
    else:
        p1 = vertices["coin_haut_avant_gauche"]
        p2 = vertices["coin_haut_arriere_gauche"]
    dim_edge(p1, p2, f"{int(W)} mm", extra_offset=LANE * 1.2)

    # Zones de hauteur intérieure (tablettes / vides) : voie 0, en chaînant
    # les sommets consécutifs du registre (bas → chaque tablette → haut) —
    # positions entièrement dérivées des dimensions paramétriques.
    shelves_sorted = sorted(cab.get("shelves", []), key=lambda s: float(s["height"]))
    prev_pt = vertices["zone_bas_z"] if use_right_edge else vertices["zone_bas_z_gauche"]
    for i in range(1, len(shelves_sorted) + 1):
        if use_right_edge:
            dessous_pt = vertices[f"tablette_{i}_dessous"]
            dessus_pt  = vertices[f"tablette_{i}_dessus"]
        else:
            dessous_pt = vertices[f"tablette_{i}_dessous_gauche"]
            dessus_pt  = vertices[f"tablette_{i}_dessus_gauche"]
        if dessous_pt[2] - prev_pt[2] > 1:
            dim_right(prev_pt, dessous_pt, f"{int(round(dessous_pt[2] - prev_pt[2]))} mm", lane_idx=0)
        th = dessus_pt[2] - dessous_pt[2]
        dim_right(
            dessous_pt,
            dessus_pt,
            f"ép. {int(round(th))} mm",
            lane_idx=0,
            col=_GREY_TXT,
            label_extra_gap=THIN_LBL_COL,
        )
        prev_pt = dessus_pt
    zone_top = vertices["zone_haut_z"] if use_right_edge else vertices["zone_haut_z_gauche"]
    if zone_top[2] - prev_pt[2] > 1:
        dim_right(prev_pt, zone_top,
                   f"{int(round(zone_top[2] - prev_pt[2]))} mm", lane_idx=0)

    # H (hauteur globale) : voie 1, toujours au-delà de la voie des zones.
    h_bottom = vertices["coin_bas_avant_droit"] if use_right_edge else vertices["coin_bas_avant_gauche"]
    h_top = vertices["coin_haut_avant_droit"] if use_right_edge else vertices["coin_haut_avant_gauche"]
    dim_right(h_bottom, h_top, f"{int(H)} mm", lane_idx=1)

    # Largeur d'une tablette (largeur = largeur de traverse, identique pour
    # toutes) : cote PARALLÈLE à l'arête avant réelle de la tablette.
    # `foreground=True` : la tablette est entourée de matière du caisson
    # (montants, porte, autres tablettes) des deux côtés, donc une cote
    # dessinée EN DESSOUS du rendu (comme les autres) serait en grande partie
    # masquée et son attache incompréhensible — on la pose au-dessus.
    if "tablette_1_avant_gauche" in vertices:
        dim_edge(vertices["tablette_1_avant_gauche"], vertices["tablette_1_avant_droit"],
                 f"{int(round(vertices['tablette_1_avant_droit'][0] - vertices['tablette_1_avant_gauche'][0]))} mm",
                 col=_GREY_TXT, foreground=True)

    # Profondeur d'une tablette (= profondeur du caisson - 19mm) : cote
    # PARALLÈLE à l'arête latérale réelle de la tablette (avant → arrière),
    # également au premier plan pour la même raison.
    if "tablette_1_arriere_gauche" in vertices:
        prof = vertices["tablette_1_arriere_gauche"][1] - vertices["tablette_1_avant_gauche"][1]
        dim_edge(vertices["tablette_1_avant_gauche"], vertices["tablette_1_arriere_gauche"],
                 f"{int(round(prof))} mm", col=_GREY_TXT, foreground=True)

    # Largeur et hauteur de la porte : cotes PARALLÈLES à la porte telle
    # qu'elle est réellement dessinée (ouverte), pas à sa position fermée —
    # `dim_edge` calcule son décalage depuis le dégagement local sous
    # l'arête mesurée ; comme les points mesurés SONT ceux du panneau
    # dessiné, ce dégagement colle naturellement au bord de la porte.
    door_props = cab.get("door_props", {})
    if door_props.get("has_door"):
        dtype = door_props.get("door_type", "single")
        if dtype == "single" and "porte_bas_gauche" in vertices:
            door_w = math.dist(vertices["porte_bas_gauche"][:2], vertices["porte_bas_droit"][:2])
            door_h = vertices["porte_haut_gauche"][2] - vertices["porte_bas_gauche"][2]
            dim_edge(vertices["porte_bas_gauche"], vertices["porte_bas_droit"],
                     f"{int(round(door_w))} mm")
            dim_edge(vertices["porte_bas_gauche"], vertices["porte_haut_gauche"],
                     f"{int(round(door_h))} mm")
        elif "porte_g_bas_gauche" in vertices:
            leaf_w = math.dist(vertices["porte_g_bas_gauche"][:2], vertices["porte_g_bas_droit"][:2])
            leaf_h = vertices["porte_g_haut_gauche"][2] - vertices["porte_g_bas_gauche"][2]
            dim_edge(vertices["porte_g_bas_gauche"], vertices["porte_g_bas_droit"],
                     f"{int(round(leaf_w))} mm")
            dim_edge(vertices["porte_g_bas_gauche"], vertices["porte_g_haut_gauche"],
                     f"{int(round(leaf_h))} mm")
            dim_edge(vertices["porte_d_bas_gauche"], vertices["porte_d_bas_droit"],
                     f"{int(round(leaf_w))} mm")
            dim_edge(vertices["porte_d_bas_droit"], vertices["porte_d_haut_droit"],
                     f"{int(round(leaf_h))} mm")

    # ── Tiroirs : largeurs et jeux HORIZONTAUX ──────────────────────────────
    # Des façades posées côte à côte se cotent à l'horizontale. Les enchaîner
    # sur la voie verticale (comme des tiroirs empilés) produisait des
    # segments de hauteur nulle : toutes les cotes de la rangée tombaient au
    # même endroit, superposées et illisibles.
    face_rows = {}
    _n_faces = 0
    while f"tiroir_{_n_faces + 1}_face_bas_gauche" in vertices:
        _n_faces += 1
    for i in range(1, _n_faces + 1):
        bg = vertices[f"tiroir_{i}_face_bas_gauche"]
        bd = vertices[f"tiroir_{i}_face_bas_droit"]
        face_rows.setdefault(round(bg[2], 1), []).append((bg, bd))

    # Un décalage commun à TOUTE la chaîne : chaque cote calculerait sinon son
    # propre dégagement local, et les segments d'une même rangée ne seraient
    # plus alignés sur une seule ligne de cote.
    _row_offset = GAP + LANE * 1.4
    for _k, (_z, row) in enumerate(sorted(face_rows.items())):
        row.sort(key=lambda seg: seg[0][0])
        if len(row) < 2:
            continue
        off = _row_offset + _k * (LANE * 0.9)
        # `foreground=True` sur TOUTE la chaîne : une rangée de tiroirs est
        # entourée de matière (traverses, montants, façades voisines) et sa
        # ligne de cote passe devant le caisson — dessinée sous le rendu, elle
        # disparaissait par morceaux et on ne rattachait plus les chiffres.
        for bg, bd in row:
            dim_edge(bg, bd, f"{int(round(bd[0] - bg[0]))} mm",
                     fixed_offset=off, foreground=True)
        for (_, bd_prev), (bg_next, _) in zip(row, row[1:]):
            jeu = bg_next[0] - bd_prev[0]
            if jeu > 1:
                dim_edge(bd_prev, bg_next, f"jeu {int(round(jeu))} mm",
                         col=_GREY_TXT, fixed_offset=off, foreground=True,
                         label_extra_gap=THIN_LBL_COL)

    # Largeur de façade quand il n'y a qu'un tiroir par rangée : une seule
    # cote suffit (les chaînes horizontales ci-dessus couvrent les autres cas).
    if "tiroir_1_bas_gauche" in vertices and all(len(r) < 2 for r in face_rows.values()):
        tiroir_w = vertices["tiroir_1_bas_droit"][0] - vertices["tiroir_1_bas_gauche"][0]
        dim_edge(vertices["tiroir_1_bas_gauche"], vertices["tiroir_1_bas_droit"],
                 f"{int(round(tiroir_w))} mm")

    # ── Tiroirs : hauteurs et jeux VERTICAUX ────────────────────────────────
    # Voie 2, en chaînant les sommets consécutifs du registre (bas du caisson →
    # jeu → façade 1 → jeu → façade 2 → … → haut), même principe que la chaîne
    # des tablettes. `_cabinet_vertices` n'y a inscrit qu'UNE colonne de
    # façades : chaîner des tiroirs côte à côte donnerait autant de cotes
    # identiques empilées au même endroit.
    _n_col = 0
    while f"tiroir_{_n_col + 1}_dessous" in vertices:
        _n_col += 1
    if _n_col and "tiroir_zone_bas_z" in vertices:
        prev_pt = vertices["tiroir_zone_bas_z"] if use_right_edge else vertices["tiroir_zone_bas_z_gauche"]
        for i in range(1, _n_col + 1):
            if use_right_edge:
                dessous_pt = vertices[f"tiroir_{i}_dessous"]
                dessus_pt  = vertices[f"tiroir_{i}_dessus"]
            else:
                dessous_pt = vertices[f"tiroir_{i}_dessous_gauche"]
                dessus_pt  = vertices[f"tiroir_{i}_dessus_gauche"]
            if dessous_pt[2] - prev_pt[2] > 1:
                dim_right(prev_pt, dessous_pt,
                          f"jeu {int(round(dessous_pt[2] - prev_pt[2]))} mm",
                          lane_idx=2, col=_GREY_TXT, foreground=True,
                          label_extra_gap=THIN_LBL_COL)
            dim_right(dessous_pt, dessus_pt,
                      f"{int(round(dessus_pt[2] - dessous_pt[2]))} mm", lane_idx=2,
                      foreground=True)
            prev_pt = dessus_pt
        zone_top = vertices["tiroir_zone_haut_z"] if use_right_edge else vertices["tiroir_zone_haut_z_gauche"]
        if zone_top[2] - prev_pt[2] > 1:
            dim_right(prev_pt, zone_top,
                      f"jeu {int(round(zone_top[2] - prev_pt[2]))} mm",
                      lane_idx=2, col=_GREY_TXT, foreground=True,
                      label_extra_gap=THIN_LBL_COL)


# ─────────────────────────────────────────────────────────────────────────────
# Page de garde (identique à la référence)
# ─────────────────────────────────────────────────────────────────────────────

def _cover_page(pdf: PdfPages, project_name: str, client: str,
                date_str: str, logo_img):
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.patch.set_facecolor("white")

    # Titre projet (grande police, gras, haut gauche). Replié aux espaces s'il
    # est trop long pour la largeur de la page : à 30 pt, un nom de chantier
    # un peu bavard sortait de la feuille par la droite.
    # La limite est le TRAIT VERTICAL de la page de garde (x=0.48, tracé plus
    # bas), pas le bord de feuille : c'est lui que le titre traversait.
    _COVER_FS = 30
    _COVER_RULE_X = 0.48
    title_wrapped, _n_lines, _cover_fs = _wrap_to_width(
        fig, project_name, _COVER_RULE_X - 0.06 - 0.02, _COVER_FS, "bold")
    fig.text(0.06, 0.68, title_wrapped,
             ha="left", va="center",
             fontsize=_cover_fs, fontweight="bold", color=_DARK,
             fontfamily="DejaVu Sans")

    # Client et date descendent de ce que le titre a pris en plus.
    _drop = (_n_lines - 1) * (_cover_fs * 1.35 / 72.0 / fig.get_figheight())

    # Client (police moyenne, gris)
    fig.text(0.06, 0.56 - _drop, client,
             ha="left", va="center",
             fontsize=13, color=_GREY_TXT,
             fontfamily="DejaVu Sans")

    # Date
    fig.text(0.06, 0.50 - _drop, date_str,
             ha="left", va="center",
             fontsize=10, color="#9A9A9A",
             fontfamily="DejaVu Sans")

    # Ligne verticale centrale (style référence)
    fig.add_artist(plt.Line2D([_COVER_RULE_X, _COVER_RULE_X], [0.12, 0.92],
                              transform=fig.transFigure,
                              color="#BBBBBB", linewidth=0.8))

    bar_top = _draw_kobo_bar(fig, logo_img)

    pdf.savefig(fig, dpi=150)
    plt.close(fig)


def _wrap_to_width(fig, text: str, max_frac: float, fontsize: float,
                   weight: str = "normal", min_scale: float = 0.6) -> tuple:
    """Coupe `text` sur des ESPACES pour qu'aucune ligne ne dépasse `max_frac`
    (fraction de la largeur de figure).
    Renvoie (texte, nombre de lignes, corps de police à utiliser).

    La largeur est MESURÉE avec la police réelle, pas estimée à partir d'un
    nombre de caractères : à 14 pt gras, « I » et « W » ne font pas la même
    largeur, et un compte de caractères laisserait passer des titres qui
    débordent quand même du trait de séparation.

    Le retour à la ligne se fait UNIQUEMENT aux espaces. Un mot seul plus
    large que `max_frac` ne peut donc pas être replié : dans ce cas seulement,
    on réduit le corps de la police (jusqu'à `min_scale`) plutôt que de
    laisser le titre traverser le trait."""
    words = str(text).split()
    if not words:
        return str(text), 1, fontsize
    renderer = fig.canvas.get_renderer()

    def width_of(s: str, fs: float) -> float:
        t = fig.text(0.0, 0.0, s, fontsize=fs, fontweight=weight,
                     fontfamily="DejaVu Sans")
        w = t.get_window_extent(renderer).width
        t.remove()
        return w / fig.bbox.width

    def wrap_at(fs: float):
        lines, cur = [], words[0]
        for word in words[1:]:
            trial = f"{cur} {word}"
            if width_of(trial, fs) <= max_frac:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        return lines

    fs = float(fontsize)
    lines = wrap_at(fs)
    floor = float(fontsize) * float(min_scale)
    while fs > floor and max(width_of(ln, fs) for ln in lines) > max_frac:
        fs = max(floor, fs * 0.92)
        lines = wrap_at(fs)
    return "\n".join(lines), len(lines), fs


# ─────────────────────────────────────────────────────────────────────────────
# Page par caisson (identique à la référence)
# ─────────────────────────────────────────────────────────────────────────────

def _cabinet_page(pdf: PdfPages, cab: Dict, cab_idx: int,
                  project_name: str, client: str,
                  logo_img: Optional[np.ndarray]):
    label = cab.get("name", f"C{cab_idx + 1}")

    fig = plt.figure(figsize=(11.69, 8.27))
    fig.patch.set_facecolor("white")

    # ── Nom du projet en haut à droite (gras) ───────────────────────────
    fig.text(0.97, 0.955, project_name.upper(),
             ha="right", va="top",
             fontsize=11, fontweight="bold", color=_DARK,
             fontfamily="DejaVu Sans")

    # ── Ligne de séparation sous le titre ───────────────────────────────
    _separator(fig, 0.935, color="#CCCCCC", lw=0.6)

    # ── Colonne gauche : texte descriptif ────────────────────────────────
    # Nom du caisson
    # Le nom du caisson doit rester DANS la colonne de texte : le trait de
    # séparation est à x=0.31, le texte démarre à 0.025. Un nom long partait
    # au travers du trait et jusque dans le dessin — on le replie sur
    # plusieurs lignes, aux espaces.
    _TITLE_FS = 14
    label_wrapped, _n_title_lines, _title_fs = _wrap_to_width(
        fig, label, 0.31 - 0.025 - 0.012, _TITLE_FS, "bold")
    fig.text(0.025, 0.90, label_wrapped,
             ha="left", va="top",
             fontsize=_title_fs, fontweight="bold", color=_DARK,
             fontfamily="DejaVu Sans")

    # Le descriptif descend d'autant que le titre a pris de lignes, sinon un
    # titre replié viendrait le recouvrir.
    _title_line_h = _title_fs * 1.35 / 72.0 / fig.get_figheight()
    desc_lines = _description_lines(cab, label)
    desc_text  = "\n".join(desc_lines)
    fig.text(0.025, 0.83 - (_n_title_lines - 1) * _title_line_h, desc_text,
             ha="left", va="top",
             fontsize=_DESC_FS, color=_DARK,
             fontfamily="DejaVu Sans",
             linespacing=1.7)

    # Trait vertical entre texte et vue
    fig.add_artist(plt.Line2D([0.31, 0.31], [0.14, 0.93],
                              transform=fig.transFigure,
                              color="#DDDDDD", linewidth=0.7, zorder=5))

    # ── Colonne droite : vue 3D ──────────────────────────────────────────────
    # Aucun axe 3D matplotlib : `_render_cabinet` calcule sa propre projection
    # (voir `_make_projector`) et rend directement une image matricielle.
    # `ax_img` (2D, pleine page, en coordonnées fraction-figure) l'affiche.
    # ── Cadrage du dessin : zone FIXE, centrée dans la moitié droite ─────────
    # La moitié droite utile va du séparateur (x=0.31) au bord de feuille
    # (0.98) et de la barre KOBO (0.10) au trait de titre (0.935). On y
    # réserve une marge périphérique pour l'appareil de cotation (pile de
    # voies + étiquettes à gauche/droite, cotes L/P au-dessus et en dessous),
    # et le dessin s'inscrit dans ce qui reste — la MÊME boîte pour tous les
    # caissons, d'où une taille apparente homogène d'une page à l'autre.
    # `rect` et `fit_box` sont dans le repère de DONNÉES de `ax_img` (celui que
    # reçoit `imshow` via `extent`), PAS en fraction figure : l'aspect 'equal'
    # rétrécit l'axe en un carré centré, les deux repères ne coïncident donc
    # pas en X. La mise en page, elle, raisonne bien en fraction figure
    # (séparateur 0.31, bord de feuille 0.98) — d'où la conversion explicite.
    _ax_span_x = fig.get_figheight() / fig.get_figwidth()
    _ax_x0 = (1.0 - _ax_span_x) / 2.0
    def _to_ax_x(f):
        return (f - _ax_x0) / _ax_span_x
    ZONE_X0, ZONE_X1 = _to_ax_x(0.31), _to_ax_x(0.98)
    ZONE_Y0, ZONE_Y1 = 0.10, 0.935
    # Marges réservées à l'appareil de cotation. Elles sont ASYMÉTRIQUES parce
    # que la cotation l'est :
    #  - la pile de voies verticales se pose toujours du même côté de l'écran
    #    (caméra fixe azim=225° + arête de référence FRONT_FACE_DIM_EDGE :
    #    voir `place_dims_right`), donc réserver la même largeur des deux
    #    côtés gaspillerait un quart de la zone et rapetissait le dessin ;
    #  - les cotes L et P sont posées sur les arêtes HAUTES, il faut donc plus
    #    de dégagement au-dessus qu'en dessous.
    # Le dessin est ainsi décentré dans la zone… pour que l'ENSEMBLE coté,
    # lui, tombe bien au centre de la moitié droite.
    MARGIN_LANES, MARGIN_PLAIN = 0.300, 0.075
    MARGIN_TOP, MARGIN_BOTTOM = 0.150, 0.055
    fit_box = (ZONE_X0 + MARGIN_LANES, ZONE_Y0 + MARGIN_BOTTOM,
               (ZONE_X1 - ZONE_X0) - MARGIN_LANES - MARGIN_PLAIN,
               (ZONE_Y1 - ZONE_Y0) - MARGIN_TOP - MARGIN_BOTTOM)
    # La toile matricielle déborde légèrement la zone de dessin : le raster ne
    # doit jamais rogner la silhouette qu'il vient d'y inscrire.
    _pad = 0.03
    rect = (fit_box[0] - _pad, fit_box[1] - _pad,
            fit_box[2] + 2 * _pad, fit_box[3] + 2 * _pad)
    # Garde-fou : la toile doit tenir dans la feuille. Le dessin est rendu en
    # `clip_on=False` (voir `_render_cabinet`) — donc plus rien ne le tronque,
    # mais rien ne l'empêcherait non plus de sortir de la page si les marges
    # ci-dessus étaient mal réglées. On le vérifie explicitement plutôt que de
    # découvrir la coupe sur un PDF livré.
    _rx1_fig = _ax_x0 + (rect[0] + rect[2]) * _ax_span_x
    _rx0_fig = _ax_x0 + rect[0] * _ax_span_x
    if not (0.0 <= _rx0_fig and _rx1_fig <= 1.0
            and 0.0 <= rect[1] and rect[1] + rect[3] <= 1.0):
        raise ValueError(
            f"Zone de dessin hors feuille : x=[{_rx0_fig:.3f}, {_rx1_fig:.3f}], "
            f"y=[{rect[1]:.3f}, {rect[1] + rect[3]:.3f}] (fraction figure) — "
            "revoir MARGIN_LANES/MARGIN_PLAIN/MARGIN_TOP/MARGIN_BOTTOM.")
    ax_img = fig.add_axes([0, 0, 1, 1])
    ax_img.set_xlim(0, 1)
    ax_img.set_ylim(0, 1)
    ax_img.axis("off")
    ax_img.set_facecolor("none")
    view_azim = 225
    view_elev = 22
    raster = _render_cabinet(ax_img, fig, rect, cab, origin=(0, 0, 0),
                    show_all_dims=True, azim=view_azim, elev=view_elev,
                    fit_box=fit_box)

    # ── Cotations 2D overlay ─────────────────────────────────────────────────
    # fig.canvas.draw() : rend `fig.canvas.get_renderer()` disponible (mesure
    # des étiquettes) ET force `apply_aspect`, de sorte que `ax_img.get_position()`
    # renvoie la position DÉFINITIVE de l'axe (rétrécie au carré par l'aspect
    # 'equal' d'imshow). _draw_dims_overlay s'en sert pour convertir ses
    # longueurs en points ; le tracé lui-même passe par `ax_img.transData`,
    # évalué au moment du rendu, donc juste quoi qu'il arrive.
    fig.canvas.draw()
    _draw_dims_overlay(fig, cab, origin=(0, 0, 0), raster=raster,
                       azim=view_azim, elev=view_elev)

    # ── Barre orange KOBO ────────────────────────────────────────────────────
    _draw_kobo_bar(fig, logo_img)

    pdf.savefig(fig, dpi=150)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée PDF
# ─────────────────────────────────────────────────────────────────────────────

def generate_3d_views_pdf(
    scene_cabinets: List[Dict],
    project_name: str = "Meuble",
    client: str = "",
    date_str: str = "",
) -> bytes:
    """Génère le PDF multi-vues cotées style KOBO. Retourne les bytes du PDF."""
    if not scene_cabinets:
        return b""

    logo_img = _load_kobo_logo()

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _cover_page(pdf, project_name, client, date_str, logo_img)
        for i, cab in enumerate(scene_cabinets):
            _cabinet_page(pdf, cab, i, project_name, client, logo_img)
        d = pdf.infodict()
        d["Title"]   = f"Vues 3D cotées – {project_name}"
        d["Author"]  = "KoboMeuble"
        d["Subject"] = "Vues 3D avec cotations pour fabrication"

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Script Ruby SketchUp (cotations natives)
# ─────────────────────────────────────────────────────────────────────────────

def generate_sketchup_ruby_script(scene_cabinets: List[Dict]) -> bytes:
    """Génère un script Ruby (.rb) à coller dans la console Ruby de SketchUp."""
    from machining_logic import calculate_origins_recursively
    origins = calculate_origins_recursively(scene_cabinets, unit_factor=1.0)

    lines = [
        "# ──────────────────────────────────────────────────────────",
        "# KoboMeuble – Script de cotation automatique pour SketchUp",
        "# Coller dans : Window > Ruby Console  puis Entrée",
        "# ──────────────────────────────────────────────────────────",
        "",
        "model = Sketchup.active_model",
        "ents  = model.entities",
        "model.start_operation('Cotations KoboMeuble', true)",
        "",
        "offset_mm = 60",
        "off = offset_mm.mm",
        "",
    ]

    for i, cab in enumerate(scene_cabinets):
        dims   = cab["dims"]
        L      = float(dims["L_raw"])
        W      = float(dims["W_raw"])
        H      = float(dims["H_raw"])
        tl     = float(dims["t_lr_raw"])
        tb     = float(dims["t_fb_raw"])
        tt     = float(dims["t_tb_raw"])
        ox, oy, oz = origins[i]
        label  = cab.get("name", f"C{i+1}")
        shelves_sorted = sorted(cab.get("shelves", []), key=lambda s: float(s["height"]))

        lines += [
            f"# ── Caisson {label} ──",
            f"ox = {ox:.2f}.mm ; oy = {oy:.2f}.mm ; oz = {oz:.2f}.mm",
            f"L  = {L:.2f}.mm  ; W  = {W:.2f}.mm  ; H  = {H:.2f}.mm",
            "",
            "# Largeur",
            "p1 = Geom::Point3d.new(ox, oy - off, oz + H + off * 0.5)",
            "p2 = Geom::Point3d.new(ox + L, oy - off, oz + H + off * 0.5)",
            "ents.add_dimension_linear(p1, p2, Geom::Vector3d.new(0, 0, 1), off)",
            "",
            "# Hauteur",
            "p1 = Geom::Point3d.new(ox + L + off * 0.5, oy, oz)",
            "p2 = Geom::Point3d.new(ox + L + off * 0.5, oy, oz + H)",
            "ents.add_dimension_linear(p1, p2, Geom::Vector3d.new(1, 0, 0), off)",
            "",
            "# Profondeur",
            "p1 = Geom::Point3d.new(ox - off * 0.5, oy, oz + H + off * 0.5)",
            "p2 = Geom::Point3d.new(ox - off * 0.5, oy + W, oz + H + off * 0.5)",
            "ents.add_dimension_linear(p1, p2, Geom::Vector3d.new(0, 0, 1), off)",
            "",
        ]

        if shelves_sorted:
            lines.append("# Hauteurs entre étagères")
            prev_z = oz + tt
            zones = []
            for s in shelves_sorted:
                sh_z  = oz + tt + float(s["height"])
                sh_th = float(s.get("thickness", 19.0))
                zones.append((prev_z, sh_z))
                prev_z = sh_z + sh_th
            zones.append((prev_z, oz + H - tt))
            x_dim = ox + L + 70
            for j, (z_bot, z_top) in enumerate(zones):
                gap = z_top - z_bot
                if gap > 5:
                    lines += [
                        f"p1 = Geom::Point3d.new({x_dim:.2f}.mm, {oy:.2f}.mm, {z_bot:.2f}.mm)",
                        f"p2 = Geom::Point3d.new({x_dim:.2f}.mm, {oy:.2f}.mm, {z_top:.2f}.mm)",
                        "ents.add_dimension_linear(p1, p2, Geom::Vector3d.new(1, 0, 0), off)",
                    ]
            lines.append("")

    lines += [
        "model.commit_operation",
        "puts \"Cotations KoboMeuble posées avec succès !\"",
    ]

    return "\n".join(lines).encode("utf-8")
