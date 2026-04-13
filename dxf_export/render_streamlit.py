"""Conversion Scene <-> Plotly pour garantir une source de vérité unique."""

from __future__ import annotations

import math
import re
from typing import List, Tuple

from .scene import Arc, Circle, Dimension, DiameterDimension, HatchSimple, Line, Leader, Polyline, Scene, Text, compute_scene_bbox


def _equilateral_triangle_from_apex(apex, side, orientation):
    x, y = float(apex[0]), float(apex[1])
    s = float(side)
    h = s * (3.0 ** 0.5) / 2.0

    if orientation == "down":
        # Sommet au bord inférieur, triangle vers le haut (intérieur)
        return [
            (x, y),
            (x - s / 2.0, y + h),
            (x + s / 2.0, y + h),
        ]
    if orientation == "up":
        # Sommet au bord supérieur, triangle vers le bas (intérieur)
        return [
            (x, y),
            (x - s / 2.0, y - h),
            (x + s / 2.0, y - h),
        ]
    if orientation == "left":
        # Sommet au bord gauche, triangle vers la droite (intérieur)
        return [
            (x, y),
            (x + h, y - s / 2.0),
            (x + h, y + s / 2.0),
        ]
    if orientation == "right":
        # Sommet au bord droit, triangle vers la gauche (intérieur)
        return [
            (x, y),
            (x - h, y - s / 2.0),
            (x - h, y + s / 2.0),
        ]

    return [
        (x, y),
        (x - s / 2.0, y + h),
        (x + s / 2.0, y + h),
    ]


def _split_polyline_by_none(xs, ys):
    segments = []
    current = []
    for x, y in zip(xs, ys):
        if x is None or y is None:
            if len(current) >= 2:
                segments.append(current)
            current = []
            continue
        current.append((float(x), float(y)))
    if len(current) >= 2:
        segments.append(current)
    return segments


def _parse_plotly_path_subpaths(path: str) -> List[List[Tuple[float, float]]]:
    tokens = re.findall(r"[MLHVZmlhvz]|-?\d+(?:\.\d+)?", path or "")
    subpaths: List[List[Tuple[float, float]]] = []

    idx = 0
    current_cmd = None
    current_path: List[Tuple[float, float]] = []
    cx = cy = None
    start_x = start_y = None

    def _flush_path():
        nonlocal current_path
        if len(current_path) >= 2:
            subpaths.append(current_path)
        current_path = []

    while idx < len(tokens):
        tok = tokens[idx]
        if re.fullmatch(r"[MLHVZmlhvz]", tok):
            cmd = tok.upper()
            current_cmd = cmd
            idx += 1

            if cmd == "M":
                if current_path:
                    _flush_path()
            elif cmd == "Z":
                if current_path and start_x is not None and start_y is not None:
                    if current_path[-1] != (start_x, start_y):
                        current_path.append((start_x, start_y))
                _flush_path()
                cx = cy = start_x = start_y = None
            continue

        if current_cmd in ("M", "L") and idx + 1 < len(tokens):
            x = float(tokens[idx])
            y = float(tokens[idx + 1])
            idx += 2
            if current_cmd == "M":
                start_x, start_y = x, y
            cx, cy = x, y
            current_path.append((x, y))
            if current_cmd == "M":
                current_cmd = "L"
            continue

        if current_cmd == "H" and idx < len(tokens):
            if cy is None:
                idx += 1
                continue
            x = float(tokens[idx])
            idx += 1
            cx = x
            current_path.append((cx, cy))
            continue

        if current_cmd == "V" and idx < len(tokens):
            if cx is None:
                idx += 1
                continue
            y = float(tokens[idx])
            idx += 1
            cy = y
            current_path.append((cx, cy))
            continue

        idx += 1

    if current_path:
        _flush_path()

    return subpaths


def _is_triangle_subpath(points: List[Tuple[float, float]], tol: float = 1e-3) -> bool:
    """Retourne True si le sous-chemin correspond a un triangle ferme."""
    if len(points) < 3:
        return False

    uniq = []
    for x, y in points:
        p = (float(x), float(y))
        if not any(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol for q in uniq):
            uniq.append(p)

    return len(uniq) == 3


def _is_zone_helper_rect(shp) -> bool:
    """Rectangles de repérage UI (blanc + transparent) à exclure du DXF."""
    if (getattr(shp, "type", "") or "").lower() != "rect":
        return False

    fillcolor = str(getattr(shp, "fillcolor", "") or "").lower().replace(" ", "")
    line_obj = getattr(shp, "line", None)
    line_color = str(getattr(line_obj, "color", "") or "").lower().replace(" ", "")

    transparent_fill = fillcolor in {"rgba(255,255,255,0.0)", "rgba(255,255,255,0)", "rgba(0,0,0,0)", "transparent", "none", ""}
    white_line = line_color.startswith("rgba(255,255,255,") or line_color.startswith("rgb(255,255,255")
    return transparent_fill and white_line


def _is_dimension_shape_line(shp, dims, tol=0.5, tick_len=6.0):
    if not dims:
        return False
    shape_type = (getattr(shp, "type", "") or "").lower()
    if shape_type != "line":
        return False

    try:
        x0, y0 = float(shp.x0), float(shp.y0)
        x1, y1 = float(shp.x1), float(shp.y1)
    except Exception:
        return False

    for dim in dims:
        axis = dim.get("axis")
        p1 = dim.get("p1") or (0.0, 0.0)
        p2 = dim.get("p2") or (0.0, 0.0)
        dim_line = dim.get("dim_line")

        try:
            p1x, p1y = float(p1[0]), float(p1[1])
            p2x, p2y = float(p2[0]), float(p2[1])
            dim_line = float(dim_line)
        except Exception:
            continue

        # Dimension line
        if axis == "x":
            if abs(y0 - dim_line) <= tol and abs(y1 - dim_line) <= tol:
                if min(p1x, p2x) - tol <= min(x0, x1) <= max(p1x, p2x) + tol:
                    return True
            # Extension lines
            if abs(x0 - x1) <= tol:
                if abs(x0 - p1x) <= tol or abs(x0 - p2x) <= tol:
                    if abs(y0 - dim_line) <= tol or abs(y1 - dim_line) <= tol:
                        return True
            # Tick marks
            if abs(x0 - x1) <= tol:
                if min(abs(x0 - p1x), abs(x0 - p2x)) <= tol:
                    if abs((y0 + y1) / 2.0 - dim_line) <= tol:
                        if abs(y0 - y1) <= tick_len * 2:
                            return True
        elif axis == "y":
            if abs(x0 - dim_line) <= tol and abs(x1 - dim_line) <= tol:
                if min(p1y, p2y) - tol <= min(y0, y1) <= max(p1y, p2y) + tol:
                    return True
            if abs(y0 - y1) <= tol:
                if abs(y0 - p1y) <= tol or abs(y0 - p2y) <= tol:
                    if abs(x0 - dim_line) <= tol or abs(x1 - dim_line) <= tol:
                        return True
            if abs(y0 - y1) <= tol:
                if min(abs(y0 - p1y), abs(y0 - p2y)) <= tol:
                    if abs((x0 + x1) / 2.0 - dim_line) <= tol:
                        if abs(x0 - x1) <= tick_len * 2:
                            return True
    return False


def _rect_from_polyline(points: List[Tuple[float, float]], tol: float = 1e-6):
    if len(points) < 4:
        return None
    uniq = []
    for p in points:
        if not any(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol for q in uniq):
            uniq.append((float(p[0]), float(p[1])))
    if len(uniq) != 4:
        return None
    xs = sorted({round(p[0], 6) for p in uniq})
    ys = sorted({round(p[1], 6) for p in uniq})
    if len(xs) != 2 or len(ys) != 2:
        return None
    min_x, max_x = float(xs[0]), float(xs[1])
    min_y, max_y = float(ys[0]), float(ys[1])
    return (min_x, min_y, max_x, max_y)


def _line_exists(scene: Scene, x0: float, y0: float, x1: float, y1: float, tol: float = 0.6) -> bool:
    for ent in scene.entities:
        if not isinstance(ent, Line):
            continue
        sx, sy = float(ent.start[0]), float(ent.start[1])
        ex, ey = float(ent.end[0]), float(ent.end[1])
        same_dir = abs(sx - x0) <= tol and abs(sy - y0) <= tol and abs(ex - x1) <= tol and abs(ey - y1) <= tol
        rev_dir = abs(sx - x1) <= tol and abs(sy - y1) <= tol and abs(ex - x0) <= tol and abs(ey - y0) <= tol
        if same_dir or rev_dir:
            return True
    return False


def _ensure_legrabox_bottom_construction_lines(scene: Scene):
    # Cherche le rectangle principal du panneau et garantit les traits de construction.
    # Les tranches sont des Scatter fill="toself" -> Polyline closed=False (premier pt == dernier pt)
    # Le panneau principal est un shape rect -> Polyline closed=True
    # => on accepte les deux cas.
    rects = []
    best = None
    best_area = -1.0
    for ent in scene.entities:
        if not isinstance(ent, Polyline):
            continue
        pts = ent.points
        # Accepter closed=True OU closed=False avec premier ≈ dernier point (boucle fermée)
        if not ent.closed:
            if len(pts) < 4:
                continue
            if abs(pts[0][0] - pts[-1][0]) > 1e-3 or abs(pts[0][1] - pts[-1][1]) > 1e-3:
                continue
        rect = _rect_from_polyline(pts)
        if rect is None:
            continue
        min_x, min_y, max_x, max_y = rect
        w = max_x - min_x
        h = max_y - min_y
        area = max(0.0, w * h)
        rects.append((min_x, min_y, max_x, max_y, w, h, area))
        if area > best_area:
            best_area = area
            best = (min_x, min_y, max_x, max_y)

    if best is None:
        return

    min_x, min_y, max_x, max_y = best
    height = max_y - min_y
    width = max_x - min_x
    if width < 90.0 or height < 30.0:
        return

    # Traits historiques (conservés): à 38 mm de chaque bord du panneau central.
    y_bot_legacy = min_y + 38.0
    y_top_legacy = max_y - 38.0

    if not _line_exists(scene, min_x, y_bot_legacy, max_x, y_bot_legacy):
        scene.entities.append(Line(layer="GEOM", start=(min_x, y_bot_legacy), end=(max_x, y_bot_legacy)))
    if not _line_exists(scene, min_x, y_top_legacy, max_x, y_top_legacy):
        scene.entities.append(Line(layer="GEOM", start=(min_x, y_top_legacy), end=(max_x, y_top_legacy)))

    # Cotations demandées: 38 mm entre chaque bord du panneau central et son trait à 38.
    p1_bot_38 = (max_x, min_y)
    p2_bot_38 = (max_x, y_bot_legacy)
    if not _dimension_exists(scene, "y", p1_bot_38, p2_bot_38):
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=p1_bot_38,
                p2=p2_bot_38,
                offset=18.0,
                text="38",
                axis="y",
                side="left",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )

    p1_top_38 = (max_x, max_y)
    p2_top_38 = (max_x, y_top_legacy)
    if not _dimension_exists(scene, "y", p1_top_38, p2_top_38):
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=p1_top_38,
                p2=p2_top_38,
                offset=18.0,
                text="38",
                axis="y",
                side="left",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )

    # Nouveaux traits: au centre des rectangles de tranche bas/haut (épaisseur 16 => milieu à 8).
    tol = 1.0

    def _x_overlap_ratio(a0, a1, b0, b1):
        inter = max(0.0, min(a1, b1) - max(a0, b0))
        base = max(1.0, min(a1 - a0, b1 - b0))
        return inter / base

    bottom_candidates = []
    top_candidates = []
    for rx0, ry0, rx1, ry1, rw, rh, _area in rects:
        if rw < max(40.0, width * 0.7):
            continue
        if rh < 6.0 or rh > 40.0:
            continue
        if _x_overlap_ratio(min_x, max_x, rx0, rx1) < 0.8:
            continue

        # Rectangle de tranche en dessous du panneau central
        if ry1 <= (min_y + tol):
            bottom_candidates.append((rx0, ry0, rx1, ry1))
        # Rectangle de tranche au-dessus du panneau central
        if ry0 >= (max_y - tol):
            top_candidates.append((rx0, ry0, rx1, ry1))

    bottom_strip = max(bottom_candidates, key=lambda r: r[3], default=None)  # le plus proche du panneau
    top_strip = min(top_candidates, key=lambda r: r[1], default=None)         # le plus proche du panneau

    if bottom_strip is not None:
        bx0, by0, bx1, by1 = bottom_strip
        y_mid_bottom_strip = (by0 + by1) / 2.0
        if not _line_exists(scene, bx0, y_mid_bottom_strip, bx1, y_mid_bottom_strip):
            scene.entities.append(Line(layer="GEOM", start=(bx0, y_mid_bottom_strip), end=(bx1, y_mid_bottom_strip)))

        # Cotation demandée: 8 mm entre le bord interne de tranche et le trait médian.
        p1 = (bx0, by1)  # bord tranche côté panneau
        p2 = (bx0, y_mid_bottom_strip)
        if not _dimension_exists(scene, "y", p1, p2):
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=p1,
                    p2=p2,
                    offset=18.0,
                    text="8",
                    axis="y",
                    side="left",
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )

    if top_strip is not None:
        tx0, ty0, tx1, ty1 = top_strip
        y_mid_top_strip = (ty0 + ty1) / 2.0
        if not _line_exists(scene, tx0, y_mid_top_strip, tx1, y_mid_top_strip):
            scene.entities.append(Line(layer="GEOM", start=(tx0, y_mid_top_strip), end=(tx1, y_mid_top_strip)))

        # Cotation demandée: 8 mm entre le bord interne de tranche et le trait médian.
        p1 = (tx0, ty0)  # bord tranche côté panneau
        p2 = (tx0, y_mid_top_strip)
        if not _dimension_exists(scene, "y", p1, p2):
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=p1,
                    p2=p2,
                    offset=18.0,
                    text="8",
                    axis="y",
                    side="left",
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )



def _reconstruct_orphan_dimensions(fig, dxf_dims, dim_labels):
    """Reconstruit les entités Dimension manquantes depuis annotations Plotly non liées à dxf_dims.

    Certaines cotes sont stockées uniquement comme shapes Plotly + annotations,
    sans entrée dans dxf_dims.
    """
    known_labels = set()
    for lx, ly, lt in dim_labels:
        known_labels.add((round(lx, 1), round(ly, 1), lt))

    shapes = list(fig.layout.shapes or [])
    horizontal_lines = []
    vertical_lines = []
    for s in shapes:
        if (getattr(s, "type", "") or "").lower() != "line":
            continue
        try:
            x0, y0, x1, y1 = float(s.x0), float(s.y0), float(s.x1), float(s.y1)
        except Exception:
            continue
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        if dx >= dy and dx >= 20.0:
            horizontal_lines.append((x0, y0, x1, y1))
        elif dy > dx and dy >= 20.0:
            vertical_lines.append((x0, y0, x1, y1))

    reconstructed = []
    for ann in fig.layout.annotations or []:
        ann_text = str(getattr(ann, "text", "") or "").strip()
        if not ann_text:
            continue
        if _is_date_like_text(ann_text):
            continue

        clean = ann_text.replace("%%c", "").replace("Ø", "").replace("⌀", "").strip()
        if not re.fullmatch(r"[-+]?\d+(?:[\.,]\d+)?", clean):
            continue

        try:
            ann_x = float(getattr(ann, "x", 0.0))
            ann_y = float(getattr(ann, "y", 0.0))
            textangle = float(getattr(ann, "textangle", 0.0) or 0.0)
        except Exception:
            continue

        key = (round(ann_x, 1), round(ann_y, 1), ann_text)
        if key in known_labels:
            continue

        if abs(textangle) < 10:
            best_line = None
            best_dist = 1e9
            for (lx0, ly0, lx1, ly1) in horizontal_lines:
                ly = (ly0 + ly1) / 2.0
                if abs(ly - ann_y) > 120.0:
                    continue
                lx_min, lx_max = min(lx0, lx1), max(lx0, lx1)
                if not (lx_min - 10 <= ann_x <= lx_max + 10):
                    continue
                dist = abs(ly - ann_y)
                if dist < best_dist:
                    best_dist = dist
                    best_line = (lx0, ly0, lx1, ly1)
            if best_line:
                lx0, ly0, lx1, ly1 = best_line
                ly = (ly0 + ly1) / 2.0
                side = "top" if ann_y > ly else "bottom"
                offset = max(abs(ann_y - ly), 8.0)
                reconstructed.append({
                    "axis": "x",
                    "p1": (min(lx0, lx1), ly),
                    "p2": (max(lx0, lx1), ly),
                    "offset": offset,
                    "side": side,
                    "text": ann_text,
                    "label": (ann_x, ann_y),
                })

        elif abs(textangle) > 80:
            best_line = None
            best_dist = 1e9
            for (lx0, ly0, lx1, ly1) in vertical_lines:
                lx = (lx0 + lx1) / 2.0
                if abs(lx - ann_x) > 120.0:
                    continue
                ly_min, ly_max = min(ly0, ly1), max(ly0, ly1)
                if not (ly_min - 10 <= ann_y <= ly_max + 10):
                    continue
                dist = abs(lx - ann_x)
                if dist < best_dist:
                    best_dist = dist
                    best_line = (lx0, ly0, lx1, ly1)
            if best_line:
                lx0, ly0, lx1, ly1 = best_line
                lx = (lx0 + lx1) / 2.0
                side = "right" if ann_x > lx else "left"
                offset = max(abs(ann_x - lx), 8.0)
                reconstructed.append({
                    "axis": "y",
                    "p1": (lx, min(ly0, ly1)),
                    "p2": (lx, max(ly0, ly1)),
                    "offset": offset,
                    "side": side,
                    "text": ann_text,
                    "label": (ann_x, ann_y),
                })

    return reconstructed


def _find_main_panel_rect(scene: Scene):
    best = None
    best_area = -1.0
    for ent in scene.entities:
        if isinstance(ent, Polyline) and ent.closed:
            rect = _rect_from_polyline(ent.points)
            if rect is None:
                continue
            min_x, min_y, max_x, max_y = rect
            area = max(0.0, (max_x - min_x) * (max_y - min_y))
            if area > best_area:
                best_area = area
                best = rect
    return best


def _normalize_sheet_name(name: str) -> str:
    return (
        str(name or "")
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
    )


def _point_inside_rect(x: float, y: float, rect, margin: float = 0.8) -> bool:
    if rect is None:
        return True
    min_x, min_y, max_x, max_y = rect
    return (
        float(min_x) - margin <= float(x) <= float(max_x) + margin
        and float(min_y) - margin <= float(y) <= float(max_y) + margin
    )


def _get_hole_leader_label(circle: Circle, figure_name: str, panel_rect) -> str | None:
    r = abs(float(circle.radius))
    if r < 1.0:
        return None

    cx, cy = float(circle.center[0]), float(circle.center[1])
    diam = r * 2.0
    name_norm = _normalize_sheet_name(figure_name)
    is_door_sheet = ("porte" in name_norm) or ("door" in name_norm)
    is_montant_sheet = "montant" in name_norm
    is_tranche_hole = not _point_inside_rect(cx, cy, panel_rect, margin=1.0)

    if is_tranche_hole:
        return "%%C8/22"

    if is_door_sheet:
        if abs(diam - 35.0) <= 1.5:
            return "%%C35/13"
        return "%%C8/13"

    if is_montant_sheet:
        if abs(diam - 5.0) <= 1.5:
            return "%%C5/11"
        if abs(diam - 3.0) <= 1.0:
            return "%%C3/19"
        return "%%C8/10"

    if abs(diam - 35.0) <= 1.5:
        return "%%C35/13"
    if abs(diam - 5.0) <= 1.5:
        return "%%C5/11"
    if abs(diam - 3.0) <= 1.0:
        return "%%C3/19"
    if abs(diam - 8.0) <= 1.5:
        return "%%C8/10"
    return f"%%C{_format_dim_value(diam)}"


def _format_dim_value(value: float) -> str:
    if abs(value - round(value)) <= 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def _is_date_like_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", value):
        return True
    if re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", value):
        return True
    return False


def _dimension_exists(scene: Scene, axis: str, p1: Tuple[float, float], p2: Tuple[float, float], tol: float = 0.8) -> bool:
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            continue
        if (ent.axis or "auto") != axis:
            continue
        ex1, ey1 = float(ent.p1[0]), float(ent.p1[1])
        ex2, ey2 = float(ent.p2[0]), float(ent.p2[1])
        same = abs(ex1 - x1) <= tol and abs(ey1 - y1) <= tol and abs(ex2 - x2) <= tol and abs(ey2 - y2) <= tol
        rev = abs(ex1 - x2) <= tol and abs(ey1 - y2) <= tol and abs(ex2 - x1) <= tol and abs(ey2 - y1) <= tol
        if same or rev:
            return True
    return False


def _ensure_bottom_x_dimensions_for_holes(scene: Scene, figure_name: str = ""):
    """Ajoute les cotes X des points (trous) en bas de la figure d'usinage.
    
    Évite les doublons pour montants et panneau arrière (qui ont déjà des dimensions de Plotly).
    """
    # Ne pas ajouter de dimensions pour montants ou panneau arrière (ils peuvent avoir déjà des dimensions)
    name_norm = str(figure_name or "").lower().replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    if any(skip in name_norm for skip in ["montant gauche", "montant droit", "panneau arriere"]):
        return
    
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect
    width = max_x - min_x
    height = max_y - min_y
    if width < 40.0 or height < 40.0:
        return

    hole_xs = []
    for ent in scene.entities:
        if not isinstance(ent, Circle):
            continue
        cx, cy = float(ent.center[0]), float(ent.center[1])
        r = abs(float(ent.radius))
        if r > 12.0:
            continue
        # Uniquement trous à l'intérieur du panneau central (exclut les tranches).
        if (min_y + 0.5) < cy < (max_y - 0.5) and (min_x + 0.5) < cx < (max_x - 0.5):
            hole_xs.append(cx)

    if not hole_xs:
        return

    unique_xs = sorted({round(x, 3) for x in hole_xs})

    base_offset = 28.0

    # Créer une chaîne complète du bord gauche au bord droit, incluant tous les trous.
    # Structure: [bord gauche] → [trou1] → [trou2] → ... → [bord droit]
    segment_xs = [min_x] + unique_xs + [max_x]

    # Cotation en chaîne: distance entre chaque segment consécutif.
    for x_prev, x_curr in zip(segment_xs[:-1], segment_xs[1:]):
        p1 = (float(x_prev), min_y)
        p2 = (float(x_curr), min_y)
        if _dimension_exists(scene, "x", p1, p2):
            continue
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=p1,
                p2=p2,
                offset=base_offset,
                text=_format_dim_value(abs(float(x_curr) - float(x_prev))),
                axis="x",
                side="bottom",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )


def _normalize_bottom_x_dimensions(scene: Scene, common_offset: float = 28.0):
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            continue
        axis = (ent.axis or "auto").lower()
        side = (ent.side or "").lower()
        if axis == "x" and side == "bottom":
            ent.offset = float(common_offset)


def _ensure_global_outer_dimensions(scene: Scene):
    """Force les 2 cotes globales (L/H) a l'exterieur du panneau principal."""
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect
    width = max_x - min_x
    height = max_y - min_y
    if width < 20.0 or height < 20.0:
        return

    # Supprime les cotes globales existantes pour les reposer proprement a l'exterieur.
    filtered = []
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            filtered.append(ent)
            continue

        axis = (ent.axis or "auto").lower()
        ex1, ey1 = float(ent.p1[0]), float(ent.p1[1])
        ex2, ey2 = float(ent.p2[0]), float(ent.p2[1])

        is_full_width = axis == "x" and abs(min(ex1, ex2) - min_x) <= 1.0 and abs(max(ex1, ex2) - max_x) <= 1.0
        is_full_height = axis == "y" and abs(min(ey1, ey2) - min_y) <= 1.0 and abs(max(ey1, ey2) - max_y) <= 1.0

        if is_full_width or is_full_height:
            continue
        filtered.append(ent)
    scene.entities = filtered

    # Largeur generale: en haut, hors panneau.
    scene.entities.append(
        Dimension(
            layer="DIM",
            category="dimension",
            p1=(min_x, max_y),
            p2=(max_x, max_y),
            offset=26.0,
            text=_format_dim_value(width),
            axis="x",
            side="top",
            dimstyle="POTECH_DIM",
            text_height=10.0,
        )
    )

    # Hauteur generale: a gauche, hors panneau.
    scene.entities.append(
        Dimension(
            layer="DIM",
            category="dimension",
            p1=(min_x, min_y),
            p2=(min_x, max_y),
            offset=26.0,
            text=_format_dim_value(height),
            axis="y",
            side="left",
            dimstyle="POTECH_DIM",
            text_height=10.0,
        )
    )


def _add_xy_chain_dimensions_for_holes(scene: Scene):
    """Ajoute des cotes X/Y en chaine pour tous les trous de porte."""
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect

    hole_pts = []
    for ent in scene.entities:
        if not isinstance(ent, Circle):
            continue
        cx, cy = float(ent.center[0]), float(ent.center[1])
        if (min_x + 0.2) <= cx <= (max_x - 0.2) and (min_y + 0.2) <= cy <= (max_y - 0.2):
            hole_pts.append((cx, cy))

    if not hole_pts:
        return

    xs = sorted({round(x, 3) for x, _ in hole_pts})
    ys = sorted({round(y, 3) for _, y in hole_pts})

    # Determiner de quel cote se trouvent les charnieres.
    avg_x = sum(x for x, _ in hole_pts) / max(1, len(hole_pts))
    hinge_on_left = avg_x <= ((min_x + max_x) / 2.0)

    # X en chaine : ligne de cote positionnee EN DESSOUS du panneau (hors panneau, cote bas).
    # Les ancres sont sur le bord inferieur du panneau, offset vers le bas -> pas de superposition avec les trous.
    x_chain = [min_x] + xs + [max_x]
    for a, b in zip(x_chain[:-1], x_chain[1:]):
        if abs(float(b) - float(a)) < 0.05:
            continue
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=(float(a), min_y),
                p2=(float(b), min_y),
                offset=16.0,
                text=_format_dim_value(abs(float(b) - float(a))),
                axis="x",
                side="bottom",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )

    # Y en chaine : ligne de cote positionnee A L'EXTERIEUR du panneau, du cote des charnieres.
    # Les ancres sont sur le bord lateral du panneau (cote charnieres), offset vers l'exterieur.
    y_chain = [min_y] + ys + [max_y]
    y_ref_x = min_x if hinge_on_left else max_x
    y_side = "left" if hinge_on_left else "right"
    for a, b in zip(y_chain[:-1], y_chain[1:]):
        if abs(float(b) - float(a)) < 0.05:
            continue
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=(y_ref_x, float(a)),
                p2=(y_ref_x, float(b)),
                offset=16.0,
                text=_format_dim_value(abs(float(b) - float(a))),
                axis="y",
                side=y_side,
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )


def _ensure_rear_panel_divider_dowel_dimensions(scene: Scene):
    """Ajoute les cotes de chaine des tourillons de montants secondaires sur panneau arriere.

    - Cotes entre trous successifs
    - Cotes entre trous exterieurs et rebords du panneau
    """
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect
    width = max_x - min_x
    height = max_y - min_y
    if width < 40.0 or height < 40.0:
        return

    dowel_pts = []
    for ent in scene.entities:
        if not isinstance(ent, Circle):
            continue
        cx, cy = float(ent.center[0]), float(ent.center[1])
        r = abs(float(ent.radius))
        # Tourillon du panneau arriere (⌀8/22) => rayon proche de 4.
        if r < 3.6 or r > 5.2:
            continue
        if (min_x + 0.5) <= cx <= (max_x - 0.5) and (min_y + 0.5) <= cy <= (max_y - 0.5):
            dowel_pts.append((cx, cy))

    if not dowel_pts:
        return

    xs = sorted({round(p[0], 3) for p in dowel_pts})
    ys = sorted({round(p[1], 3) for p in dowel_pts})

    # Chaine X: bord gauche -> trous -> bord droit
    if len(xs) >= 1:
        x_chain = [min_x] + xs + [max_x]
        for a, b in zip(x_chain[:-1], x_chain[1:]):
            if abs(float(b) - float(a)) < 0.05:
                continue
            p1 = (float(a), min_y)
            p2 = (float(b), min_y)
            if _dimension_exists(scene, "x", p1, p2):
                continue
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=p1,
                    p2=p2,
                    offset=18.0,
                    text=_format_dim_value(abs(float(b) - float(a))),
                    axis="x",
                    side="bottom",
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )

    # Chaine Y: bord bas -> trous -> bord haut
    if len(ys) >= 2:
        y_chain = [min_y] + ys + [max_y]
        for a, b in zip(y_chain[:-1], y_chain[1:]):
            if abs(float(b) - float(a)) < 0.05:
                continue
            p1 = (max_x, float(a))
            p2 = (max_x, float(b))
            if _dimension_exists(scene, "y", p1, p2):
                continue
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=p1,
                    p2=p2,
                    offset=18.0,
                    text=_format_dim_value(abs(float(b) - float(a))),
                    axis="y",
                    side="right",
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )


def _normalize_montant_consecutive_hole_dimensions(scene: Scene):
    """Pour les montants, cote uniquement les espacements entre trous consecutifs.

    Regle metier: ignorer la nature du trou (vis/tourillon) et produire une chaine
    unique de cotes d'espacement entre X consecutifs.
    """
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect

    # Rassembler toutes les positions X de trous de face a l'interieur du panneau.
    xs = []
    for ent in scene.entities:
        if not isinstance(ent, Circle):
            continue
        cx, cy = float(ent.center[0]), float(ent.center[1])
        r = abs(float(ent.radius))
        if r > 12.0:
            continue
        if (min_x + 0.2) <= cx <= (max_x - 0.2) and (min_y + 0.2) <= cy <= (max_y - 0.2):
            xs.append(cx)

    unique_xs = sorted({round(x, 3) for x in xs})
    if len(unique_xs) < 2:
        return

    # Supprimer les cotes X detail existantes dans le panneau (souvent separees par type de trou).
    kept = []
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            kept.append(ent)
            continue

        axis = (ent.axis or "auto").lower()
        if axis != "x":
            kept.append(ent)
            continue

        ex1, ey1 = float(ent.p1[0]), float(ent.p1[1])
        ex2, ey2 = float(ent.p2[0]), float(ent.p2[1])
        x_lo, x_hi = min(ex1, ex2), max(ex1, ex2)
        span = abs(ex2 - ex1)
        full_width = abs(x_lo - min_x) <= 1.0 and abs(x_hi - max_x) <= 1.0

        # Conserver les cotes globales pleine largeur, supprimer les details internes.
        if full_width:
            kept.append(ent)
            continue

        y_on_panel = (min_y - 1.5) <= ey1 <= (max_y + 1.5) and (min_y - 1.5) <= ey2 <= (max_y + 1.5)
        inside_x = x_lo >= (min_x - 1.5) and x_hi <= (max_x + 1.5)
        if y_on_panel and inside_x and span > 0.2:
            continue

        kept.append(ent)

    scene.entities = kept

    # Creer la chaine consecutive unique (trous 1->2->3...).
    for a, b in zip(unique_xs[:-1], unique_xs[1:]):
        if abs(float(b) - float(a)) < 0.05:
            continue
        p1 = (float(a), min_y)
        p2 = (float(b), min_y)
        if _dimension_exists(scene, "x", p1, p2):
            continue
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=p1,
                p2=p2,
                offset=18.0,
                text=_format_dim_value(abs(float(b) - float(a))),
                axis="x",
                side="bottom",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )

    # Regrouper les cotes verticales placees a gauche du panneau central
    # sur UNE seule ligne de cotation (meme offset).
    left_chain_offset = 18.0
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            continue
        axis = (ent.axis or "auto").lower()
        if axis != "y":
            continue

        x1 = float(ent.p1[0])
        x2 = float(ent.p2[0])
        # Cotes attachees au bord gauche du panneau central.
        on_left_border = abs(x1 - min_x) <= 1.0 and abs(x2 - min_x) <= 1.0
        if not on_left_border:
            continue

        ent.side = "left"
        ent.offset = left_chain_offset


def _normalize_main_upright_outer_y_chain(scene: Scene):
    """Pour Montant Gauche/Droit: une seule ligne exterieure de cotes Y.

    Regle: remplacer les cotes verticales exterieures multiples par une chaine
    unique de succesion de trous (avec bord->1er et dernier->bord).
    """
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect

    ys = []
    shelf_ys = []
    for ent in scene.entities:
        if not isinstance(ent, Circle):
            continue
        cx, cy = float(ent.center[0]), float(ent.center[1])
        r = abs(float(ent.radius))
        if r > 12.0:
            continue
        if (min_x + 0.2) <= cx <= (max_x - 0.2) and (min_y + 0.2) <= cy <= (max_y - 0.2):
            ys.append(cy)
            # Trous d'etagere typiques (ex: ⌀5) => rayon ~2.5
            if 1.8 <= r <= 3.2:
                shelf_ys.append(cy)

    uniq_ys = sorted({round(y, 3) for y in ys})
    uniq_shelf_ys = sorted({round(y, 3) for y in shelf_ys})
    if len(uniq_ys) < 1:
        return

    # Supprimer UNIQUEMENT les cotes Y exterieures sur les bords du panneau
    # (hors cote globale pleine hauteur) et conserver les cotes internes.
    kept = []
    for ent in scene.entities:
        if not isinstance(ent, Dimension):
            kept.append(ent)
            continue

        axis = (ent.axis or "auto").lower()
        if axis != "y":
            kept.append(ent)
            continue

        ex1, ey1 = float(ent.p1[0]), float(ent.p1[1])
        ex2, ey2 = float(ent.p2[0]), float(ent.p2[1])
        y_lo, y_hi = min(ey1, ey2), max(ey1, ey2)
        span = abs(ey2 - ey1)
        full_height = abs(y_lo - min_y) <= 1.0 and abs(y_hi - max_y) <= 1.0

        if full_height:
            kept.append(ent)
            continue

        on_left_border = abs(ex1 - min_x) <= 1.0 and abs(ex2 - min_x) <= 1.0
        on_right_border = abs(ex1 - max_x) <= 1.0 and abs(ex2 - max_x) <= 1.0
        inside_y = y_lo >= (min_y - 1.5) and y_hi <= (max_y + 1.5)
        # Ne supprimer que les cotes sur bords externes (gauche/droite).
        # Les cotes internes (x hors bord) sont explicitement preservees.
        if (on_left_border or on_right_border) and inside_y and span > 0.2:
            continue

        kept.append(ent)

    scene.entities = kept

    # Cas prioritaire demande: si trous d'etagere presents,
    # poser la chaine de cotes A L'INTERIEUR du panneau (trou->trou successifs).
    if len(uniq_shelf_ys) >= 2:
        panel_w = max_x - min_x
        x_ref_inside = min_x + max(3.0, min(12.0, panel_w * 0.22))
        for a, b in zip(uniq_shelf_ys[:-1], uniq_shelf_ys[1:]):
            if abs(float(b) - float(a)) < 0.05:
                continue
            p1 = (x_ref_inside, float(a))
            p2 = (x_ref_inside, float(b))
            if _dimension_exists(scene, "y", p1, p2):
                continue
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=p1,
                    p2=p2,
                    offset=8.0,
                    text=_format_dim_value(abs(float(b) - float(a))),
                    axis="y",
                    side="right",
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )
        return

    # Sinon: chaine unique sur une ligne exterieure gauche.
    chain = [min_y] + uniq_ys + [max_y]
    x_ref = min_x
    for a, b in zip(chain[:-1], chain[1:]):
        if abs(float(b) - float(a)) < 0.05:
            continue
        p1 = (x_ref, float(a))
        p2 = (x_ref, float(b))
        if _dimension_exists(scene, "y", p1, p2):
            continue
        scene.entities.append(
            Dimension(
                layer="DIM",
                category="dimension",
                p1=p1,
                p2=p2,
                offset=18.0,
                text=_format_dim_value(abs(float(b) - float(a))),
                axis="y",
                side="left",
                dimstyle="POTECH_DIM",
                text_height=10.0,
            )
        )


def _deduplicate_triangle_outlines(scene: Scene):
    """Supprime les polylignes triangulaires dupliquees (superposition de triangles)."""
    seen = set()
    kept = []
    for ent in scene.entities:
        if not isinstance(ent, Polyline):
            kept.append(ent)
            continue

        pts = list(ent.points or [])
        if len(pts) < 3:
            kept.append(ent)
            continue

        # normaliser triangle: 3 sommets uniques
        uniq = []
        for x, y in pts:
            p = (round(float(x), 3), round(float(y), 3))
            if p not in uniq:
                uniq.append(p)
        if len(uniq) != 3:
            kept.append(ent)
            continue

        key = tuple(sorted(uniq))
        if key in seen:
            continue
        seen.add(key)
        kept.append(ent)
    scene.entities = kept


def _strip_edge_machining_for_rear_panel(scene: Scene, edge_band_mm: float = 22.0):
    """Retire les usinages parasites au voisinage des tranches du panneau arriere.

    Regle metier demandee: pour la feuille panneau arriere, ne rien poser en usinage
    le long des tranches (hors cotations). Les entites de cote sont conservees.
    """
    rect = _find_main_panel_rect(scene)
    if rect is None:
        return

    min_x, min_y, max_x, max_y = rect
    band = max(0.0, float(edge_band_mm))

    def _near_panel_edge(x: float, y: float) -> bool:
        return (
            abs(x - min_x) <= band
            or abs(x - max_x) <= band
            or abs(y - min_y) <= band
            or abs(y - max_y) <= band
        )

    kept = []
    for ent in scene.entities:
        if isinstance(ent, (Dimension, Text, Leader)):
            kept.append(ent)
            continue

        if isinstance(ent, Circle):
            cx, cy = float(ent.center[0]), float(ent.center[1])
            if _near_panel_edge(cx, cy):
                continue
            kept.append(ent)
            continue

        if isinstance(ent, HatchSimple):
            # Les hachures de symbole/triangle ne sont pas des usinages utiles ici.
            continue

        if isinstance(ent, Polyline):
            pts = list(ent.points or [])
            if _is_triangle_subpath(pts):
                continue
            kept.append(ent)
            continue

        if isinstance(ent, Line):
            x0, y0 = float(ent.start[0]), float(ent.start[1])
            x1, y1 = float(ent.end[0]), float(ent.end[1])
            length = math.hypot(x1 - x0, y1 - y0)
            if length <= 45.0 and (_near_panel_edge(x0, y0) or _near_panel_edge(x1, y1)):
                continue
            kept.append(ent)
            continue

        kept.append(ent)

    scene.entities = kept


def _add_hole_leaders_with_depth(scene: Scene, figure_name: str = ""):
    """Ajoute un repère AutoCAD par spécification de trou (diamètre/profondeur).

    Les libellés suivent les règles métier de production fournies pour DXF.
    Un seul repère est créé par type visible sur la feuille.
    """
    panel_rect = _find_main_panel_rect(scene)
    groups: dict[str, Circle] = {}
    for ent in list(scene.entities):
        if not isinstance(ent, Circle):
            continue
        r = abs(float(ent.radius))
        if r < 1.0:
            continue
        key = _get_hole_leader_label(ent, figure_name=figure_name, panel_rect=panel_rect)
        if not key:
            continue
        if key not in groups:
            groups[key] = ent

    bbox = compute_scene_bbox(scene, geometry_only=True)
    if bbox is not None:
        mid_x = (float(bbox[0]) + float(bbox[2])) / 2.0
        mid_y = (float(bbox[1]) + float(bbox[3])) / 2.0
    else:
        mid_x = 0.0
        mid_y = 0.0

    for label, circle in sorted(groups.items()):
        cx, cy = circle.center
        if cx >= mid_x and cy >= mid_y:
            angle = 45.0
        elif cx < mid_x and cy >= mid_y:
            angle = 135.0
        elif cx < mid_x and cy < mid_y:
            angle = 225.0
        else:
            angle = 315.0

        leader_distance = abs(float(circle.radius)) + 26.0
        angle_rad = math.radians(angle)
        text_location = (
            float(cx) + leader_distance * math.cos(angle_rad),
            float(cy) + leader_distance * math.sin(angle_rad),
        )

        scene.entities.append(
            DiameterDimension(
                layer="DIM",
                category="dimension",
                center=circle.center,
                radius=circle.radius,
                angle=angle,
                text_location=text_location,
                dimstyle="POTECH_DIM",
                text_height=10.0,
                label=label,
            )
        )



def convert_plotly_figure_to_scene(fig, name="SHEET") -> Scene:
    scene = Scene(name=name)

    # Mode fidelite: conserver strictement la geometrie/txt Plotly pour que
    # trous + cotations restent au meme endroit que dans l'interface Streamlit.
    strict_streamlit_fidelity = True
    # Exigence DXF: cotations en vraies dimensions AutoCAD (rouge) basees
    # uniquement sur les coordonnees stockees par Streamlit.
    use_autocad_dimensions = True

    name_norm = (
        str(name or "")
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
    )
    is_drawer_bottom_sheet = (
        ("tiroir-fond" in name_norm)
        or ("fond" in name_norm and "tiroir" in name_norm)
    )
    is_rear_panel_sheet = ("panneau arriere" in name_norm) or ("back panel" in name_norm)
    is_main_upright_sheet = ("montant gauche" in name_norm) or ("montant droit" in name_norm)
    is_montant_sheet = (
        ("montant gauche" in name_norm)
        or ("montant droit" in name_norm)
        or ("montant secondaire" in name_norm)
    )
    is_traverse_sheet = ("traverse haut" in name_norm) or ("traverse basse" in name_norm) or ("traverse bas" in name_norm)
    is_door_sheet = ("porte" in name_norm) or ("door" in name_norm)
    is_shelf_sheet = ("etagere" in name_norm) or ("shelf" in name_norm)

    # Les textes libres de la figure sont ignores: les cotes seront exportees
    # comme objets DIM AutoCAD; la geometrie d'usinage reste conservee.
    suppress_free_text = True

    meta = getattr(fig.layout, "meta", None) or {}
    dxf_dims = list(meta.get("dxf_dimensions", []))
    has_meta_triangles = bool(meta.get("dxf_triangles"))

    # Quantite en cartouche pour porte double: forcer 2.
    if is_door_sheet and ("double" in name_norm):
        try:
            meta["quantity"] = 2
        except Exception:
            pass
    dim_labels = []
    for dim in dxf_dims:
        label = dim.get("label")
        text = dim.get("text")
        if label and text is not None:
            dim_labels.append((float(label[0]), float(label[1]), str(text)))

    if use_autocad_dimensions:
        for dim in dxf_dims:
            p1 = dim.get("p1")
            p2 = dim.get("p2")
            if not p1 or not p2:
                continue
            dim_text = str(dim.get("text")) if dim.get("text") is not None else None
            if dim_text and _is_date_like_text(dim_text):
                continue
            scene.entities.append(
                Dimension(
                    layer="DIM",
                    category="dimension",
                    p1=(float(p1[0]), float(p1[1])),
                    p2=(float(p2[0]), float(p2[1])),
                    offset=float(dim.get("offset", 10.0)),
                    text=dim_text,
                    axis=dim.get("axis", "auto"),
                    side=dim.get("side"),
                    dimstyle="POTECH_DIM",
                    text_height=10.0,
                )
            )

    # Reconstruire les cotes manquantes (non présentes dans dxf_dims) depuis les annotations Plotly
    # Pas de reconstruction heuristique des cotes: elle deplace des cotes
    # par rapport au visuel Streamlit. On ne garde que dxf_dimensions.

    # Traiter les triangles depuis les métadonnées
    dxf_triangles = list(meta.get("dxf_triangles", []))
    for triangle in dxf_triangles:
        if is_rear_panel_sheet:
            continue
        filled = triangle.get("filled", False)
        layer = triangle.get("layer", "GEOM")
        orientation = triangle.get("orientation", "down")
        center = triangle.get("center")
        size = triangle.get("size", 20.0)
        
        # Pour les tiroirs fond LEGRABOX uniquement, garder seulement les triangles pleins en noir
        if is_drawer_bottom_sheet and ("legrabox" in name_norm) and not filled:
            continue

        if center:
            pts = _equilateral_triangle_from_apex(center, float(size), orientation)
            scene.entities.append(Polyline(layer=layer, points=pts, closed=True))
            if filled:
                scene.entities.append(HatchSimple(layer=layer, boundary=pts, pattern="SOLID", scale=1.0))

    if fig.layout and fig.layout.shapes:
        main_rect = None
        for _sh in fig.layout.shapes:
            if (getattr(_sh, "type", "") or "").lower() == "rect":
                try:
                    x0, y0, x1, y1 = float(_sh.x0), float(_sh.y0), float(_sh.x1), float(_sh.y1)
                except Exception:
                    continue
                area = abs((x1 - x0) * (y1 - y0))
                if main_rect is None or area > main_rect[0]:
                    main_rect = (area, min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

        for shp in fig.layout.shapes:
            layer = getattr(shp, "name", None) or "GEOM"
            shape_type = (getattr(shp, "type", "") or "").lower()

            if shape_type == "line":
                # Filtrer les traits de cotation Plotly quand les DIM AutoCAD sont actives.
                if use_autocad_dimensions and _is_dimension_shape_line(shp, dxf_dims):
                    continue

                # Etageres fixes: les traits pointilles de rappel doivent etre
                # portes par les DIM AutoCAD (rouges), pas conserves en noir.
                if use_autocad_dimensions and is_shelf_sheet:
                    line_obj = getattr(shp, "line", None)
                    line_dash = str(getattr(line_obj, "dash", "") or "").lower()
                    try:
                        line_w = float(getattr(line_obj, "width", 1.0) or 1.0)
                    except Exception:
                        line_w = 1.0
                    if line_dash in {"dot", "dash", "dashdot"} and line_w <= 1.1:
                        continue

                # En particulier sur les portes: ignorer les traits de cotation hors panneau,
                # les cotes seront regenerees en DIM AutoCAD rouges.
                if use_autocad_dimensions and is_door_sheet and main_rect is not None:
                    _, rx0, ry0, rx1, ry1 = main_rect
                    try:
                        x0, y0, x1, y1 = float(shp.x0), float(shp.y0), float(shp.x1), float(shp.y1)
                        lw = float(getattr(getattr(shp, "line", None), "width", 1.0) or 1.0)
                    except Exception:
                        x0 = y0 = x1 = y1 = 0.0
                        lw = 1.0

                    axis_aligned = abs(x0 - x1) <= 0.6 or abs(y0 - y1) <= 0.6
                    near_or_outside = (
                        (x0 < rx0 - 0.2 or x0 > rx1 + 0.2 or y0 < ry0 - 0.2 or y0 > ry1 + 0.2)
                        or (x1 < rx0 - 0.2 or x1 > rx1 + 0.2 or y1 < ry0 - 0.2 or y1 > ry1 + 0.2)
                    )
                    if axis_aligned and near_or_outside and lw <= 1.1:
                        continue

                # Montants (gauche/droit/secondaire): supprimer les petits traits de rappel
                # parasites (normaux aux bords, pointeurs vers trous de tranches).
                if use_autocad_dimensions and is_montant_sheet and main_rect is not None:
                    _, rx0, ry0, rx1, ry1 = main_rect
                    try:
                        x0, y0, x1, y1 = float(shp.x0), float(shp.y0), float(shp.x1), float(shp.y1)
                        line_obj = getattr(shp, "line", None)
                        lw = float(getattr(line_obj, "width", 1.0) or 1.0)
                        line_dash = str(getattr(line_obj, "dash", "") or "").lower()
                    except Exception:
                        x0 = y0 = x1 = y1 = 0.0
                        lw = 1.0
                        line_dash = ""

                    dx = abs(x1 - x0)
                    dy = abs(y1 - y0)
                    axis_aligned = dx <= 0.6 or dy <= 0.6
                    seg_len = math.hypot(x1 - x0, y1 - y0)
                    near_edge = (
                        abs(x0 - rx0) <= 28.0 or abs(x0 - rx1) <= 28.0
                        or abs(y0 - ry0) <= 28.0 or abs(y0 - ry1) <= 28.0
                        or abs(x1 - rx0) <= 28.0 or abs(x1 - rx1) <= 28.0
                        or abs(y1 - ry0) <= 28.0 or abs(y1 - ry1) <= 28.0
                    )
                    if line_dash in {"dot", "dash", "dashdot"} and lw <= 1.2:
                        continue
                    if axis_aligned and seg_len <= 60.0 and lw <= 1.2 and near_edge:
                        continue
                
                # Traverses: certains traits de cotation (distance trou<->rebord + raccord)
                # sont parfois des primitives; les forcer en rouge sur layer DIM.
                if use_autocad_dimensions and is_traverse_sheet:
                    try:
                        x0, y0, x1, y1 = float(shp.x0), float(shp.y0), float(shp.x1), float(shp.y1)
                        line_obj = getattr(shp, "line", None)
                        line_dash = str(getattr(line_obj, "dash", "") or "").lower()
                        lw = float(getattr(line_obj, "width", 1.0) or 1.0)
                        seg_len = math.hypot(x1 - x0, y1 - y0)
                        axis_aligned = abs(x0 - x1) <= 0.6 or abs(y0 - y1) <= 0.6
                    except Exception:
                        line_dash = ""
                        lw = 1.0
                        seg_len = 0.0
                        axis_aligned = False

                    is_dim_helper = (
                        (line_dash in {"dot", "dash", "dashdot"} and lw <= 1.3)
                        or (axis_aligned and seg_len <= 30.0 and lw <= 1.4)
                    )
                    if is_dim_helper:
                        scene.entities.append(
                            Line(layer="DIM", color=1, start=(float(shp.x0), float(shp.y0)), end=(float(shp.x1), float(shp.y1)))
                        )
                        continue

                scene.entities.append(Line(layer=layer, start=(float(shp.x0), float(shp.y0)), end=(float(shp.x1), float(shp.y1))))
            elif shape_type == "rect":
                if (not is_drawer_bottom_sheet) and _is_zone_helper_rect(shp):
                    continue
                pts = [
                    (float(shp.x0), float(shp.y0)),
                    (float(shp.x1), float(shp.y0)),
                    (float(shp.x1), float(shp.y1)),
                    (float(shp.x0), float(shp.y1)),
                ]
                scene.entities.append(Polyline(layer=layer, points=pts, closed=True))
            elif shape_type == "circle":
                cx = (float(shp.x0) + float(shp.x1)) / 2.0
                cy = (float(shp.y0) + float(shp.y1)) / 2.0
                r = abs(float(shp.x1) - float(shp.x0)) / 2.0
                scene.entities.append(Circle(layer=layer, center=(cx, cy), radius=r))
            elif shape_type == "path":
                # Conserver tous les path (avec sous-chemins) pour garder
                # tous les traits de construction/usinage de la figure source.
                for pts in _parse_plotly_path_subpaths(getattr(shp, "path", "")):
                    # Si les triangles sont deja fournis en metadata dxf_triangles,
                    # ignorer les path triangulaires Plotly pour eviter la superposition
                    # (triangle plein + triangle vide en doublon).
                    if has_meta_triangles and _is_triangle_subpath(pts):
                        continue
                    is_closed = len(pts) >= 3 and pts[0] == pts[-1]
                    scene.entities.append(Polyline(layer=layer, points=pts, closed=is_closed))

    for trace in fig.data or []:
        mode = (getattr(trace, "mode", "") or "").lower()
        layer = getattr(trace, "name", None) or "GEOM"

        xs = list(getattr(trace, "x", []) or [])
        ys = list(getattr(trace, "y", []) or [])
        texts = list(getattr(trace, "text", []) or [])

        if "lines" in mode and xs and ys:
            for seg in _split_polyline_by_none(xs, ys):
                scene.entities.append(Polyline(layer=layer, points=seg, closed=False))

        if "markers" in mode and xs and ys:
            marker_size = float(getattr(getattr(trace, "marker", None), "size", 6) or 6)
            radius = max(0.5, marker_size * 0.15)
            for x, y in zip(xs, ys):
                if x is None or y is None:
                    continue
                scene.entities.append(Circle(layer=layer, center=(float(x), float(y)), radius=radius))

        if (not suppress_free_text) and "text" in mode and xs and ys and texts:
            for x, y, t in zip(xs, ys, texts):
                if x is None or y is None:
                    continue
                t_str = str(t)
                # Skip diameter labels — handled by AutoCAD diameter dims
                if any(s in t_str for s in ("Ø", "⌀", "%%c", "%%C")):
                    continue
                scene.entities.append(Text(layer="TEXT", category="text", text=t_str, insert=(float(x), float(y)), height=2.5, align="CENTER"))

    if not suppress_free_text:
        for ann in fig.layout.annotations or []:
            ann_text = str(getattr(ann, "text", ""))
            ann_x = float(getattr(ann, "x", 0.0))
            ann_y = float(getattr(ann, "y", 0.0))
            # Skip diameter/radius annotations — replaced by proper AutoCAD diameter dims
            if any(s in ann_text for s in ("Ø", "⌀", "%%c", "%%C")):
                continue
            if dim_labels:
                for lx, ly, lt in dim_labels:
                    if ann_text == lt and abs(ann_x - lx) <= 0.5 and abs(ann_y - ly) <= 0.5:
                        break
                else:
                    scene.entities.append(Text(layer="TEXT", category="text", text=ann_text, insert=(ann_x, ann_y), height=2.5, align="CENTER"))
                continue
            scene.entities.append(Text(layer="TEXT", category="text", text=ann_text, insert=(ann_x, ann_y), height=2.5, align="CENTER"))

    if fig.layout and fig.layout.xaxis and fig.layout.yaxis:
        xr = getattr(fig.layout.xaxis, "range", None)
        yr = getattr(fig.layout.yaxis, "range", None)
        if xr and yr and len(xr) == 2 and len(yr) == 2:
            scene.width = abs(float(xr[1]) - float(xr[0]))
            scene.height = abs(float(yr[1]) - float(yr[0]))

    if not use_autocad_dimensions:
        _ensure_bottom_x_dimensions_for_holes(scene, figure_name=name)
        _normalize_bottom_x_dimensions(scene)

    if use_autocad_dimensions:
        # Cotes generales exterieures pour toutes les feuilles.
        _ensure_global_outer_dimensions(scene)

        if is_rear_panel_sheet:
            _ensure_rear_panel_divider_dowel_dimensions(scene)

        if is_montant_sheet:
            _normalize_montant_consecutive_hole_dimensions(scene)

        if is_main_upright_sheet:
            _normalize_main_upright_outer_y_chain(scene)

        # Portes: cotes X/Y en chaine de tous les trous, plus lisibles.
        if is_door_sheet:
            rect_door = _find_main_panel_rect(scene)
            # Sauvegarder les cotes d'epaisseur de tranches avant le nettoyage :
            # ce sont les cotes courtes (span < 50mm) dont au moins un point est hors du panneau.
            saved_tranche_thickness_dims = []
            if rect_door:
                dx_min, dy_min, dx_max, dy_max = rect_door
                for ent in scene.entities:
                    if not isinstance(ent, Dimension):
                        continue
                    ex1, ey1 = float(ent.p1[0]), float(ent.p1[1])
                    ex2, ey2 = float(ent.p2[0]), float(ent.p2[1])
                    span = max(abs(ex2 - ex1), abs(ey2 - ey1))
                    any_outside = (
                        min(ex1, ex2) < dx_min - 2.0
                        or max(ex1, ex2) > dx_max + 2.0
                        or min(ey1, ey2) < dy_min - 2.0
                        or max(ey1, ey2) > dy_max + 2.0
                    )
                    if span < 55.0 and any_outside:
                        saved_tranche_thickness_dims.append(ent)
            # On remplace les cotes detail porte par une chaine X/Y complete.
            scene.entities = [e for e in scene.entities if not isinstance(e, Dimension)]
            _ensure_global_outer_dimensions(scene)
            _add_xy_chain_dimensions_for_holes(scene)
            # Re-ajouter les cotes d'epaisseur des tranches.
            scene.entities.extend(saved_tranche_thickness_dims)

    # Toujours ajouter une cote diametre par diametre de trou present dans la feuille.
    # Le rendu DXF utilisera en priorite des MULTILEADER AutoCAD.
    _add_hole_leaders_with_depth(scene, figure_name=name)

    # Supprimer les triangles en double (superposition vide + plein).
    _deduplicate_triangle_outlines(scene)

    if is_drawer_bottom_sheet and ("legrabox" in name_norm):
        _ensure_legrabox_bottom_construction_lines(scene)

    if is_rear_panel_sheet:
        _strip_edge_machining_for_rear_panel(scene)

    return scene


def render_scene_to_streamlit(scene: Scene):
    import plotly.graph_objects as go

    fig = go.Figure()

    for ent in scene.entities:
        if isinstance(ent, Line):
            fig.add_trace(go.Scatter(x=[ent.start[0], ent.end[0]], y=[ent.start[1], ent.end[1]], mode="lines", name=ent.layer, showlegend=False))
        elif isinstance(ent, Polyline):
            xs = [p[0] for p in ent.points]
            ys = [p[1] for p in ent.points]
            if ent.closed and ent.points:
                xs += [ent.points[0][0]]
                ys += [ent.points[0][1]]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=ent.layer, showlegend=False))
        elif isinstance(ent, Circle):
            x0, y0 = ent.center[0] - ent.radius, ent.center[1] - ent.radius
            x1, y1 = ent.center[0] + ent.radius, ent.center[1] + ent.radius
            fig.add_shape(type="circle", x0=x0, y0=y0, x1=x1, y1=y1)
        elif isinstance(ent, Arc):
            pass
        elif isinstance(ent, Text):
            fig.add_trace(go.Scatter(x=[ent.insert[0]], y=[ent.insert[1]], mode="text", text=[ent.text], showlegend=False))
        elif isinstance(ent, Leader):
            if len(ent.points) >= 2:
                fig.add_trace(go.Scatter(x=[p[0] for p in ent.points], y=[p[1] for p in ent.points], mode="lines", showlegend=False))
                if ent.text:
                    p = ent.points[-1]
                    fig.add_trace(go.Scatter(x=[p[0]], y=[p[1]], mode="text", text=[ent.text], showlegend=False))
        elif isinstance(ent, Dimension):
            fig.add_trace(go.Scatter(x=[ent.p1[0], ent.p2[0]], y=[ent.p1[1], ent.p2[1]], mode="lines", showlegend=False))
            label = ent.text or "DIM"
            fig.add_trace(go.Scatter(x=[(ent.p1[0]+ent.p2[0])/2], y=[(ent.p1[1]+ent.p2[1])/2], mode="text", text=[label], showlegend=False))
        elif isinstance(ent, HatchSimple):
            if len(ent.boundary) >= 3:
                pts = ent.boundary + [ent.boundary[0]]
                fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="lines", showlegend=False))

    fig.update_layout(title=scene.name, xaxis=dict(scaleanchor="y", scaleratio=1), yaxis=dict(), showlegend=False)
    return fig
