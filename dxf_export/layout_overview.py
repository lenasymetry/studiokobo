"""Layout1 Overview Grid: arrangement des feuilles en grille avec padding configurable.

Génère une vue d'ensemble (vignettes) de toutes les feuilles sur un Layout spécifique,
avec espacement (padding) ajustable pour éviter chevauchement et améliorer lisibilité.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from ezdxf.math import Vec2


def layout_overview_grid(
    doc: object,  # ezdxf.Drawing
    scenes: List[object],  # List[Scene]
    paper_w: float = 420.0,
    paper_h: float = 297.0,
    margin: float = 10.0,
    padding: float = 20.0,
    cols: int = 2,
) -> Optional[str]:
    """Crée Layout1 et arrange toutes les feuilles en grille avec padding.
    
    Note: Dessine les vignettes dans ModelSpace avec offsets, puis crée Layout1
    qui affiche le ModelSpace avec viewport configuré pour la grille.
    
    Args:
        doc: ezdxf Drawing object
        scenes: List[Scene] — feuilles à arranger
        paper_w: Largeur papier A3 (mm), défaut 420
        paper_h: Hauteur papier A3 (mm), défaut 297
        margin: Marge bord papier (mm), défaut 10
        padding: Espacement entre vignettes (mm), défaut 20
        cols: Nombre colonnes grille (défaut 2)
    
    Returns:
        str: Nom du layout (Layout1) si succès, None sinon
    """
    if not scenes:
        return None

    try:
        from ezdxf.enums import TextEntityAlignment
    except ImportError:
        TextEntityAlignment = None

    # Crée Layout1 s'il n'existe pas
    layout1_name = "Layout1"
    try:
        # Tente d'accéder au Layout1 existant
        layout1 = doc.layouts.get(layout1_name)
        if layout1 is None:
            layout1 = doc.layouts.new(layout1_name)
    except Exception:
        # Fallback: créer nouveau layout
        try:
            layout1 = doc.layouts.new(layout1_name)
        except Exception:
            return None

    # Configure papier A3 landscape
    try:
        layout1.page_setup.set_landscape()
        layout1.page_setup.set_paperformat("A3", margins=(margin / 10.0, margin / 10.0, margin / 10.0, margin / 10.0))
    except Exception:
        pass  # Fallback si API change

    # IMPORTANT: Dessine dans ModelSpace global, pas dans layout1
    msp = doc.modelspace()

    # Calcule grille: layout_width = paper_w - 2*margin, layout_height = paper_h - 2*margin
    layout_width = paper_w - 2.0 * margin
    layout_height = paper_h - 2.0 * margin

    rows = math.ceil(len(scenes) / float(cols))
    
    # Taille vignette: partage l'espace disponible moins padding
    total_padding_x = padding * (cols - 1) if cols > 1 else 0.0
    total_padding_y = padding * (rows - 1) if rows > 1 else 0.0
    
    thumb_w = (layout_width - total_padding_x) / float(cols)
    thumb_h = (layout_height - total_padding_y) / float(rows)
    
    # Place chaque vignette
    for idx, scene in enumerate(scenes):
        row = idx // cols
        col = idx % cols
        
        # Position coin bas-gauche vignette
        x_start = margin + col * (thumb_w + padding)
        y_start = margin + row * (thumb_h + padding)
        
        # Cadre titre vignette (rectangle + texte nom)
        frame_color = 1  # Red
        frame_weight = 13  # 0.3mm
        
        # Rectangle contour vignette
        pts = [
            (x_start, y_start),
            (x_start + thumb_w, y_start),
            (x_start + thumb_w, y_start + thumb_h),
            (x_start, y_start + thumb_h),
            (x_start, y_start),
        ]
        msp.add_lwpolyline(pts, dxfattribs={"layer": "OVERVIEW", "color": frame_color, "lineweight": frame_weight})
        
        # Texte nom feuille (bas-gauche du cadre)
        try:
            title_text = msp.add_text(
                str(scene.name or f"Sheet {idx+1}"),
                dxfattribs={
                    "layer": "OVERVIEW",
                    "height": 5.0,
                    "color": frame_color,
                    "style": "Standard",
                },
            )
            title_text.set_placement((x_start + 1.0, y_start + 1.0), align=TextEntityAlignment.LEFT if TextEntityAlignment else None)
        except Exception:
            pass  # Fallback si add_text échoue

        # Rendu géométrie scène dans vignette offset
        # Import local pour éviter circular dependency
        from .render_dxf import render_scene_to_modelspace, DxfRenderConfig

        config = DxfRenderConfig(
            text_height=2.0,  # Texte petit pour vignettes
            dimensions_text_height=2.0,
            arrow_size=1.5,
        )
        
        try:
            render_scene_to_modelspace(
                scene, 
                msp, 
                config,
                offset=(x_start + 1.0, y_start + 6.0),  # Offset pour placer dans vignette
                debug_stage=None,
                log=False,
            )
        except Exception as e:
            # Log mais continue (fallback gracieux)
            pass

    # Setup viewport pour Layout1 (affiche toute la grille)
    try:
        from .layout import fit_viewport_to_bbox
        
        # Crée viewport
        vports = layout1.viewports
        if len(vports) > 0:
            viewport = vports[0]  # Utilise viewport existant
        else:
            viewport = vports.new()  # Crée nouveau viewport
        
        # Bbox totale de la grille
        grid_bbox = (margin - 5, margin - 5, paper_w - margin + 5, paper_h - margin + 5)
        fit_viewport_to_bbox(viewport, bbox=grid_bbox, margin_factor=1.0)
    except Exception:
        pass  # Fallback si viewport setup échoue

    return layout1_name
