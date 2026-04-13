"""Centrage automatique du contenu d'un layout PaperSpace sur la zone imprimable.

Fonctionne avec ezdxf R2010+.
Usage typique (après avoir posé le viewport ET le cartouche) :

    from .centering import center_layout_content
    center_layout_content(layout, paper_w=420.0, paper_h=297.0, margin=10.0)
"""

from __future__ import annotations

from typing import Optional, Tuple

import ezdxf
from ezdxf.math import BoundingBox2d, Vec2


# ---------------------------------------------------------------------------
# 1. Zone imprimable
# ---------------------------------------------------------------------------

def printable_area(
    paper_w: float,
    paper_h: float,
    margin_left: float = 10.0,
    margin_right: float = 10.0,
    margin_top: float = 10.0,
    margin_bottom: float = 10.0,
) -> Tuple[float, float, float, float]:
    """Retourne (x_min, y_min, x_max, y_max) de la zone imprimable en mm.

    Args:
        paper_w: largeur du papier (mm). Ex: 420 pour A3, 297 pour A4 paysage.
        paper_h: hauteur du papier (mm). Ex: 297 pour A3, 210 pour A4 paysage.
        margin_*: marges en mm (gauche, droite, haut, bas).
    """
    x_min = float(margin_left)
    y_min = float(margin_bottom)
    x_max = float(paper_w) - float(margin_right)
    y_max = float(paper_h) - float(margin_top)
    return (x_min, y_min, x_max, y_max)


def printable_center(
    paper_w: float,
    paper_h: float,
    margin_left: float = 10.0,
    margin_right: float = 10.0,
    margin_top: float = 10.0,
    margin_bottom: float = 10.0,
) -> Tuple[float, float]:
    """Centre de la zone imprimable (mm)."""
    x_min, y_min, x_max, y_max = printable_area(
        paper_w, paper_h, margin_left, margin_right, margin_top, margin_bottom
    )
    return ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)


# ---------------------------------------------------------------------------
# 2. Collecte des entités imprimables en PaperSpace
# ---------------------------------------------------------------------------

# Types d'entités ezdxf qui ne s'impriment pas (viewports hors page, calques OFF…)
_NON_PRINTABLE_TYPES = {"VIEWPORT"}


def get_printable_entities(layout):
    """Retourne la liste des entités imprimables d'un layout PaperSpace.

    Exclut :
    - le viewport fixe (status=1, le cadre de la feuille lui-même)
    - les entités sur des calques gelés / éteints
    """
    doc = layout.doc
    frozen_layers: set[str] = set()
    off_layers: set[str] = set()
    try:
        for layer in doc.layers:
            name = layer.dxf.name
            if getattr(layer.dxf, "flags", 0) & 1:   # bit 1 = frozen
                frozen_layers.add(name)
            if getattr(layer.dxf, "color", 1) < 0:   # couleur négative = OFF
                off_layers.add(name)
    except Exception:
        pass

    printable = []
    for ent in layout:
        dxf_type = ent.dxftype()

        # Viewport status=1 = viewport feuille (non déplaçable, non imprimé)
        if dxf_type == "VIEWPORT":
            try:
                status = int(getattr(ent.dxf, "status", 0) or 0)
                if status <= 1:
                    continue  # viewport feuille cadre fixe → skip
            except Exception:
                continue

        # Calques gelés / éteints → non imprimé
        try:
            layer_name = ent.dxf.layer
            if layer_name in frozen_layers or layer_name in off_layers:
                continue
        except Exception:
            pass

        printable.append(ent)

    return printable


# ---------------------------------------------------------------------------
# 3. Bounding box des entités PaperSpace
# ---------------------------------------------------------------------------

def _bbox_of_entity(ent) -> Optional[BoundingBox2d]:
    """Calcule la bbox 2D d'une entité ezdxf en coordonnées PaperSpace."""
    try:
        dxf_type = ent.dxftype()

        if dxf_type == "VIEWPORT":
            # Viewport flottant : position = centre PaperSpace + demi-dimensions
            cx = float(ent.dxf.center.x)
            cy = float(ent.dxf.center.y)
            w  = float(ent.dxf.width)  / 2.0
            h  = float(ent.dxf.height) / 2.0
            return BoundingBox2d([(cx - w, cy - h), (cx + w, cy + h)])

        if dxf_type == "LINE":
            return BoundingBox2d([ent.dxf.start.vec2, ent.dxf.end.vec2])

        if dxf_type == "LWPOLYLINE":
            pts = [Vec2(p[0], p[1]) for p in ent.get_points()]
            return BoundingBox2d(pts) if pts else None

        if dxf_type in ("TEXT", "MTEXT"):
            try:
                # insert = point d'ancrage en PaperSpace
                ins = ent.dxf.insert
                return BoundingBox2d([Vec2(ins.x, ins.y)])
            except Exception:
                return None

        if dxf_type == "INSERT":
            ins = ent.dxf.insert
            return BoundingBox2d([Vec2(ins.x, ins.y)])

        if dxf_type == "CIRCLE":
            c = ent.dxf.center
            r = float(ent.dxf.radius)
            return BoundingBox2d([(c.x - r, c.y - r), (c.x + r, c.y + r)])

        if dxf_type in ("ARC", "ELLIPSE"):
            c = ent.dxf.center
            r = float(ent.dxf.radius) if hasattr(ent.dxf, "radius") else 1.0
            return BoundingBox2d([(c.x - r, c.y - r), (c.x + r, c.y + r)])

        if dxf_type == "DIMENSION":
            try:
                pts = []
                for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                    try:
                        p = getattr(ent.dxf, attr)
                        pts.append(Vec2(p.x, p.y))
                    except Exception:
                        pass
                return BoundingBox2d(pts) if pts else None
            except Exception:
                return None

    except Exception:
        pass

    return None


def compute_layout_bbox(layout) -> Optional[Tuple[float, float, float, float]]:
    """Bounding box de toutes les entités imprimables du layout (mm PaperSpace).

    Retourne (x_min, y_min, x_max, y_max) ou None si aucune entité.
    """
    global_bbox = BoundingBox2d()
    for ent in get_printable_entities(layout):
        bb = _bbox_of_entity(ent)
        if bb and bb.has_data:
            global_bbox.extend(bb)

    if not global_bbox.has_data:
        return None

    extmin = global_bbox.extmin
    extmax = global_bbox.extmax
    return (float(extmin.x), float(extmin.y), float(extmax.x), float(extmax.y))


# ---------------------------------------------------------------------------
# 4. Translation des entités PaperSpace
# ---------------------------------------------------------------------------

def _translate_entity(ent, dx: float, dy: float) -> None:
    """Translate une entité ezdxf de (dx, dy) en coordonnées PaperSpace."""
    translate_entity(ent, dx, dy)


def translate_entity(ent, dx: float, dy: float) -> None:
    """Translate une entité ezdxf de (dx, dy) en coordonnées PaperSpace (alias public)."""
    try:
        dxf_type = ent.dxftype()

        if dxf_type == "VIEWPORT":
            # Déplace le centre PaperSpace du viewport
            c = ent.dxf.center
            ent.dxf.center = (c.x + dx, c.y + dy, 0.0)
            # NB: view_center_point reste inchangé (pointe vers le modèle)
            return

        if dxf_type == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            ent.dxf.start = (s.x + dx, s.y + dy, 0.0)
            ent.dxf.end   = (e.x + dx, e.y + dy, 0.0)
            return

        if dxf_type == "LWPOLYLINE":
            new_pts = [(p[0] + dx, p[1] + dy) + p[2:] for p in ent.get_points()]
            ent.set_points(new_pts)
            return

        if dxf_type == "TEXT":
            ins = ent.dxf.insert
            ent.dxf.insert = (ins.x + dx, ins.y + dy, 0.0)
            try:
                ap = ent.dxf.align_point
                ent.dxf.align_point = (ap.x + dx, ap.y + dy, 0.0)
            except Exception:
                pass
            return

        if dxf_type == "MTEXT":
            ins = ent.dxf.insert
            ent.dxf.insert = (ins.x + dx, ins.y + dy, 0.0)
            return

        if dxf_type == "INSERT":
            ins = ent.dxf.insert
            ent.dxf.insert = (ins.x + dx, ins.y + dy, 0.0)
            return

        if dxf_type == "CIRCLE":
            c = ent.dxf.center
            ent.dxf.center = (c.x + dx, c.y + dy, 0.0)
            return

        if dxf_type in ("ARC", "ELLIPSE"):
            c = ent.dxf.center
            ent.dxf.center = (c.x + dx, c.y + dy, 0.0)
            return

        if dxf_type == "DIMENSION":
            for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                try:
                    p = getattr(ent.dxf, attr)
                    setattr(ent.dxf, attr, (p.x + dx, p.y + dy, 0.0))
                except Exception:
                    pass
            return

        if dxf_type == "SOLID":
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                try:
                    v = getattr(ent.dxf, attr)
                    setattr(ent.dxf, attr, (v.x + dx, v.y + dy, 0.0))
                except Exception:
                    pass
            return

    except Exception:
        pass


# ---------------------------------------------------------------------------
# 5. Fonction principale : center_layout_content
# ---------------------------------------------------------------------------

def center_layout_content(
    layout,
    paper_w: float,
    paper_h: float,
    margin: float = 10.0,
    margin_left: Optional[float] = None,
    margin_right: Optional[float] = None,
    margin_top: Optional[float] = None,
    margin_bottom: Optional[float] = None,
) -> Tuple[float, float]:
    """Centre automatiquement tout le contenu imprimable d'un layout PaperSpace.

    Translate viewport + cartouche + toutes entités PaperSpace pour que leur
    bounding box combinée soit centrée dans la zone imprimable.

    Args:
        layout      : ezdxf layout PaperSpace (doc.layouts.get("SHEET_01"))
        paper_w     : largeur du papier (mm). 297 = A4 paysage, 210 = A4 portrait
        paper_h     : hauteur du papier (mm). 210 = A4 paysage, 297 = A4 portrait
        margin      : marge uniforme en mm (ignorée si margin_left/right/top/bottom fournis)
        margin_*    : marges individuelles (optionnel, surcharge margin)

    Returns:
        (dx, dy) translation appliquée en mm.  (0.0, 0.0) si rien à centrer.
    """
    ml = float(margin_left  if margin_left  is not None else margin)
    mr = float(margin_right if margin_right is not None else margin)
    mt = float(margin_top   if margin_top   is not None else margin)
    mb = float(margin_bottom if margin_bottom is not None else margin)

    # Centre de la zone imprimable
    pc_x, pc_y = printable_center(paper_w, paper_h, ml, mr, mt, mb)

    # Bbox du contenu actuel du layout
    content_bbox = compute_layout_bbox(layout)
    if content_bbox is None:
        return (0.0, 0.0)  # rien à déplacer

    cx_min, cy_min, cx_max, cy_max = content_bbox
    content_cx = (cx_min + cx_max) / 2.0
    content_cy = (cy_min + cy_max) / 2.0

    # Décalage à appliquer
    dx = pc_x - content_cx
    dy = pc_y - content_cy

    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return (0.0, 0.0)  # déjà centré

    # Applique la translation à toutes les entités imprimables
    for ent in get_printable_entities(layout):
        _translate_entity(ent, dx, dy)

    return (dx, dy)
