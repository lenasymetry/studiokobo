"""Rendu DXF depuis Scene graph: géométrie en ModelSpace, cartouche/légende en PaperSpace."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

import ezdxf
from ezdxf.enums import TextEntityAlignment

from .dimensions import add_dimension_autocad, add_diameter_dim_autocad, ensure_potech_dimstyle
from .scene import Arc, Circle, Dimension, DiameterDimension, HatchSimple, Line, Polyline, Scene, Text, Leader, BlockRef, compute_scene_bbox
from .sanitize import sanitize_layer_name, sanitize_table_name, sanitize_text


def _ensure_triangle_blocks(doc):
    """Crée les définitions de blocks pour les triangles équilatéraux vide et plein."""
    import math
    
    # Triangle équilatéral de côté 10mm, centré à l'origine, pointe vers le HAUT
    side = 10.0
    height = side * math.sqrt(3) / 2.0  # hauteur d'un triangle équilatéral
    
    # Centre de gravité à l'origine (0, 0)
    # Sommet haut
    top = (0.0, height * 2/3)
    # Base gauche
    left = (-side/2, -height/3)
    # Base droite  
    right = (side/2, -height/3)
    
    # Triangle vide (contour blanc uniquement)
    if "TRIANGLE_VIDE" not in doc.blocks:
        block = doc.blocks.new(name="TRIANGLE_VIDE")
        # 3 lignes blanches formant le triangle équilatéral
        block.add_line(top, left, dxfattribs={"color": 256})  # ByLayer
        block.add_line(left, right, dxfattribs={"color": 256})
        block.add_line(right, top, dxfattribs={"color": 256})
    
    # Triangle plein (contour blanc + remplissage noir)
    if "TRIANGLE_PLEIN" not in doc.blocks:
        block = doc.blocks.new(name="TRIANGLE_PLEIN")
        # 3 lignes blanches formant le triangle
        block.add_line(top, left, dxfattribs={"color": 256})
        block.add_line(left, right, dxfattribs={"color": 256})
        block.add_line(right, top, dxfattribs={"color": 256})
        # Remplissage noir solide
        hatch = block.add_hatch(color=0, dxfattribs={"layer": "HATCH"})  # Noir
        hatch.paths.add_polyline_path([top, left, right], is_closed=True)
        hatch.set_solid_fill()


@dataclass
class DxfRenderConfig:
    units: int = 4  # millimeters
    text_height: float = 2.5
    dimensions_text_height: float = 10.0
    arrow_size: float = 3.0
    mode: str = "editable"  # cnc|editable
    force_primitives_dims: bool = False
    allow_hatch: bool = False

    # page/layout
    paper_width_mm: float = 420.0
    paper_height_mm: float = 297.0
    page_margin_mm: float = 10.0
    bbox_margin_factor: float = 1.05

    # misc
    style_name: str = "TXT_STD"
    dimstyle_name: str = "POTECH_DIM"
    model_gap_mm: float = 200.0
    layers: dict = field(default_factory=lambda: {
        "GEOM": 7,
        "TEXT": 7,
        "DIM": 1,
        "HATCH": 8,
        "LEADER": 7,
        "SYMBOLS": 7,
        "VIEWPORTS": 8,
        "TITLEBLOCK": 7,
    })


def _ensure_table_basics(doc, config: DxfRenderConfig):
    for lname, color in config.layers.items():
        sn = sanitize_layer_name(lname, fallback="GEOM")
        if sn not in doc.layers:
            doc.layers.new(sn, dxfattribs={"color": int(color)})

    style = sanitize_table_name(config.style_name, fallback="TXT_STD")
    if style not in doc.styles:
        doc.styles.new(style, dxfattribs={"font": "txt"})

    ensure_potech_dimstyle(
        doc,
        dimstyle_name=sanitize_table_name(config.dimstyle_name, fallback="POTECH_DIM"),
        text_height=float(config.dimensions_text_height),
        arrow_size=float(config.arrow_size),
    )
    
    # Créer les blocks pour les triangles
    _ensure_triangle_blocks(doc)


def create_empty_doc(config: Optional[DxfRenderConfig] = None):
    config = config or DxfRenderConfig()
    doc = ezdxf.new("R2010")
    doc.units = config.units
    _ensure_table_basics(doc, config)
    return doc


def _entity_allowed(ent, stage: str):
    if stage == "all":
        return True
    if stage == "geometry":
        return isinstance(ent, (Line, Polyline, Arc, Circle, BlockRef))
    if stage == "text":
        return isinstance(ent, (Line, Polyline, Arc, Circle, BlockRef, Text, Leader))
    if stage == "legend":
        return isinstance(ent, (Line, Polyline, Arc, Circle, BlockRef, Text, Leader, HatchSimple))
    if stage == "dimensions":
        return True
    return True


def _add_point_offset(pt, offset):
    return (float(pt[0]) + float(offset[0]), float(pt[1]) + float(offset[1]))


def add_part_geometry(msp, part, offset=(0.0, 0.0)):
    dxfattribs = {"layer": sanitize_layer_name(part.layer, "GEOM")}
    if getattr(part, "color", None) is not None:
        dxfattribs["color"] = int(part.color)
    else:
        # Construction/usinage en noir par defaut.
        dxfattribs["color"] = 7

    if isinstance(part, Line):
        msp.add_line(_add_point_offset(part.start, offset), _add_point_offset(part.end, offset), dxfattribs=dxfattribs)
    elif isinstance(part, Polyline):
        pts = [_add_point_offset(p, offset) for p in part.points]
        if part.closed and pts:
            pts = pts + [pts[0]]
        msp.add_lwpolyline(pts, dxfattribs=dxfattribs)
    elif isinstance(part, Circle):
        msp.add_circle(_add_point_offset(part.center, offset), part.radius, dxfattribs=dxfattribs)
    elif isinstance(part, Arc):
        msp.add_arc(_add_point_offset(part.center, offset), part.radius, part.start_angle, part.end_angle, dxfattribs=dxfattribs)


def add_holes(msp, holes, offset=(0.0, 0.0)):
    for hole in holes:
        if isinstance(hole, Circle):
            msp.add_circle(
                _add_point_offset(hole.center, offset),
                hole.radius,
                dxfattribs={"layer": sanitize_layer_name(hole.layer, "GEOM"), "color": 7},
            )


def add_pockets(msp, pockets, offset=(0.0, 0.0), allow_hatch=False):
    for pocket in pockets:
        if not isinstance(pocket, HatchSimple):
            continue
        if len(pocket.boundary) < 3:
            continue
        boundary = [_add_point_offset(p, offset) for p in pocket.boundary]
        msp.add_lwpolyline(
            boundary + [boundary[0]],
            dxfattribs={"layer": sanitize_layer_name(pocket.layer, "HATCH"), "color": 7},
        )
        if allow_hatch:
            hatch = msp.add_hatch(color=8, dxfattribs={"layer": sanitize_layer_name(pocket.layer, "HATCH")})
            hatch.paths.add_polyline_path(boundary, is_closed=True)
            # Utiliser un remplissage solide si pattern="SOLID"
            if pocket.pattern.upper() == "SOLID":
                hatch.set_solid_fill()
            else:
                hatch.set_pattern_fill(pocket.pattern, scale=pocket.scale)


def add_labels(msp, labels, offset=(0.0, 0.0)):
    for label in labels:
        if isinstance(label, Text):
            p = _add_point_offset(label.insert, offset)
            t = msp.add_text(
                sanitize_text(label.text),
                dxfattribs={
                    "layer": sanitize_layer_name(label.layer or "TEXT", "TEXT"),
                    "height": float(label.height or 2.5),
                    "style": sanitize_table_name(label.style or "TXT_STD", "TXT_STD"),
                },
            )
            if (label.align or "LEFT").upper() == "CENTER":
                t.set_placement(p, align=TextEntityAlignment.MIDDLE_CENTER)
            else:
                t.set_placement(p, align=TextEntityAlignment.LEFT)


def render_scene_to_modelspace(scene: Scene, msp, config: DxfRenderConfig, offset=(0.0, 0.0), debug_stage="all", log=None):
    """Rendu d'une Scene vers ModelSpace + bbox résultante.
    
    Filtre entities avec export=False (eg. rectangles transparents).
    Utilise config.dimensions_text_height pour dimensions (défaut 10.0 mm).
    """
    if log is None:
        log = []

    for ent in scene.entities:
        # FILTRE: Skip si export=False
        if hasattr(ent, 'export') and not ent.export:
            continue
        
        if not _entity_allowed(ent, debug_stage):
            continue
        try:
            if isinstance(ent, (Line, Polyline, Arc)):
                add_part_geometry(msp, ent, offset=offset)
            elif isinstance(ent, Circle):
                if (ent.category or "geometry") in ("hole", "holes"):
                    add_holes(msp, [ent], offset=offset)
                else:
                    add_part_geometry(msp, ent, offset=offset)
            elif isinstance(ent, Text):
                add_labels(msp, [ent], offset=offset)
            elif isinstance(ent, Leader):
                if len(ent.points) >= 2:
                    pts = [_add_point_offset(p, offset) for p in ent.points]
                    msp.add_lwpolyline(pts, dxfattribs={"layer": sanitize_layer_name(ent.layer or "LEADER", "LEADER")})
                if ent.text:
                    p = _add_point_offset(ent.points[-1], offset) if ent.points else (offset[0], offset[1])
                    add_labels(msp, [Text(layer="TEXT", text=ent.text, insert=p, height=config.text_height)], offset=(0.0, 0.0))
            elif isinstance(ent, HatchSimple):
                add_pockets(msp, [ent], offset=offset, allow_hatch=config.allow_hatch)
            elif isinstance(ent, Dimension):
                p1 = _add_point_offset(ent.p1, offset)
                p2 = _add_point_offset(ent.p2, offset)
                dim_obj = Dimension(
                    layer=ent.layer,
                    color=ent.color,
                    lineweight=ent.lineweight,
                    category=ent.category,
                    export=getattr(ent, 'export', True),
                    p1=p1,
                    p2=p2,
                    offset=ent.offset,
                    text=ent.text,
                    axis=ent.axis,
                    dimstyle=ent.dimstyle,
                    side=getattr(ent, 'side', None),
                    text_height=getattr(ent, 'text_height', None),
                )
                add_dimension_autocad(
                    msp,
                    dim_data=dim_obj,
                    dimstyle=config.dimstyle_name,
                    text_height=float(config.dimensions_text_height),
                    arrow_size=float(config.arrow_size),
                )
            elif isinstance(ent, DiameterDimension):
                diam_with_offset = type(ent)(
                    layer=ent.layer, color=ent.color, lineweight=ent.lineweight,
                    category=ent.category, export=getattr(ent, 'export', True),
                    center=_add_point_offset(ent.center, offset),
                    radius=ent.radius, angle=ent.angle,
                    text_location=_add_point_offset(ent.text_location, offset) if ent.text_location else None,
                    dimstyle=ent.dimstyle,
                    text_height=ent.text_height,
                    label=getattr(ent, 'label', None),
                )
                add_diameter_dim_autocad(
                    msp,
                    diam_dim=diam_with_offset,
                    dimstyle=config.dimstyle_name,
                    text_height=float(config.dimensions_text_height),
                    arrow_size=float(config.arrow_size),
                )
            elif isinstance(ent, BlockRef):
                msp.add_blockref(
                    sanitize_table_name(ent.name, fallback="BLK"),
                    _add_point_offset(ent.insert, offset),
                    dxfattribs={
                        "layer": sanitize_layer_name(ent.layer, "GEOM"),
                        "xscale": ent.xscale,
                        "yscale": ent.yscale,
                        "rotation": ent.rotation
                    },
                )
        except Exception as exc:
            log.append(f"RENDER_ERROR[{scene.name}][{type(ent).__name__}]: {exc}")

    bbox = compute_scene_bbox(scene, offset=offset)
    if bbox is None:
        bbox = (float(offset[0]), float(offset[1]), float(offset[0]) + 10.0, float(offset[1]) + 10.0)
    # bbox géométrique seule (sans cotes) pour centrage fiable du viewport
    geom_bbox = compute_scene_bbox(scene, offset=offset, geometry_only=True)
    if geom_bbox is None:
        geom_bbox = bbox
    return bbox, geom_bbox


def doc_to_ascii_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("ascii", errors="strict")
