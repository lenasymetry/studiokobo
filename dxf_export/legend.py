"""Légende/cartouche PaperSpace (TEXT seulement, ASCII safe)."""

from __future__ import annotations

from ezdxf.enums import TextEntityAlignment

from .sanitize import sanitize_layer_name, sanitize_text, sanitize_table_name


MANDATORY_FIELDS = [
    ("PROJET", "project_name"),
    ("DESIGNATION", "part_name"),
    ("CORPS DE MEUBLE", "corps_meuble"),
    ("Reference", "reference"),
    ("Dimensions", "dimensions"),
    ("Matiere", "material"),
    ("Date", "date"),
    ("Version", "version"),
    ("Client", "client"),
    ("Commentaires", "comments"),
]


def add_legend(
    layout,
    metadata_dict,
    paper_width_mm: float = 420.0,
    paper_height_mm: float = 297.0,
    margin_mm: float = 10.0,
    width_mm: float = 170.0,
    title_height_mm: float = 4.0,
    text_height_mm: float = 4.0,
    line_gap_mm: float = 4.5,
    layer_name: str = "LEGEND",
    text_style: str = "TXT_STD",
):
    """Ajoute une légende obligatoire centrée en bas du PaperSpace."""
    layer = sanitize_layer_name(layer_name, fallback="LEGEND")
    style = sanitize_table_name(text_style, fallback="TXT_STD")

    fields = []
    md = metadata_dict or {}
    for label, key in MANDATORY_FIELDS:
        value = md.get(key, "")
        if key == "dimensions":
            # fallback dimensions depuis L/H/Ep
            if not value:
                L = md.get("length", "")
                H = md.get("height", "")
                E = md.get("thickness", "")
                if L or H or E:
                    value = f"{L} x {H} x {E}"
        fields.append((label, sanitize_text(str(value), fallback="-")))

    extra = md.get("html_meta", {}) if isinstance(md.get("html_meta", {}), dict) else {}
    for k, v in extra.items():
        if not v:
            continue
        fields.append((sanitize_text(str(k), fallback="Info"), sanitize_text(str(v), fallback="-")))

    fields.append(("CREATEUR", "PASCAL OLIBARES"))

    uniform_text_height = max(float(text_height_mm), float(title_height_mm))

    rows = max(1, len(fields))
    line_pitch = uniform_text_height + float(line_gap_mm)
    body_height = rows * line_pitch + 8.0
    total_height = body_height + 12.0

    # Utilise toute la zone imprimable (de marge à papier-marge).
    x0 = float(margin_mm)
    x1 = float(paper_width_mm) - float(margin_mm)
    y0 = float(margin_mm)
    y1 = y0 + total_height

    layout.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], dxfattribs={"layer": layer})
    header_y = y1 - (uniform_text_height + 4.0)
    layout.add_line((x0, header_y), (x1, header_y), dxfattribs={"layer": layer})

    title = sanitize_text(md.get("legend_title", "LEGEND / CARTOUCHE"), fallback="LEGEND / CARTOUCHE")
    t = layout.add_text(title, dxfattribs={"layer": layer, "height": uniform_text_height, "style": style})
    t.set_placement((x0 + 2.0, y1 - ((uniform_text_height + 4.0) / 2.0)), align=TextEntityAlignment.MIDDLE_LEFT)

    y = header_y - 4.0
    label_w = 48.0
    for label, value in fields:
        left = f"{sanitize_text(label, fallback='Info')}:"
        t1 = layout.add_text(left, dxfattribs={"layer": layer, "height": uniform_text_height, "style": style})
        t1.set_placement((x0 + 2.0, y), align=TextEntityAlignment.MIDDLE_LEFT)

        t2 = layout.add_text(value, dxfattribs={"layer": layer, "height": uniform_text_height, "style": style})
        t2.set_placement((x0 + 2.0 + label_w, y), align=TextEntityAlignment.MIDDLE_LEFT)

        y -= line_pitch

    return {
        "legend_bbox": (x0, y0, x1, y1),
        "legend_top": y1,
        "legend_left": x0,
        "legend_right": x1,
        "legend_bottom": y0,
    }
