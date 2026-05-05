import streamlit as st
import datetime
from io import BytesIO, StringIO
from machining_logic import calculate_hole_positions
from machining_logic import calculate_back_panel_holes, get_hinge_y_positions, get_mobile_shelf_holes, get_traverse_holes_for_divider, get_traverse_face_holes_for_divider, calculate_all_zones_2d, calculate_zones_from_dividers, get_vertical_divider_tranche_holes
from drawing_interface import draw_machining_view_pro_final
from project_definitions import get_legrabox_specs

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

try:
    import base64
    from io import BytesIO
    BASE64_AVAILABLE = True
except ImportError:
    BASE64_AVAILABLE = False

try:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    TextEntityAlignment = None

def _extract_diameter_from_string(diam_str, default_value=5.0):
    import re
    try:
        nums = re.findall(r"[\d\.]+", str(diam_str).split('/')[0])
        if nums:
            return float(nums[0])
    except Exception:
        pass
    return float(default_value)

def _format_dim_value(value):
    try:
        v = float(value)
        if abs(v - int(v)) < 1e-6:
            return str(int(v))
        return f"{v:.1f}".rstrip('0').rstrip('.')
    except Exception:
        return str(value)

def _convert_diameter_string_autocad(diam_str):
    return str(diam_str).replace('⌀', '%%c').replace('Ø', '%%c')

def _apply_anglaise_slide_offset(drawer_system, x_slide_holes, panel_depth_mm, inset_mm=40.0):
    """Décale les perçages de coulisses des tiroirs ANGLAISE de 40 mm vers l'intérieur."""
    base_positions = [float(x) for x in (x_slide_holes or [])]
    if str(drawer_system) != 'ANGLAISE':
        return base_positions

    max_pos = max(0.0, float(panel_depth_mm) - 1.0)
    shifted = [x + float(inset_mm) for x in base_positions]
    filtered = [x for x in shifted if 0.0 <= x <= max_pos]
    # Fallback: ne jamais renvoyer une liste vide pour éviter de perdre la feuille d'usinage.
    return filtered if filtered else ([max_pos] if max_pos > 0.0 else [])

def _drawer_handle_signature(drp):
    """Retourne une signature hashable de poignée tiroir pour le regroupement des plans."""
    handle_type = str(drp.get('drawer_handle_type', 'none'))
    if handle_type == 'integrated_cutout':
        return (
            'integrated_cutout',
            float(drp.get('drawer_handle_width', 150.0)),
            float(drp.get('drawer_handle_height', 40.0)),
            float(drp.get('drawer_handle_offset_top', 10.0)),
        )
    if handle_type == 'finger_pull':
        return (
            'finger_pull',
            float(drp.get('drawer_handle_offset_top', 10.0)),
            float(drp.get('drawer_finger_pull_depth', 12.0)),
            float(drp.get('drawer_finger_pull_drop', 35.0)),
        )
    return None

def _handle_signature_to_cutout_props(signature):
    """Convertit une signature de poignée tiroir en dict de paramètres de dessin."""
    if not signature:
        return None
    sig_type = str(signature[0])
    if sig_type == 'integrated_cutout':
        return {
            'type': 'integrated_cutout',
            'width': float(signature[1]),
            'height': float(signature[2]),
            'offset_top': float(signature[3]),
        }
    if sig_type == 'finger_pull':
        return {
            'type': 'finger_pull',
            'offset_top': float(signature[1]),
            'depth': float(signature[2]),
            'drop': float(signature[3]),
        }
    return None

def _safe_add_text(msp, text, dxfattribs, placement=None, align=None):
    if text is None:
        return None
    text_str = str(text)
    if not text_str.strip():
        return None
    ent = msp.add_text(text_str, dxfattribs=dxfattribs)
    if placement is not None:
        ent.set_placement(placement, align=align)
    return ent

def _get_secondary_divider_title(div_idx, cab_idx, part_label, output_format):
    """Nom de feuille montant secondaire, unique par caisson et partie."""
    if output_format == 'dxf':
        return f"Montant Secondaire {div_idx+1} (C{cab_idx}) - {part_label}"
    return f"Montant Secondaire {div_idx+1} (C{cab_idx}) - {part_label}"

def _is_secondary_divider_title(title):
    txt = str(title or "").lower()
    return "montant secondaire" in txt and ("1/2" in txt or "2/2" in txt)

def _sanitize_dxf_doc(doc):
    try:
        msp = doc.modelspace()
    except Exception:
        return
    to_delete = []
    for ent in msp:
        dxftype = ent.dxftype()
        if dxftype in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            try:
                text_val = ent.text if dxftype == "MTEXT" else ent.dxf.text
            except Exception:
                text_val = ""
            if not str(text_val).strip():
                to_delete.append(ent)
        if dxftype in ("ACAD_PROXY_ENTITY", "PROXY_ENTITY"):
            to_delete.append(ent)
    for ent in to_delete:
        try:
            msp.delete_entity(ent)
        except Exception:
            pass

def _sanitize_layout_name(raw_name, fallback="SHEET"):
    import re
    base = re.sub(r"[^A-Za-z0-9_\- ]+", "", str(raw_name or "")).strip()
    base = re.sub(r"\s+", "_", base)
    base = base.strip("_")
    return (base or fallback)[:31]

def _sheet_layout_name(index):
    """Nom de layout déterministe, unique et sans limite pratique de nombre de feuilles."""
    return f"SHEET_{int(index):04d}"

def _fit_view_height_for_bbox(bbox, viewport_w, viewport_h, margin_factor=1.08):
    min_x, min_y, max_x, max_y = bbox
    bw = max(1.0, float(max_x) - float(min_x))
    bh = max(1.0, float(max_y) - float(min_y))
    vp_ratio = max(1e-6, float(viewport_w) / float(viewport_h))
    bbox_ratio = bw / bh

    if bbox_ratio > vp_ratio:
        # Contenu plus large que le viewport: hauteur pilotée par la largeur.
        return max(10.0, (bw / vp_ratio) * float(margin_factor))
    return max(10.0, bh * float(margin_factor))

def _create_layout_for_plan(doc, layout_name, bbox, paper_w=420.0, paper_h=297.0, margin=10.0):
    if layout_name in doc.layouts:
        layout = doc.layouts.get(layout_name)
    else:
        layout = doc.layouts.new(layout_name)

    try:
        layout.page_setup(size=(float(paper_w), float(paper_h)), margins=(float(margin),) * 4, units="mm")
    except Exception:
        pass

    # Recrée un viewport unique par feuille.
    for vp in list(layout.query("VIEWPORT")):
        try:
            layout.delete_entity(vp)
        except Exception:
            pass

    vp_center = (float(paper_w) / 2.0, float(paper_h) / 2.0)
    vp_size = (
        max(1.0, float(paper_w) - 2.0 * float(margin)),
        max(1.0, float(paper_h) - 2.0 * float(margin)),
    )

    min_x, min_y, max_x, max_y = bbox
    model_center = ((float(min_x) + float(max_x)) / 2.0, (float(min_y) + float(max_y)) / 2.0)
    view_height = _fit_view_height_for_bbox(bbox, vp_size[0], vp_size[1])

    layout.add_viewport(
        center=vp_center,
        size=vp_size,
        view_center_point=model_center,
        view_height=view_height,
        status=2,
    )

def _add_linear_dimension_dxf(msp, base, p1, p2, angle, layer="COTES", text_override=None, dimstyle="COTATIONS_PRO"):
    """Ajoute une vraie dimension AutoCAD éditable avec l'outil COTE."""
    try:
        # Créer dimension AutoCAD standard éditables
        dim = msp.add_linear_dim(
            base=base,
            p1=p1,
            p2=p2,
            angle=angle,
            dimstyle=dimstyle,
            dxfattribs={"layer": layer, "color": 3},  # Color 3 = Green = Cotations
        )
        if text_override:
            # Pour DimStyleOverride object
            try:
                dim.text = text_override
            except Exception:
                pass
        # Configurer via l'override (si supporté par la version ezdxf)
        try:
            dim.dimtad = 1  # Text above dimension line
            dim.dimgap = 2.0
            dim.dimtix = 0  # Text inside: no
            dim.dimdli = 3.75  # Spacing for stacked dimensions
        except Exception:
            pass
        dim.render()
        return dim
    except Exception as e:
        # Log error for debugging but don't silently fail
        import sys
        print(f"[DXF] Warning: Failed to create dimension at {base}: {e}", file=sys.stderr)
        return None

def _add_cartouche_dxf(msp, x0, y0, width, height, proj_info, panel_name):
    cart_x0 = x0
    cart_y0 = y0 - height
    cart_x1 = x0 + width
    cart_y1 = y0

    msp.add_lwpolyline(
        [(cart_x0, cart_y0), (cart_x1, cart_y0), (cart_x1, cart_y1), (cart_x0, cart_y1), (cart_x0, cart_y0)],
        dxfattribs={"layer": "CARTOUCHE"},
    )

    col_widths = [width * 0.15, width * 0.20, width * 0.20, width * 0.11, width * 0.09, width * 0.25]
    col_x = [cart_x0]
    for w in col_widths:
        col_x.append(col_x[-1] + w)

    for cx in col_x[1:-1]:
        msp.add_line((cx, cart_y0), (cx, cart_y1), dxfattribs={"layer": "CARTOUCHE"})

    y_title = cart_y1 - height * 0.25
    y_value = cart_y1 - height * 0.65
    text_h_title = height * 0.15
    text_h_value = height * 0.12

    cols_data = [
        ("Projet", proj_info.get('project_name', 'N/A')),
        ("Corps de meuble", proj_info.get('corps_meuble', 'N/A')),
        ("Désignation", panel_name),
        ("Quantité", str(proj_info.get('quantity', 1))),
        ("Date", proj_info.get('date', 'N/A')),
    ]

    for i, (title_txt, val_txt) in enumerate(cols_data):
        cx_mid = (col_x[i] + col_x[i + 1]) / 2.0
        _safe_add_text(
            msp,
            title_txt,
            {"height": text_h_title, "layer": "TEXTES"},
            (cx_mid, y_title),
            TextEntityAlignment.MIDDLE_CENTER,
        )
        _safe_add_text(
            msp,
            str(val_txt),
            {"height": text_h_value, "layer": "TEXTES"},
            (cx_mid, y_value),
            TextEntityAlignment.MIDDLE_CENTER,
        )

    leg_x0 = col_x[5]
    leg_x1 = col_x[6]
    leg_cx = (leg_x0 + leg_x1) / 2.0

    _safe_add_text(
        msp,
        "Légende",
        {"height": text_h_title, "layer": "TEXTES"},
        (leg_cx, cart_y1 - height * 0.12),
        TextEntityAlignment.MIDDLE_CENTER,
    )

    tri_size = height * 0.08
    y_tri1 = cart_y1 - height * 0.35
    tri1_pts = [
        (leg_cx, y_tri1 - tri_size),
        (leg_cx - tri_size * 0.6, y_tri1),
        (leg_cx + tri_size * 0.6, y_tri1),
        (leg_cx, y_tri1 - tri_size),
    ]
    msp.add_lwpolyline(tri1_pts, dxfattribs={"layer": "LEGENDE"})
    _safe_add_text(
        msp,
        "Corps inf.",
        {"height": height * 0.09, "layer": "TEXTES"},
        (leg_cx, y_tri1 - tri_size - height * 0.05),
        TextEntityAlignment.TOP_CENTER,
    )

    y_tri2 = cart_y1 - height * 0.70
    tri2_pts = [
        (leg_cx, y_tri2 - tri_size),
        (leg_cx - tri_size * 0.6, y_tri2),
        (leg_cx + tri_size * 0.6, y_tri2),
        (leg_cx, y_tri2 - tri_size),
    ]
    hatch = msp.add_hatch(color=256, dxfattribs={"layer": "LEGENDE"})
    hatch.paths.add_polyline_path(tri2_pts, is_closed=True)
    msp.add_lwpolyline(tri2_pts, dxfattribs={"layer": "LEGENDE"})
    _safe_add_text(
        msp,
        "Avant",
        {"height": height * 0.09, "layer": "TEXTES"},
        (leg_cx, y_tri2 - tri_size - height * 0.05),
        TextEntityAlignment.TOP_CENTER,
    )

def _add_tranche_dxf(msp, x_coords, y_coords, Tp, layer="TRANCHES"):
    if len(x_coords) != 4 or len(y_coords) != 4:
        return
    poly_pts = [(x_coords[i], y_coords[i]) for i in range(4)]
    poly_pts.append(poly_pts[0])
    msp.add_lwpolyline(poly_pts, dxfattribs={"layer": layer})

def _add_plan_to_dxf(msp, title, Lp, Wp, Tp, fh, t_long_h, t_cote_h, proj_for_plan, origin_x=0.0, origin_y=0.0, ch=None, has_rebate=False, center_cutout_props=None):
    Lp = float(Lp)
    Wp = float(Wp)
    Tp = float(Tp)
    
    # === ENSURE ALL HOLE LISTS ARE VALID (not None) ===
    fh = fh or []
    t_long_h = t_long_h or []
    t_cote_h = t_cote_h or []
    
    # === VALIDATION: Track all holes to ensure they match Streamlit ===
    holes_drawn = []  # [(x, y, diam), ...]
    validation_errors = []  # Collect any mismatches to log (non-blocking for now)

    margin = 400.0
    tranche_thick = max(Tp * 1.5, 30.0)

    cartouche_height = 150.0
    cartouche_width = max(Lp, 1200.0)

    cartouche_center_x = float(origin_x) + margin + cartouche_width / 2.0
    panel_center_x = cartouche_center_x

    x0 = panel_center_x - Lp / 2.0
    y0 = float(origin_y) + margin
    x1 = x0 + Lp
    y1 = y0 + Wp

    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], dxfattribs={"layer": "PANNEAU"})

    _safe_add_text(
        msp,
        f"FEUILLE D'USINAGE : {title}",
        {"height": 28, "layer": "TEXTES"},
        (x0, y1 + 200.0),
        TextEntityAlignment.BOTTOM_LEFT,
    )

    tb_y0 = y0 - 150.0
    tb_y1 = tb_y0 - tranche_thick
    _add_tranche_dxf(msp, [x0, x1, x1, x0], [tb_y0, tb_y0, tb_y1, tb_y1], Tp, layer="TRANCHES")

    th_y0 = y1 + 150.0
    th_y1 = th_y0 + tranche_thick
    _add_tranche_dxf(msp, [x0, x1, x1, x0], [th_y0, th_y0, th_y1, th_y1], Tp, layer="TRANCHES")

    tg_x0 = x0 - 150.0
    tg_x1 = tg_x0 - tranche_thick
    _add_tranche_dxf(msp, [tg_x0, tg_x1, tg_x1, tg_x0], [y0, y0, y1, y1], Tp, layer="TRANCHES")

    td_x0 = x1 + 150.0
    td_x1 = td_x0 + tranche_thick
    _add_tranche_dxf(msp, [td_x0, td_x1, td_x1, td_x0], [y0, y0, y1, y1], Tp, layer="TRANCHES")

    _add_linear_dimension_dxf(msp, base=(x1 + 80.0, tb_y0), p1=(x1, tb_y0), p2=(x1, tb_y1), angle=90, layer="COTES")
    _add_linear_dimension_dxf(msp, base=(tg_x0, y1 + 80.0), p1=(tg_x0, y1), p2=(tg_x1, y1), angle=0, layer="COTES")

    # Usinage poignée sur façade tiroir.
    if center_cutout_props:
        cut_type = str(center_cutout_props.get('type', 'integrated_cutout'))
        if cut_type == 'finger_pull':
            # Exigence métier: sur la FACE, afficher uniquement un trait à la hauteur de la découpe.
            offset_top = float(center_cutout_props.get('offset_top', 10.0))
            groove_depth = max(1.0, float(center_cutout_props.get('depth', 12.0)))
            groove_drop = max(5.0, float(center_cutout_props.get('drop', 35.0)))
            # Demi-cercle: rayon piloté par profondeur et moitié de la hauteur demandée.
            arc_radius_base = max(1.0, min(groove_depth, groove_drop / 2.0))
            # Abaisser la ligne sur la face pour augmenter l'amplitude visuelle.
            line_lowering = max(4.0, arc_radius_base * 0.8)
            y_line = max(y0 + 2.0 * arc_radius_base + 1.0, min(y1 - 2.0, y1 - offset_top - line_lowering))
            arc_radius = max(1.0, min(arc_radius_base, (y_line - y0 - 1.0) / 2.0))
            face_margin = max(5.0, min(25.0, Lp * 0.02))
            msp.add_line((x0 + face_margin, y_line), (x1 - face_margin, y_line), dxfattribs={"layer": "FEUILLURE", "color": 1})

            # Sur les 2 tranches latérales: demi-cercle + verticale longeant la face.
            profile_layer = {"layer": "FEUILLURE", "color": 1}

            for tx_inner, is_left in ((tg_x0, True), (td_x0, False)):
                y_arc_end = y_line - 2.0 * arc_radius

                # Entrée en demi-cercle (approximation en polyligne) puis segment vertical vers le haut.
                import math
                cx = tx_inner
                cy = y_line - arc_radius
                if is_left:
                    theta_start, theta_end = math.pi / 2.0, 3.0 * math.pi / 2.0
                else:
                    theta_start, theta_end = -math.pi / 2.0, math.pi / 2.0
                n_seg = 12
                curve_pts = []
                for i in range(n_seg + 1):
                    t = i / float(n_seg)
                    th = theta_start + (theta_end - theta_start) * t
                    px = cx + arc_radius * math.cos(th)
                    py = cy + arc_radius * math.sin(th)
                    curve_pts.append((px, py))
                msp.add_lwpolyline(curve_pts, dxfattribs=profile_layer)

                # Remontée verticale jusqu'au haut de la face pour conserver l'épaisseur demandée.
                msp.add_line((tx_inner, y_arc_end), (tx_inner, y1), dxfattribs=profile_layer)
        else:
            # Découpe poignée intégrée: rectangle centré sur la façade.
            cW = float(center_cutout_props.get('width', 150.0))
            cH = float(center_cutout_props.get('height', 40.0))
            cOff = float(center_cutout_props.get('offset_top', 10.0))
            cW = min(max(1.0, cW), max(1.0, Lp - 2.0))
            cH = min(max(1.0, cH), max(1.0, Wp - 2.0))
            cx0 = x0 + (Lp - cW) / 2.0
            cx1 = cx0 + cW
            cy1 = y0 + Wp - cOff
            cy0 = cy1 - cH
            cy0 = max(y0 + 1.0, cy0)
            cy1 = min(y1 - 1.0, cy1)
            msp.add_lwpolyline([(cx0, cy0), (cx1, cy0), (cx1, cy1), (cx0, cy1), (cx0, cy0)], dxfattribs={"layer": "FEUILLURE", "color": 1})

    annotated_diams = set()
    
    # === DRAW HOLES - THREE SEPARATE LOOPS FOR DIFFERENT HOLE TYPES ===
    # DO NOT merge fh with t_long_h and t_cote_h - they need different positioning!
    
    # Count holes for logging
    total_fh = len(fh or [])
    total_t_long_h = len(t_long_h or [])
    total_t_cote_h = len(t_cote_h or [])
    holes_drawn_count = 0
    
    # === FACE HOLES (fh) - drawn in XY on the front face ===
    for hole in fh or []:
        try:
            hx = x0 + float(hole.get('x', 0.0))
            hy = y0 + float(hole.get('y', 0.0))
            if hx < x0 or hx > x1 or hy < y0 or hy > y1:
                continue
            
            diam_str = hole.get('diam_str', '⌀5')
            diam = _extract_diameter_from_string(diam_str, default_value=5.0)
            radius = max(0.8, diam / 2.0)
            
            holes_drawn.append((round(hx - x0, 1), round(hy - y0, 1), diam))
            holes_drawn_count += 1
            
            msp.add_circle((hx, hy), radius=radius, dxfattribs={"layer": "TROUS"})
            if 'vis' in hole.get('type', '').lower():
                try:
                    hatch = msp.add_hatch(color=256, dxfattribs={"layer": "TROUS"})
                    hatch.paths.add_edge_path()
                    hatch.paths[-1].add_arc((hx, hy), radius=radius, start_angle=0, end_angle=360)
                except:
                    pass
            
            if diam_str not in annotated_diams:
                diam_autocad = _convert_diameter_string_autocad(diam_str)
                _safe_add_text(msp, diam_autocad, {"height": 12, "layer": "TEXTES"},
                    (hx + radius * 2.5, hy + radius * 2.5), TextEntityAlignment.BOTTOM_LEFT)
                annotated_diams.add(diam_str)
        except:
            continue
    
    # === SIDE EDGE HOLES (t_cote_h) - drawn on left and right edges ===
    for hole in t_cote_h or []:
        try:
            hy = y0 + float(hole.get('y', 0.0))
            if hy < y0 or hy > y1:
                continue
            
            diam_str = hole.get('diam_str', '⌀8')
            diam = _extract_diameter_from_string(diam_str, default_value=8.0)
            radius = max(0.8, diam / 2.0)
            tranche_offset = max(12.0, Tp / 2.0)
            
            # Draw on BOTH side edges (left and right)
            for tx in [tg_x0 + tranche_offset, td_x0 + tranche_offset]:
                holes_drawn.append((round(tx - x0, 1), round(hy - y0, 1), diam, "SIDE"))
                holes_drawn_count += 1
                
                msp.add_circle((tx, hy), radius=radius, dxfattribs={"layer": "TROUS"})
                if 'vis' in hole.get('type', '').lower():
                    try:
                        hatch = msp.add_hatch(color=256, dxfattribs={"layer": "TROUS"})
                        hatch.paths.add_edge_path()
                        hatch.paths[-1].add_arc((tx, hy), radius=radius, start_angle=0, end_angle=360)
                    except:
                        pass
            
            if diam_str not in annotated_diams:
                diam_autocad = _convert_diameter_string_autocad(diam_str)
                _safe_add_text(msp, diam_autocad, {"height": 10, "layer": "TEXTES"},
                    (tg_x0 + tranche_offset - 30.0, hy), TextEntityAlignment.MIDDLE_RIGHT)
                annotated_diams.add(diam_str)
        except:
            continue
    
    # === TOP/BOTTOM EDGE HOLES (t_long_h) - drawn on top and bottom edges ===
    for hole in t_long_h or []:
        try:
            # NOTE: For top/bottom edges, 'y' in the hole dict contains X position
            hole_x_coord = float(hole.get('y', 0.0))
            hx = x0 + hole_x_coord
            if hx < x0 or hx > x1:
                continue
            
            diam_str = hole.get('diam_str', '⌀8')
            diam = _extract_diameter_from_string(diam_str, default_value=8.0)
            radius = max(0.8, diam / 2.0)
            tranche_offset = max(12.0, Tp / 2.0)
            
            # Draw on BOTH top and bottom edges
            for ty in [tb_y0 - tranche_offset, th_y0 + tranche_offset]:
                holes_drawn.append((round(hx - x0, 1), round(ty - y0, 1), diam, "TOPBOTTOM"))
                holes_drawn_count += 1
                
                msp.add_circle((hx, ty), radius=radius, dxfattribs={"layer": "TROUS"})
                if 'vis' in hole.get('type', '').lower():
                    try:
                        hatch = msp.add_hatch(color=256, dxfattribs={"layer": "TROUS"})
                        hatch.paths.add_edge_path()
                        hatch.paths[-1].add_arc((hx, ty), radius=radius, start_angle=0, end_angle=360)
                    except:
                        pass
        except:
            continue
    
    if total_fh + total_t_long_h + total_t_cote_h > 0:
        import sys
        print(f"[DXF] {title}: drew {holes_drawn_count} holes (fh:{total_fh}, t_long_h:{total_t_long_h}, t_cote_h:{total_t_cote_h})", file=sys.stderr)



    _add_linear_dimension_dxf(msp, base=(x0, y0 - 80.0), p1=(x0, y0), p2=(x1, y0), angle=0, layer="COTES")
    _add_linear_dimension_dxf(msp, base=(x0 - 80.0, y0), p1=(x0, y0), p2=(x0, y1), angle=90, layer="COTES")

    hole_x_positions = sorted({round(float(h.get('x', 0.0)), 1) for h in (fh or []) if isinstance(h, dict) and 'x' in h and 0.0 <= h.get('x', -1) <= Lp})
    hole_y_positions = sorted({round(float(h.get('y', 0.0)), 1) for h in (fh or []) if isinstance(h, dict) and 'y' in h and 0.0 <= h.get('y', -1) <= Wp})

    for idx, hx in enumerate(hole_x_positions):
        base_y = y0 - 180.0 - (idx * 50.0)
        _add_linear_dimension_dxf(msp, base=(x0, base_y), p1=(x0, y0), p2=(x0 + hx, y0), angle=0, layer="COTES")

    for idx, hy in enumerate(hole_y_positions):
        base_x = x0 - 180.0 - (idx * 50.0)
        _add_linear_dimension_dxf(msp, base=(base_x, y0), p1=(x0, y0), p2=(x0, y0 + hy), angle=90, layer="COTES")

    for i in range(len(hole_x_positions) - 1):
        x_start = hole_x_positions[i]
        x_end = hole_x_positions[i + 1]
        base_y_inter = y0 - 280.0 - (i * 35.0)
        _add_linear_dimension_dxf(
            msp,
            base=(x0 + x_start, base_y_inter),
            p1=(x0 + x_start, y0),
            p2=(x0 + x_end, y0),
            angle=0,
            layer="COTES_INTER",
        )

    for i in range(len(hole_y_positions) - 1):
        y_start = hole_y_positions[i]
        y_end = hole_y_positions[i + 1]
        base_x_inter = x0 - 280.0 - (i * 35.0)
        _add_linear_dimension_dxf(
            msp,
            base=(base_x_inter, y0 + y_start),
            p1=(x0, y0 + y_start),
            p2=(x0, y0 + y_end),
            angle=90,
            layer="COTES_INTER",
        )

    # === TRIANGLES: Match drawning_interface.py logic exactly ===
    # Triangle size proportional to panel area
    panel_area = Lp * Wp
    tri_size = max(15.0, min(50.0, (panel_area / 50.0) ** 0.5))
    tri_offset = 100.0  # Distance from panel edge to make triangles visible
    
    def _draw_solid_triangle_dxf(msp, cx, cy, size, orientation='down'):
        """Draw a filled triangle (using LWPOLYLINE + HATCH)"""
        if orientation == 'down':
            # Apex at bottom, base at top
            pts = [
                (cx, cy - size),
                (cx - size * 0.6, cy),
                (cx + size * 0.6, cy),
            ]
        elif orientation == 'up':
            # Apex at top, base at bottom
            pts = [
                (cx, cy + size),
                (cx - size * 0.6, cy),
                (cx + size * 0.6, cy),
            ]
        elif orientation == 'left':
            # Apex at left, base at right
            pts = [
                (cx - size, cy),
                (cx, cy - size * 0.6),
                (cx, cy + size * 0.6),
            ]
        else:  # 'right'
            # Apex at right, base at left
            pts = [
                (cx + size, cy),
                (cx, cy - size * 0.6),
                (cx, cy + size * 0.6),
            ]
        
        msp.add_lwpolyline(pts, dxfattribs={"layer": "REPERAGE"}, close=True)
        try:
            hatch = msp.add_hatch(color=256, dxfattribs={"layer": "REPERAGE"})
            hatch.paths.add_polyline_path(pts, is_closed=True)
        except Exception:
            pass
    
    def _draw_empty_triangle_dxf(msp, cx, cy, size, orientation='down'):
        """Draw an empty triangle (outline only, no fill)"""
        if orientation == 'down':
            pts = [
                (cx, cy - size),
                (cx - size * 0.6, cy),
                (cx + size * 0.6, cy),
            ]
        elif orientation == 'up':
            pts = [
                (cx, cy + size),
                (cx - size * 0.6, cy),
                (cx + size * 0.6, cy),
            ]
        elif orientation == 'left':
            pts = [
                (cx - size, cy),
                (cx, cy - size * 0.6),
                (cx, cy + size * 0.6),
            ]
        else:  # 'right'
            pts = [
                (cx + size, cy),
                (cx, cy - size * 0.6),
                (cx, cy + size * 0.6),
            ]
        
        # Outline only with proper closure, no hatch
        msp.add_lwpolyline(pts, dxfattribs={"layer": "REPERAGE"}, close=True)
    
    # Determine triangle configuration based on title (matches drawing_interface.py)
    title_lower = title.lower().replace('é', 'e').replace('è', 'e').replace('ê', 'e')
    
    if "traverse bas" in title_lower or "traverse haut" in title_lower:
        # Black triangle at bottom center
        _draw_solid_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "montant droit" in title_lower or ("montant" in title_lower and "1/2" in title_lower):
        # Empty triangle at bottom center + Black triangle at right middle
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
        _draw_solid_triangle_dxf(msp, x1 + tri_offset, Wp / 2.0 + y0, tri_size, orientation='right')
    
    elif "montant gauche" in title_lower or ("montant" in title_lower and "2/2" in title_lower):
        # Empty triangle at bottom center + Black triangle at left middle
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
        _draw_solid_triangle_dxf(msp, x0 - tri_offset, Wp / 2.0 + y0, tri_size, orientation='left')
    
    elif "etagere" in title_lower or "étagère" in title_lower:
        # Black triangle at bottom center for all shelves
        _draw_solid_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "fond" in title_lower and "tiroir" in title_lower:
        # Black triangle at bottom center for drawer bottom
        _draw_solid_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "face" in title_lower and "tiroir" in title_lower:
        # Empty triangle at bottom center for drawer face
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "dos" in title_lower and "tiroir" in title_lower:
        # Empty triangle at bottom center for drawer back
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "porte" in title_lower:
        # Empty triangle at bottom center for door
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    elif "panneau arrière" in title_lower or ("fond" in title_lower and "tiroir" not in title_lower):
        # Empty triangle at bottom center for rear panel
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')
    
    else:
        # Default: empty triangle at bottom if uncertain
        _draw_empty_triangle_dxf(msp, Lp / 2.0 + x0, y0 - tri_offset, tri_size, orientation='down')

    if has_rebate:
        REBATE_WIDTH = 38.0
        msp.add_line((x0 + REBATE_WIDTH, y0), (x0 + REBATE_WIDTH, y1), dxfattribs={"layer": "FEUILLURE", "color": 1})
        msp.add_line((x1 - REBATE_WIDTH, y0), (x1 - REBATE_WIDTH, y1), dxfattribs={"layer": "FEUILLURE", "color": 1})
        _add_linear_dimension_dxf(msp, base=(x0, y1 + 50.0), p1=(x0, y1), p2=(x0 + REBATE_WIDTH, y1), angle=0, layer="COTES")
        _add_linear_dimension_dxf(msp, base=(x1 - REBATE_WIDTH, y1 + 50.0), p1=(x1 - REBATE_WIDTH, y1), p2=(x1, y1), angle=0, layer="COTES")
        trait_tranche_gauche_x = tg_x0 - tranche_thick / 2.0
        trait_tranche_droite_x = td_x0 + tranche_thick / 2.0
        msp.add_line((trait_tranche_gauche_x, y0), (trait_tranche_gauche_x, y1), dxfattribs={"layer": "FEUILLURE", "color": 1})
        msp.add_line((trait_tranche_droite_x, y0), (trait_tranche_droite_x, y1), dxfattribs={"layer": "FEUILLURE", "color": 1})
        _add_linear_dimension_dxf(msp, base=(tg_x1, y1 + 90.0), p1=(tg_x1, y1), p2=(trait_tranche_gauche_x, y1), angle=0, layer="COTES")
        _add_linear_dimension_dxf(msp, base=(trait_tranche_gauche_x, y1 + 90.0), p1=(trait_tranche_gauche_x, y1), p2=(tg_x0, y1), angle=0, layer="COTES")

    cartouche_y_top = tb_y1 - 100.0
    cartouche_x_start = panel_center_x - cartouche_width / 2.0
    _add_cartouche_dxf(msp, cartouche_x_start, cartouche_y_top, cartouche_width, cartouche_height, proj_for_plan, title)
    
    # === VALIDATION SUMMARY ===
    # Log validation errors if any (non-blocking)
    if validation_errors:
        import sys
        for err in validation_errors:
            print(f"[DXF VALIDATION] {title}: {err}", file=sys.stderr)

    # Bounding box large pour cadrer correctement la feuille dans son layout dédié.
    sheet_min_x = min(tg_x1, x0 - 300.0, cartouche_x_start - 50.0)
    sheet_max_x = max(td_x1, x1 + 300.0, cartouche_x_start + cartouche_width + 50.0)
    sheet_min_y = min(tb_y1 - 350.0, cartouche_y_top - cartouche_height - 50.0, y0 - 350.0)
    sheet_max_y = max(y1 + 320.0, th_y1 + 200.0)
    return (sheet_min_x, sheet_min_y, sheet_max_x, sheet_max_y)


def get_automatic_edge_banding_export(part_name):
    name = part_name.lower()
    if "etagère" in name or "etagere" in name: return True, False, False, False
    elif "fond" in name or "dos" in name:
        if "façade" in name or "face" in name: return True, True, True, True
        return False, False, False, False
    elif "traverse" in name: return True, True, False, False
    else: return True, True, True, True

def generate_stacked_html_plans(cabinets_to_process, indices_to_process, output_format='html', selected_materials=None):
    """
    Génère un fichier HTML ou PDF contenant toutes les feuilles d'usinage du projet.
    
    Args:
        cabinets_to_process: Liste des caissons à traiter
        indices_to_process: Liste des indices des caissons
        output_format: 'html' pour HTML interactif, 'pdf' pour images statiques (base64), 'figures' pour liste de figures Plotly
    
    Returns:
        Pour 'html': (html_bytes, success)
        Pour 'pdf': (html_with_images_bytes, success)
    """
    import traceback

    dxf_doc = None
    dxf_msp = None
    dxf_plan_index = 0
    dxf_plan_layout_specs = []
    rendered_plan_titles = []
    figures_out = []
    selected_materials_set = {
        str(mat).strip()
        for mat in (selected_materials or [])
        if str(mat).strip()
    }

    def _is_material_allowed(material_name):
        if not selected_materials_set:
            return True
        return str(material_name or "").strip() in selected_materials_set

    if output_format == 'dxf':
        if not EZDXF_AVAILABLE:
            return b"Le module 'ezdxf' est requis pour l'export AutoCAD.", False
        dxf_doc = ezdxf.new('R2010')
        dxf_doc.units = 4
        for layer_name, color in [
            ('PANNEAU', 7),
            ('TROUS', 2),
            ('COTES', 3),
            ('COTES_INTER', 1),
            ('TEXTES', 7),
            ('TRANCHES', 252),
            ('CARTOUCHE', 7),
            ('LEGENDE', 7),
            ('REPERAGE', 1),
            ('FEUILLURE', 1),
        ]:
            if layer_name not in dxf_doc.layers:
                dxf_doc.layers.new(layer_name, dxfattribs={'color': color})
        try:
            if 'COTATIONS_PRO' not in dxf_doc.dimstyles:
                dimstyle = dxf_doc.dimstyles.new('COTATIONS_PRO')
                # Configuration pour dimensions éditables dans AutoCAD
                dimstyle.dxf.dimblk = 'CLOSEDBLANK'  # Arrow type: closed blank
                dimstyle.dxf.dimblk1 = 'CLOSEDBLANK'  # First arrow
                dimstyle.dxf.dimblk2 = 'CLOSEDBLANK'  # Second arrow
                dimstyle.dxf.dimasz = 3.0  # Arrow size
                dimstyle.dxf.dimtxt = 10.0  # Text height
                dimstyle.dxf.dimexe = 1.5  # Extension beyond dimension line
                dimstyle.dxf.dimexo = 1.0  # Extension origin offset
                dimstyle.dxf.dimgap = 2.0  # Gap between text and dimension line
                dimstyle.dxf.dimtad = 1  # Text position: above
                dimstyle.dxf.dimdec = 1  # Decimal places
                dimstyle.dxf.dimzin = 8  # Suppress trailing zeros
                dimstyle.dxf.dimtix = 0  # Text inside extension lines: no
                dimstyle.dxf.dimdli = 3.75  # Baseline spacing for stacked dims
                dimstyle.dxf.dimclrd = 3  # Dimension line color: green
                dimstyle.dxf.dimclre = 3  # Extension line color: green
                dimstyle.dxf.dimclrt = 3  # Text color: green
        except Exception as e:
            import sys
            print(f"[DXF] Warning: Could not create COTATIONS_PRO dimstyle: {e}", file=sys.stderr)
        dxf_msp = dxf_doc.modelspace()

    def _add_plan(title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut, has_rebate, proj_for_plan):
        nonlocal dxf_plan_index
        if output_format == 'dxf':
            bbox = _add_plan_to_dxf(
                dxf_msp,
                title,
                Lp,
                Wp,
                Tp,
                fh,
                t_long_h,
                t_cote_h,
                proj_for_plan,
                origin_x=(dxf_plan_index * 2600.0),
                origin_y=0.0,
                ch=ch,
                has_rebate=has_rebate,
                center_cutout_props=cut,
            )
            dxf_plan_layout_specs.append((str(title), bbox))
            dxf_plan_index += 1
            return
        return draw_machining_view_pro_final(
            title,
            Lp,
            Wp,
            Tp,
            st.session_state.unit_select,
            proj_for_plan,
            ch,
            fh,
            t_long_h,
            t_cote_h,
            cut,
            has_rebate,
        )

    # CSS STRICT POUR A4 PAYSAGE
    if output_format == 'pdf':
        # CSS pour PDF avec images
        full_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>Dossier Technique</title>
    <style>
        @page { size: A4 landscape; margin: 0mm; }
        body { margin: 0; padding: 0; background-color: white; font-family: Arial, sans-serif; }
        .page-container {
            width: 297mm;
            height: 209mm; 
            background: white;
            margin: 0mm auto;
            page-break-after: always;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }
        .page-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
    </style>
</head>
<body>
"""
    else:
        # CSS pour HTML interactif
        full_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>Dossier Technique</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        @page { size: A4 landscape; margin: 0mm; }
        body { margin: 0; padding: 0; background-color: #eee; font-family: Arial, sans-serif; }
        .page-container {
            width: 297mm;
            height: 209mm; 
            background: white;
            margin: 10mm auto;
            box-shadow: 0 0 10px rgba(0,0,0,0.2);
            page-break-after: always;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }
        @media print {
            body { background: white; }
            .page-container {
                margin: 0;
                box-shadow: none;
                width: 100%;
                height: 100vh;
            }
            .no-print { display: none; }
        }
    </style>
</head>
<body>
<div class="no-print" style="text-align:center; padding:20px;">
    <h1>Dossier Technique</h1>
    <p>Pour imprimer : CTRL+P > Destination "Enregistrer au format PDF" > Mise en page "Paysage" > Marges "Aucune"</p>
</div>
"""
    
    try:
        plan_quantities = {}  # Initialiser au niveau de la fonction pour éviter UnboundLocalError
        for i, cab in enumerate(cabinets_to_process):
            cab_idx = indices_to_process[i]
            dims = cab['dims']
            
            L_raw = float(dims['L_raw'])
            W_raw = float(dims['W_raw'])
            H_raw = float(dims['H_raw'])
            t_lr = float(dims['t_lr_raw'])
            t_fb = float(dims['t_fb_raw'])
            t_tb = float(dims['t_tb_raw'])
            
            h_side = H_raw
            L_trav = L_raw - 2 * t_lr
            W_mont = W_raw
            W_back = L_raw - 2.0
            H_back = H_raw - 2.0
            
            ys_vis, ys_dowel = calculate_hole_positions(W_raw)
            holes_mg, holes_md = [], []
            tranche_holes_mg, tranche_holes_md = [], []
            
            # --- STRUCTURE : Trous d'assemblage montant/traverse ---
            # Ces trous sont uniquement sur la FACE des montants principaux.
            # Pas de trous en tranche sur Mg/Md.
            for x in ys_vis:
                holes_mg.append({'type':'vis','x':x,'y':t_tb/2,'diam_str':"⌀3"})
                holes_mg.append({'type':'vis','x':x,'y':h_side-t_tb/2,'diam_str':"⌀3"})
                holes_md.append({'type':'vis','x':x,'y':t_tb/2,'diam_str':"⌀3"})
                holes_md.append({'type':'vis','x':x,'y':h_side-t_tb/2,'diam_str':"⌀3"})
            for x in ys_dowel:
                holes_mg.append({'type':'tourillon','x':x,'y':t_tb/2,'diam_str':"⌀8/22"})
                holes_mg.append({'type':'tourillon','x':x,'y':h_side-t_tb/2,'diam_str':"⌀8/22"})
                holes_md.append({'type':'tourillon','x':x,'y':t_tb/2,'diam_str':"⌀8/22"})
                holes_md.append({'type':'tourillon','x':x,'y':h_side-t_tb/2,'diam_str':"⌀8/22"})
            
            # Même règle que dans 2.py : pour que les trous d'étagères fixes
            # soient parfaitement alignés avec les trous d'assemblage
            # montant/traverse, on réutilise EXACTEMENT la même trame X
            # (ys_vis / ys_dowel) calculée pour W_raw.
            ys_vis_sf, ys_dowel_sf = ys_vis, ys_dowel
            fixed_shelf_tr_draw = {}
            
            # Calculer les zones pour les éléments (MÊME LOGIQUE QUE 2.PY)
            all_zones_2d = calculate_all_zones_2d(cab)
            zones = calculate_zones_from_dividers(cab)  # Pour compatibilité
            
            # Dictionnaires pour stocker les trous d'assemblage par montant secondaire
            divider_element_holes_left = {}
            divider_element_holes_right = {}
            if 'vertical_dividers' in cab and cab['vertical_dividers']:
                divider_element_holes_left = {i: [] for i in range(len(cab['vertical_dividers']))}
                divider_element_holes_right = {i: [] for i in range(len(cab['vertical_dividers']))}
            
            plans = []
            shelf_groups = {}  # Dictionnaire pour regrouper les étagères identiques
            # plan_quantities est initialisé au niveau de la fonction (ligne 119)
            
            # --- ETAGERES (MÊME LOGIQUE QUE 2.PY) ---
            if 'shelves' in cab:
                for s_idx, s in enumerate(cab['shelves']):
                    s_type = s.get('shelf_type', 'mobile')
                    s_th = float(s.get('thickness', 19.0))
                    zone_id = s.get('zone_id', None)
                    
                    if s_type == 'fixe':
                        L_shelf = float(L_raw - (2 * t_lr))
                        yc_val = t_tb + s['height'] + s_th/2.0 
                        
                        # Utiliser les LIMITES DE LA ZONE pour détecter les montants (MÊME LOGIQUE QUE 2.PY)
                        zone_x_min = None
                        zone_x_max = None
                        
                        if s.get('stored_zone_coords'):
                            zone_x_min = s['stored_zone_coords']['x_min']
                            zone_x_max = s['stored_zone_coords']['x_max']
                        elif zone_id is not None and zone_id < len(all_zones_2d):
                            zone = all_zones_2d[zone_id]
                            zone_x_min = zone['x_min']
                            zone_x_max = zone['x_max']
                        
                        if zone_x_min is not None and zone_x_max is not None:
                            # Montant gauche principal si la zone commence au montant gauche
                            if abs(zone_x_min - t_lr) < 1.0:
                                for x in ys_vis_sf: holes_mg.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                for x in ys_dowel_sf: holes_mg.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                            # Montant droit principal si la zone se termine au montant droit
                            if abs(zone_x_max - (L_raw - t_lr)) < 1.0:
                                for x in ys_vis_sf: holes_md.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                for x in ys_dowel_sf: holes_md.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                            
                            # Montants secondaires qui touchent cette étagère fixe
                            for div_idx, div in enumerate(cab['vertical_dividers']):
                                div_x = div['position_x']
                                div_th = div.get('thickness', 19.0)
                                div_left_edge = div_x - div_th / 2.0
                                div_right_edge = div_x + div_th / 2.0
                                
                                touches_left_face = abs(zone_x_max - div_left_edge) < 1.0
                                touches_right_face = abs(zone_x_min - div_right_edge) < 1.0
                                
                                if touches_left_face:
                                    for x in ys_vis_sf:
                                        divider_element_holes_left[div_idx].append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                    for x in ys_dowel_sf:
                                        divider_element_holes_left[div_idx].append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                                if touches_right_face:
                                    for x in ys_vis_sf:
                                        divider_element_holes_right[div_idx].append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                    for x in ys_dowel_sf:
                                        divider_element_holes_right[div_idx].append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                        else:
                            # Étagère sur tout le caisson : trous sur les deux montants principaux
                            for x in ys_vis_sf: holes_mg.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                            for x in ys_dowel_sf: holes_mg.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                            for x in ys_vis_sf: holes_md.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                            for x in ys_dowel_sf: holes_md.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                        
                        # Trous de liaison dans la tranche de l'étagère fixe
                        # Regle: uniquement des tourillons, avec extremites a 25 mm de chaque bord
                        # sur la profondeur reelle de l'etagere (W_raw), comme les traverses.
                        tr = []
                        shelf_width = float(W_raw)
                        _, ys_dowel_shelf = calculate_hole_positions(shelf_width)
                        for y_pos in ys_dowel_shelf:
                            tr.append({'type':'tourillon','x':s_th/2,'y':y_pos,'diam_str':"⌀8/22"})
                        fixed_shelf_tr_draw[s_idx] = tr
                    else:
                        # Étagère mobile : les taquets sont déjà calculés, on les filtre par zone
                        L_shelf = float(L_raw - (2 * t_lr) - 2.0)
                        mobile_holes = get_mobile_shelf_holes(h_side, t_tb, s, W_mont)
                        
                        zone_x_min = None
                        zone_x_max = None
                        
                        if s.get('stored_zone_coords'):
                            zone_x_min = s['stored_zone_coords']['x_min']
                            zone_x_max = s['stored_zone_coords']['x_max']
                        elif zone_id is not None and zone_id < len(all_zones_2d):
                            zone = all_zones_2d[zone_id]
                            zone_x_min = zone['x_min']
                            zone_x_max = zone['x_max']
                        
                        if zone_x_min is not None and zone_x_max is not None:
                            # Montant gauche si la zone commence au montant gauche
                            if abs(zone_x_min - t_lr) < 1.0:
                                holes_mg.extend(mobile_holes)
                            # Montant droit si la zone se termine au montant droit
                            if abs(zone_x_max - (L_raw - t_lr)) < 1.0:
                                holes_md.extend(mobile_holes)
                        else:
                            # Étagère mobile sur tout le caisson : trous sur les deux montants principaux
                            holes_mg.extend(mobile_holes)
                            holes_md.extend(mobile_holes)
                        
                        # Montants secondaires qui touchent cette étagère
                        if zone_x_min is not None and zone_x_max is not None:
                            for div_idx, div in enumerate(cab['vertical_dividers']):
                                div_x = div['position_x']
                                div_th = div.get('thickness', 19.0)
                                div_left_edge = div_x - div_th / 2.0
                                div_right_edge = div_x + div_th / 2.0
                                
                                touches_left_face = abs(zone_x_max - div_left_edge) < 1.0
                                touches_right_face = abs(zone_x_min - div_right_edge) < 1.0
                                
                                if touches_left_face:
                                    divider_element_holes_left[div_idx].extend(mobile_holes)
                                if touches_right_face:
                                    divider_element_holes_right[div_idx].extend(mobile_holes)
                        # Pour les étagères mobiles, pas de trous dans la tranche
                        fixed_shelf_tr_draw[s_idx] = []
                        # Pour les étagères mobiles, L_shelf est déjà défini à la ligne 240
                    
                    # Règle demandée: étagère = traverse (même profondeur).
                    W_shelf = float(W_raw)
                    c_shelf = {"Chant Avant":True, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":False}
                    th_shelf = fixed_shelf_tr_draw.get(s_idx, [])
                    shelf_material = str(s.get('material', 'Matière Étagère'))
                    # Créer une clé unique pour regrouper les étagères identiques
                    # Utiliser les dimensions et les trous pour identifier les étagères identiques
                    shelf_key = (round(L_shelf, 1), round(W_shelf, 1), round(s_th, 1), shelf_material, s_type, tuple(sorted([(round(h['x'], 1), round(h['y'], 1), h['type'], h['diam_str']) for h in th_shelf])))
                    shelf_title = f"Etagère {s_type.capitalize()} (C{cab_idx})"
                    # Stocker les informations de l'étagère pour regroupement ultérieur
                    if shelf_key not in shelf_groups:
                        shelf_groups[shelf_key] = {
                            'title': shelf_title,
                            'L': L_shelf,
                            'W': W_shelf,
                            'T': s_th,
                            'ch': c_shelf,
                            'fh': [],
                            't_long_h': [],
                            't_cote_h': th_shelf,
                            'cut': None,
                            'material': shelf_material,
                            'quantity': 0
                        }
                    shelf_groups[shelf_key]['quantity'] += 1
            
            # Ajouter les étagères regroupées à la liste des plans
            for shelf_key, shelf_data in shelf_groups.items():
                if output_format == 'dxf':
                    # DXF: 1 élément = 1 feuille, même pour des étagères identiques.
                    for idx_instance in range(int(shelf_data['quantity'])):
                        shelf_title_instance = f"{shelf_data['title']} #{idx_instance + 1}"
                        plans.append((shelf_title_instance, shelf_data['L'], shelf_data['W'], shelf_data['T'], shelf_data['ch'], shelf_data['fh'], shelf_data['t_long_h'], shelf_data['t_cote_h'], shelf_data['cut'], False, shelf_data['material']))
                        plan_quantities[shelf_title_instance] = 1
                else:
                    shelf_title_with_qty = shelf_data['title']
                    if shelf_data['quantity'] > 1:
                        shelf_title_with_qty = f"{shelf_data['title']} (x{shelf_data['quantity']})"
                    plans.append((shelf_title_with_qty, shelf_data['L'], shelf_data['W'], shelf_data['T'], shelf_data['ch'], shelf_data['fh'], shelf_data['t_long_h'], shelf_data['t_cote_h'], shelf_data['cut'], False, shelf_data['material']))
                    plan_quantities[shelf_title_with_qty] = shelf_data['quantity']

            # --- PORTE (MÊME LOGIQUE QUE 2.PY) ---
            if cab['door_props']['has_door']:
                # Utiliser les positions personnalisées si le mode est 'custom'
                door_props = cab['door_props']
                if door_props.get('hinge_mode') == 'custom' and door_props.get('custom_hinge_positions'):
                    yh = get_hinge_y_positions(h_side, custom_positions=door_props['custom_hinge_positions'])
                else:
                    yh = get_hinge_y_positions(h_side)
                door_type = cab['door_props'].get('door_type', 'single')
                if door_type == 'double':
                    # Porte double : trous sur les deux montants
                    # Porte gauche : trous sur montant gauche (x=20.0 et x=52.0 depuis le bord gauche)
                    # Porte droite : trous sur montant droit (x=20.0 et x=52.0 depuis le bord droit, donc W_mont-20.0 et W_mont-52.0)
                    for y in yh:
                        # Trous pour la porte gauche sur montant gauche
                        holes_mg.append({'type':'vis','x':20.0,'y':y,'diam_str':"⌀5/11.5"})
                        holes_mg.append({'type':'vis','x':52.0,'y':y,'diam_str':"⌀5/11.5"})
                        # Trous pour la porte droite sur montant droit
                        holes_md.append({'type':'vis','x':W_mont-20.0,'y':y,'diam_str':"⌀5/11.5"})
                        holes_md.append({'type':'vis','x':W_mont-52.0,'y':y,'diam_str':"⌀5/11.5"})
                else:
                    # Porte simple : trous selon le sens d'ouverture
                    for y in yh:
                        if cab['door_props']['door_opening']=='left':
                            holes_mg.append({'type':'vis','x':20.0,'y':y,'diam_str':"⌀5/11.5"})
                            holes_mg.append({'type':'vis','x':52.0,'y':y,'diam_str':"⌀5/11.5"})
                        else:
                            holes_md.append({'type':'vis','x':20.0,'y':y,'diam_str':"⌀5/11.5"})
                            holes_md.append({'type':'vis','x':52.0,'y':y,'diam_str':"⌀5/11.5"})

            # --- TIROIRS : trous de coulisses sur montants principaux et secondaires ---
            if 'drawers' in cab and cab['drawers']:
                for drawer_idx, drp in enumerate(cab['drawers']):
                    drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                    tech_type = drp.get('drawer_tech_type', 'K')
                    y_slide = t_tb + 33.0 + drp.get('drawer_bottom_offset', 0.0)
                    drawer_zone_id = drp.get('zone_id', None)

                    # Calcul des positions X des trous de coulisse selon la profondeur du caisson
                    x_slide_holes = [19, 37]
                    wr = W_raw
                    if wr > 643:
                        x_slide_holes = [19, 37, 133, 261, 293, 389, 421, 549]
                    else:
                        if tech_type == 'N':
                            if 403 < wr < 452: x_slide_holes = [19, 37, 133, 165, 229, 325]
                            elif 453 < wr < 502: x_slide_holes = [19, 37, 133, 165, 261, 357]
                            elif 503 < wr < 552: x_slide_holes = [19, 37, 133, 261, 293, 453]
                            elif 553 < wr < 602: x_slide_holes = [19, 37, 133, 261, 293, 453]
                        if not x_slide_holes or x_slide_holes == [19, 37]:
                            if wr <= 153: x_slide_holes = [19, 37]
                            elif 153 < wr < 302: x_slide_holes = [19, 37, 133]
                            elif 273 < wr < 302: x_slide_holes = [19, 37, 133, 261]
                            elif 303 < wr < 352: x_slide_holes = [19, 37, 133, 165, 261]
                            elif 353 < wr < 402: x_slide_holes = [19, 37, 133, 165, 325]
                            elif 403 < wr < 452: x_slide_holes = [19, 37, 133, 165, 229, 325]
                            elif 453 < wr < 502: x_slide_holes = [19, 37, 133, 165, 261, 357]
                            elif 503 < wr < 552: x_slide_holes = [19, 37, 133, 261, 293, 453]
                            elif 553 < wr < 602: x_slide_holes = [19, 37, 133, 261, 293, 453]
                            elif 603 < wr < 652: x_slide_holes = [19, 37, 133, 261, 293, 325, 357, 517]

                    # Tiroir à l'anglaise: perçages décalés de 40 mm depuis le bord.
                    # (doit être APRÈS tout le calcul des positions, quel que soit le cas de profondeur)
                    x_slide_holes = _apply_anglaise_slide_offset(drawer_system, x_slide_holes, W_mont)

                    # Utiliser les LIMITES DE LA ZONE pour détecter les montants adjacents
                    zone_x_min = None
                    zone_x_max = None

                    if drawer_zone_id is not None and drawer_zone_id < len(all_zones_2d):
                        zone = all_zones_2d[drawer_zone_id]
                        zone_x_min = zone['x_min']
                        zone_x_max = zone['x_max']

                    if zone_x_min is not None and zone_x_max is not None:
                        # Montant gauche principal si la zone borde le montant gauche
                        if abs(zone_x_min - t_lr) < 1.0:
                            for x_s in x_slide_holes:
                                holes_mg.append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                        # Montant droit principal si la zone borde le montant droit
                        if abs(zone_x_max - (L_raw - t_lr)) < 1.0:
                            for x_s in x_slide_holes:
                                holes_md.append({'type': 'vis', 'x': W_mont - x_s, 'y': y_slide, 'diam_str': "⌀5/12"})

                        # Montants secondaires mitoyens à ce tiroir (gauche ET droite de la zone)
                        if 'vertical_dividers' in cab:
                            for div_idx, div in enumerate(cab['vertical_dividers']):
                                div_x = div['position_x']
                                div_th = div.get('thickness', 19.0)
                                div_left_edge = div_x - div_th / 2.0
                                div_right_edge = div_x + div_th / 2.0

                                # Face droite du montant secondaire = bord gauche de la zone suivante
                                touches_left_face = abs(zone_x_max - div_left_edge) < 1.0
                                # Face gauche du montant secondaire = bord droit de la zone précédente
                                touches_right_face = abs(zone_x_min - div_right_edge) < 1.0

                                if touches_left_face:
                                    for x_s in x_slide_holes:
                                        divider_element_holes_left[div_idx].append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                                if touches_right_face:
                                    for x_s in x_slide_holes:
                                        divider_element_holes_right[div_idx].append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                    else:
                        # Tiroir sur tout le caisson : trous sur les deux montants principaux
                        for x_s in x_slide_holes:
                            holes_mg.append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                            holes_md.append({'type': 'vis', 'x': W_mont - x_s, 'y': y_slide, 'diam_str': "⌀5/12"})

            # Les tourillons de traverse vont TOUJOURS sur tranche_cote (gauche/droite),
            # jamais sur tranche_longue (haut/bas = chants Avant/Arrière).
            tholes_longue = []
            tholes_cote = [{'type':'tourillon','x':t_tb/2,'y':y,'diam_str':"⌀8/22"} for y in ys_dowel]
            
            # Ajouter les trous sur les FACES des traverses (gauche et droite) pour les montants secondaires
            traverse_face_holes_left = []
            traverse_face_holes_right = []
            if 'vertical_dividers' in cab and cab['vertical_dividers']:
                for div in cab['vertical_dividers']:
                    div_x = div['position_x']
                    # IMPORTANT : Les montants secondaires ont une profondeur de W - t_fb
                    div_traverse_face_holes = get_traverse_face_holes_for_divider(L_trav, div_x, t_lr, t_tb, W_raw, t_fb)
                    traverse_face_holes_left.extend(div_traverse_face_holes)
                    traverse_face_holes_right.extend(div_traverse_face_holes)
            
            # Panneau arrière: aucun trou d'usinage (règle métier)
            holes_fond = []

            proj = {"project_name": st.session_state.project_name, "corps_meuble": f"Caisson {cab_idx}", "quantity": 1, "date": datetime.date.today().strftime("%d/%m/%Y")}
            # plan_quantities est déjà initialisé avant la boucle des étagères (ligne 171)
            
            cav_t, car_t, cg_t, cd_t = get_automatic_edge_banding_export("Traverse")
            c_trav = {"Chant Avant":cav_t, "Chant Arrière":car_t, "Chant Gauche":cg_t, "Chant Droit":cd_t}
            cav_m, car_m, cg_m, cd_m = get_automatic_edge_banding_export("Montant")
            c_mont = {"Chant Avant":cav_m, "Chant Arrière":car_m, "Chant Gauche":cg_m, "Chant Droit":cd_m}
            c_fond = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":False}
            
            # Récupérer les préférences des éléments de base (par défaut tous activés)
            base_el = cab.get('base_elements', {
                'has_back_panel': True,
                'has_left_upright': True,
                'has_right_upright': True,
                'has_bottom_traverse': True,
                'has_top_traverse': True
            })
            
            if base_el.get('has_bottom_traverse', True):
                plans.append(("Traverse Bas (Tb)", L_trav, W_mont, t_tb, c_trav, traverse_face_holes_left, tholes_longue, tholes_cote, None, False, cab.get('material_body', 'Matière Corps')))
            if base_el.get('has_top_traverse', True):
                plans.append(("Traverse Haut (Th)", L_trav, W_mont, t_tb, c_trav, traverse_face_holes_right, tholes_longue, tholes_cote, None, False, cab.get('material_body', 'Matière Corps')))
            if base_el.get('has_left_upright', True):
                plans.append(("Montant Gauche (Mg)", W_mont, h_side, t_lr, c_mont, holes_mg, [], tranche_holes_mg, None, False, cab.get('material_body', 'Matière Corps')))
            if base_el.get('has_right_upright', True):
                plans.append(("Montant Droit (Md)", W_mont, h_side, t_lr, c_mont, holes_md, [], tranche_holes_md, None, False, cab.get('material_body', 'Matière Corps')))
            if base_el.get('has_back_panel', True):
                plans.append(("Panneau Arrière (F)", W_back, H_back, t_fb, c_fond, holes_fond, [], [], None, False, cab.get('material_body', 'Matière Corps')))

            # --- TIROIRS (GROUPÉS PAR DIMENSIONS IDENTIQUES) ---
            if 'drawers' in cab and cab['drawers']:
                # Fonction helper pour calculer la signature d'un tiroir (dimensions + usinages)
                def get_drawer_signature_export(drp, all_zones_2d, L_raw, W_raw, t_fb, t_lr):
                    drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                    drawer_zone_id = drp.get('zone_id', None)
                    gap_mm = drp.get('drawer_gap', 2.0)
                    
                    if drawer_zone_id is not None and drawer_zone_id < len(all_zones_2d):
                        zone = all_zones_2d[drawer_zone_id]
                        zone_width_total = zone['x_max'] - zone['x_min']
                        zone_width_interior = zone_width_total - (2 * t_lr)
                    else:
                        zone_width_total = L_raw
                        zone_width_interior = L_raw - (2 * t_lr)
                    
                    dr_H = drp.get('drawer_face_H_raw', 150.0)
                    tech_type = drp.get('drawer_tech_type', 'K')
                    dr_thickness = drp.get('drawer_face_thickness', 19.0)
                    inner_thickness = float(drp.get('inner_thickness', 16.0))
                    face_material = str(drp.get('material', 'Matière Tiroir'))
                    inner_material = str(drp.get('material_inner', cab.get('material_body', 'Matière Corps')))
                    cutout = _drawer_handle_signature(drp)
                    
                    if drawer_system == 'LÉGRABOX':
                        legrabox_specs = get_legrabox_specs()
                        legrabox_spec = legrabox_specs.get(tech_type, legrabox_specs['K'])
                        fixed_back_h = legrabox_spec['back_height']
                        dr_L = zone_width_total - (2 * gap_mm)
                        d_L_t = max(0.0, zone_width_interior - 38.0)
                        zone_depth_interior = W_raw - (2 * t_lr)
                        fond_L = max(0.0, zone_width_interior - 35.0)
                        fond_H = max(0.0, zone_depth_interior - 10.0)
                    elif drawer_system == 'ANGLAISE':
                        # ANGLAISE : 2 mm de jeu de chaque côté par rapport aux montants
                        dr_L = max(0.0, zone_width_total - 4.0)
                        back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                        fixed_back_h = back_height_map.get(tech_type, 116.0)
                        d_L_t = max(0.0, dr_L - 40.0)
                        fond_L = max(0.0, dr_L - 49.0)
                        fond_H = round(W_raw - (20.0 + t_fb), 1)
                    else:
                        dr_L = zone_width_total - (2 * gap_mm)
                        back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                        fixed_back_h = back_height_map.get(tech_type, 116.0)
                        d_L_t = max(0.0, dr_L - 49.0)
                        fond_L = max(0.0, dr_L - 49.0)
                        fond_H = round(W_raw - (20.0 + t_fb), 1)
                    
                    # Signature : (face_L, face_H, face_th, system, tech_type, cutout, dos_L, dos_H, dos_th, fond_L, fond_H, fond_th)
                    return (
                        round(dr_L, 1), round(dr_H, 1), round(dr_thickness, 1), face_material, drawer_system, tech_type, cutout,
                        round(d_L_t, 1), round(fixed_back_h, 1), round(inner_thickness, 1),
                        round(fond_L, 1), round(fond_H, 1), round(inner_thickness, 1), inner_material
                    )
                
                # Grouper les tiroirs par signature
                drawer_groups = {}
                for drawer_idx, drp in enumerate(cab['drawers']):
                    sig = get_drawer_signature_export(drp, all_zones_2d, L_raw, W_raw, t_fb, t_lr)
                    if sig not in drawer_groups:
                        drawer_groups[sig] = []
                    drawer_groups[sig].append((drawer_idx, drp))
                
                # Générer les plans pour chaque groupe (une seule feuille par groupe avec quantité)
                group_num = 0
                legrabox_specs = get_legrabox_specs()
                for sig, group in drawer_groups.items():
                    group_num += 1
                    dr_L, dr_H, dr_thickness, face_material, drawer_system, tech_type, cutout, d_L_t, fixed_back_h, inner_thickness, fond_L, fond_H, _, inner_material = sig
                    quantity = len(group)
                    
                    # Prendre le premier tiroir du groupe pour les données de référence
                    first_drawer_idx, first_drp = group[0]
                    
                    # Calculer les trous d'usinage selon le système
                    f_holes = []
                    d_holes_t = []
                    bottom_holes = []
                    
                    if drawer_system == 'LÉGRABOX':
                        legrabox_spec = legrabox_specs.get(tech_type, legrabox_specs['K'])
                        # Trous face (tourillons 10/12)
                        for y in legrabox_spec['face_holes']['y_coords']:
                            if y < dr_H:
                                x_offset = legrabox_spec['face_holes']['x_offset']
                                f_holes.append({'type': 'tourillon_facade', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['face_holes']['diam_str']})
                                f_holes.append({'type': 'tourillon_facade', 'x': dr_L - x_offset, 'y': y, 'diam_str': legrabox_spec['face_holes']['diam_str']})
                        # Trous dos (vis 2.5/3)
                        for y in legrabox_spec['back_holes']['y_coords']:
                            if y < fixed_back_h:
                                x_offset = legrabox_spec['back_holes']['x_offset']
                                d_holes_t.append({'type': 'vis_dos', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['back_holes']['diam_str']})
                                d_holes_t.append({'type': 'vis_dos', 'x': d_L_t - x_offset, 'y': y, 'diam_str': legrabox_spec['back_holes']['diam_str']})
                        # Trous fond (vis 2.5/3)
                        for y in legrabox_spec['bottom_holes']['y_coords']:
                            if y < fond_H:
                                x_offset = legrabox_spec['bottom_holes']['x_offset']
                                bottom_holes.append({'type': 'vis_fond', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['bottom_holes']['diam_str']})
                                bottom_holes.append({'type': 'vis_fond', 'x': fond_L - x_offset, 'y': y, 'diam_str': legrabox_spec['bottom_holes']['diam_str']})
                    else:
                        # TANDEMBOX : logique existante
                        y_coords_face = {'K': [47.5, 79.5, 111.5], 'M': [47.5, 79.5], 'N': [32.5, 64.5], 'D': [47.5, 79.5, 207.5]}.get(tech_type, [47.5, 79.5, 111.5])
                        for y in y_coords_face:
                            if y < dr_H:
                                f_holes.append({'type': 'tourillon_facade', 'x': 32.5, 'y': y, 'diam_str': "⌀10/12"})
                                f_holes.append({'type': 'tourillon_facade', 'x': dr_L - 32.5, 'y': y, 'diam_str': "⌀10/12"})
                        y_coords_back = {'K': [30.0, 62.0, 94.0], 'M': [32.0, 64.0], 'N': [31.0, 47.0], 'D': [31.0, 63.0, 95.0, 159.0, 191.0]}.get(tech_type, [30.0, 62.0, 94.0])
                        for dy in y_coords_back:
                            d_holes_t.append({'type': 'vis_dos', 'x': 9.0, 'y': dy, 'diam_str': "⌀3"}) 
                            d_holes_t.append({'type': 'vis_dos', 'x': d_L_t - 9.0, 'y': dy, 'diam_str': "⌀3"}) 
                    
                    # Convertir la signature de poignée en paramètres de découpe.
                    cutout_dict = _handle_signature_to_cutout_props(cutout)
                    
                    # Créer un proj avec la quantité pour ce groupe
                    proj_group = proj.copy()
                    proj_group['quantity'] = quantity
                    
                    # Titre avec quantité si > 1
                    title_suffix = f" (x{quantity})" if quantity > 1 else ""
                    system_label = f" [{drawer_system} {tech_type}]" if drawer_system == 'LÉGRABOX' else f" [Type {tech_type}]"
                    
                    c_fa = {"Chant Avant":True, "Chant Arrière":True, "Chant Gauche":True, "Chant Droit":True}
                    title_face = f"Façade Tiroir Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                    if output_format == 'dxf':
                        for idx_instance in range(quantity):
                            title_face_instance = f"Façade Tiroir Groupe {group_num} (C{cab_idx}){system_label} #{idx_instance + 1}"
                            plans.append((title_face_instance, dr_L, dr_H, dr_thickness, c_fa, f_holes, [], [], cutout_dict, False, face_material))
                            plan_quantities[title_face_instance] = 1
                    else:
                        plans.append((title_face, dr_L, dr_H, dr_thickness, c_fa, f_holes, [], [], cutout_dict, False, face_material))
                        plan_quantities[title_face] = quantity
                    
                    c_td = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":False}
                    title_dos = f"Tiroir-Dos Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                    if output_format == 'dxf':
                        for idx_instance in range(quantity):
                            title_dos_instance = f"Tiroir-Dos Groupe {group_num} (C{cab_idx}){system_label} #{idx_instance + 1}"
                            plans.append((title_dos_instance, d_L_t, fixed_back_h, inner_thickness, c_td, d_holes_t, [], [], None, False, inner_material))
                            plan_quantities[title_dos_instance] = 1
                    else:
                        plans.append((title_dos, d_L_t, fixed_back_h, inner_thickness, c_td, d_holes_t, [], [], None, False, inner_material))
                        plan_quantities[title_dos] = quantity
                    
                    title_fond = f"Tiroir-Fond Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                    # Passer has_rebate=True pour LÉGRABOX (le paramètre sera ajouté à la fin de la tuple)
                    if output_format == 'dxf':
                        for idx_instance in range(quantity):
                            title_fond_instance = f"Tiroir-Fond Groupe {group_num} (C{cab_idx}){system_label} #{idx_instance + 1}"
                            plan_tuple = (title_fond_instance, fond_L, fond_H, inner_thickness, c_td, bottom_holes if drawer_system == 'LÉGRABOX' else [], [], [], None, drawer_system == 'LÉGRABOX', inner_material)
                            plans.append(plan_tuple)
                            plan_quantities[title_fond_instance] = 1
                    else:
                        plan_tuple = (title_fond, fond_L, fond_H, inner_thickness, c_td, bottom_holes if drawer_system == 'LÉGRABOX' else [], [], [], None, drawer_system == 'LÉGRABOX', inner_material)
                        plans.append(plan_tuple)
                        plan_quantities[title_fond] = quantity
            
            # --- PORTE ---
            if cab['door_props']['has_door']:
                dp = cab['door_props']
                door_type = dp.get('door_type', 'single')
                dH = H_raw + 80.0 - dp['door_gap'] if dp.get('door_model')=='floor_length' else H_raw - (2 * dp['door_gap'])
                hinge_base_h = max(1.0, dH - 80.0) if dp.get('door_model') == 'floor_length' else dH
                hinge_y_offset = 80.0 if dp.get('door_model') == 'floor_length' else 0.0
                
                if door_type == 'double':
                    # Porte double : générer deux feuilles d'usinage (une pour chaque battant)
                    dW_half = (L_raw - (2 * dp['door_gap'])) / 2.0
                    # Utiliser les positions personnalisées si le mode est 'custom'
                    if dp.get('hinge_mode') == 'custom' and dp.get('custom_hinge_positions'):
                        y_h = get_hinge_y_positions(hinge_base_h, custom_positions=dp['custom_hinge_positions'])
                    else:
                        y_h = get_hinge_y_positions(hinge_base_h)
                    y_h = [y + hinge_y_offset for y in y_h]
                    
                    # Porte gauche : trous à gauche (xc=23.5, xv=33.0)
                    holes_p_g = []
                    xc_g = 23.5
                    xv_g = 33.0
                    for y in y_h:
                        holes_p_g.append({'type':'tourillon','x':xc_g,'y':y,'diam_str':"⌀35"})
                        holes_p_g.append({'type':'vis','x':xv_g,'y':y+22.5,'diam_str':"⌀8"})
                        holes_p_g.append({'type':'vis','x':xv_g,'y':y-22.5,'diam_str':"⌀8"})
                    
                    # Porte droite : trous à droite (xc=dW_half-23.5, xv=dW_half-33.0)
                    holes_p_d = []
                    xc_d = dW_half - 23.5
                    xv_d = dW_half - 33.0
                    for y in y_h:
                        holes_p_d.append({'type':'tourillon','x':xc_d,'y':y,'diam_str':"⌀35"})
                        holes_p_d.append({'type':'vis','x':xv_d,'y':y+22.5,'diam_str':"⌀8"})
                        holes_p_d.append({'type':'vis','x':xv_d,'y':y-22.5,'diam_str':"⌀8"})
                    
                    # Utiliser holes_p_g pour la feuille d'usinage (les deux battants sont identiques)
                    holes_p = holes_p_g
                    dW = dW_half
                    door_quantity = 2
                else:
                    # Porte simple : une seule feuille d'usinage
                    dW = L_raw - (2 * dp['door_gap'])
                    # Utiliser les positions personnalisées si le mode est 'custom'
                    if dp.get('hinge_mode') == 'custom' and dp.get('custom_hinge_positions'):
                        y_h = get_hinge_y_positions(hinge_base_h, custom_positions=dp['custom_hinge_positions'])
                    else:
                        y_h = get_hinge_y_positions(hinge_base_h)
                    y_h = [y + hinge_y_offset for y in y_h]
                    
                    holes_p = []
                    xc = 23.5 if dp['door_opening']=='left' else dW-23.5
                    xv = 33.0 if dp['door_opening']=='left' else dW-33.0
                    for y in y_h:
                        holes_p.append({'type':'tourillon','x':xc,'y':y,'diam_str':"⌀35"})
                        holes_p.append({'type':'vis','x':xv,'y':y+22.5,'diam_str':"⌀8"})
                        holes_p.append({'type':'vis','x':xv,'y':y-22.5,'diam_str':"⌀8"})
                    door_quantity = 1
                
                c_p = {"Chant Avant":True, "Chant Arrière":True, "Chant Gauche":True, "Chant Droit":True}
                # Stocker la quantité pour la porte dans plan_quantities
                plan_quantities[f"Porte (C{cab_idx})"] = door_quantity
                plans.append((f"Porte (C{cab_idx})", dW, dH, dp['door_thickness'], c_p, holes_p, [], [], None, False, dp.get('material', 'Matière Porte')))
            
            # --- MONTANTS SECONDAIRES (MÊME LOGIQUE QUE 2.PY) ---
            if 'vertical_dividers' in cab and cab['vertical_dividers']:
                for div_idx, div in enumerate(cab['vertical_dividers']):
                    div_th = div.get('thickness', 19.0)
                    div_h = h_side
                    div_w = W_mont
                    
                    # Trous sur les tranches haut et bas
                    div_tranche_holes = get_vertical_divider_tranche_holes(W_mont, div_th)
                    
                    c_div_12 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":True}
                    c_div_22 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":True, "Chant Droit":False}
                    
                    # TOUJOURS générer 2 plans (1/2 et 2/2) pour chaque montant secondaire
                    divider_material = div.get('material', cab.get('material_body', 'Matière Corps'))
                    holes_div_left = divider_element_holes_left.get(div_idx, [])
                    holes_div_right = divider_element_holes_right.get(div_idx, [])
                    title_12 = _get_secondary_divider_title(div_idx, cab_idx, "1/2", output_format)
                    title_22 = _get_secondary_divider_title(div_idx, cab_idx, "2/2", output_format)
                    plans.append((title_12, div_w, div_h, div_th, c_div_12, holes_div_left, div_tranche_holes, [], None, False, divider_material))
                    plans.append((title_22, div_w, div_h, div_th, c_div_22, holes_div_right, div_tranche_holes, [], None, False, divider_material))

            # Garde-fou: forcer les 2 feuilles par montant secondaire même si un traitement amont a sauté.
            if 'vertical_dividers' in cab and cab['vertical_dividers']:
                existing_plan_titles = {str(p[0]) for p in plans if isinstance(p, tuple) and len(p) >= 1}
                for div_idx, div in enumerate(cab['vertical_dividers']):
                    div_th = div.get('thickness', 19.0)
                    div_h = h_side
                    div_w = W_mont
                    div_tranche_holes = get_vertical_divider_tranche_holes(W_mont, div_th)
                    c_div_12 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":True}
                    c_div_22 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":True, "Chant Droit":False}
                    divider_material = div.get('material', cab.get('material_body', 'Matière Corps'))

                    title_12 = _get_secondary_divider_title(div_idx, cab_idx, "1/2", output_format)
                    if title_12 not in existing_plan_titles:
                        plans.append((title_12, div_w, div_h, div_th, c_div_12, divider_element_holes_left.get(div_idx, []), div_tranche_holes, [], None, False, divider_material))
                        existing_plan_titles.add(title_12)

                    title_22 = _get_secondary_divider_title(div_idx, cab_idx, "2/2", output_format)
                    if title_22 not in existing_plan_titles:
                        plans.append((title_22, div_w, div_h, div_th, c_div_22, divider_element_holes_right.get(div_idx, []), div_tranche_holes, [], None, False, divider_material))
                        existing_plan_titles.add(title_22)

            for item in plans:
                material_name = ""
                if len(item) == 11:
                    title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut, has_rebate, material_name = item
                elif len(item) == 10:
                    title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut, has_rebate = item
                elif len(item) == 9: 
                    title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut = item
                    has_rebate = False
                else: 
                    title, Lp, Wp, Tp, ch, fh, t_long_h, cut = item
                    t_cote_h = []
                    has_rebate = False

                if not _is_material_allowed(material_name):
                    # Les montants secondaires doivent toujours apparaître en DXF.
                    if not (output_format == 'dxf' and _is_secondary_divider_title(title)):
                        continue

                # Utiliser la quantité spécifique pour ce plan si disponible (tiroirs groupés)
                proj_for_plan = proj.copy()
                if title in plan_quantities:
                    proj_for_plan['quantity'] = plan_quantities[title]
                fig = _add_plan(title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut, has_rebate, proj_for_plan)
                rendered_plan_titles.append(title)
                if output_format == 'dxf':
                    continue

                if output_format == 'figures':
                    try:
                        meta = getattr(fig.layout, 'meta', None) or {}
                        meta['quantity'] = proj_for_plan.get('quantity', 1)
                        meta['sheet_title'] = title
                        meta['cabinet_index'] = int(cab_idx)
                        meta['corps_meuble'] = f"Caisson {int(cab_idx)}"
                        meta['material'] = material_name
                        fig.layout.meta = meta
                    except Exception:
                        pass
                    figures_out.append((title, fig))
                    continue

                if output_format == 'pdf':
                    # Pour PDF : convertir la figure en image PNG base64
                    try:
                        # Essayer d'abord avec kaleido
                        img_bytes = fig.to_image(format="png", width=1400, height=1000)
                        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                        full_html += f'<div class="page-container"><img src="data:image/png;base64,{img_base64}" alt="{title}"></div>\n'
                    except Exception as e:
                        # Si kaleido n'est pas disponible ou échoue, utiliser HTML (moins fiable pour PDF)
                        # Mais cela ne fonctionnera probablement pas avec weasyprint
                        import traceback
                        error_details = traceback.format_exc()
                        # Essayer quand même avec HTML statique
                        html_fig = fig.to_html(include_plotlyjs=False, full_html=False, config={'staticPlot': True})
                        full_html += f'<div class="page-container">{html_fig}</div>\n'
                        # Note: Si kaleido échoue, le PDF risque d'avoir des pages vierges
                else:
                    # Pour HTML : utiliser le HTML interactif de Plotly
                    html_fig = fig.to_html(include_plotlyjs=False, full_html=False, config={'staticPlot': True})
                    full_html += f'<div class="page-container">{html_fig}</div>'
        
        # === VALIDATION STRICTE POUR DXF ===
        if output_format == 'dxf':
            # Vérifier que le nombre de layouts DXF = nombre d'éléments dans plans
            expected_count = len(rendered_plan_titles)
            actual_count = dxf_plan_index
            
            if expected_count != actual_count:
                error_msg = (
                    f"[DXF EXPORT ERROR] VALIDATION FAILED:\n"
                    f"Expected {expected_count} layouts in DXF (after material filter)\n"
                    f"Got {actual_count} layouts actually drawn\n"
                    f"{expected_count - actual_count} elements were SKIPPED!\n"
                    f"\nRendered plans list contents:\n"
                )
                for i, title in enumerate(rendered_plan_titles, 1):
                    try:
                        error_msg += f"  {i}. {title}\n"
                    except:
                        error_msg += f"  {i}. [Error parsing item]\n"
                
                import sys
                print(error_msg, file=sys.stderr)
                return error_msg.encode('utf-8'), False

            if dxf_plan_index > 0:
                # 1 objet en modelspace = 1 feuille PaperSpace dédiée.
                layout_creation_errors = []
                for i, (plan_title, plan_bbox) in enumerate(dxf_plan_layout_specs, start=1):
                    # Nommage séquentiel robuste: évite toute collision/troncature liée aux titres.
                    layout_name = _sheet_layout_name(i)
                    try:
                        _create_layout_for_plan(dxf_doc, layout_name, plan_bbox)
                    except Exception as e:
                        layout_creation_errors.append(f"{layout_name} ({plan_title}): {e}")

                if layout_creation_errors:
                    error_msg = "[DXF EXPORT ERROR] Impossible de créer toutes les feuilles d'usinage:\n"
                    for err in layout_creation_errors:
                        error_msg += f" - {err}\n"
                    return error_msg.encode('utf-8'), False

                legend_x = dxf_plan_index * 2600.0 + 500.0
                legend_y = 0.0

                _safe_add_text(
                    dxf_msp,
                    "LÉGENDE GÉNÉRALE",
                    {"height": 50, "layer": "TEXTES"},
                    (legend_x, legend_y + 800),
                    TextEntityAlignment.MIDDLE_CENTER,
                )

                legend_width = 600.0
                legend_height = 600.0
                legend_x0 = legend_x - legend_width / 2
                legend_x1 = legend_x + legend_width / 2
                legend_y0 = legend_y
                legend_y1 = legend_y + legend_height

                dxf_msp.add_lwpolyline([
                    (legend_x0, legend_y0), (legend_x1, legend_y0),
                    (legend_x1, legend_y1), (legend_x0, legend_y1), (legend_x0, legend_y0)
                ], dxfattribs={"layer": "CARTOUCHE"})

                tri_size = 80.0
                y_tri1 = legend_y + 500.0
                tri1_pts = [
                    (legend_x, y_tri1 - tri_size),
                    (legend_x - tri_size * 0.6, y_tri1),
                    (legend_x + tri_size * 0.6, y_tri1),
                    (legend_x, y_tri1 - tri_size)
                ]
                dxf_msp.add_lwpolyline(tri1_pts, dxfattribs={"layer": "LEGENDE"})
                _safe_add_text(
                    dxf_msp,
                    "Triangle vide = Corps meuble inférieur",
                    {"height": 25, "layer": "TEXTES"},
                    (legend_x, y_tri1 - tri_size - 50),
                    TextEntityAlignment.TOP_CENTER,
                )

                y_tri2 = legend_y + 250.0
                tri2_pts = [
                    (legend_x, y_tri2 - tri_size),
                    (legend_x - tri_size * 0.6, y_tri2),
                    (legend_x + tri_size * 0.6, y_tri2),
                    (legend_x, y_tri2 - tri_size)
                ]
                try:
                    hatch = dxf_msp.add_hatch(color=256, dxfattribs={"layer": "LEGENDE"})
                    hatch.paths.add_polyline_path(tri2_pts, is_closed=True)
                except Exception:
                    pass
                dxf_msp.add_lwpolyline(tri2_pts, dxfattribs={"layer": "LEGENDE"})
                _safe_add_text(
                    dxf_msp,
                    "Triangle noir = Avant corps du meuble",
                    {"height": 25, "layer": "TEXTES"},
                    (legend_x, y_tri2 - tri_size - 50),
                    TextEntityAlignment.TOP_CENTER,
                )

                _safe_add_text(
                    dxf_msp,
                    "Les triangles de repérage se trouvent également",
                    {"height": 18, "layer": "TEXTES"},
                    (legend_x, legend_y + 80),
                    TextEntityAlignment.MIDDLE_CENTER,
                )
                _safe_add_text(
                    dxf_msp,
                    "sur chaque feuille d'usinage (cartouche).",
                    {"height": 18, "layer": "TEXTES"},
                    (legend_x, legend_y + 50),
                    TextEntityAlignment.MIDDLE_CENTER,
                )

            dxf_stream = StringIO()
            _sanitize_dxf_doc(dxf_doc)
            dxf_doc.write(dxf_stream)
            dxf_content = dxf_stream.getvalue()
            return dxf_content.encode('utf-8'), True

        if output_format == 'figures':
            return figures_out, True

        full_html += '</body></html>'
        return full_html.encode('utf-8'), True
    except Exception as e:
        error_msg = f"Erreur lors de la génération HTML : {str(e)}<br><pre>{traceback.format_exc()}</pre>"
        return error_msg.encode('utf-8'), False

def _generate_all_plans_list(cabinets_to_process, indices_to_process):
    """
    Fonction helper qui génère la liste de tous les plans à dessiner.
    Retourne une liste de tuples : (title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h, cut, has_rebate, proj_for_plan)
    """
    all_plans = []
    
    # Cette fonction contient toute la logique de préparation des plans
    # (identique à generate_stacked_html_plans mais sans générer le HTML)
    # Pour éviter la duplication complète, je vais extraire cette partie
    
    # Pour l'instant, je vais utiliser une approche différente :
    # modifier generate_stacked_html_plans pour accepter un paramètre use_images
    # qui détermine si on génère du HTML ou des images
    
    return all_plans


def _count_fasteners_in_holes(*hole_lists):
    """Compte les vis et tourillons dans une ou plusieurs listes de trous."""
    vis_count = 0
    tourillon_hole_count = 0
    for hole_list in hole_lists:
        if not hole_list:
            continue
        for hole in hole_list:
            h_type = str(hole.get('type', '')).lower()

            # Les vis sont comptées trou par trou.
            if 'vis' in h_type:
                vis_count += 1

            # Les gros trous de portes/façades tiroirs ne sont PAS des tourillons à compter.
            # - porte : type "tourillon" + diametre cuvette charniere (ex: ⌀35)
            # - facade tiroir : type "tourillon_facade" (ex: ⌀10/12)
            if 'tourillon' in h_type:
                diam_str = str(hole.get('diam_str', '')).replace(' ', '').lower()
                is_door_cup_hole = ('35' in diam_str)
                is_drawer_face_hole = ('tourillon_facade' in h_type)
                if is_door_cup_hole or is_drawer_face_hole:
                    continue
                tourillon_hole_count += 1

    # Règle métier: 2 trous de tourillon = 1 tourillon réel.
    # En cas d'impair, on arrondit au supérieur pour éviter de sous-estimer.
    tourillon_count = (tourillon_hole_count + 1) // 2
    return vis_count, tourillon_count


def get_all_machining_plans_figures(cabinets_to_process, indices_to_process, include_hole_counts=False):
    """
    Génère toutes les figures Plotly des feuilles d'usinage pour affichage dans Streamlit.
    
    Returns:
        Liste de tuples (titre, figure_plotly)
    """
    import streamlit as st
    from drawing_interface import draw_machining_view_pro_final
    from machining_logic import (
        calculate_hole_positions, get_vertical_divider_tranche_holes,
        get_traverse_face_holes_for_divider, calculate_back_panel_holes, calculate_all_zones_2d,
        calculate_zones_from_dividers
    )
    
    all_figures = []
    hole_counts = []

    def _append_figure(title, fig, *hole_lists, quantity=1):
        all_figures.append((title, fig))
        if include_hole_counts:
            vis_count, tourillon_count = _count_fasteners_in_holes(*hole_lists)
            qty = max(1, int(quantity) if quantity is not None else 1)
            hole_counts.append({
                'scene': title,
                'vis': vis_count * qty,
                'tourillon': tourillon_count * qty,
            })
    
    for i, cab in enumerate(cabinets_to_process):
        cab_idx = indices_to_process[i]
        dims = cab['dims']
        
        L_raw = float(dims['L_raw'])
        W_raw = float(dims['W_raw'])
        H_raw = float(dims['H_raw'])
        t_lr = float(dims['t_lr_raw'])
        t_fb = float(dims['t_fb_raw'])
        t_tb = float(dims['t_tb_raw'])
        
        h_side = H_raw
        L_trav = L_raw - 2 * t_lr
        W_mont = W_raw
        W_back = L_raw - 2.0
        H_back = H_raw - 2.0
        
        ys_vis, ys_dowel = calculate_hole_positions(W_raw)
        holes_mg, holes_md = [], []
        tranche_holes_mg, tranche_holes_md = [], []
        
        # Trous d'assemblage pour les montants principaux
        for x in ys_vis:
            holes_mg.append({'type':'vis','x':x,'y':t_tb/2,'diam_str':"⌀3"})
            holes_mg.append({'type':'vis','x':x,'y':h_side-t_tb/2,'diam_str':"⌀3"})
            holes_md.append({'type':'vis','x':x,'y':t_tb/2,'diam_str':"⌀3"})
            holes_md.append({'type':'vis','x':x,'y':h_side-t_tb/2,'diam_str':"⌀3"})
        for x in ys_dowel:
            holes_mg.append({'type':'tourillon','x':x,'y':t_tb/2,'diam_str':"⌀8/10"})
            holes_mg.append({'type':'tourillon','x':x,'y':h_side-t_tb/2,'diam_str':"⌀8/10"})
            holes_md.append({'type':'tourillon','x':x,'y':t_tb/2,'diam_str':"⌀8/10"})
            holes_md.append({'type':'tourillon','x':x,'y':h_side-t_tb/2,'diam_str':"⌀8/10"})
        
        ys_vis_sf, ys_dowel_sf = ys_vis, ys_dowel
        all_zones_2d = calculate_all_zones_2d(cab)
        
        divider_element_holes_left = {}
        divider_element_holes_right = {}
        if 'vertical_dividers' in cab and cab['vertical_dividers']:
            divider_element_holes_left = {i: [] for i in range(len(cab['vertical_dividers']))}
            divider_element_holes_right = {i: [] for i in range(len(cab['vertical_dividers']))}
        
        traverse_face_holes_left = []
        traverse_face_holes_right = []
        
        if 'vertical_dividers' in cab and cab['vertical_dividers']:
            for div_idx, div in enumerate(cab['vertical_dividers']):
                div_x = div['position_x']
                t_div = div.get('thickness', 19.0)
                div_traverse_face_holes = get_traverse_face_holes_for_divider(L_trav, div_x, t_lr, t_tb, W_raw, t_fb)
                traverse_face_holes_left.extend(div_traverse_face_holes)
                traverse_face_holes_right.extend(div_traverse_face_holes)
        
        # Ajouter les trous pour étagères et tiroirs sur les montants
        base_el = cab.get('base_elements', {
            'has_back_panel': True,
            'has_left_upright': True,
            'has_right_upright': True,
            'has_bottom_traverse': True,
            'has_top_traverse': True,
        })
        shelves_list = cab.get('shelves', []) or base_el.get('shelves', []) or []
        if shelves_list:
            for s in shelves_list:
                if s.get('enabled', True):
                    yc_val = s.get('y_center', 0)
                    zone_id = s.get('zone_id', None)
                    zone_x_min = None
                    zone_x_max = None
                    if s.get('stored_zone_coords'):
                        zone_x_min = s['stored_zone_coords']['x_min']
                        zone_x_max = s['stored_zone_coords']['x_max']
                    elif zone_id is not None and zone_id < len(all_zones_2d):
                        zone = all_zones_2d[zone_id]
                        zone_x_min = zone['x_min']
                        zone_x_max = zone['x_max']
                    
                    if zone_x_min is not None and zone_x_max is not None:
                        if abs(zone_x_min - t_lr) < 1.0:
                            for x in ys_vis_sf: holes_mg.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                            for x in ys_dowel_sf: holes_mg.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                        if abs(zone_x_max - (L_raw - t_lr)) < 1.0:
                            for x in ys_vis_sf: holes_md.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                            for x in ys_dowel_sf: holes_md.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                        
                        for div_idx, div in enumerate(cab.get('vertical_dividers', [])):
                            div_x = div['position_x']
                            div_th = div.get('thickness', 19.0)
                            div_left_edge = div_x - div_th / 2.0
                            div_right_edge = div_x + div_th / 2.0
                            
                            touches_left_face = abs(zone_x_max - div_left_edge) < 1.0
                            touches_right_face = abs(zone_x_min - div_right_edge) < 1.0
                            
                            if touches_left_face:
                                for x in ys_vis_sf:
                                    divider_element_holes_left[div_idx].append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                for x in ys_dowel_sf:
                                    divider_element_holes_left[div_idx].append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                            if touches_right_face:
                                for x in ys_vis_sf:
                                    divider_element_holes_right[div_idx].append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                                for x in ys_dowel_sf:
                                    divider_element_holes_right[div_idx].append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                    else:
                        for x in ys_vis_sf: holes_mg.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                        for x in ys_dowel_sf: holes_mg.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
                        for x in ys_vis_sf: holes_md.append({'type':'vis','x':x,'y':yc_val,'diam_str':"⌀3"})
                        for x in ys_dowel_sf: holes_md.append({'type':'tourillon','x':x,'y':yc_val,'diam_str':"⌀8/10"})
        
        # Trous pour tiroirs
        if 'drawers' in cab and cab['drawers']:
            for drawer_idx, drp in enumerate(cab['drawers']):
                drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                y_slide = t_tb + 33.0 + drp.get('drawer_bottom_offset', 0.0)
                drawer_zone_id = drp.get('zone_id', None)
                x_slide_holes = [19, 37]
                
                wr = W_raw
                if 153 < wr < 302: x_slide_holes = [19, 37, 133]
                elif 303 < wr < 352: x_slide_holes = [19, 37, 133, 261]
                elif 353 < wr < 402: x_slide_holes = [19, 37, 133, 261]
                elif 403 < wr < 452: x_slide_holes = [19, 37, 133, 261, 293]
                elif 453 < wr < 502: x_slide_holes = [19, 37, 133, 261, 293]
                elif 503 < wr < 552: x_slide_holes = [19, 37, 133, 261, 293, 453]
                elif 553 < wr < 602: x_slide_holes = [19, 37, 133, 261, 293, 453]
                elif 603 < wr < 652: x_slide_holes = [19, 37, 133, 261, 293, 325, 357, 517]

                # Tiroir à l'anglaise: perçages décalés de 40 mm depuis le bord.
                x_slide_holes = _apply_anglaise_slide_offset(drawer_system, x_slide_holes, W_mont)
                
                zone_x_min = None
                zone_x_max = None
                if drawer_zone_id is not None and drawer_zone_id < len(all_zones_2d):
                    zone = all_zones_2d[drawer_zone_id]
                    zone_x_min = zone['x_min']
                    zone_x_max = zone['x_max']
                
                if zone_x_min is not None and zone_x_max is not None:
                    if abs(zone_x_min - t_lr) < 1.0:
                        for x_s in x_slide_holes:
                            holes_mg.append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                    if abs(zone_x_max - (L_raw - t_lr)) < 1.0:
                        for x_s in x_slide_holes:
                            holes_md.append({'type': 'vis', 'x': W_mont - x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                    for div_idx, div in enumerate(cab.get('vertical_dividers', [])):
                        div_x = div['position_x']
                        div_th = div.get('thickness', 19.0)
                        div_left_edge = div_x - div_th / 2.0
                        div_right_edge = div_x + div_th / 2.0
                        
                        touches_left_face = abs(zone_x_max - div_left_edge) < 1.0
                        touches_right_face = abs(zone_x_min - div_right_edge) < 1.0
                        
                        if touches_left_face:
                            for x_s in x_slide_holes:
                                divider_element_holes_left[div_idx].append({'type':'vis','x':x_s,'y':y_slide,'diam_str':"⌀5/12"})
                        if touches_right_face:
                            for x_s in x_slide_holes:
                                divider_element_holes_right[div_idx].append({'type':'vis','x':x_s,'y':y_slide,'diam_str':"⌀5/12"})
                else:
                    for x_s in x_slide_holes:
                        holes_mg.append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': "⌀5/12"})
                        holes_md.append({'type': 'vis', 'x': W_mont - x_s, 'y': y_slide, 'diam_str': "⌀5/12"})

        # Trous de fixation charnières sur montants (comptage vis/tourillons)
        if cab.get('door_props', {}).get('has_door', False):
            dp = cab['door_props']
            if dp.get('hinge_mode') == 'custom' and dp.get('custom_hinge_positions'):
                y_hinges = get_hinge_y_positions(h_side, custom_positions=dp['custom_hinge_positions'])
            else:
                y_hinges = get_hinge_y_positions(h_side)

            zone_id = dp.get('zone_id', None)
            touches_left_outer = True
            touches_right_outer = True
            if zone_id is not None and zone_id < len(all_zones_2d):
                zone = all_zones_2d[zone_id]
                touches_left_outer = abs(zone['x_min'] - t_lr) < 1.0
                touches_right_outer = abs(zone['x_max'] - (L_raw - t_lr)) < 1.0

            door_type = dp.get('door_type', 'single')
            if door_type == 'double':
                for y_h in y_hinges:
                    if touches_left_outer:
                        holes_mg.append({'type': 'vis', 'x': 20.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                        holes_mg.append({'type': 'vis', 'x': 52.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                    if touches_right_outer:
                        holes_md.append({'type': 'vis', 'x': W_mont - 20.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                        holes_md.append({'type': 'vis', 'x': W_mont - 52.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
            else:
                opening = dp.get('door_opening', 'right')
                for y_h in y_hinges:
                    if opening == 'left' and touches_left_outer:
                        holes_mg.append({'type': 'vis', 'x': 20.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                        holes_mg.append({'type': 'vis', 'x': 52.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                    elif opening != 'left' and touches_right_outer:
                        holes_md.append({'type': 'vis', 'x': 20.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
                        holes_md.append({'type': 'vis', 'x': 52.0, 'y': y_h, 'diam_str': "⌀5/11.5"})
        
        # Panneau arrière: aucun trou d'usinage (règle métier)
        holes_fond = []
        
        proj = {
            "project_name": st.session_state.project_name,
            "corps_meuble": f"Caisson {cab_idx}",
            "quantity": 1,
            "date": datetime.date.today().strftime("%d/%m/%Y")
        }
        
        # === GÉNÉRER LES FIGURES POUR LES FAÇADES, DOS ET FONDS DES TIROIRS ===
        if 'drawers' in cab and cab['drawers']:
            # Helper function pour calculer la signature d'un tiroir
            def get_drawer_signature_for_figures(drp, all_zones_2d, L_raw, W_raw, t_fb, t_lr):
                drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                drawer_zone_id = drp.get('zone_id', None)
                gap_mm = drp.get('drawer_gap', 2.0)
                
                if drawer_zone_id is not None and drawer_zone_id < len(all_zones_2d):
                    zone = all_zones_2d[drawer_zone_id]
                    zone_width_total = zone['x_max'] - zone['x_min']
                    zone_width_interior = zone_width_total - (2 * t_lr)
                else:
                    zone_width_total = L_raw
                    zone_width_interior = L_raw - (2 * t_lr)
                
                dr_H = drp.get('drawer_face_H_raw', 150.0)
                tech_type = drp.get('drawer_tech_type', 'K')
                dr_thickness = drp.get('drawer_face_thickness', 19.0)
                inner_thickness = float(drp.get('inner_thickness', 16.0))
                cutout = _drawer_handle_signature(drp)
                
                if drawer_system == 'LÉGRABOX':
                    legrabox_specs = get_legrabox_specs()
                    legrabox_spec = legrabox_specs.get(tech_type, legrabox_specs['K'])
                    fixed_back_h = legrabox_spec['back_height']
                    dr_L = zone_width_total - (2 * gap_mm)
                    d_L_t = max(0.0, zone_width_interior - 38.0)
                    zone_depth_interior = W_raw - (2 * t_lr)
                    fond_L = max(0.0, zone_width_interior - 35.0)
                    fond_H = max(0.0, zone_depth_interior - 10.0)
                elif drawer_system == 'ANGLAISE':
                    # ANGLAISE : 2 mm de jeu de chaque côté par rapport aux montants
                    dr_L = max(0.0, zone_width_total - 4.0)
                    back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                    fixed_back_h = back_height_map.get(tech_type, 116.0)
                    d_L_t = max(0.0, dr_L - 40.0)
                    fond_L = max(0.0, dr_L - 49.0)
                    fond_H = round(W_raw - (20.0 + t_fb), 1)
                else:
                    dr_L = zone_width_total - (2 * gap_mm)
                    back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                    fixed_back_h = back_height_map.get(tech_type, 116.0)
                    d_L_t = max(0.0, dr_L - 49.0)
                    fond_L = max(0.0, dr_L - 49.0)
                    fond_H = round(W_raw - (20.0 + t_fb), 1)
                
                return (
                    round(dr_L, 1), round(dr_H, 1), round(dr_thickness, 1), drawer_system, tech_type, cutout,
                    round(d_L_t, 1), round(fixed_back_h, 1), round(inner_thickness, 1),
                    round(fond_L, 1), round(fond_H, 1), round(inner_thickness, 1)
                )
            
            # Grouper les tiroirs par signature
            drawer_groups = {}
            for drawer_idx, drp in enumerate(cab['drawers']):
                sig = get_drawer_signature_for_figures(drp, all_zones_2d, L_raw, W_raw, t_fb, t_lr)
                if sig not in drawer_groups:
                    drawer_groups[sig] = []
                drawer_groups[sig].append((drawer_idx, drp))
            
            # Générer les figures pour chaque groupe
            group_num = 0
            legrabox_specs = get_legrabox_specs()
            for sig, group in drawer_groups.items():
                group_num += 1
                dr_L, dr_H, dr_thickness, drawer_system, tech_type, cutout, d_L_t, fixed_back_h, inner_thickness, fond_L, fond_H, _ = sig
                quantity = len(group)
                
                # Prendre le premier tiroir du groupe pour les données de référence
                first_drawer_idx, first_drp = group[0]
                
                # Calculer les trous d'usinage selon le système
                f_holes = []
                d_holes_t = []
                bottom_holes = []
                
                if drawer_system == 'LÉGRABOX':
                    legrabox_spec = legrabox_specs.get(tech_type, legrabox_specs['K'])
                    # Trous face (tourillons 10/12)
                    for y in legrabox_spec['face_holes']['y_coords']:
                        if y < dr_H:
                            x_offset = legrabox_spec['face_holes']['x_offset']
                            f_holes.append({'type': 'tourillon_facade', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['face_holes']['diam_str']})
                            f_holes.append({'type': 'tourillon_facade', 'x': dr_L - x_offset, 'y': y, 'diam_str': legrabox_spec['face_holes']['diam_str']})
                    # Trous dos (vis 2.5/3)
                    for y in legrabox_spec['back_holes']['y_coords']:
                        if y < fixed_back_h:
                            x_offset = legrabox_spec['back_holes']['x_offset']
                            d_holes_t.append({'type': 'vis_dos', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['back_holes']['diam_str']})
                            d_holes_t.append({'type': 'vis_dos', 'x': d_L_t - x_offset, 'y': y, 'diam_str': legrabox_spec['back_holes']['diam_str']})
                    # Trous fond (vis 2.5/3)
                    for y in legrabox_spec['bottom_holes']['y_coords']:
                        if y < fond_H:
                            x_offset = legrabox_spec['bottom_holes']['x_offset']
                            bottom_holes.append({'type': 'vis_fond', 'x': x_offset, 'y': y, 'diam_str': legrabox_spec['bottom_holes']['diam_str']})
                            bottom_holes.append({'type': 'vis_fond', 'x': fond_L - x_offset, 'y': y, 'diam_str': legrabox_spec['bottom_holes']['diam_str']})
                else:
                    # TANDEMBOX : logique existante
                    y_coords_face = {'K': [47.5, 79.5, 111.5], 'M': [47.5, 79.5], 'N': [32.5, 64.5], 'D': [47.5, 79.5, 207.5]}.get(tech_type, [47.5, 79.5, 111.5])
                    for y in y_coords_face:
                        if y < dr_H:
                            f_holes.append({'type': 'tourillon_facade', 'x': 32.5, 'y': y, 'diam_str': "⌀10/12"})
                            f_holes.append({'type': 'tourillon_facade', 'x': dr_L - 32.5, 'y': y, 'diam_str': "⌀10/12"})
                    y_coords_back = {'K': [30.0, 62.0, 94.0], 'M': [32.0, 64.0], 'N': [31.0, 47.0], 'D': [31.0, 63.0, 95.0, 159.0, 191.0]}.get(tech_type, [30.0, 62.0, 94.0])
                    for dy in y_coords_back:
                        d_holes_t.append({'type': 'vis_dos', 'x': 9.0, 'y': dy, 'diam_str': "⌀3"})
                        d_holes_t.append({'type': 'vis_dos', 'x': d_L_t - 9.0, 'y': dy, 'diam_str': "⌀3"})
                    # Ajouter les mêmes trous au fond pour TANDEMBOX
                    for dy in y_coords_back:
                        if dy < fond_H:
                            bottom_holes.append({'type': 'vis_fond', 'x': 9.0, 'y': dy, 'diam_str': "⌀3"})
                            bottom_holes.append({'type': 'vis_fond', 'x': fond_L - 9.0, 'y': dy, 'diam_str': "⌀3"})
                
                # Convertir la signature de poignée en paramètres de découpe.
                cutout_dict = _handle_signature_to_cutout_props(cutout)
                
                # Créer un proj avec la quantité pour ce groupe
                proj_group = proj.copy()
                proj_group['quantity'] = quantity
                
                # Titre avec quantité si > 1
                title_suffix = f" (x{quantity})" if quantity > 1 else ""
                system_label = f" [{drawer_system} {tech_type}]" if drawer_system == 'LÉGRABOX' else f" [Type {tech_type}]"
                
                c_fa = {"Chant Avant":True, "Chant Arrière":True, "Chant Gauche":True, "Chant Droit":True}
                title_face = f"Façade Tiroir Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                fig_face = draw_machining_view_pro_final(
                    title_face, dr_L, dr_H, dr_thickness, st.session_state.unit_select, proj_group, c_fa,
                    f_holes, [], [], cutout_dict, False
                )
                _append_figure(title_face, fig_face, f_holes, quantity=quantity)
                
                c_td = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":False}
                title_dos = f"Tiroir-Dos Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                fig_dos = draw_machining_view_pro_final(
                    title_dos, d_L_t, fixed_back_h, inner_thickness, st.session_state.unit_select, proj_group, c_td,
                    d_holes_t, [], [], None, False
                )
                _append_figure(title_dos, fig_dos, d_holes_t, quantity=quantity)
                
                title_fond = f"Tiroir-Fond Groupe {group_num} (C{cab_idx}){system_label}{title_suffix}"
                fig_fond = draw_machining_view_pro_final(
                    title_fond, fond_L, fond_H, inner_thickness, st.session_state.unit_select, proj_group, c_td,
                    bottom_holes, [], [], None, False
                )
                _append_figure(title_fond, fig_fond, bottom_holes, quantity=quantity)
        
        proj = {
            "project_name": st.session_state.project_name,
            "corps_meuble": f"Caisson {cab_idx}",
            "quantity": 1,
            "date": datetime.date.today().strftime("%d/%m/%Y")
        }
        
        # Générer les plans pour ce caisson
        cav_t, car_t, cg_t, cd_t = get_automatic_edge_banding_export("Traverse")
        c_trav = {"Chant Avant": cav_t, "Chant Arrière": car_t, "Chant Gauche": cg_t, "Chant Droit": cd_t}
        # Les tourillons de traverse vont TOUJOURS sur tranche_cote (jamais sur les chants).
        tholes_longue = []
        tholes_cote = [{'type':'tourillon','x':t_tb/2,'y':y,'diam_str':"⌀8/22"} for y in ys_dowel]
        
        # Traverse Haute
        fig_th = draw_machining_view_pro_final(
            f"Traverse Haute (C{cab_idx})", L_trav, W_mont, t_tb, 
            st.session_state.unit_select, proj, c_trav, 
            traverse_face_holes_left, tholes_longue, tholes_cote, None, False
        )
        # Les traverses ont des tourillons sur les 2 tranches (gauche et droite).
        _append_figure(
            f"Traverse Haute (C{cab_idx})",
            fig_th,
            traverse_face_holes_left,
            tholes_longue,
            tholes_cote,
            tholes_cote,
        )
        
        # Traverse Basse
        fig_tb = draw_machining_view_pro_final(
            f"Traverse Basse (C{cab_idx})", L_trav, W_mont, t_tb,
            st.session_state.unit_select, proj, c_trav,
            traverse_face_holes_right, tholes_longue, tholes_cote, None, False
        )
        # Les traverses ont des tourillons sur les 2 tranches (gauche et droite).
        _append_figure(
            f"Traverse Basse (C{cab_idx})",
            fig_tb,
            traverse_face_holes_right,
            tholes_longue,
            tholes_cote,
            tholes_cote,
        )
        
        # Montants
        cav_m, car_m, cg_m, cd_m = get_automatic_edge_banding_export("Montant")
        c_mont = {"Chant Avant": cav_m, "Chant Arrière": car_m, "Chant Gauche": cg_m, "Chant Droit": cd_m}
        
        fig_mg = draw_machining_view_pro_final(
            f"Montant Gauche (C{cab_idx})", W_mont, h_side, t_lr,
            st.session_state.unit_select, proj, c_mont,
            holes_mg, tranche_holes_mg, [], None, False
        )
        _append_figure(f"Montant Gauche (C{cab_idx})", fig_mg, holes_mg, tranche_holes_mg)
        
        fig_md = draw_machining_view_pro_final(
            f"Montant Droit (C{cab_idx})", W_mont, h_side, t_lr,
            st.session_state.unit_select, proj, c_mont,
            holes_md, tranche_holes_md, [], None, False
        )
        _append_figure(f"Montant Droit (C{cab_idx})", fig_md, holes_md, tranche_holes_md)
        
        # Panneau arrière
        if base_el.get('has_back_panel', True):
            cav_f, car_f, cg_f, cd_f = get_automatic_edge_banding_export("Fond")
            c_fond = {"Chant Avant": cav_f, "Chant Arrière": car_f, "Chant Gauche": cg_f, "Chant Droit": cd_f}
            fig_fond = draw_machining_view_pro_final(
                f"Panneau Arrière (C{cab_idx})", W_back, H_back, t_fb,
                st.session_state.unit_select, proj, c_fond,
                holes_fond, [], [], None, False
            )
            _append_figure(f"Panneau Arrière (C{cab_idx})", fig_fond, holes_fond)
        
        # Montants secondaires (diviseurs verticaux)
        if 'vertical_dividers' in cab and cab['vertical_dividers']:
            added_divider_sheet_titles = set()
            for div_idx, div in enumerate(cab['vertical_dividers']):
                div_h = div.get('height', h_side)
                div_w = W_mont
                div_th = div.get('thickness', 19.0)
                div_tranche_holes = get_vertical_divider_tranche_holes(W_mont, div_th)
                c_div_12 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":True}
                c_div_22 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":True, "Chant Droit":False}
                
                fig_div1 = draw_machining_view_pro_final(
                    f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 1/2",
                    div_w, div_h, div_th, st.session_state.unit_select, proj, c_div_12,
                    divider_element_holes_left[div_idx], div_tranche_holes, [], None, False
                )
                _append_figure(
                    f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 1/2",
                    fig_div1,
                    divider_element_holes_left[div_idx],
                    div_tranche_holes,
                )
                added_divider_sheet_titles.add(f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 1/2")
                
                fig_div2 = draw_machining_view_pro_final(
                    f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 2/2",
                    div_w, div_h, div_th, st.session_state.unit_select, proj, c_div_22,
                    divider_element_holes_right[div_idx], div_tranche_holes, [], None, False
                )
                _append_figure(
                    f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 2/2",
                    fig_div2,
                    divider_element_holes_right[div_idx],
                    div_tranche_holes,
                )
                added_divider_sheet_titles.add(f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 2/2")

            # Garde-fou: ajouter les feuilles manquantes de montants secondaires si nécessaire.
            for div_idx, div in enumerate(cab['vertical_dividers']):
                div_h = div.get('height', h_side)
                div_w = W_mont
                div_th = div.get('thickness', 19.0)
                div_tranche_holes = get_vertical_divider_tranche_holes(W_mont, div_th)
                c_div_12 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":False, "Chant Droit":True}
                c_div_22 = {"Chant Avant":False, "Chant Arrière":False, "Chant Gauche":True, "Chant Droit":False}

                title_12 = f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 1/2"
                if title_12 not in added_divider_sheet_titles:
                    fig_div1 = draw_machining_view_pro_final(
                        title_12,
                        div_w, div_h, div_th, st.session_state.unit_select, proj, c_div_12,
                        divider_element_holes_left.get(div_idx, []), div_tranche_holes, [], None, False
                    )
                    _append_figure(title_12, fig_div1, divider_element_holes_left.get(div_idx, []), div_tranche_holes)
                    added_divider_sheet_titles.add(title_12)

                title_22 = f"Montant Secondaire {div_idx+1} (C{cab_idx}) - 2/2"
                if title_22 not in added_divider_sheet_titles:
                    fig_div2 = draw_machining_view_pro_final(
                        title_22,
                        div_w, div_h, div_th, st.session_state.unit_select, proj, c_div_22,
                        divider_element_holes_right.get(div_idx, []), div_tranche_holes, [], None, False
                    )
                    _append_figure(title_22, fig_div2, divider_element_holes_right.get(div_idx, []), div_tranche_holes)
                    added_divider_sheet_titles.add(title_22)
        
        # Étagères
        if shelves_list:
            shelf_groups = {}
            for s in shelves_list:
                if s.get('enabled', True):
                    # Règle demandée: étagère = traverse (mêmes dimensions).
                    s_L = L_trav
                    s_W = W_raw
                    s_T = s.get('thickness', 19.0)
                    s_type = s.get('shelf_type', 'mobile')
                    th_shelf = []
                    if s_type == 'fixe':
                        shelf_depth_for_dowels = max(0.0, float(s_W))
                        _, ys_dowel_shelf = calculate_hole_positions(shelf_depth_for_dowels)
                        th_shelf = [
                            {'type': 'tourillon', 'x': float(s_T) / 2.0, 'y': y_pos, 'diam_str': "⌀8/22"}
                            for y_pos in ys_dowel_shelf
                        ]
                    shelf_key = (s_type, s_L, s_W, s_T, tuple((h['type'], h['x'], h['y'], h['diam_str']) for h in th_shelf))
                    if shelf_key not in shelf_groups:
                        cav_e, car_e, cg_e, cd_e = get_automatic_edge_banding_export("Étagère")
                        c_etag = {"Chant Avant": cav_e, "Chant Arrière": car_e, "Chant Gauche": cg_e, "Chant Droit": cd_e}
                        shelf_groups[shelf_key] = {
                            'title': f"Étagère {s_type.capitalize()} (C{cab_idx})",
                            'L': s_L, 'W': s_W, 'T': s_T,
                            'ch': c_etag, 'fh': [], 't_long_h': [], 't_cote_h': th_shelf,
                            'cut': None, 'quantity': 0
                        }
                    shelf_groups[shelf_key]['quantity'] += 1
            
            for shelf_key, shelf_data in shelf_groups.items():
                title = shelf_data['title']
                if shelf_data['quantity'] > 1:
                    title = f"{title} (x{shelf_data['quantity']})"
                proj_shelf = proj.copy()
                proj_shelf['quantity'] = shelf_data['quantity']
                fig_shelf = draw_machining_view_pro_final(
                    title, shelf_data['L'], shelf_data['W'], shelf_data['T'],
                    st.session_state.unit_select, proj_shelf, shelf_data['ch'],
                    shelf_data['fh'], shelf_data['t_long_h'], shelf_data['t_cote_h'],
                    shelf_data['cut'], False
                )
                # Les étagères fixes ont des tourillons sur 2 tranches.
                if "Fixe" in title or "fixe" in title:
                    _append_figure(
                        title,
                        fig_shelf,
                        shelf_data['fh'],
                        shelf_data['t_long_h'],
                        shelf_data['t_cote_h'],
                        shelf_data['t_cote_h'],
                        quantity=shelf_data['quantity'],
                    )
                else:
                    _append_figure(
                        title,
                        fig_shelf,
                        shelf_data['fh'],
                        shelf_data['t_long_h'],
                        shelf_data['t_cote_h'],
                        quantity=shelf_data['quantity'],
                    )
        
        # Portes (logique complète: simple/double + perçages de charnières)
        if cab.get('door_props', {}).get('has_door', False):
            dp = cab['door_props']
            dH = H_raw + 80.0 - dp['door_gap'] if dp.get('door_model') == 'floor_length' else H_raw - (2 * dp['door_gap'])
            hinge_base_h = max(1.0, dH - 80.0) if dp.get('door_model') == 'floor_length' else dH
            hinge_y_offset = 80.0 if dp.get('door_model') == 'floor_length' else 0.0

            if dp.get('hinge_mode') == 'custom' and dp.get('custom_hinge_positions'):
                y_h = get_hinge_y_positions(hinge_base_h, custom_positions=dp['custom_hinge_positions'])
            else:
                y_h = get_hinge_y_positions(hinge_base_h)
            y_h = [y + hinge_y_offset for y in y_h]

            holes_p = []
            door_type = dp.get('door_type', 'single')
            if door_type == 'double':
                dW = (L_raw - (2 * dp['door_gap'])) / 2.0
                xc = 23.5
                xv = 33.0
                for y in y_h:
                    holes_p.append({'type': 'tourillon', 'x': xc, 'y': y, 'diam_str': "⌀35"})
                    holes_p.append({'type': 'vis', 'x': xv, 'y': y + 22.5, 'diam_str': "⌀8"})
                    holes_p.append({'type': 'vis', 'x': xv, 'y': y - 22.5, 'diam_str': "⌀8"})
            else:
                dW = L_raw - (2 * dp['door_gap'])
                xc = 23.5 if dp.get('door_opening') == 'left' else dW - 23.5
                xv = 33.0 if dp.get('door_opening') == 'left' else dW - 33.0
                for y in y_h:
                    holes_p.append({'type': 'tourillon', 'x': xc, 'y': y, 'diam_str': "⌀35"})
                    holes_p.append({'type': 'vis', 'x': xv, 'y': y + 22.5, 'diam_str': "⌀8"})
                    holes_p.append({'type': 'vis', 'x': xv, 'y': y - 22.5, 'diam_str': "⌀8"})

            c_fa = {"Chant Avant": True, "Chant Arrière": True, "Chant Gauche": True, "Chant Droit": True}
            fig_door = draw_machining_view_pro_final(
                f"Porte (C{cab_idx})", dW, dH, dp.get('door_thickness', 19.0),
                st.session_state.unit_select, proj, c_fa, holes_p, [], [], None, False
            )
            _append_figure(f"Porte (C{cab_idx})", fig_door, holes_p)

    if include_hole_counts:
        return all_figures, hole_counts
    return all_figures
