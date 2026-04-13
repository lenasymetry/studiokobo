import datetime
import os
import re
from io import StringIO

try:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    TextEntityAlignment = None

from export_manager import get_all_machining_plans_figures


def _is_black_layer(doc, layer_name):
    try:
        layer = doc.layers.get(layer_name)
        return abs(int(layer.dxf.color)) == 7
    except Exception:
        return False


def _text_color_for_layer(doc, layer_name):
    return 256 if _is_black_layer(doc, layer_name) else 7


def _safe_text(msp, text, x, y, height, align, layer="TEXTES"):
    if text is None:
        return
    text_str = str(text).strip()
    if not text_str:
        return
    ent = msp.add_text(
        text_str,
        dxfattribs={"height": height, "layer": layer, "color": _text_color_for_layer(msp.doc, layer)},
    )
    ent.set_placement((x, y), align=align)


def _parse_qty_from_title(title):
    match = re.search(r"\(\s*x\s*(\d+)\s*\)", str(title or ""), flags=re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


def _clean_title_designation(title):
    return re.sub(r"\(\s*x\s*\d+\s*\)", "", str(title or ""), flags=re.IGNORECASE).strip()


def _add_logo(msp, logo_path, target_rect):
    x0, y0, x1, y1 = target_rect
    w = max(1.0, float(x1) - float(x0))
    h = max(1.0, float(y1) - float(y0))

    candidates = []
    if logo_path:
        candidates.append(logo_path)
        candidates.append(os.path.join(os.getcwd(), logo_path))
        candidates.append(os.path.join(os.path.dirname(__file__), logo_path))

    resolved = None
    for path in candidates:
        if path and os.path.isfile(path):
            resolved = path
            break

    if not resolved:
        return False

    try:
        px_size = (1000, 400)
        try:
            pil_mod = __import__("PIL.Image", fromlist=["Image"])
            with pil_mod.open(resolved) as img:
                px_size = tuple(img.size)
        except Exception:
            pass

        rel_path = os.path.relpath(resolved, os.getcwd())
        image_def = msp.doc.add_image_def(filename=rel_path, size_in_pixel=px_size)
        msp.add_image(image_def, insert=(x0, y0), size_in_units=(w, h), dxfattribs={"layer": "LEGENDE"})
        return True
    except Exception:
        return False


def generate_basic_dwg(cabinets_data, project_name):
    if not EZDXF_AVAILABLE:
        return b"Le module 'ezdxf' est requis.", False, None

    try:
        titles = []
        try:
            figures = get_all_machining_plans_figures(cabinets_data, list(range(len(cabinets_data))))
            titles = [title for title, _fig in figures]
        except Exception:
            titles = []

        if not titles:
            titles = ["Feuille usinage"]

        doc = ezdxf.new("R2010")
        doc.units = 4

        for layer_name, color in [
            ("PANNEAU", 7),
            ("TEXTES", 7),
            ("LEGENDE", 7),
        ]:
            if layer_name not in doc.layers:
                doc.layers.new(layer_name, dxfattribs={"color": color})

        msp = doc.modelspace()

        sheet_w = 1200.0
        sheet_h = 800.0
        spacing = 1600.0

        for idx, title in enumerate(titles):
            x0 = idx * spacing
            y0 = 0.0
            x1 = x0 + sheet_w
            y1 = y0 + sheet_h

            msp.add_lwpolyline(
                [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                dxfattribs={"layer": "PANNEAU"},
            )

            _safe_text(msp, title, x0 + 20.0, y1 + 40.0, 20.0, TextEntityAlignment.BOTTOM_LEFT)

            # Cartouche
            cartouche_h = 90.0
            cartouche_top = y0 - 20.0
            cartouche_bottom = cartouche_top - cartouche_h

            msp.add_lwpolyline(
                [(x0, cartouche_bottom), (x1, cartouche_bottom), (x1, cartouche_top), (x0, cartouche_top), (x0, cartouche_bottom)],
                dxfattribs={"layer": "LEGENDE"},
            )

            logo_w = 120.0
            msp.add_line((x0 + logo_w, cartouche_bottom), (x0 + logo_w, cartouche_top), dxfattribs={"layer": "LEGENDE"})
            msp.add_line((x0, cartouche_bottom + cartouche_h * 0.5), (x1, cartouche_bottom + cartouche_h * 0.5), dxfattribs={"layer": "LEGENDE"})

            # Logo
            _add_logo(msp, "logo.png", (x0 + 8.0, cartouche_bottom + 8.0, x0 + logo_w - 8.0, cartouche_top - 8.0))

            content_x = x0 + logo_w
            content_w = sheet_w - logo_w
            cols = 5
            col_w = content_w / float(cols)

            # Séparateurs verticaux
            for c in range(1, cols):
                x_sep = content_x + col_w * c
                msp.add_line((x_sep, cartouche_bottom), (x_sep, cartouche_top), dxfattribs={"layer": "LEGENDE"})

            qty = _parse_qty_from_title(title) or 1
            designation = _clean_title_designation(title)
            fields = [
                ("DATE", datetime.date.today().isoformat()),
                ("PROJECT", project_name or ""),
                ("BODY", "caisson"),
                ("PART", designation),
                ("QTY", qty),
            ]

            label_y = cartouche_top - 15.0
            value_y = cartouche_bottom + 20.0

            for idx, (label, value) in enumerate(fields):
                cx = content_x + col_w * idx + col_w * 0.5
                _safe_text(msp, label, cx, label_y, 12.0, TextEntityAlignment.MIDDLE_CENTER)
                _safe_text(msp, value, cx, value_y, 14.0, TextEntityAlignment.MIDDLE_CENTER)

        stream = StringIO()
        doc.write(stream)
        dxf_text = stream.getvalue()
        safe_name = str(project_name).replace(" ", "_") if project_name else "Projet"
        filename = f"Plans_{safe_name}_{datetime.date.today().isoformat()}.dxf"
        return dxf_text.encode("utf-8"), True, filename
    except Exception as exc:
        return f"Erreur export DXF: {exc}".encode("utf-8"), False, None
