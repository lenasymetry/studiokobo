"""Rendu des cotations: DIMENSION AutoCAD prioritaire + fallback primitives."""

from __future__ import annotations

import math

from .sanitize import sanitize_layer_name, sanitize_table_name, sanitize_text


def ensure_potech_dimstyle(doc, dimstyle_name="POTECH_DIM", text_height=12.0, arrow_size=3.0, dimgap=1.2):
    """Crée un DIMSTYLE minimal robuste R2010."""
    safe_name = sanitize_table_name(dimstyle_name, fallback="POTECH_DIM")
    if safe_name in doc.dimstyles:
        return safe_name

    if "Standard" not in doc.styles:
        doc.styles.new("Standard", dxfattribs={"font": "txt"})

    doc.dimstyles.new(
        safe_name,
        dxfattribs={
            "dimtxsty": "Standard",
            "dimtxt": float(text_height),
            "dimgap": float(dimgap),
            "dimasz": float(arrow_size),
            "dimclrd": 1,
            "dimclre": 1,
            "dimclrt": 1,
            "dimdec": 1,
            "dimtix": 0,
            "dimexo": 1.0,
        },
    )
    return safe_name


def _resolve_dimension_axis(dim):
    x1, y1 = float(dim.p1[0]), float(dim.p1[1])
    x2, y2 = float(dim.p2[0]), float(dim.p2[1])
    if dim.axis == "x":
        return "x"
    if dim.axis == "y":
        return "y"
    return "x" if abs(y2 - y1) <= abs(x2 - x1) else "y"


def add_dimension_autocad(layout, dim_data, dimstyle="POTECH_DIM", text_height=12.0, arrow_size=3.0, dimgap=1.2):
    """Ajoute une vraie DIMENSION AutoCAD (associative au sens AutoCAD, non simulée)."""
    layer_name = sanitize_layer_name(getattr(dim_data, "layer", None) or "DIM", fallback="DIM")
    if layer_name not in layout.doc.layers:
        layout.doc.layers.new(layer_name, dxfattribs={"color": 1})

    dimstyle_name = ensure_potech_dimstyle(layout.doc, dimstyle_name=dimstyle, text_height=text_height, arrow_size=arrow_size, dimgap=dimgap)

    x1, y1 = float(dim_data.p1[0]), float(dim_data.p1[1])
    x2, y2 = float(dim_data.p2[0]), float(dim_data.p2[1])
    offset = float(getattr(dim_data, "dim_line_offset", getattr(dim_data, "offset", 10.0)))
    side = (getattr(dim_data, "side", None) or "").lower()
    axis = _resolve_dimension_axis(dim_data)

    if axis == "x":
        if side == "bottom":
            y_dim = min(y1, y2) - offset
        else:
            y_dim = max(y1, y2) + offset
        base = (min(x1, x2), y_dim)
        override = layout.add_linear_dim(
            base=base,
            p1=(x1, y1),
            p2=(x2, y2),
            angle=0,
            dimstyle=dimstyle_name,
            dxfattribs={"layer": layer_name},
        )
    else:
        if side == "left":
            x_dim = min(x1, x2) - offset
        else:
            x_dim = max(x1, x2) + offset
        base = (x_dim, min(y1, y2))
        override = layout.add_linear_dim(
            base=base,
            p1=(x1, y1),
            p2=(x2, y2),
            angle=90,
            dimstyle=dimstyle_name,
            dxfattribs={"layer": layer_name},
        )

    text_override = getattr(dim_data, "text_override", None) or getattr(dim_data, "text", None)
    if text_override:
        try:
            override.dimension.dxf.text = sanitize_text(str(text_override), fallback="<>")
        except Exception:
            pass

    override.render()
    return override


def _draw_arrow_triangle(layout, tip, direction, size, layer):
    ux, uy = direction
    norm = math.hypot(ux, uy)
    if norm == 0:
        return
    ux, uy = ux / norm, uy / norm
    nx, ny = -uy, ux
    bx = tip[0] - ux * size
    by = tip[1] - uy * size
    p1 = (bx + nx * size * 0.45, by + ny * size * 0.45)
    p2 = (bx - nx * size * 0.45, by - ny * size * 0.45)
    layout.add_lwpolyline([tip, p1, p2, tip], dxfattribs={"layer": layer})


def add_dimensions_as_primitives(layout, dim, text_height=10.0, arrow_size=3.0, text_style="Standard"):
    """Cotation ultra-compatible: LINE + flèches + TEXT.
    
    Args:
        layout: ezdxf layout (ModelSpace ou PaperSpace)
        dim: Dimension object avec (p1, p2, offset, axis, side, text_height)
        text_height: Hauteur texte en mm (défaut: 10.0 pour respect coords Streamlit)
        arrow_size: Taille flèches en mm
        text_style: Style texte DXF
    
    respect_dim_text_height = dim.text_height if dim.text_height is not None else text_height
    """
    p1 = dim.p1
    p2 = dim.p2
    offset = float(dim.offset)
    final_text_height = dim.text_height if dim.text_height is not None else text_height
    layer_name = sanitize_layer_name(dim.layer or "DIMS", fallback="DIMS")

    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])

    # Détermine axe: x/y ou auto (minimum de la variation)
    horizontal = (dim.axis == "x") or (dim.axis == "auto" and abs(y2 - y1) <= abs(x2 - x1))
    
    if horizontal:
        # Cotation horizontale: texte au dessus (ou selon 'side')
        if dim.side == "bottom":
            yy = min(y1, y2) - offset
        else:
            yy = max(y1, y2) + offset  # Défaut: top
        
        layout.add_line((x1, y1), (x1, yy), dxfattribs={"layer": layer_name})
        layout.add_line((x2, y2), (x2, yy), dxfattribs={"layer": layer_name})
        layout.add_line((x1, yy), (x2, yy), dxfattribs={"layer": layer_name})
        _draw_arrow_triangle(layout, (x1, yy), (1.0, 0.0), arrow_size, layer_name)
        _draw_arrow_triangle(layout, (x2, yy), (-1.0, 0.0), arrow_size, layer_name)

        label = sanitize_text(dim.text or f"{abs(x2 - x1):.1f}", fallback=f"{abs(x2 - x1):.1f}")
        ent = layout.add_text(
            label,
            dxfattribs={
                "layer": layer_name,
                "height": final_text_height,
                "style": sanitize_table_name(text_style, fallback="Standard"),
            },
        )
        mid_y = yy + (max(1.0, final_text_height * 0.6) if dim.side != "bottom" else -max(1.0, final_text_height * 0.6))
        ent.set_placement(((x1 + x2) / 2.0, mid_y), align="MIDDLE_CENTER")
        return
    
    # Cotation verticale: texte à droite (ou selon 'side')
    if dim.side == "left":
        xx = min(x1, x2) - offset
    else:
        xx = max(x1, x2) + offset  # Défaut: right
    
    layout.add_line((x1, y1), (xx, y1), dxfattribs={"layer": layer_name})
    layout.add_line((x2, y2), (xx, y2), dxfattribs={"layer": layer_name})
    layout.add_line((xx, y1), (xx, y2), dxfattribs={"layer": layer_name})
    _draw_arrow_triangle(layout, (xx, y1), (0.0, 1.0), arrow_size, layer_name)
    _draw_arrow_triangle(layout, (xx, y2), (0.0, -1.0), arrow_size, layer_name)

    label = sanitize_text(dim.text or f"{abs(y2 - y1):.1f}", fallback=f"{abs(y2 - y1):.1f}")
    ent = layout.add_text(
        label,
        dxfattribs={
            "layer": layer_name,
            "height": final_text_height,
            "style": sanitize_table_name(text_style, fallback="Standard"),
        },
    )
    mid_x = xx + (max(1.0, final_text_height * 0.6) if dim.side != "left" else -max(1.0, final_text_height * 0.6))
    ent.set_placement((mid_x, (y1 + y2) / 2.0), align="LEFT")


def add_dimensions_editable(layout, dim):
    """Compatibilité API existante: route vers add_dimension_autocad()."""
    return add_dimension_autocad(
        layout,
        dim_data=dim,
        dimstyle=getattr(dim, "dimstyle", "POTECH_DIM") or "POTECH_DIM",
        text_height=float(getattr(dim, "text_height", None) or 12.0),
    )


def add_diameter_dim_autocad(layout, diam_dim, dimstyle="POTECH_DIM", text_height=10.0, arrow_size=3.0, dimgap=1.2):
    """Ajoute une cote diamètre avec repère multiple (MULTILEADER) en priorité.

    Fallback:
    1) add_diameter_dim()
    2) ligne + texte %%C...

    Paramètres:
        diam_dim: DiameterDimension (center, radius, angle, dimstyle, text_height)
    """
    layer_name = sanitize_layer_name(getattr(diam_dim, "layer", None) or "DIM", fallback="DIM")
    if layer_name not in layout.doc.layers:
        layout.doc.layers.new(layer_name, dxfattribs={"color": 1})

    th = float(getattr(diam_dim, "text_height", None) or text_height)
    dimstyle_name = ensure_potech_dimstyle(
        layout.doc,
        dimstyle_name=dimstyle,
        text_height=th,
        arrow_size=arrow_size,
        dimgap=dimgap,
    )

    cx, cy = float(diam_dim.center[0]), float(diam_dim.center[1])
    r = abs(float(diam_dim.radius))
    angle_deg = float(getattr(diam_dim, "angle", 45.0))
    angle_rad = math.radians(angle_deg)

    # Point sur le cercle dans la direction du leader
    p_on_circle = (cx + r * math.cos(angle_rad), cy + r * math.sin(angle_rad))

    location = getattr(diam_dim, "text_location", None)
    if location is None:
        location = (
            cx + (r + 22.0) * math.cos(angle_rad),
            cy + (r + 22.0) * math.sin(angle_rad),
        )

    diam_val = r * 2.0
    default_label = f"%%C{diam_val:.1f}"
    label = sanitize_text(getattr(diam_dim, "label", None) or default_label, fallback=default_label)

    # 1) Priorité: MULTILEADER (outil AutoCAD "ligne de repère multiple")
    try:
        from ezdxf.math import Vec2
        from ezdxf.render.mleader import HorizontalConnection

        target = Vec2(p_on_circle[0], p_on_circle[1])
        insert = Vec2(float(location[0]), float(location[1]))

        segment1 = insert - target
        if segment1.magnitude < 1e-6:
            segment1 = Vec2(12.0, 0.0)

        outward = 1.0 if float(location[0]) >= cx else -1.0
        segment2 = Vec2(outward * max(8.0, float(arrow_size) * 3.0), 0.0)

        connection_type = HorizontalConnection.middle_of_top_line

        mleader = layout.add_multileader_mtext(style="Standard", dxfattribs={"layer": layer_name})
        mleader.set_content(label, char_height=th, style="Standard")
        mleader.set_arrow_properties(name="", size=float(arrow_size))
        mleader.set_connection_properties(
            landing_gap=max(0.2, float(dimgap)),
            dogleg_length=max(2.0, float(arrow_size) * 2.0),
        )
        mleader.quick_leader(
            label,
            target=target,
            segment1=segment1,
            segment2=segment2,
            connection_type=connection_type,
        )
        return mleader
    except Exception:
        pass

    # 2) Fallback: cote diamètre AutoCAD classique
    if label == default_label:
        try:
            dim = layout.add_diameter_dim(
                center=(cx, cy),
                radius=r,
                angle=angle_deg,
                location=location,
                dimstyle=dimstyle_name,
                dxfattribs={"layer": layer_name},
            )
            dim.render()
            return dim
        except Exception:
            pass

    # 3) Fallback final: leader + texte personnalisé
    p_end = (float(location[0]), float(location[1]))
    layout.add_line(p_on_circle, p_end, dxfattribs={"layer": layer_name})
    t = layout.add_text(
        sanitize_text(label, fallback=label),
        dxfattribs={"layer": layer_name, "height": th},
    )
    t.set_placement(p_end)
    return t

