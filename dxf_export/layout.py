"""Gestion layouts PaperSpace + viewport auto-fit pour DXF multi-feuilles."""

from __future__ import annotations

from typing import Tuple

from .sanitize import sanitize_table_name


def setup_layout_with_viewport(
    doc,
    layout_name: str,
    paper_width_mm: float = 420.0,
    paper_height_mm: float = 297.0,
    margin_mm: float = 10.0,
):
    """Crée/initialise un layout A3 paysage avec un viewport flottant unique.

    Retourne: (layout, viewport)
    """
    safe_layout_name = sanitize_table_name(layout_name, fallback="SHEET_01", max_len=31)

    if safe_layout_name in doc.layouts:
        layout = doc.layouts.get(safe_layout_name)
    else:
        layout = doc.layouts.new(safe_layout_name)

    try:
        layout.page_setup(
            size=(float(paper_width_mm), float(paper_height_mm)),
            margins=(float(margin_mm), float(margin_mm), float(margin_mm), float(margin_mm)),
            units="mm",
            offset=(0.0, 0.0),
            rotation=0,
            scale=(1, 1),
            name="",
            device="DWG To PDF.pc3",
        )
    except Exception:
        # fallback si version ezdxf ne supporte pas tous les paramètres
        try:
            layout.page_setup(size=(float(paper_width_mm), float(paper_height_mm)), margins=(float(margin_mm),) * 4, units="mm")
        except Exception:
            pass

    # Nettoie les viewports flottants existants (status > 1)
    for vp in list(layout.query("VIEWPORT")):
        try:
            if int(getattr(vp.dxf, "status", 0) or 0) > 1:
                layout.delete_entity(vp)
        except Exception:
            continue

    viewport_center_ps = (float(paper_width_mm) / 2.0, float(paper_height_mm) / 2.0)
    viewport_size_ps = (
        max(1.0, float(paper_width_mm) - 2.0 * float(margin_mm)),
        max(1.0, float(paper_height_mm) - 2.0 * float(margin_mm)),
    )

    viewport = layout.add_viewport(
        center=viewport_center_ps,
        size=viewport_size_ps,
        view_center_point=(0.0, 0.0),
        view_height=100.0,
        status=2,
        dxfattribs={"layer": "VIEWPORTS"},
    )

    # verrouille le viewport (bit 0x4000)
    try:
        flags = int(getattr(viewport.dxf, "flags", 0) or 0)
        viewport.dxf.flags = flags | 0x4000
    except Exception:
        pass

    return layout, viewport


def setup_layout_with_viewport_excluding_titleblock(
    doc,
    layout_name: str,
    paper_width_mm: float = 420.0,
    paper_height_mm: float = 297.0,
    margin_mm: float = 10.0,
    titleblock_height_mm: float = 32.0,
):
    """Crée un layout avec 1 viewport limité à la zone au-dessus du cartouche.

    Le viewport et le cartouche sont tous deux centrés sur la même largeur
    (block_width = min(390mm, papier - 2*marges)) pour être visuellement alignés.
    """
    safe_layout_name = sanitize_table_name(layout_name, fallback="SHEET_01", max_len=31)

    if safe_layout_name in doc.layouts:
        layout = doc.layouts.get(safe_layout_name)
    else:
        layout = doc.layouts.new(safe_layout_name)

    try:
        layout.page_setup(
            size=(float(paper_width_mm), float(paper_height_mm)),
            margins=(float(margin_mm), float(margin_mm), float(margin_mm), float(margin_mm)),
            units="mm",
            offset=(0.0, 0.0),
            rotation=0,
            scale=(1, 1),
            name="",
            device="DWG To PDF.pc3",
        )
    except Exception:
        try:
            layout.page_setup(size=(float(paper_width_mm), float(paper_height_mm)), margins=(float(margin_mm),) * 4, units="mm")
        except Exception:
            pass

    for vp in list(layout.query("VIEWPORT")):
        try:
            if int(getattr(vp.dxf, "status", 0) or 0) > 1:
                layout.delete_entity(vp)
        except Exception:
            continue

    # Viewport = exactement la zone imprimable (de marge à papier-marge).
    # x de margin_mm à paper_w-margin_mm, centré sur le papier.
    draw_w = max(1.0, float(paper_width_mm) - 2.0 * float(margin_mm))
    x_left = float(margin_mm)

    draw_h = max(1.0, float(paper_height_mm) - 2.0 * float(margin_mm) - float(titleblock_height_mm))
    draw_bottom = float(margin_mm) + float(titleblock_height_mm)

    viewport_center_ps = (
        float(paper_width_mm) / 2.0,
        draw_bottom + draw_h / 2.0,
    )

    viewport = layout.add_viewport(
        center=viewport_center_ps,
        size=(draw_w, draw_h),
        view_center_point=(0.0, 0.0),
        view_height=100.0,
        status=2,
        dxfattribs={"layer": "VIEWPORTS"},
    )

    try:
        flags = int(getattr(viewport.dxf, "flags", 0) or 0)
        viewport.dxf.flags = flags | 0x4000
    except Exception:
        pass

    return layout, viewport, {
        "draw_zone": {
            "left": float(margin_mm),
            "bottom": draw_bottom,
            "width": draw_w,
            "height": draw_h,
            "top": draw_bottom + draw_h,
        }
    }


def fit_viewport_to_bbox(
    viewport,
    bbox: Tuple[float, float, float, float],
    margin_factor: float = 1.05,
    min_view_height: float = 10.0,
    geometry_bbox: Tuple[float, float, float, float] = None,
):
    """Ajuste un viewport à la bbox géométrique, sans déformation.

    - centre vue = centre de geometry_bbox (géométrie pure, non biaisé par les cotes)
    - hauteur vue = max(hauteur_bbox_totale, largeur_bbox_totale/aspect_viewport) * marge
    """
    min_x, min_y, max_x, max_y = [float(v) for v in bbox]

    bbox_w = max(1e-6, max_x - min_x)
    bbox_h = max(1e-6, max_y - min_y)

    # Centre = centre de la géométrie pure pour éviter le biais des cotes asymétriques
    if geometry_bbox is not None:
        gx1, gy1, gx2, gy2 = [float(v) for v in geometry_bbox]
        cx = (gx1 + gx2) / 2.0
        cy = (gy1 + gy2) / 2.0
    else:
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

    vp_w = max(1e-6, float(getattr(viewport.dxf, "width", 1.0) or 1.0))
    vp_h = max(1e-6, float(getattr(viewport.dxf, "height", 1.0) or 1.0))
    vp_aspect = vp_w / vp_h

    # Sécurité de cadrage pour les pointillés/cotations: padding absolu + marge minimale.
    abs_pad = 20.0
    bbox_w_padded = bbox_w + 2.0 * abs_pad
    bbox_h_padded = bbox_h + 2.0 * abs_pad
    target_h = max(bbox_h_padded, bbox_w_padded / vp_aspect)
    effective_margin = max(1.08, float(margin_factor))
    view_h = max(float(min_view_height), target_h * effective_margin)

    viewport.dxf.view_center_point = (cx, cy)
    viewport.dxf.view_height = view_h

    # relock viewport
    try:
        flags = int(getattr(viewport.dxf, "flags", 0) or 0)
        viewport.dxf.flags = flags | 0x4000
    except Exception:
        pass

    return {
        "view_center": (cx, cy),
        "view_height": view_h,
        "bbox": (min_x, min_y, max_x, max_y),
        "bbox_size": (bbox_w, bbox_h),
    }
