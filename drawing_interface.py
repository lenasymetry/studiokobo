import plotly.graph_objects as go
import numpy as np
import re
import base64
import os
import io

from machining_logic import is_drawer_slide_hole, merge_drawer_panel_holes

try:
    from PIL import Image
except ImportError:
    def load_image_base64(filename): return None

if 'Image' in locals():
    def load_image_base64(filename):
        candidates = [filename]
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, filename))
        candidates.append(os.path.join(os.path.dirname(script_dir), filename))
        final_path = None
        for path in candidates:
            if os.path.exists(path):
                final_path = path
                break
        if not final_path: return None
        try:
            img = Image.open(final_path)
            output_buffer = io.BytesIO()
            img.save(output_buffer, format="PNG")
            encoded = base64.b64encode(output_buffer.getvalue()).decode()
            return f"data:image/png;base64,{encoded}"
        except Exception as e:
            return None

def format_number_no_decimal(val):
    """Formate un nombre en retirant les virgules zéro (.0) si c'est un nombre rond."""
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val)}"
        else:
            return f"{val:.1f}".rstrip('0').rstrip('.')
    return f"{val}"

def create_hatch_lines(x0, y0, x1, y1, density=20):
    lines_x, lines_y = [], []
    xmin, xmax = min(x0, x1), max(x0, x1)
    ymin, ymax = min(y0, y1), max(y0, y1)
    start_c = ymin - xmax
    end_c = ymax - xmin
    c = start_c
    while c <= end_c:
        pts = []
        if ymin <= xmin + c <= ymax: pts.append((xmin, xmin + c))
        if ymin <= xmax + c <= ymax: pts.append((xmax, xmax + c))
        if xmin <= ymin - c <= xmax: pts.append((ymin - c, ymin))
        if xmin <= ymax - c <= xmax: pts.append((ymax - c, ymax))
        pts = sorted(list(set(pts)))
        if len(pts) >= 2:
            lines_x.extend([pts[0][0], pts[-1][0], None])
            lines_y.extend([pts[0][1], pts[-1][1], None])
        c += density
    return lines_x, lines_y

def calculate_stagger_levels(coords, min_dist=45):
    if not coords: return []
    indices = np.argsort(coords)
    levels = [0] * len(coords)
    last_pos_at_level = {}
    for i in indices:
        val = coords[i]
        lvl = 0
        while True:
            last_val = last_pos_at_level.get(lvl, -99999)
            if (val - last_val) >= min_dist:
                levels[i] = lvl
                last_pos_at_level[lvl] = val
                break
            lvl += 1
    return levels

def find_nearest_edge(x, y, L, W):
    """
    Trouve le bord le plus proche d'un point (x, y) dans un panneau de dimensions L x W.
    Retourne le bord ('top', 'bottom', 'left', 'right') et les coordonnées du point sur ce bord.
    """
    dist_to_top = abs(y - W)
    dist_to_bottom = abs(y - 0)
    dist_to_left = abs(x - 0)
    dist_to_right = abs(x - L)
    
    min_dist = min(dist_to_top, dist_to_bottom, dist_to_left, dist_to_right)
    
    if min_dist == dist_to_top:
        return 'top', (x, W)
    elif min_dist == dist_to_bottom:
        return 'bottom', (x, 0)
    elif min_dist == dist_to_left:
        return 'left', (0, y)
    else:
        return 'right', (L, y)

def get_short_extension_line(x_hole, y_hole, L, W, extension_length=12):
    """
    Crée une ligne de rappel courte depuis un trou vers le bord le plus proche.
    Retourne (x_start, y_start, x_end, y_end) où (x_start, y_start) est sur le bord le plus proche
    et (x_end, y_end) est à extension_length du bord vers l'extérieur.
    """
    edge, (x_edge, y_edge) = find_nearest_edge(x_hole, y_hole, L, W)
    
    if edge == 'top':
        return (x_edge, y_edge, x_edge, y_edge + extension_length)
    elif edge == 'bottom':
        return (x_edge, y_edge, x_edge, y_edge - extension_length)
    elif edge == 'left':
        return (x_edge, y_edge, x_edge - extension_length, y_edge)
    else:  # right
        return (x_edge, y_edge, x_edge + extension_length, y_edge)

# Tracker global pour éviter les doublons d'annotations
_annotation_tracker = set()


def _push_dxf_dimension(fig, dim_payload):
    if fig is None or not hasattr(fig, "layout"):
        return
    meta = getattr(fig.layout, "meta", None) or {}
    dims = list(meta.get("dxf_dimensions", []))
    dims.append(dim_payload)
    meta["dxf_dimensions"] = dims
    fig.layout.meta = meta

def _push_dxf_triangle(fig, triangle_payload):
    """Stocke les métadonnées d'un triangle pour export DXF."""
    if fig is None or not hasattr(fig, "layout"):
        return
    meta = getattr(fig.layout, "meta", None) or {}
    triangles = list(meta.get("dxf_triangles", []))
    triangles.append(triangle_payload)
    meta["dxf_triangles"] = triangles
    fig.layout.meta = meta

class DimItem:
    """Représente une cote avec ses propriétés pour l'empilement intelligent."""
    def __init__(self, axis, p0, p1, text, base_offset, layer, kind='hole',
                 feature_pos=None, x_dim_pos=None, y_dim_pos=None, chain_id=None, color_group=None):
        """
        axis: 'x' (horizontale) ou 'y' (verticale)
        p0, p1: coordonnées projetées le long de l'axe perpendiculaire
               - pour axis='x' : p0, p1 sont des x (positions horizontales)
               - pour axis='y' : p0, p1 sont des y (positions verticales)
        text: string de la cote, ex "178"
        base_offset: distance de base par rapport à la pièce (palier 1=30, 2=50, 3=70)
        layer: 0 = cotes globales, 1 = perçages, 2 = accessoires
        kind: 'global', 'hole', 'accessory', etc.
        feature_pos: (x, y) d'un point caractéristique (pour collisions avec trous)
        x_dim_pos, y_dim_pos: positions X/Y de la ligne de cotation (pour axis='y' et 'x' respectivement)
        """
        self.axis = axis
        self.p0 = p0
        self.p1 = p1
        self.text = text
        self.base_offset = base_offset
        self.layer = layer
        self.kind = kind
        self.feature_pos = feature_pos
        self.x_dim_pos = x_dim_pos  # Position X de la ligne de cotation (pour axis='y')
        self.y_dim_pos = y_dim_pos  # Position Y de la ligne de cotation (pour axis='x')
        self.chain_id = chain_id    # Identifiant de chaîne de cotes (pour regrouper et entourer)
        self.color_group = color_group  # Groupe de couleur pour cette cotation
        # Ces champs seront calculés par l'algorithme
        self.final_offset = base_offset
        self.skip = False
        self.final_x_dim = x_dim_pos
        self.final_y_dim = y_dim_pos

def stack_dimensions_on_axis(dim_items, min_gap=25.0, step_per_conflict=30.0, panel_L=None, panel_W=None):
    """
    Empile les cotes sur un même axe pour éviter les chevauchements.
    
    Args:
        dim_items: liste de DimItem tous sur le même axis
        min_gap: distance minimale entre cotes (en mm sur l'axe long) - AUGMENTÉ pour autoscale
        step_per_conflict: décalage supplémentaire (en mm) quand deux cotes se touchent au même palier
    
    Returns:
        Liste de DimItem avec final_offset calculé
    """
    if not dim_items:
        return []
    
    # Trier par layer (palier), puis par position moyenne (p0+p1)/2
    dim_items.sort(key=lambda d: (d.layer, (d.p0 + d.p1) * 0.5))
    
    # Garder pour chaque layer une liste des intervalles déjà placés avec leurs tailles estimées
    placed_by_layer = {}
    
    for dim in dim_items:
        if dim.skip:
            continue
        layer = dim.layer
        mid = 0.5 * (dim.p0 + dim.p1)
        
        # Estimer la taille du texte pour cette cote (relatif au panneau pour fonctionner à tous les zooms)
        text_length = len(str(dim.text))
        base_text_size = max(25, text_length * 10)
        
        # Ajuster la taille estimée en fonction de la taille du panneau (relatif au zoom)
        if panel_L and panel_W:
            min_dim = min(panel_L, panel_W)
            relative_text_size = min_dim * 0.025  # 2.5% de la dimension minimale
            text_size_estimate = max(base_text_size, relative_text_size)
        else:
            text_size_estimate = base_text_size
        
        if layer not in placed_by_layer:
            placed_by_layer[layer] = []
        
        # Chercher si cette cote est trop proche d'une cote déjà placée sur le même layer
        # En tenant compte de la taille des textes et de la taille du panneau
        conflicts = []
        for (other_mid, other_text, other_size) in placed_by_layer[layer]:
            # Calculer la distance requise en fonction des tailles des deux textes
            combined_size = (text_size_estimate + other_size) / 2
            
            # Ajuster min_gap pour être relatif au panneau
            if panel_L and panel_W:
                min_dim = min(panel_L, panel_W)
                relative_gap = min_dim * 0.03  # 3% de la dimension minimale
                effective_min_gap = max(min_gap, relative_gap)
            else:
                effective_min_gap = min_gap
            
            required_gap = max(effective_min_gap, combined_size * 1.2)  # Augmenté de 0.8 à 1.2 pour plus d'espace
            
            if abs(mid - other_mid) < required_gap:
                conflicts.append((other_mid, other_text, other_size))
        
        if conflicts:
            # Décalage progressif : plus il y a de conflits, plus on éloigne
            level = len(conflicts)
            
            # Ajuster step_per_conflict pour être relatif au panneau
            if panel_L and panel_W:
                min_dim = min(panel_L, panel_W)
                relative_step = min_dim * 0.02  # 2% de la dimension minimale
                effective_step = max(step_per_conflict, relative_step)
            else:
                effective_step = step_per_conflict
            
            # RÈGLE SPÉCIALE POUR LES TROUS : ne jamais s'éloigner trop du trou (max ~2cm écran)
            if dim.kind == 'hole':
                dim.final_offset = dim.base_offset  # on garde l'offset de base, très proche du trou
            else:
                dim.final_offset = dim.base_offset + level * effective_step
        else:
            dim.final_offset = dim.base_offset
        
        # Ajuster aussi la position de la ligne de cotation si nécessaire
        if dim.axis == 'y' and dim.x_dim_pos is not None:
            # Pour les racks d'étagères dans la zone colorée : limiter le décalage pour rester dans la zone
            if dim.kind == 'shelf_rack':
                # Décalage minimal pour rester dans la zone colorée (max 15mm de chaque côté)
                # Les cotations seront empilées verticalement (en Y) via cascade_tracker, pas horizontalement
                shift = min(len(conflicts) * 5, 15)
            else:
                # Pour les autres cotes verticales, décalage normal
                shift = min(len(conflicts) * 12, 30)
            dim.final_x_dim = dim.x_dim_pos - shift
        elif dim.axis == 'x' and dim.y_dim_pos is not None:
            # Pour les racks d'étagères dans la zone colorée : limiter le décalage pour rester dans la zone
            if dim.kind == 'shelf_rack':
                # Décalage minimal relatif au panneau pour rester dans la zone colorée
                if panel_L and panel_W:
                    min_dim = min(panel_L, panel_W)
                    max_shift_rel = max(8, min_dim * 0.008)  # 0.8% de la dimension minimale, min 8mm
                    shift_per_conflict_rel = max(4, min_dim * 0.004)  # 0.4% par conflit, min 4mm
                    shift = min(len(conflicts) * shift_per_conflict_rel, max_shift_rel)
                else:
                    shift = min(len(conflicts) * 5, 10)
            else:
                # Pour les autres cotes horizontales, décalage normal et relatif
                if panel_L and panel_W:
                    min_dim = min(panel_L, panel_W)
                    max_shift_rel = max(25, min_dim * 0.025)  # 2.5% de la dimension minimale, min 25mm
                    shift_per_conflict_rel = max(10, min_dim * 0.01)  # 1% par conflit, min 10mm
                    shift = min(len(conflicts) * shift_per_conflict_rel, max_shift_rel)
                else:
                    shift = min(len(conflicts) * 12, 30)
            if dim.base_offset < 0:
                dim.final_y_dim = dim.y_dim_pos - shift
            else:
                dim.final_y_dim = dim.y_dim_pos + shift
        
        placed_by_layer[layer].append((mid, dim.text, text_size_estimate))
    
    return dim_items

def clean_overlapping_labels(labels, min_dist=12.0):
    """
    Nettoie les labels qui se chevauchent en les fusionnant ou en les décalant.
    
    Args:
        labels: liste de dicts {'x':..., 'y':..., 'text':..., 'axis': 'x'/'y'}
        min_dist: distance minimale entre labels
    
    Returns:
        Liste nettoyée de labels
    """
    result = []
    for lbl in sorted(labels, key=lambda l: (l.get('axis', ''), l.get('x', 0), l.get('y', 0))):
        if not result:
            result.append(lbl)
            continue
        
        last = result[-1]
        lbl_axis = lbl.get('axis', '')
        last_axis = last.get('axis', '')
        
        if (lbl_axis == last_axis and
            abs(lbl.get('x', 0) - last.get('x', 0)) < min_dist and
            abs(lbl.get('y', 0) - last.get('y', 0)) < min_dist and
            lbl.get('text') == last.get('text')):
            # Fusion simple : on garde un seul label (doublon)
            continue
        elif (lbl_axis == last_axis and
              abs(lbl.get('x', 0) - last.get('x', 0)) < min_dist and
              abs(lbl.get('y', 0) - last.get('y', 0)) < min_dist):
            # Déplacement latéral si nécessaire
            lbl = dict(lbl)
            if lbl_axis == 'x':
                lbl['y'] = lbl.get('y', 0) + min_dist  # décaler verticalement
            else:
                lbl['x'] = lbl.get('x', 0) + min_dist  # décaler horizontalement
            result.append(lbl)
        else:
            result.append(lbl)
    
    return result

def add_pro_dimension(fig, x0, y0, x1, y1, text_val, offset_dist, axis='x', color="black", font_size=11, line_dash='solid', xanchor=None, yanchor=None, rotate_coords_fn=None, vertical_dims_tracker=None, cascade_tracker=None, panel_bounds=None, panel_L=None, panel_W=None, is_montant=False, panel_name=None, zone_y_dim=None):
    tick_len = 5
    line_width = 0.8
    ext_overshoot = 2  # Réduit de 5 à 2 pour éviter que les lignes passent sur la face du panneau
    max_extension_length = 12  # Maximum 1cm (environ 12 unités) pour les lignes de rappel courtes
    
    # Formater text_val pour retirer les virgules zéro si c'est un nombre rond
    if isinstance(text_val, (int, float)):
        text_val = format_number_no_decimal(text_val)
    elif isinstance(text_val, str):
        # Essayer de convertir en nombre pour formater
        try:
            num_val = float(text_val)
            text_val = format_number_no_decimal(num_val)
        except:
            pass  # Garder la chaîne telle quelle si ce n'est pas un nombre
    
    # Pour les montants principaux : cotations verticales ET horizontales en bleu
    # (après rotation, les montants sont horizontaux sur la feuille).
    # Exception : si une couleur explicite est fournie (rouge pour la zone rouge),
    # on la respecte et on ne force pas le bleu.
    # Uniformiser : toutes les cotations (texte et traits) en NOIR,
    # pour tous les panneaux (montants compris).
    # Exception : préserver la couleur bleue UNIQUEMENT pour les charnières (explicitement passée)
    if color != "blue":
        color = "#000000"
    text_font = dict(color=color, size=font_size, family="Arial") 
    line_color = color
    text_bg = "white"
    
    # Pour les cotations internes des montants : traits en gris pointillés
    is_montant_internal = (panel_name and ("Montant Gauche" in panel_name or "Montant Droit" in panel_name) 
                           and panel_bounds is None)
    if is_montant_internal:
        line_color = "rgba(180,180,180,1.0)"  # Gris pour les traits internes des montants
    
    if xanchor is None: xanchor = 'center'
    if yanchor is None: yanchor = 'middle'
    
    # Transformer les coordonnées si rotation nécessaire
    if rotate_coords_fn:
        x0, y0 = rotate_coords_fn(x0, y0)
        x1, y1 = rotate_coords_fn(x1, y1)
        # Échanger l'axe après rotation pour que les cotations restent visuellement correctes
        # Après rotation 90° horaire : horizontal devient vertical et vice versa
        if rotate_coords_fn(0, 0) != (0, 0):  # Si rotation active
            axis = 'y' if axis == 'x' else 'x'
    
    if axis == 'x':
        # Détecter si c'est une cotation horizontale appartenant à un montant principal.
        # On ne restreint plus par offset_dist ou longueur du segment, afin que
        # toutes les cotes internes de montants (y compris charnières) bénéficient
        # du style "pro" avec traits de cotation complets.
        is_main_upright_horizontal = (
            panel_name
            and "Montant" in panel_name
            and panel_bounds is None
        )

        # Pour les montants, les cotations passées avec zone_y_dim correspondent déjà
        # aux chaînes internes spécifiques (zone rouge). On ne filtre plus par couleur
        # ici afin de pouvoir colorer chaque ligne de façon indépendante.
        
        # EXTÉRIORISATION : S'assurer que la cotation est à l'extérieur si panel_bounds est fourni
        # Pour axis='x', offset positif = au-dessus (extérieur), offset négatif = en-dessous (intérieur)
        # Si panel_bounds est fourni, forcer l'extériorisation
        if panel_bounds:
            panel_y_min, panel_y_max = panel_bounds.get('y', (0, 0))
            y_max_panel = max(y0, y1, panel_y_max)
            # S'assurer que y_dim est au-dessus du panneau (extérieur)
            if offset_dist <= 0:
                offset_dist = 20  # Minimum pour être à l'extérieur
            y_dim = y_max_panel + offset_dist
        else:
            # Si zone_y_dim est fourni, utiliser cette position (zone décalée)
            if zone_y_dim is not None:
                y_dim = zone_y_dim
            # Pour les cotations à côté des trous (p0 == p1), décaler légèrement la ligne de cotation
            # pour qu'elle ne passe pas directement sur le trou
            elif abs(x0 - x1) < 0.1 and panel_L and panel_W:
                # Décalage relatif au panneau pour fonctionner en autoscale
                min_dim = min(panel_L, panel_W)
                small_offset = max(5, min_dim * 0.005)  # 0.5% de la dimension minimale, min 5mm
                y_dim = y0 + small_offset
            else:
                y_dim = y0 + offset_dist if offset_dist != 0 else y0
        
        # Position du texte : avec cascade si cascade_tracker est fourni pour éviter les chevauchements
        # Pour les cotations dans la zone colorée : texte dans la zone décalée
        if is_main_upright_horizontal or zone_y_dim is not None:
            # Texte dans la zone décalée (zone_y_dim si fourni, sinon y_dim)
            text_y_pos = zone_y_dim if zone_y_dim is not None else y_dim
            text_x_center = (x0 + x1) / 2
            # Réduire l'espacement pour éviter les chevauchements
            cascade_min_gap_horizontal = 20
        else:
            # Position standard du texte par rapport à la ligne de cote horizontale
            text_y_pos_base = y_dim + (np.sign(offset_dist) * 15)
            text_x_center = (x0 + x1) / 2
            cascade_min_gap_horizontal = 45
            text_y_pos = text_y_pos_base
        
        # Appliquer la cascade pour les cotations horizontales si cascade_tracker est fourni
        # Pour les cotations dans la zone colorée (shelf_rack), espacement réduit et relatif
        is_shelf_rack_horizontal = (panel_bounds is None and offset_dist == 0.0)
        if is_shelf_rack_horizontal and panel_L and panel_W:
            # Espacement relatif au panneau pour fonctionner en autoscale
            min_dim = min(panel_L, panel_W)
            cascade_min_gap_horizontal = max(15, min_dim * 0.015)  # 1.5% de la dimension minimale, min 15mm
        else:
            cascade_min_gap_horizontal = 45 if not is_shelf_rack_horizontal else 20
        
        if cascade_tracker is not None:
            text_x_cascaded = check_cascade_overlap(text_x_center, text_val, cascade_tracker, axis='x', min_gap=cascade_min_gap_horizontal, panel_L=panel_L, panel_W=panel_W)
            cascade_tracker.append((text_x_cascaded, text_val, 'x'))
            text_x_center = text_x_cascaded

        # ---- FILTRAGE APRÈS ROTATION POUR LA ZONE ROUGE (CÔTÉ DROIT) : MONTANTS ----
        # Si, une fois la figure tournée et la cascade appliquée, le texte d'une cote
        # horizontale de montant se retrouve sur le côté droit (zone rouge externe),
        # on supprime cette cote.
        if panel_name and "Montant" in panel_name and panel_bounds:
            panel_x_min, panel_x_max = panel_bounds.get('x', (0, 0))
            # marge de 1mm au‑delà du bord droit
            if text_x_center > panel_x_max + 1.0:
                return
        
        # Pour les cotations dans la zone colorée : traits de cotation professionnels OBLIGATOIRES
        # Pour les montants, on ne veut PAS de trait bleu direct entre chaque trou et sa cote,
        # mais uniquement des traits entre les chiffres (gérés plus bas via shelf_rack_positions).
        # Vérifier si c'est une cotation à côté d'un trou (p0 == p1).
        is_shelf_rack_hole_dimension = (panel_name and ("Montant Gauche" in panel_name or "Montant Droit" in panel_name)
                                        and panel_bounds is None and abs(x0 - x1) < 0.1)
        
        should_draw_pro_lines = is_main_upright_horizontal or zone_y_dim is not None
        
        if should_draw_pro_lines:
            # Calculer des tailles relatives au panneau pour fonctionner en autoscale
            if panel_L and panel_W:
                min_dim = min(panel_L, panel_W)
                extension_length = min_dim * 0.008  # 0.8% de la dimension minimale
                arrow_len = min_dim * 0.003  # 0.3% de la dimension minimale
                line_width_pro = max(0.8, min_dim * 0.0008)  # Largeur relative
            else:
                extension_length = 8.0
                arrow_len = 3.0
                line_width_pro = 0.8
            
            # Si c'est un point unique (p0 == p1), c'est une cotation à côté d'un trou
            if abs(x0 - x1) < 0.1:
                # Pour les montants dans la zone bleue, on NE trace plus le trait
                # de rappel entre le trou et la cote (on garde seulement les traits
                # entre les chiffres, dessinés plus bas via shelf_rack_positions).
                if not (panel_name and ("Montant Gauche" in panel_name or "Montant Droit" in panel_name) and zone_y_dim is not None):
                    # Pour les autres panneaux, conserver le trait de rappel classique.
                    y_line_dim = zone_y_dim if zone_y_dim is not None else y_dim
                    fig.add_shape(
                        type="line",
                        x0=x0,
                        y0=y0,
                        x1=x0,
                        y1=y_line_dim,
                        line=dict(color=line_color, width=line_width_pro * 0.8, dash='dot')
                    )
                # Pas de ligne horizontale pour les cotations individuelles (sera reliée par des traits entre chiffres)
            else:
                # Cotation entre deux trous : ligne de cotation complète
                fig.add_shape(type="line", x0=x0, y0=y_dim, x1=x1, y1=y_dim, 
                             line=dict(color=line_color, width=line_width_pro, dash=line_dash))
                # Traits de rappel verticaux depuis les trous
                fig.add_shape(type="line", x0=x0, y0=y0, x1=x0, y1=y_dim, 
                             line=dict(color=line_color, width=line_width_pro*0.8, dash=line_dash))
                fig.add_shape(type="line", x0=x1, y0=y1, x1=x1, y1=y_dim, 
                             line=dict(color=line_color, width=line_width_pro*0.8, dash=line_dash))
                # Flèches aux extrémités (petits traits perpendiculaires)
                fig.add_shape(type="line", x0=x0-arrow_len, y0=y_dim, x1=x0+arrow_len, y1=y_dim, 
                             line=dict(color=line_color, width=line_width_pro*1.2, dash='solid'))
                fig.add_shape(type="line", x0=x1-arrow_len, y0=y_dim, x1=x1+arrow_len, y1=y_dim, 
                             line=dict(color=line_color, width=line_width_pro*1.2, dash='solid'))
        else:
            # TICKET #09 : ANCRAGE COURT - Lignes de rappel courtes depuis le bord le plus proche uniquement
            # Les lignes ne traversent JAMAIS le panneau - elles s'arrêtent court depuis le bord
            if panel_L is not None and panel_W is not None:
                # Trouver le bord le plus proche pour chaque point
                edge0, (x_edge0, y_edge0) = find_nearest_edge(x0, y0, panel_L, panel_W)
                edge1, (x_edge1, y_edge1) = find_nearest_edge(x1, y1, panel_L, panel_W)
                
                # Lignes de rappel courtes : depuis le bord vers l'extérieur seulement (max_extension_length)
                # Pour axis='x', les lignes sont verticales
                if edge0 == 'top':
                    ext_y0 = y_edge0 + max_extension_length
                    fig.add_shape(type="line", x0=x0, y0=y_edge0, x1=x0, y1=ext_y0, line=dict(color=line_color, width=0.5, dash=line_dash))
                elif edge0 == 'bottom':
                    ext_y0 = y_edge0 - max_extension_length
                    fig.add_shape(type="line", x0=x0, y0=y_edge0, x1=x0, y1=ext_y0, line=dict(color=line_color, width=0.5, dash=line_dash))
                else:
                    # Si le bord le plus proche est left/right, ligne horizontale courte
                    if edge0 == 'left':
                        ext_x0 = x_edge0 - max_extension_length
                    else:
                        ext_x0 = x_edge0 + max_extension_length
                    fig.add_shape(type="line", x0=x_edge0, y0=y0, x1=ext_x0, y1=y0, line=dict(color=line_color, width=0.5, dash=line_dash))
                
                if edge1 == 'top':
                    ext_y1 = y_edge1 + max_extension_length
                    fig.add_shape(type="line", x0=x1, y0=y_edge1, x1=x1, y1=ext_y1, line=dict(color=line_color, width=0.5, dash=line_dash))
                elif edge1 == 'bottom':
                    ext_y1 = y_edge1 - max_extension_length
                    fig.add_shape(type="line", x0=x1, y0=y_edge1, x1=x1, y1=ext_y1, line=dict(color=line_color, width=0.5, dash=line_dash))
                else:
                    # Si le bord le plus proche est left/right, ligne horizontale courte
                    if edge1 == 'left':
                        ext_x1 = x_edge1 - max_extension_length
                    else:
                        ext_x1 = x_edge1 + max_extension_length
                    fig.add_shape(type="line", x0=x_edge1, y0=y1, x1=ext_x1, y1=y1, line=dict(color=line_color, width=0.5, dash=line_dash))
            else:
                # Fallback : lignes courtes depuis y0/y1 vers l'extérieur
                if offset_dist > 0:
                    fig.add_shape(type="line", x0=x0, y0=y0, x1=x0, y1=y0 + max_extension_length, line=dict(color=line_color, width=0.5, dash=line_dash))
                    fig.add_shape(type="line", x0=x1, y0=y1, x1=x1, y1=y1 + max_extension_length, line=dict(color=line_color, width=0.5, dash=line_dash))
                else:
                    fig.add_shape(type="line", x0=x0, y0=y0, x1=x0, y1=y0 - max_extension_length, line=dict(color=line_color, width=0.5, dash=line_dash))
                    fig.add_shape(type="line", x0=x1, y0=y1, x1=x1, y1=y1 - max_extension_length, line=dict(color=line_color, width=0.5, dash=line_dash))
            # Ligne de cotation principale
            fig.add_shape(type="line", x0=x0, y0=y_dim, x1=x1, y1=y_dim, line=dict(color=line_color, width=line_width, dash=line_dash))
            fig.add_shape(type="line", x0=x0, y0=y_dim-tick_len, x1=x0, y1=y_dim+tick_len, line=dict(color=line_color, width=1.2, dash='solid'))
            fig.add_shape(type="line", x0=x1, y0=y_dim-tick_len, x1=x1, y1=y_dim+tick_len, line=dict(color=line_color, width=1.2, dash='solid'))
        
        # Éviter les doublons : vérifier si cette annotation existe déjà
        annotation_key = (round(text_x_center, 1), round(text_y_pos, 1), str(text_val), axis)
        if annotation_key not in _annotation_tracker:
            # Pour les cotations horizontales dans la zone colorée (shelf_rack) sur montants principaux :
            # - rotation de 90° pour faciliter la lecture
            # - taille de police réduite et relative pour éviter les chevauchements en autoscale
            # - texte dans la zone décalée (à côté des trous)
            if is_main_upright_horizontal or zone_y_dim is not None:
                # Taille de police relative au panneau pour fonctionner en autoscale
                # Réduite encore plus pour les cotations dans la zone rouge
                if panel_L and panel_W:
                    min_dim = min(panel_L, panel_W)
                    font_size_relative = max(5, min_dim * 0.002)  # 0.2% de la dimension minimale, min 5 (réduit)
                else:
                    font_size_relative = 6  # Réduit de 8 à 6
                
                # Forcer la couleur bleue pour les cotations dans la zone rouge
                # Les charnières sont maintenant en noir, donc on ne force plus le bleu pour elles
                text_color = "blue" if zone_y_dim is not None else color
                text_font_small = dict(color=text_color, size=font_size_relative, family="Arial")
                # Pour les cotations des charnières (32 et 20) : fond totalement transparent
                # Détecter si c'est une cotation de charnière : texte est "32" ou "20" (les charnières sont maintenant en noir)
                is_hinge_dimension = (str(text_val).strip() == "32" or str(text_val).strip() == "20")
                annotation_params = {
                    'x': text_x_center,
                    'y': text_y_pos,
                    'text': str(text_val),
                    'showarrow': False,
                    'textangle': 0,
                    'font': text_font_small,
                    'yanchor': 'middle',
                    'xanchor': 'center'
                }
                # Pour les charnières : bgcolor explicitement None pour fond totalement transparent
                # Pour les autres : fond blanc semi-transparent
                if is_hinge_dimension:
                    annotation_params['bgcolor'] = None  # Fond totalement transparent pour charnières
                else:
                    annotation_params['bgcolor'] = 'rgba(255,255,255,0.8)'
            else:
                annotation_params = {
                    'x': text_x_center,
                    'y': text_y_pos,
                    'text': str(text_val),
                    'showarrow': False,
                    'font': text_font,
                    'bgcolor': text_bg,
                    'yanchor': yanchor,
                    'xanchor': xanchor
                }
            fig.add_annotation(**annotation_params)
            _annotation_tracker.add(annotation_key)

        side = "top" if offset_dist > 0 else "bottom"
        _push_dxf_dimension(
            fig,
            {
                "axis": "x",
                "p1": (float(x0), float(y0)),
                "p2": (float(x1), float(y1)),
                "offset": float(abs(offset_dist)),
                "side": side,
                "text": str(text_val),
                "dim_line": float(y_dim),
                "label": (float(text_x_center), float(text_y_pos)),
            },
        )
    elif axis == 'y':
        # EXTÉRIORISATION : S'assurer que la cotation est à l'extérieur si panel_bounds est fourni
        # Pour axis='y', offset négatif = à gauche (extérieur), offset positif = à droite (intérieur)
        # Si panel_bounds est fourni, forcer l'extériorisation
        if panel_bounds:
            panel_x_min, panel_x_max = panel_bounds.get('x', (0, 0))
            x_min_panel = min(x0, x1, panel_x_min)
            # S'assurer que x_dim est à gauche du panneau (extérieur)
            if offset_dist >= 0:
                offset_dist = -20  # Minimum pour être à l'extérieur
            x_dim = x_min_panel + offset_dist
        else:
            x_dim = x0 + offset_dist
        
        # Vérifier les chevauchements pour les cotations verticales
        if vertical_dims_tracker is not None:
            adjusted_x_dim, should_skip = check_vertical_dim_overlap(x_dim, y0, y1, vertical_dims_tracker, min_gap=30)
            if should_skip:
                return
            x_dim = adjusted_x_dim
            # Ajouter cette cotation au tracker
            vertical_dims_tracker.append((x_dim, y0, y1))
        
        # Position du texte : avec cascade si cascade_tracker est fourni (pour montants avec plusieurs éléments)
        text_x_pos_base = x_dim + (np.sign(offset_dist) * 15)
        text_y_center = (y0 + y1) / 2
        
        # Pour les montants : espacement adapté selon le type de cotation
        # Pour les cotations dans la zone colorée (shelf_rack), espacement plus petit
        # Détecter les cotations shelf_rack verticales dans la zone rouge (position X élevée, offset 0)
        is_shelf_rack_dim = (panel_bounds is None and offset_dist == 0.0)  # Détecter les cotations shelf_rack
        # Pour les cotations verticales dans la zone rouge : utiliser couleur bleue et taille réduite
        if is_shelf_rack_dim and panel_L and x_dim > panel_L * 0.7:  # Si position X > 70% de la largeur, c'est dans la zone rouge
            cascade_min_gap = 20  # Espacement encore plus réduit pour rester dans la zone
            # Forcer couleur bleue et taille réduite pour les cotations dans la zone rouge
            if color == "black":  # Seulement si pas déjà défini
                color = "blue"
            if font_size >= 10:  # Seulement si pas déjà réduit
                font_size = 6
        elif is_shelf_rack_dim:
            cascade_min_gap = 30  # Espacement réduit pour rester dans la zone
        else:
            cascade_min_gap = 70 if is_montant else 45
        
        if cascade_tracker is not None:
            # Utiliser la cascade pour éviter les chevauchements de textes (espacement adapté)
            text_y_cascaded = check_cascade_overlap(text_y_center, text_val, cascade_tracker, axis='y', min_gap=cascade_min_gap, panel_L=panel_L, panel_W=panel_W)
            cascade_tracker.append((text_y_cascaded, text_val, 'y'))
            text_y_center = text_y_cascaded
        text_x_pos = text_x_pos_base
        
        # Définir la police de texte pour les cotations verticales
        text_font = dict(color=color, size=font_size, family="Arial")
        text_bg = 'rgba(255,255,255,0.8)' if is_shelf_rack_dim and panel_L and x_dim > panel_L * 0.7 else 'rgba(255,255,255,0.9)'
        xanchor = 'left' if offset_dist < 0 else 'right'
        yanchor = 'middle'

        # ---- FILTRAGE APRÈS ROTATION POUR LA ZONE ROUGE (CÔTÉ DROIT) : MONTANTS ----
        # Même logique que pour axis='x' : si la position finale du texte est à droite
        # du panneau (zone rouge), on supprime la cote.
        if panel_name and "Montant" in panel_name and panel_bounds:
            panel_x_min, panel_x_max = panel_bounds.get('x', (0, 0))
            if text_x_pos > panel_x_max + 1.0:
                return
        
        # TICKET #09 : ANCRAGE COURT - Lignes de rappel courtes depuis le bord le plus proche uniquement
        # Les lignes ne traversent JAMAIS le panneau - elles s'arrêtent court depuis le bord
        if panel_L is not None and panel_W is not None:
            # Trouver le bord le plus proche pour chaque point
            edge0, (x_edge0, y_edge0) = find_nearest_edge(x0, y0, panel_L, panel_W)
            edge1, (x_edge1, y_edge1) = find_nearest_edge(x1, y1, panel_L, panel_W)
            
            # Lignes de rappel courtes : depuis le bord vers l'extérieur seulement (max_extension_length)
            # Pour axis='y', les lignes sont horizontales
            if edge0 == 'left':
                ext_x0 = x_edge0 - max_extension_length
                fig.add_shape(type="line", x0=x_edge0, y0=y0, x1=ext_x0, y1=y0, line=dict(color=line_color, width=0.5, dash=line_dash))
            elif edge0 == 'right':
                ext_x0 = x_edge0 + max_extension_length
                fig.add_shape(type="line", x0=x_edge0, y0=y0, x1=ext_x0, y1=y0, line=dict(color=line_color, width=0.5, dash=line_dash))
            else:
                # Si le bord le plus proche est top/bottom, ligne verticale courte
                if edge0 == 'top':
                    ext_y0 = y_edge0 + max_extension_length
                else:
                    ext_y0 = y_edge0 - max_extension_length
                fig.add_shape(type="line", x0=x0, y0=y_edge0, x1=x0, y1=ext_y0, line=dict(color=line_color, width=0.5, dash=line_dash))
            
            if edge1 == 'left':
                ext_x1 = x_edge1 - max_extension_length
                fig.add_shape(type="line", x0=x_edge1, y0=y1, x1=ext_x1, y1=y1, line=dict(color=line_color, width=0.5, dash=line_dash))
            elif edge1 == 'right':
                ext_x1 = x_edge1 + max_extension_length
                fig.add_shape(type="line", x0=x_edge1, y0=y1, x1=ext_x1, y1=y1, line=dict(color=line_color, width=0.5, dash=line_dash))
            else:
                # Si le bord le plus proche est top/bottom, ligne verticale courte
                if edge1 == 'top':
                    ext_y1 = y_edge1 + max_extension_length
                else:
                    ext_y1 = y_edge1 - max_extension_length
                fig.add_shape(type="line", x0=x1, y0=y_edge1, x1=x1, y1=ext_y1, line=dict(color=line_color, width=0.5, dash=line_dash))
        else:
            # Fallback : lignes courtes depuis x0/x1 vers l'extérieur
            if offset_dist < 0:
                fig.add_shape(type="line", x0=x0, y0=y0, x1=x0 - max_extension_length, y1=y0, line=dict(color=line_color, width=0.5, dash=line_dash))
                fig.add_shape(type="line", x0=x1, y0=y1, x1=x1 - max_extension_length, y1=y1, line=dict(color=line_color, width=0.5, dash=line_dash))
            else:
                fig.add_shape(type="line", x0=x0, y0=y0, x1=x0 + max_extension_length, y1=y0, line=dict(color=line_color, width=0.5, dash=line_dash))
                fig.add_shape(type="line", x0=x1, y0=y1, x1=x1 + max_extension_length, y1=y1, line=dict(color=line_color, width=0.5, dash=line_dash))
        # Ligne de cotation principale
        # Pour les cotations verticales bleues dans la zone rouge : utiliser des traits pointillés
        line_dash_to_use = 'dot' if (axis == 'y' and color == "blue" and panel_L and x_dim > panel_L * 0.7) else line_dash
        fig.add_shape(type="line", x0=x_dim, y0=y0, x1=x_dim, y1=y1, line=dict(color=line_color, width=line_width, dash=line_dash_to_use))
        fig.add_shape(type="line", x0=x_dim-tick_len, y0=y0, x1=x_dim+tick_len, y1=y0, line=dict(color=line_color, width=1.2, dash='solid'))
        fig.add_shape(type="line", x0=x_dim-tick_len, y0=y1, x1=x_dim+tick_len, y1=y1, line=dict(color=line_color, width=1.2, dash='solid'))
        
        # Éviter les doublons : vérifier si cette annotation existe déjà
        annotation_key = (round(text_x_pos, 1), round(text_y_center, 1), str(text_val), axis)
        if annotation_key not in _annotation_tracker:
            # Pour les cotations dans la zone rouge : utiliser couleur bleue et taille réduite
            if is_shelf_rack_dim and panel_L and x_dim > panel_L * 0.7:
                # Créer une police avec couleur bleue et taille réduite
                text_font_zone = dict(color="blue", size=max(5, font_size), family="Arial")
                fig.add_annotation(x=text_x_pos, y=text_y_center, text=str(text_val), showarrow=False, textangle=-90, font=text_font_zone, bgcolor='rgba(255,255,255,0.8)', xanchor=xanchor, yanchor=yanchor)
            else:
                fig.add_annotation(x=text_x_pos, y=text_y_center, text=str(text_val), showarrow=False, textangle=-90, font=text_font, bgcolor=text_bg, xanchor=xanchor, yanchor=yanchor)
            _annotation_tracker.add(annotation_key)

        side = "left" if offset_dist < 0 else "right"
        _push_dxf_dimension(
            fig,
            {
                "axis": "y",
                "p1": (float(x0), float(y0)),
                "p2": (float(x1), float(y1)),
                "offset": float(abs(offset_dist)),
                "side": side,
                "text": str(text_val),
                "dim_line": float(x_dim),
                "label": (float(text_x_pos), float(text_y_center)),
            },
        )

def check_label_overlap(new_pos, existing_positions, min_dist=80):
    """Vérifie si une nouvelle position chevauche avec des positions existantes."""
    nx, ny = new_pos
    for ex, ey in existing_positions:
        dist = ((nx - ex)**2 + (ny - ey)**2)**0.5
        if dist < min_dist: return True
    return False

class DimensionAnnotation:
    """Classe pour stocker une annotation de dimension avant son placement final."""
    def __init__(self, x, y, text, axis='x', font_size=10, panel_L=None, panel_W=None):
        self.x = x
        self.y = y
        self.text = str(text)
        self.axis = axis
        self.font_size = font_size
        self.panel_L = panel_L
        self.panel_W = panel_W
        self.final_x = x
        self.final_y = y
        self.skip = False
    
    def get_text_size_estimate(self):
        """Estime la taille du texte en unités de données."""
        text_length = len(self.text)
        # Estimation basée sur la taille du panneau pour être relatif au zoom
        base_size = max(15, text_length * 6)
        if self.panel_L and self.panel_W:
            # Taille relative : environ 2% de la plus petite dimension du panneau
            min_dim = min(self.panel_L, self.panel_W)
            relative_size = min_dim * 0.02
            return max(base_size, relative_size)
        return base_size
    
    def overlaps_with(self, other):
        """Vérifie si cette annotation chevauche avec une autre."""
        if self.skip or other.skip:
            return False
        
        # Calculer la distance requise en fonction de la taille des textes
        size1 = self.get_text_size_estimate()
        size2 = other.get_text_size_estimate()
        min_gap = (size1 + size2) / 2 * 1.2  # 20% de marge
        
        # Distance euclidienne
        dist = ((self.final_x - other.final_x)**2 + (self.final_y - other.final_y)**2)**0.5
        
        return dist < min_gap

def resolve_dimension_overlaps(annotations, panel_L=None, panel_W=None):
    """Résout les chevauchements entre annotations de dimensions.
    
    Args:
        annotations: Liste de DimensionAnnotation
        panel_L: Largeur du panneau (pour calculs relatifs)
        panel_W: Hauteur du panneau (pour calculs relatifs)
    
    Returns:
        Liste d'annotations avec positions ajustées
    """
    if not annotations:
        return annotations
    
    # Mettre à jour les tailles de panneau si nécessaire
    for ann in annotations:
        if ann.panel_L is None:
            ann.panel_L = panel_L
        if ann.panel_W is None:
            ann.panel_W = panel_W
    
    # Trier par position pour traiter dans l'ordre
    annotations.sort(key=lambda a: (a.axis, a.y if a.axis == 'y' else a.x))
    
    # Calculer la taille de référence pour les espacements
    if panel_L and panel_W:
        ref_size = min(panel_L, panel_W) * 0.02  # 2% de la plus petite dimension
    else:
        ref_size = 20  # Valeur par défaut
    
    # Résoudre les chevauchements par itération
    max_iterations = 50
    for iteration in range(max_iterations):
        overlaps_found = False
        
        for i, ann1 in enumerate(annotations):
            if ann1.skip:
                continue
            
            for j, ann2 in enumerate(annotations[i+1:], start=i+1):
                if ann2.skip:
                    continue
                
                if ann1.overlaps_with(ann2):
                    overlaps_found = True
                    
                    # Calculer le décalage nécessaire
                    size1 = ann1.get_text_size_estimate()
                    size2 = ann2.get_text_size_estimate()
                    min_gap = (size1 + size2) / 2 * 1.2
                    
                    # Distance actuelle
                    dx = ann2.final_x - ann1.final_x
                    dy = ann2.final_y - ann1.final_y
                    current_dist = (dx**2 + dy**2)**0.5
                    
                    if current_dist < min_gap:
                        # Calculer le vecteur de séparation
                        if current_dist > 0:
                            sep_x = dx / current_dist * min_gap
                            sep_y = dy / current_dist * min_gap
                        else:
                            # Même position : séparer selon l'axe
                            if ann1.axis == 'x':
                                sep_x = min_gap
                                sep_y = 0
                            else:
                                sep_x = 0
                                sep_y = min_gap
                        
                        # Déplacer ann2 pour créer l'espacement
                        ann2.final_x = ann1.final_x + sep_x
                        ann2.final_y = ann1.final_y + sep_y
                        
                        # Si les deux sont sur le même axe, créer une cascade
                        if ann1.axis == ann2.axis:
                            if ann1.axis == 'y':
                                # Cascade verticale : décaler horizontalement
                                shift = ref_size * 0.5
                                ann2.final_x = ann1.final_x - shift
                            else:
                                # Cascade horizontale : décaler verticalement
                                shift = ref_size * 0.5
                                ann2.final_y = ann1.final_y - shift
        
        if not overlaps_found:
            break
    
    return annotations

def check_cascade_overlap(text_pos, text_val, existing_texts, axis='y', min_gap=45, panel_L=None, panel_W=None):
    """Système de cascade pour éviter les chevauchements de textes de cotations.
    Fonctionne à tous les niveaux de zoom grâce à des espacements relatifs.
    
    Args:
        text_pos: Position du texte (y pour axis='x', x pour axis='y')
        text_val: Valeur du texte (pour détecter les doublons)
        existing_texts: Liste de tuples (pos, val, axis) des textes existants
        axis: 'x' ou 'y' selon l'orientation de la cotation
        min_gap: Distance minimale entre les textes (base, sera ajustée relativement)
        panel_L: Largeur du panneau (pour calculs relatifs au zoom)
        panel_W: Hauteur du panneau (pour calculs relatifs au zoom)
    
    Returns:
        adjusted_pos: Position ajustée pour créer une cascade
    """
    # Estimer la taille du texte en fonction de sa longueur (approximation)
    text_length = len(str(text_val))
    text_width_estimate = max(20, text_length * 8)  # Estimation plus généreuse de la largeur du texte
    
    # Ajuster min_gap pour être relatif à la taille du panneau (fonctionne à tous les zooms)
    if panel_L and panel_W:
        # Espacement relatif : environ 2-3% de la plus petite dimension du panneau
        min_dim = min(panel_L, panel_W)
        relative_gap = min_dim * 0.025  # 2.5% de la dimension minimale
        effective_min_gap = max(min_gap, relative_gap, text_width_estimate * 1.0)
    else:
        effective_min_gap = max(min_gap, text_width_estimate * 0.8)
    
    # Vérifier d'abord les doublons exacts (même position et même valeur)
    for ex_pos, ex_val, ex_axis in existing_texts:
        if ex_axis == axis and abs(ex_pos - text_pos) < 1.0 and str(ex_val) == str(text_val):
            # Doublon détecté - retourner la même position (sera ignoré plus tard)
            return text_pos
    
    # Chercher la première position disponible en cascade
    cascade_offset = 0
    max_iterations = 30  # Augmenté pour gérer plus de conflits
    
    for _ in range(max_iterations):
        test_pos = text_pos + cascade_offset
        overlap_found = False
        
        for ex_pos, ex_val, ex_axis in existing_texts:
            if ex_axis == axis:
                # Calculer la distance en tenant compte de la taille des deux textes
                ex_text_length = len(str(ex_val))
                ex_text_width = max(20, ex_text_length * 8)  # Estimation plus généreuse
                combined_width = (text_width_estimate + ex_text_width) / 2
                required_gap = max(effective_min_gap, combined_width * 1.0)  # Augmenté de 0.7 à 1.0 pour plus d'espace
                
                if abs(test_pos - ex_pos) < required_gap:
                    overlap_found = True
                    break
        
        if not overlap_found:
            return test_pos
        
        # Incrémenter l'offset de cascade avec l'espacement effectif
        cascade_offset += effective_min_gap
    
    # Si aucune position n'est trouvée après max_iterations, retourner la position avec le plus grand offset
    return text_pos + cascade_offset

def check_vertical_dim_overlap(x_dim, y0, y1, existing_dims, min_gap=30):
    """Vérifie si une cotation verticale chevauche avec des cotations existantes.
    Si chevauchement, trouve la première position X disponible pour créer plusieurs lignes verticales.
    
    Args:
        x_dim: Position X initiale de la cotation
        y0, y1: Positions Y (début et fin)
        existing_dims: Liste de tuples (x_dim, y0, y1) des cotations existantes
        min_gap: Distance minimale entre les cotations pour éviter le chevauchement
    
    Returns:
        (adjusted_x_dim, should_skip): Position X ajustée et booléen indiquant si on doit sauter cette cotation
    """
    y_min = min(y0, y1)
    y_max = max(y0, y1)
    
    # Trier les positions X existantes pour trouver la première disponible
    x_positions_used = sorted(set([ex_dim for ex_dim, _, _ in existing_dims]), reverse=True)  # De droite à gauche
    
    # Vérifier chaque position X possible, en commençant par la position initiale
    for test_x in [x_dim] + [x - min_gap for x in x_positions_used]:
        overlap_found = False
        for ex_dim, ey0, ey1 in existing_dims:
            ey_min = min(ey0, ey1)
            ey_max = max(ey0, ey1)
            
            # Vérifier si les segments Y se chevauchent ET si la position X est trop proche
            if not (y_max < ey_min or y_min > ey_max):
                if abs(test_x - ex_dim) < min_gap:
                    overlap_found = True
                    break
        
        if not overlap_found:
            # Position X disponible trouvée
            return (test_x, False)
    
    # Si aucune position n'est trouvée, décaler encore plus à gauche
    if x_positions_used:
        min_x_used = min(x_positions_used)
        return (min_x_used - min_gap, False)
    
    return (x_dim, False)

def get_smart_label_pos(cx, cy, r, existing_labels):
    # Plus de candidats pour éviter les superpositions
    candidates = [
        (30, -30), (30, 30), (-30, 30), (-30, -30),
        (50, -50), (50, 50), (-50, 50), (-50, -50),
        (70, -70), (70, 70), (-70, 70), (-70, -70),
        (40, 0), (-40, 0), (0, 40), (0, -40),
        (60, -20), (60, 20), (-60, -20), (-60, 20),
        (20, -60), (20, 60), (-20, -60), (-20, 60)
    ]
    for ax, ay in candidates:
        test_pos = (cx + ax, cy - ay)
        if not check_label_overlap(test_pos, existing_labels):
            return ax, ay, test_pos
    # Si aucun candidat ne fonctionne, essayer des positions plus éloignées
    for dist in range(80, 200, 20):
        for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
            import math
            rad = math.radians(angle)
            ax = int(dist * math.cos(rad))
            ay = int(dist * math.sin(rad))
            test_pos = (cx + ax, cy - ay)
            if not check_label_overlap(test_pos, existing_labels):
                return ax, ay, test_pos
    return 30, -30, (cx+30, cy+30)

def group_holes_for_dimensioning(y_vals):
    if not y_vals: return []
    y_vals = [round(y, 1) for y in y_vals]
    sorted_y = sorted(list(set(y_vals)))
    if not sorted_y: return []
    groups = []
    current_group = [sorted_y[0]]
    for i in range(1, len(sorted_y)):
        y = sorted_y[i]
        prev = current_group[-1]
        diff = y - prev
        if 31.0 < diff < 33.0:
            current_group.append(y)
        else:
            groups.append(current_group)
            current_group = [y]
    groups.append(current_group)
    result = []
    for grp in groups:
        if len(grp) >= 2: 
            result.append({'start': grp[0], 'end': grp[-1], 'count': len(grp), 'type': 'rack', 'positions': grp})
        else: 
            result.append({'start': grp[0], 'end': grp[0], 'count': 1, 'type': 'single', 'positions': grp})
    return result

def draw_machining_view_pro_final(panel_name, L, W, T, unit_str, project_info, 
                                 chants, face_holes_list=[], tranche_longue_holes_list=[], 
                                 tranche_cote_holes_list=[], center_cutout_props=None, has_rebate=False):
    # Réinitialiser le tracker d'annotations pour éviter les doublons entre les appels
    global _annotation_tracker
    _annotation_tracker = set()
    
    fig = go.Figure()
    
    line_color = "black"
    dim_line_color = "black"
    HATCH_COLOR = "rgba(100, 100, 100, 0.5)"
    HATCH_SPACING = 20.0
    max_extension_length = 12  # Maximum 1cm (environ 12 unités) pour les lignes de rappel courtes
    
    # Détecter si rotation nécessaire (hauteur > largeur)
    needs_rotation = W > L
    
    # Définir panel_lower tôt pour éviter les erreurs de référence
    panel_lower = panel_name.lower()
    
    # Définir les couleurs par groupe pour les montants
    # Chaque groupe (type de trou, position) aura une couleur différente
    dimension_color_groups = {}  # Dictionnaire pour stocker les couleurs par groupe
    color_palette = [
        "black",      # Groupe 0 : par défaut
        "blue",       # Groupe 1 : zone rouge (rack_shelf_y) - réservé
        "red",        # Groupe 2 : cotations externes type 1
        "green",      # Groupe 3 : cotations externes type 2
        "orange",     # Groupe 4 : cotations externes type 3
        "purple",     # Groupe 5 : cotations externes type 4
        "brown",      # Groupe 6 : cotations externes type 5
        "pink",       # Groupe 7 : cotations internes shelf_rack type 1
        "cyan",       # Groupe 8 : cotations internes shelf_rack type 2
        "magenta",    # Groupe 9 : cotations internes shelf_rack type 3
        "teal",       # Groupe 10 : cotations internes shelf_rack type 4
        "olive",      # Groupe 11 : cotations internes shelf_rack type 5
    ]
    
    def get_color_for_group(group_key, is_montant=False):
        """Retourne une couleur pour un groupe de cotations."""
        if not is_montant:
            return "black"  # Pour les non-montants, couleur noire par défaut
        
        # Couleurs fixes pour certains groupes très importants sur les montants.
        # - Chaîne qui longe le BAS du montant (bord où pointe le triangle vide) :
        #   toujours ROUGE vif pour être repérable immédiatement.
        # - Chaîne interne spéciale de la zone rack (zone rouge) :
        #   toujours BLEU (cohérent avec la zone colorée).
        if group_key == "montant_bottom_chain":
            return "red"
        if group_key == "montant_rack_zone":
            return "blue"
        
        if group_key not in dimension_color_groups:
            # Assigner la prochaine couleur disponible (en évitant le bleu réservé pour la zone rouge)
            # Commencer à l'index 2 pour éviter black (0) et blue (1)
            group_index = (len(dimension_color_groups) + 2) % len(color_palette)
            if group_index < 2:  # Si on retombe sur black ou blue, passer à la suivante
                group_index = 2
            dimension_color_groups[group_key] = color_palette[group_index]
        return dimension_color_groups[group_key]
    
    # Garder L et W originaux pour les dimensions affichées (ne pas échanger)
    # Les valeurs affichées doivent rester identiques
    L_actual, W_actual = L, W
    
    # Fonction de transformation pour rotation de 90° dans le sens horaire : (x, y) -> (W - y, x)
    def rotate_coords(x, y):
        if needs_rotation:
            return (W_actual - y, x)
        return (x, y)
    
    # Marges entre la face principale et les tranches / zones de cotes.
    # On réduit fortement l'espacement pour les montants afin de "zoomer"
    # naturellement la figure et rapprocher les tranches.
    min_panel_dim = max(1.0, min(L_actual, W_actual))
    panel_norm_no_acc = panel_lower.replace("é", "e").replace("è", "e").replace("ê", "e")
    is_fond_sheet = ("fond" in panel_norm_no_acc) or ("panneau arriere" in panel_norm_no_acc)
    if "Montant" in panel_name:
        # Montants: rapprocher un peu plus les tranches.
        MARGIN_DIMS = max(82.0, min(120.0, min_panel_dim * 0.09))
    else:
        # Panneaux courants: ecarter un tout petit peu plus les tranches.
        MARGIN_DIMS = max(86.0, min(126.0, min_panel_dim * 0.125))
        # Portes: garder un compromis compact/lisible.
        if "Porte" in panel_name:
            MARGIN_DIMS = max(80.0, min(104.0, min_panel_dim * 0.11))
        
    TRANCHE_THICK = max(T * 1.5, 30.0)
    
    # Calculer les bounds après rotation si nécessaire
    if needs_rotation:
        # Après rotation, les dimensions visuelles sont échangées
        x0, y0 = rotate_coords(0, 0)
        x1, y1 = rotate_coords(L_actual, W_actual)
        bounds_x = [min(x0, x1), max(x0, x1)]
        bounds_y = [min(y0, y1), max(y0, y1)]
        # Calculer les bounds du panneau pour l'extériorisation
        panel_bounds = {
            'x': (min(x0, x1), max(x0, x1)),
            'y': (min(y0, y1), max(y0, y1))
        }
    else:
        bounds_x = [0, L]
        bounds_y = [0, W]
        # Calculer les bounds du panneau pour l'extériorisation
        panel_bounds = {
            'x': (0, L_actual),
            'y': (0, W_actual)
        }
    
    # Initialiser les trackers pour le système de cascade
    cascade_tracker = []  # Liste de (position, valeur, axis) pour éviter les chevauchements
    
    # Dessiner le rectangle principal (avec rotation si nécessaire)
    if needs_rotation:
        # Rectangle pivoté : transformer les coins (rotation 90° horaire)
        x0, y0 = rotate_coords(0, 0)
        x1, y1 = rotate_coords(L_actual, 0)
        x2, y2 = rotate_coords(L_actual, W_actual)
        x3, y3 = rotate_coords(0, W_actual)
        fig.add_shape(
            type="path", 
            path=f"M {x0},{y0} L {x1},{y1} L {x2},{y2} L {x3},{y3} Z",
            line=dict(color=line_color, width=1.5),
            fillcolor="white",
            layer="below"
        )
    else:
        fig.add_shape(
            type="rect",
            x0=0, y0=0, x1=L, y1=W,
            line=dict(color=line_color, width=1.5),
            fillcolor="white",
            layer="below"
        )
    # La zone colorée VERTICALE pour les montants sera dessinée plus bas,
    # une fois que l'on connaît la position de la deuxième colonne de trous.
    
    def draw_tranche(tx, ty, hatch_key):
        # Transformer les coordonnées si rotation nécessaire
        if needs_rotation:
            tx_rot, ty_rot = zip(*[rotate_coords(x, y) for x, y in zip(tx, ty)])
            tx, ty = list(tx_rot), list(ty_rot)
        fig.add_trace(go.Scatter(x=tx, y=ty, fill="toself", fillcolor="#f9f9f9", line=dict(color=line_color, width=1), hoverinfo="none", showlegend=False, mode='lines'))
        if chants.get(hatch_key):
            hx, hy = create_hatch_lines(min(tx), min(ty), max(tx), max(ty), density=HATCH_SPACING)
            fig.add_trace(go.Scatter(x=hx, y=hy, mode='lines', line=dict(color=HATCH_COLOR, width=1), hoverinfo='skip', showlegend=False))

    # ===== LES 4 TRANCHES SONT OBLIGATOIRES SUR TOUTES LES FEUILLES D'USINAGE =====
    # Calculer les positions des tranches (avec rotation si nécessaire)
    # Les 4 tranches représentent l'épaisseur du panneau sur les 4 côtés :
    # - Tranche Bas (Chant Avant) : en bas du panneau
    # - Tranche Haut (Chant Arrière) : en haut du panneau
    # - Tranche Gauche (Chant Gauche) : à gauche du panneau
    # - Tranche Droit (Chant Droit) : à droite du panneau
    if needs_rotation:
        # Après rotation, les tranches changent de position.
        # RÈGLE SPÉCIALE POUR MONTANTS & PORTES :
        # - laisser les tranches HAUT/BAS à distance "normale" (lisibilité)
        # - éloigner davantage les tranches GAUCHE/DROITE
        # => zoom général (via autoscale) + lisibilité des tranches.
        if "Montant" in panel_name:
            # Tranches haut / bas : distance standard (pas rapprochées)
            y_tb_0, y_tb_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
            y_th_0, y_th_1 = W_actual + MARGIN_DIMS, W_actual + MARGIN_DIMS + TRANCHE_THICK
            # Tranches gauche / droite plus éloignées
            x_tg_0, x_tg_1 = -MARGIN_DIMS * 1.8, -MARGIN_DIMS * 1.8 - TRANCHE_THICK
            x_td_0, x_td_1 = L_actual + MARGIN_DIMS * 1.8, L_actual + MARGIN_DIMS * 1.8 + TRANCHE_THICK
        elif "Porte" in panel_name:
            # Pour les portes tournees: tranches proches de la face, y compris gauche/droite.
            y_tb_0, y_tb_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
            y_th_0, y_th_1 = W_actual + MARGIN_DIMS, W_actual + MARGIN_DIMS + TRANCHE_THICK
            x_tg_0, x_tg_1 = -MARGIN_DIMS * 1.15, -MARGIN_DIMS * 1.15 - TRANCHE_THICK
            x_td_0, x_td_1 = L_actual + MARGIN_DIMS * 1.15, L_actual + MARGIN_DIMS * 1.15 + TRANCHE_THICK
        else:
            # Comportement générique pour les autres pièces tournées :
            # léger écart symétrique sur les 4 côtés.
            y_tb_0, y_tb_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
            y_th_0, y_th_1 = W_actual + MARGIN_DIMS, W_actual + MARGIN_DIMS + TRANCHE_THICK
            x_tg_0, x_tg_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
            x_td_0, x_td_1 = L_actual + MARGIN_DIMS, L_actual + MARGIN_DIMS + TRANCHE_THICK
    else:
        y_tb_0, y_tb_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
        y_th_0, y_th_1 = W + MARGIN_DIMS, W + MARGIN_DIMS + TRANCHE_THICK
        x_tg_0, x_tg_1 = -MARGIN_DIMS, -MARGIN_DIMS - TRANCHE_THICK
        x_td_0, x_td_1 = L + MARGIN_DIMS, L + MARGIN_DIMS + TRANCHE_THICK
    
    # TOUJOURS dessiner les 4 tranches - OBLIGATOIRES pour toutes les feuilles d'usinage
    draw_tranche([0, L_actual, L_actual, 0, 0], [y_tb_0, y_tb_0, y_tb_1, y_tb_1, y_tb_0], "Chant Avant")
    draw_tranche([0, L_actual, L_actual, 0, 0], [y_th_0, y_th_0, y_th_1, y_th_1, y_th_0], "Chant Arrière")
    draw_tranche([x_tg_0, x_tg_1, x_tg_1, x_tg_0, x_tg_0], [0, 0, W_actual, W_actual, 0], "Chant Gauche")
    draw_tranche([x_td_0, x_td_1, x_td_1, x_td_0, x_td_0], [0, 0, W_actual, W_actual, 0], "Chant Droit")
    
    # Calculer les bounds avec rotation si nécessaire
    if needs_rotation:
        # Transformer les bounds
        x_tg_1_rot, y_tb_1_rot = rotate_coords(x_tg_1, y_tb_1)
        x_td_1_rot, y_th_1_rot = rotate_coords(x_td_1, y_th_1)
        bounds_y.extend([y_tb_1_rot, y_th_1_rot])
        bounds_x.extend([x_tg_1_rot, x_td_1_rot])
    else:
        bounds_y.extend([y_tb_1, y_th_1])
        bounds_x.extend([x_tg_1, x_td_1])

    # --- Zones rectangulaires de repérage pour les montants et traverses ---
    if ("Montant" in panel_name or "traverse" in panel_lower) and panel_bounds and not is_fond_sheet:
        # Utiliser les bounds actuels du panneau (après rotation éventuelle)
        panel_x_min, panel_x_max = panel_bounds['x']
        panel_y_min, panel_y_max = panel_bounds['y']
        panel_width_x = panel_x_max - panel_x_min   # longueur (L) dans le repère actuel
        panel_height_y = panel_y_max - panel_y_min  # largeur (W) dans le repère actuel

        # Épaisseur (courte) des rectangles de repère
        rect_thickness = max(20.0, min(panel_width_x, panel_height_y) * 0.06)
        offset = rect_thickness * 0.8

        # Rectangle AU-DESSUS du montant (repère 1) :
        # - même longueur que la pièce suivant X (panel_width_x)
        # - faible hauteur (rect_thickness)
        fig.add_shape(
            type="rect",
            x0=panel_x_min,
            x1=panel_x_max,
            y0=panel_y_max + offset,
            y1=panel_y_max + offset + rect_thickness,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle EN DESSOUS du montant (repère 2) :
        fig.add_shape(
            type="rect",
            x0=panel_x_min,
            x1=panel_x_max,
            y0=panel_y_min - offset - rect_thickness,
            y1=panel_y_min - offset,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle À GAUCHE du montant (repère 3) :
        # - même hauteur que la pièce suivant Y (panel_height_y)
        # - faible largeur (rect_thickness)
        fig.add_shape(
            type="rect",
            x0=panel_x_min - offset - rect_thickness,
            x1=panel_x_min - offset,
            y0=panel_y_min,
            y1=panel_y_max,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle À DROITE du montant (repère 4) :
        fig.add_shape(
            type="rect",
            x0=panel_x_max + offset,
            x1=panel_x_max + offset + rect_thickness,
            y0=panel_y_min,
            y1=panel_y_max,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )
    
    # Zones colorées pour les portes (identique aux montants)
    if "Porte" in panel_name and panel_bounds and not is_fond_sheet:
        # Utiliser les bounds actuels du panneau (après rotation éventuelle)
        panel_x_min, panel_x_max = panel_bounds['x']
        panel_y_min, panel_y_max = panel_bounds['y']
        panel_width_x = panel_x_max - panel_x_min   # longueur (L) dans le repère actuel
        panel_height_y = panel_y_max - panel_y_min  # largeur (W) dans le repère actuel

        # Épaisseur (courte) des rectangles de repère
        # TRIPLÉ pour les portes pour permettre la cascade sur 2 lignes
        base_rect_thickness = max(20.0, min(panel_width_x, panel_height_y) * 0.06)
        rect_thickness = base_rect_thickness * 3  # TRIPLÉ
        offset = base_rect_thickness * 0.8

        # Rectangle AU-DESSUS de la porte (repère 1) :
        # - même longueur que la pièce suivant X (panel_width_x)
        # - faible hauteur (rect_thickness)
        fig.add_shape(
            type="rect",
            x0=panel_x_min,
            x1=panel_x_max,
            y0=panel_y_max + offset,
            y1=panel_y_max + offset + rect_thickness,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle EN DESSOUS de la porte (repère 2) :
        fig.add_shape(
            type="rect",
            x0=panel_x_min,
            x1=panel_x_max,
            y0=panel_y_min - offset - rect_thickness,
            y1=panel_y_min - offset,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle À GAUCHE de la porte (repère 3) :
        # - même hauteur que la pièce suivant Y (panel_height_y)
        # - faible largeur (rect_thickness)
        fig.add_shape(
            type="rect",
            x0=panel_x_min - offset - rect_thickness,
            x1=panel_x_min - offset,
            y0=panel_y_min,
            y1=panel_y_max,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

        # Rectangle À DROITE de la porte (repère 4) :
        fig.add_shape(
            type="rect",
            x0=panel_x_max + offset,
            x1=panel_x_max + offset + rect_thickness,
            y0=panel_y_min,
            y1=panel_y_max,
            line=dict(color="rgba(255,255,255,1.0)", width=1),
            fillcolor="rgba(255,255,255,0.0)",  # transparent/blanc
            layer="above"
        )

    # Cotations générales (épaisseurs, L/W globales) :
    # Désormais actives pour TOUS les panneaux, y compris les montants.
    # Elles donnent pour chaque tranche les infos de longueur et d'épaisseur.
    # Cotations d'épaisseur des tranches - EXTÉRIORISATION TOTALE
    # Les valeurs doivent être à l'extérieur du rectangle de la tranche
    offset_epaisseur = 50  # Offset pour placer les cotations à l'extérieur
    
    # Définir is_porte tôt pour éviter les erreurs de référence
    is_porte = "Porte" in panel_name
    
    # Pour montants et portes avec rotation : rapprocher les cotations des tranches
    is_montant_or_porte_rotated = needs_rotation and (("Montant" in panel_name) or ("Porte" in panel_name))
    # Initialiser les offsets par défaut
    offset_tranche_hb = offset_epaisseur
    offset_tranche_gd = offset_epaisseur
    if is_montant_or_porte_rotated:
        # Rapprocher les cotations des tranches haut/bas (réduire l'offset et la position X)
        offset_tranche_hb = 30  # Offset réduit pour les tranches haut/bas
        pos_x_tranche_hb = L_actual + 10  # Position X plus proche
        # Rapprocher les cotations des tranches gauche/droite (réduire l'offset et la position Y)
        offset_tranche_gd = 30  # Offset réduit pour les tranches gauche/droite
        pos_y_tranche_gd = W_actual + 10  # Position Y plus proche
        
        # Cotations verticales (tranches haut/bas) - rapprochées
        add_pro_dimension(fig, pos_x_tranche_hb, y_tb_0, pos_x_tranche_hb, y_tb_1, format_number_no_decimal(T), -offset_tranche_hb, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        add_pro_dimension(fig, pos_x_tranche_hb, y_th_0, pos_x_tranche_hb, y_th_1, format_number_no_decimal(T), -offset_tranche_hb, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        
        # Cotations horizontales (tranches gauche/droite) - rapprochées
        add_pro_dimension(fig, x_tg_0, pos_y_tranche_gd, x_tg_1, pos_y_tranche_gd, format_number_no_decimal(T), offset_tranche_gd, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
        add_pro_dimension(fig, x_td_0, pos_y_tranche_gd, x_td_1, pos_y_tranche_gd, format_number_no_decimal(T), offset_tranche_gd, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
    else:
        # Comportement standard pour les autres éléments
        # Cotations verticales (gauche et droite) - à l'extérieur (offset négatif pour axis='y')
        add_pro_dimension(fig, L_actual+20, y_tb_0, L_actual+20, y_tb_1, format_number_no_decimal(T), -offset_epaisseur, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        add_pro_dimension(fig, L_actual+20, y_th_0, L_actual+20, y_th_1, format_number_no_decimal(T), -offset_epaisseur, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        
        # Cotations horizontales (haut et bas) - à l'extérieur (offset positif pour axis='x')
        add_pro_dimension(fig, x_tg_0, W_actual+20, x_tg_1, W_actual+20, format_number_no_decimal(T), offset_epaisseur, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
        add_pro_dimension(fig, x_td_0, W_actual+20, x_td_1, W_actual+20, format_number_no_decimal(T), offset_epaisseur, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
    
    # Pour les portes : ajouter les cotations principales AU-DESSUS DES TRANCHES HAUT ET DROIT
    # Mesurer les dimensions générales des tranches (haut et droit)
    if is_porte:
        # Cotation de longueur : mesurer la longueur totale AU-DESSUS de la tranche haute
        # Positionner la cote au-dessus de y_th_1 (haut de la tranche haut)
        y_above_tranche_haut = y_th_1 + 50  # Position au-dessus de la tranche haut
        add_pro_dimension(fig, 0, y_above_tranche_haut, L_actual, y_above_tranche_haut, format_number_no_decimal(L_actual), 50, axis='x', font_size=14, yanchor='bottom', color="black", rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        
        # Cotation de largeur : mesurer la largeur totale AU-DESSUS de la tranche droite
        # Positionner la cote au-dessus de x_td_1 (extrémité droite de la tranche droite)
        x_above_tranche_droit = x_td_1 + 50  # Position au-dessus de la tranche droite
        add_pro_dimension(fig, x_above_tranche_droit, 0, x_above_tranche_droit, W_actual, format_number_no_decimal(W_actual), 50, axis='y', font_size=14, xanchor='right', color="black", rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
    
    # Écarter les lignes de cotations pour montants et portes si hauteur > 1500mm
    height_for_spacing = W if "Montant" in panel_name or "Porte" in panel_name else L
    if ("Montant" in panel_name or "Porte" in panel_name) and height_for_spacing > 1500:
        # Augmenter l'espacement des cotations de 100mm supplémentaire pour les grands éléments
        dist_global = MARGIN_DIMS + TRANCHE_THICK + 50 + 100
    else:
        dist_global = MARGIN_DIMS + TRANCHE_THICK + 50
    
    # Pour les dimensions principales, garder les valeurs originales (L et W)
    # S'assurer qu'elles sont à l'EXTÉRIEUR de la pièce
    # Logique : après rotation dans add_pro_dimension, les axes sont échangés
    # Donc on doit toujours utiliser les offsets qui placent les dimensions à l'extérieur
    # Dimension L (largeur, axis='x') : offset positif = au-dessus (extérieur)
    # Dimension W (hauteur, axis='y') : offset négatif = à gauche (extérieur)
    # Après rotation dans add_pro_dimension : axis='x' devient 'y', donc offset positif devient à droite (pas extérieur)
    # Solution : utiliser toujours les offsets qui donnent l'extérieur APRÈS l'échange d'axes
    # Pour les portes : NE PAS dessiner les cotations principales ici (elles sont déjà dessinées plus haut sur les tranches)
    if not is_porte:
        # Comportement standard pour les autres éléments
        if needs_rotation:
            # Après rotation, dans add_pro_dimension :
            # - L (axis='x') devient axis='y', donc pour être à gauche (extérieur) : offset NÉGATIF
            # - W (axis='y') devient axis='x', donc pour être au-dessus (extérieur) : offset POSITIF
            main_dim_color = "black"
            add_pro_dimension(fig, 0, W_actual, L_actual, W_actual, format_number_no_decimal(L), -dist_global, axis='x', font_size=14, yanchor='bottom', color=main_dim_color, rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
            add_pro_dimension(fig, 0, 0, 0, W_actual, format_number_no_decimal(W), dist_global, axis='y', font_size=14, xanchor='right', color=main_dim_color, rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
        else:
            # Sans rotation : dimension L (axis='x') à l'extérieur = offset positif (au-dessus)
            # Dimension W (axis='y') à l'extérieur = offset négatif (à gauche)
            main_dim_color = "black"
            add_pro_dimension(fig, 0, W_actual, L_actual, W_actual, format_number_no_decimal(L), dist_global, axis='x', font_size=14, yanchor='bottom', color=main_dim_color, rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
            add_pro_dimension(fig, 0, 0, 0, W_actual, format_number_no_decimal(W), -dist_global, axis='y', font_size=14, xanchor='right', color=main_dim_color, rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
    
    # Calculer les bounds avec rotation si nécessaire
    if needs_rotation:
        # Transformer les bounds pour la rotation
        bound_x_rot, bound_y_rot = rotate_coords(-dist_global - 50, W_actual + dist_global + 50)
        bounds_x.append(bound_x_rot)
        bounds_y.append(bound_y_rot)
    else:
        bounds_x.append(-dist_global - 50)
        bounds_y.append(W + dist_global + 50)

    # --- REPÈRES TRIANGULAIRES ---
    # Placement selon le type de panneau
    # Taille proportionnelle à l'aire du panneau (environ 1/50)
    panel_area = L * W
    TRIANGLE_SIZE = max(10.0, min(30.0, (panel_area / 50.0) ** 0.5))  # Racine carrée pour une taille proportionnelle
    TRIANGLE_OFFSET = TRIANGLE_SIZE * 1.3  # Distance depuis le bord
    
    def draw_triangle(x, y, filled=True, orientation='down'):
        """Dessine un triangle pointant depuis le bord (inversé de 180°)"""
        # Transformer les coordonnées si rotation nécessaire
        # MAIS garder la même orientation relative au panneau (le triangle pointe toujours le même bord)
        if needs_rotation:
            x, y = rotate_coords(x, y)
            # Adapter l'orientation après rotation pour pointer le même bord qu'avant
            # Rotation 90° horaire : down->left, left->up, up->right, right->down
            if orientation == 'down':
                orientation = 'left'  # Le bas devient la gauche après rotation
            elif orientation == 'right':
                orientation = 'down'  # La droite devient le bas après rotation
            elif orientation == 'left':
                orientation = 'up'  # La gauche devient le haut après rotation
            elif orientation == 'up':
                orientation = 'right'  # Le haut devient la droite après rotation
            # Rotation supplémentaire de 180° pour les triangles si l'élément a tourné
            # Après rotation 90° + 180° = 270° au total
            if orientation == 'down':
                orientation = 'up'
            elif orientation == 'up':
                orientation = 'down'
            elif orientation == 'left':
                orientation = 'right'
            elif orientation == 'right':
                orientation = 'left'
        
        # Calculer le path SVG pour Plotly selon l'orientation
        if orientation == 'down':
            # Triangle pointant depuis le bord inférieur (vers le haut, depuis y=0)
            path = f"M {x},{y} L {x - TRIANGLE_SIZE/2},{y + TRIANGLE_OFFSET} L {x + TRIANGLE_SIZE/2},{y + TRIANGLE_OFFSET} Z"
        elif orientation == 'right':
            # Triangle pointant depuis le bord droit (vers la gauche, depuis x=L)
            path = f"M {x},{y} L {x - TRIANGLE_OFFSET},{y - TRIANGLE_SIZE/2} L {x - TRIANGLE_OFFSET},{y + TRIANGLE_SIZE/2} Z"
        elif orientation == 'left':
            # Triangle pointant depuis le bord gauche (vers la droite, depuis x=0)
            path = f"M {x},{y} L {x + TRIANGLE_OFFSET},{y - TRIANGLE_SIZE/2} L {x + TRIANGLE_OFFSET},{y + TRIANGLE_SIZE/2} Z"
        else:  # up
            # Triangle pointant depuis le bord supérieur (vers le bas, depuis y=W)
            path = f"M {x},{y} L {x - TRIANGLE_SIZE/2},{y - TRIANGLE_OFFSET} L {x + TRIANGLE_SIZE/2},{y - TRIANGLE_OFFSET} Z"
        
        # Dessiner le triangle dans Plotly (visualisation)
        fig.add_shape(
            type="path",
            path=path,
            line=dict(color="black", width=1.5),
            fillcolor="black" if filled else "white",
            layer="above"
        )
        
        # Stocker les métadonnées du triangle pour l'export DXF (blocks AutoCAD)
        _push_dxf_triangle(fig, {
            "filled": filled,
            "orientation": orientation,
            "center": (x, y),
            "size": TRIANGLE_SIZE,
            "layer": "GEOM"
        })
    
    # Utiliser les dimensions originales pour les triangles (avant rotation)
    if "traverse bas" in panel_lower:
        # Triangle noir au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=True, orientation='down')
    
    elif "traverse haut" in panel_lower:
        # Triangle noir au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=True, orientation='down')
    
    elif "montant droit" in panel_lower or ("montant" in panel_lower and "1/2" in panel_lower):
        # Triangle vide au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')
        # Triangle noir au milieu du bord droit (x=L)
        draw_triangle(L_actual, W_actual/2, filled=True, orientation='right')
    
    elif "montant gauche" in panel_lower or ("montant" in panel_lower and "2/2" in panel_lower):
        # Triangle vide au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')
        # Triangle noir au milieu du bord gauche (x=0)
        draw_triangle(0, W_actual/2, filled=True, orientation='left')
    
    # Détection des étagères (y compris groupées) - normaliser les accents
    panel_normalized = panel_lower.replace("é", "e").replace("è", "e").replace("ê", "e")
    is_shelf = "etagere" in panel_normalized or "étagère" in panel_lower
    
    if is_shelf:
        # Triangle noir au centre du bord inférieur (y=0) - pour toutes les étagères (y compris groupées)
        draw_triangle(L_actual/2, 0, filled=True, orientation='down')
    
    elif "fond" in panel_lower and "tiroir" in panel_lower:
        # Triangle noir au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=True, orientation='down')
    
    elif (("face" in panel_lower) or ("facade" in panel_lower) or ("façade" in panel_lower)) and "tiroir" in panel_lower:
        # Triangle vide au milieu du bord inférieur (y=0)
        # (tiroir-face / façade tiroir)
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')
    
    elif "dos" in panel_lower and "tiroir" in panel_lower:
        # Triangle vide au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')
    
    elif "porte" in panel_lower:
        # Triangle vide au milieu du bord inférieur (y=0)
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')
    
    elif "panneau arrière" in panel_lower or ("fond" in panel_lower and "tiroir" not in panel_lower):
        # Triangle vide au milieu du bord inférieur (y=0) - exactement comme pour la porte
        draw_triangle(L_actual/2, 0, filled=False, orientation='down')

    if center_cutout_props:
        cutout_type = str(center_cutout_props.get('type', 'integrated_cutout'))
        if cutout_type == 'finger_pull':
            # Exigence métier: passe-doigt affiché en face par un seul trait horizontal.
            cOff = float(center_cutout_props.get('offset_top', 10.0))
            groove_depth_req = float(center_cutout_props.get('depth', 12.0))
            groove_drop_req = float(center_cutout_props.get('drop', 35.0))
            groove_depth = max(1.0, min(groove_depth_req, max(1.0, TRANCHE_THICK - 1.0)))
            # Demi-cercle: le rayon suit la profondeur et la moitié de la hauteur demandée.
            arc_radius_base = max(1.0, min(groove_depth, max(1.0, groove_drop_req / 2.0)))
            # Abaisser la ligne sur la face pour augmenter l'amplitude visuelle du passe-doigt.
            line_lowering = max(4.0, arc_radius_base * 0.8)
            y_line = max(2.0 * arc_radius_base + 1.0, min(W_actual - 1.0, W_actual - cOff - line_lowering))
            arc_radius = max(1.0, min(arc_radius_base, (y_line - 1.0) / 2.0))
            if needs_rotation:
                x0_rot, y0_rot = rotate_coords(0.0, y_line)
                x1_rot, y1_rot = rotate_coords(L_actual, y_line)
                fig.add_shape(type="line", x0=x0_rot, y0=y0_rot, x1=x1_rot, y1=y1_rot, line=dict(color="black", width=1), layer="above")
            else:
                fig.add_shape(type="line", x0=0.0, y0=y_line, x1=L_actual, y1=y_line, line=dict(color="black", width=1), layer="above")

            # Ajouter aussi la forme de découpe sur les 2 tranches latérales:
            # profil identique à la PJ (petit dôme haut + creux arrondi + remontée sur la face).
            left_inner_x = x_tg_0
            left_outer_x = x_tg_1
            right_inner_x = x_td_0
            right_outer_x = x_td_1

            def _draw_side_finger_pull_curve(x_face, x_outer):
                # Gorge en U ouverte par le HAUT de la tranche, contre le bord face (x_face).
                # Le bord extérieur (x_outer) reste INTACT (non dessiné ici).
                import math
                gw = min(groove_depth_req, abs(x_outer - x_face) * 0.75)
                gw = max(4.0, gw)
                d = 1.0 if (x_outer > x_face) else -1.0
                # Murs gauche/droit de la gorge
                if d > 0:   # tranche droite : face = gauche
                    xL = x_face
                    xR = x_face + gw
                else:       # tranche gauche : face = droite
                    xL = x_face - gw
                    xR = x_face
                xC = (xL + xR) / 2.0
                r_bot = gw / 2.0                          # demi-cercle au fond
                r_top = max(1.5, gw * 0.18)               # arrondis coins du haut
                y_top = W_actual                          # ouverture en haut
                y_arc_center = max(r_bot + 2.0,
                                   min(y_top - r_top - 1.0,
                                       y_top - groove_drop_req))

                def arc_seg(cx, cy, r, a0, a1, n=12):
                    pts = []
                    for i in range(n + 1):
                        t = math.radians(a0 + (a1 - a0) * i / float(n))
                        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
                    return pts

                pts = []
                # 1. Arrondi coin sup-gauche (90°→180°)
                pts += arc_seg(xL + r_top, y_top - r_top, r_top, 90, 180, n=8)
                # 2. Mur gauche (descend)
                pts.append((xL, y_arc_center))
                # 3. Fond demi-cercle (180°→360° = via le bas)
                bot = arc_seg(xC, y_arc_center, r_bot, 180, 360, n=16)
                pts += bot[1:]
                # 4. Mur droit (remonte)
                pts.append((xR, y_top - r_top))
                # 5. Arrondi coin sup-droit (0°→90°)
                trc = arc_seg(xR - r_top, y_top - r_top, r_top, 0, 90, n=8)
                pts += trc[1:]

                if needs_rotation:
                    rot = [rotate_coords(px, py) for (px, py) in pts]
                    fig.add_trace(go.Scatter(x=[p[0] for p in rot], y=[p[1] for p in rot],
                                            mode='lines', line=dict(color='black', width=1),
                                            hoverinfo='skip', showlegend=False))
                else:
                    fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts],
                                            mode='lines', line=dict(color='black', width=1),
                                            hoverinfo='skip', showlegend=False))

            _draw_side_finger_pull_curve(left_inner_x, left_outer_x)
            _draw_side_finger_pull_curve(right_inner_x, right_outer_x)
        else:
            cW, cH = center_cutout_props['width'], center_cutout_props['height']
            cOff = center_cutout_props['offset_top']
            x0, x1 = (L_actual-cW)/2, (L_actual-cW)/2 + cW
            y1, y0 = W_actual-cOff, W_actual-cOff-cH
            if needs_rotation:
                # Transformer les coins du rectangle
                x0_rot, y0_rot = rotate_coords(x0, y0)
                x1_rot, y1_rot = rotate_coords(x1, y1)
                x0_rot2, y1_rot2 = rotate_coords(x0, y1)
                x1_rot2, y0_rot2 = rotate_coords(x1, y0)
                fig.add_shape(type="path", 
                             path=f"M {x0_rot},{y0_rot} L {x1_rot},{y0_rot2} L {x1_rot2},{y1_rot} L {x0_rot2},{y1_rot2} Z",
                             line=dict(color="black", width=1, dash="dash"), layer="above")
            else:
                fig.add_shape(type="rect", x0=x0, y0=y0, x1=x1, y1=y1, line=dict(color="black", width=1, dash="dash"), layer="above")
                add_pro_dimension(fig, x0, y1, x1, y1, format_number_no_decimal(cW), -30, axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
                add_pro_dimension(fig, x0, y0, x0, y1, format_number_no_decimal(cH), -30, axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
    
    # --- FEUILLURE (REBATE) POUR LÉGRABOX ---
    if has_rebate:
        REBATE_WIDTH = 38.0  # Largeur de la feuillure depuis le bord
        REBATE_THICKNESS = 8.0  # Épaisseur réduite (8mm)
        
        # Lignes verticales sur le panneau principal en traits pleins fins
        if needs_rotation:
            x1_0, y1_0 = rotate_coords(REBATE_WIDTH, 0)
            x1_1, y1_1 = rotate_coords(REBATE_WIDTH, W_actual)
            x2_0, y2_0 = rotate_coords(L_actual-REBATE_WIDTH, 0)
            x2_1, y2_1 = rotate_coords(L_actual-REBATE_WIDTH, W_actual)
            fig.add_shape(type="line", x0=x1_0, y0=y1_0, x1=x1_1, y1=y1_1, 
                         line=dict(color="black", width=1.0), layer="above")
            fig.add_shape(type="line", x0=x2_0, y0=y2_0, x1=x2_1, y1=y2_1, 
                         line=dict(color="black", width=1.0), layer="above")
        else:
            fig.add_shape(type="line", x0=REBATE_WIDTH, y0=0, x1=REBATE_WIDTH, y1=W, 
                         line=dict(color="black", width=1.0), layer="above")
            fig.add_shape(type="line", x0=L-REBATE_WIDTH, y0=0, x1=L-REBATE_WIDTH, y1=W, 
                         line=dict(color="black", width=1.0), layer="above")
        
        # Cote de la largeur de feuillure sur le panneau principal - AU DESSUS du panneau (extérieur)
        add_pro_dimension(fig, 0, W_actual+25, REBATE_WIDTH, W_actual+25, format_number_no_decimal(REBATE_WIDTH), 25, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds)
        add_pro_dimension(fig, L_actual-REBATE_WIDTH, W_actual+25, L_actual, W_actual+25, format_number_no_decimal(REBATE_WIDTH), 25, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds)
        
        # RÉVISION DES TRANCHES (TICKET #08)
        # Rectangle de tranche : Un seul trait au milieu à X=8 (équivalent à TRANCHE_THICK/2 depuis le bord)
        # Cote 16 : En BAS, à l'extérieur
        # Cotes 8 & 8 : En HAUT, à l'extérieur, décalées l'une par rapport à l'autre si nécessaire
        
        # Trait au milieu de la largeur de la tranche gauche (un seul trait)
        # La tranche gauche va de x_tg_1 (bord gauche) à x_tg_0 (bord droit)
        # Le milieu est à x_tg_0 - TRANCHE_THICK/2 (équivalent à X=8 depuis le bord)
        trait_tranche_gauche_x = x_tg_0 - TRANCHE_THICK/2
        if needs_rotation:
            x_line0, y_line0 = rotate_coords(trait_tranche_gauche_x, 0)
            x_line1, y_line1 = rotate_coords(trait_tranche_gauche_x, W_actual)
            fig.add_shape(type="line", x0=x_line0, y0=y_line0, x1=x_line1, y1=y_line1,
                         line=dict(color="black", width=1.0), layer="above")
        else:
            fig.add_shape(type="line", x0=trait_tranche_gauche_x, y0=0, x1=trait_tranche_gauche_x, y1=W,
                         line=dict(color="black", width=1.0), layer="above")
        
        # Cote 16 : En BAS, à l'extérieur
        # Pour être en bas, on utilise y=0 et offset négatif (en dessous)
        # Mais avec panel_bounds=None pour ne pas forcer l'extériorisation automatique ici
        add_pro_dimension(fig, x_tg_1, 0, x_tg_0, 0, 
                         format_number_no_decimal(T), -30, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
        
        # Cote 8 gauche : En HAUT, à l'extérieur (offset positif pour axis='x' = au-dessus)
        add_pro_dimension(fig, trait_tranche_gauche_x, W_actual, x_tg_0, W_actual, 
                         format_number_no_decimal(REBATE_THICKNESS), 30, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
        
        # Trait au milieu de la largeur de la tranche droite (un seul trait)
        # La tranche droite va de x_td_0 (bord gauche) à x_td_1 (bord droit)
        # Le milieu est à x_td_0 + TRANCHE_THICK/2 (équivalent à X=8 depuis le bord)
        trait_tranche_droite_x = x_td_0 + TRANCHE_THICK/2
        if needs_rotation:
            x_line0, y_line0 = rotate_coords(trait_tranche_droite_x, 0)
            x_line1, y_line1 = rotate_coords(trait_tranche_droite_x, W_actual)
            fig.add_shape(type="line", x0=x_line0, y0=y_line0, x1=x_line1, y1=y_line1,
                         line=dict(color="black", width=1.0), layer="above")
        else:
            fig.add_shape(type="line", x0=trait_tranche_droite_x, y0=0, x1=trait_tranche_droite_x, y1=W,
                         line=dict(color="black", width=1.0), layer="above")
        
        # Cote 8 droite : En HAUT, à l'extérieur, décalée par rapport à la cote 8 gauche (cascade automatique)
        add_pro_dimension(fig, x_td_0, W_actual, trait_tranche_droite_x, W_actual, 
                         format_number_no_decimal(REBATE_THICKNESS), 30, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=None)

    # Détection des panneaux de tiroir pour rétablir les cotes Y
    panel_name_norm = (
        panel_lower.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ç", "c")
    )
    is_drawer_face = (
        "tiroir" in panel_name_norm
        and ("face" in panel_name_norm or "facade" in panel_name_norm)
    )
    is_drawer_back = (
        "tiroir" in panel_name_norm
        and ("dos" in panel_name_norm or "arriere" in panel_name_norm)
    )

    drawer_y_dims_done = False

    if is_drawer_face or is_drawer_back:
        drawer_holes_all = merge_drawer_panel_holes(
            face_holes_list,
            tranche_longue_holes_list,
            tranche_cote_holes_list
        )
        if drawer_holes_all:
            y_positions = sorted(list(set([round(h.get('y', 0.0), 1) for h in drawer_holes_all])))
            if y_positions:
                drawer_y_dims_done = True
                tick_len = 5
                line_width = 0.8
                x_dim = -60

                bounds_x.append(-80)
                if L_actual is not None:
                    bounds_x.append(L_actual + 100)

                # Bottom to first hole
                y_first = y_positions[0]
                dist_to_first = y_first - 0
                if dist_to_first > 1.0:
                    add_pro_dimension(fig, x_dim, 0, x_dim, y_first, format_number_no_decimal(dist_to_first), -15, 
                                    axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None, 
                                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)

                # Spacings between consecutive holes
                for i in range(len(y_positions) - 1):
                    y_curr = y_positions[i]
                    y_next = y_positions[i + 1]
                    dist_between = y_next - y_curr
                    if dist_between > 1.0:
                        add_pro_dimension(fig, x_dim, y_curr, x_dim, y_next, format_number_no_decimal(dist_between), -15,
                                        axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                        cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)

                # Last hole to top edge
                y_last = y_positions[-1]
                dist_to_top = W_actual - y_last
                if dist_to_top > 1.0:
                    add_pro_dimension(fig, x_dim, y_last, x_dim, W_actual, format_number_no_decimal(dist_to_top), -15,
                                    axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)

    if face_holes_list or ((is_drawer_face or is_drawer_back) and (tranche_longue_holes_list or tranche_cote_holes_list)):
        holes_by_func = {}
        # Pour certains types (racks d'étagères sur montants), on veut connaître
        # une position X représentative afin de placer les cotes Y "à l'intérieur"
        rack_x_by_func = {}
        for h in face_holes_list:
            full_type = h.get('type', 'autre')
            key = full_type 
            if key not in holes_by_func: 
                holes_by_func[key] = []
            holes_by_func[key].append(h['y'])
            # Mémoriser une position X typique pour ce type de trou
            x_h = h.get('x', None)
            if x_h is not None:
                if key not in rack_x_by_func:
                    rack_x_by_func[key] = x_h
                else:
                    # Garder la colonne la plus proche de la gauche (plus lisible pour les montants)
                    rack_x_by_func[key] = min(rack_x_by_func[key], x_h)
        
        # Position de départ et espacement horizontal entre chaque "ligne" de cotation
        x_dim_start = -40 
        layer_width = 50 
        sorted_keys = sorted(holes_by_func.keys())

        # TICKET #09 - Montants uniquement :
        # Si un montant possède plusieurs éléments (étagères, tiroirs, etc.),
        # on décale chaque usinage sur UNE LIGNE UNIQUE bien séparée.
        # Concrètement : on augmente l'espacement horizontal entre les colonnes de cotations
        # pour les montants qui ont plus de 2 groupes d'usinage.
        if "Montant" in panel_name and len(sorted_keys) > 2:
            layer_width = 80  # lignes plus espacées pour une meilleure lisibilité
        
        # Tracker pour éviter les chevauchements des cotations verticales
        vertical_dims_tracker = []
        
        # Pour les montants avec plusieurs éléments : activer la cascade (colimasson) si les cotations se chevauchent
        use_cascade_for_montants = "Montant" in panel_name and len(sorted_keys) > 2
        
        # Variable commune pour la rangée d'étagère centrale (rack) des montants.
        # Initialisée ici pour éviter tout problème de portée, puis éventuellement
        # recalculée plus bas si on traite les cotations verticales.
        rack_zone_y = None
        
        # Pour les portes : pas de cotations verticales, seulement horizontales (voir section horizontale plus bas)
        is_door_with_hinges = "Porte" in panel_name
        is_montant_panel = "Montant" in panel_name
        is_back_panel = "panneau arrière" in panel_lower or ("fond" in panel_lower and "tiroir" not in panel_lower)
        
        # Déterminer le type de montant et la présence de trous de tiroirs
        is_montant_principal = "Montant Gauche" in panel_name or "Montant Droit" in panel_name
        is_montant_secondaire = is_montant_panel and not is_montant_principal
        drawer_slide_holes_for_panel = [h for h in face_holes_list if is_drawer_slide_hole(h)] if is_montant_panel else []
        is_montant_with_drawers = len(drawer_slide_holes_for_panel) > 0
        
        # DÉTECTION PRÉCOCE DE RACK_ZONE_Y POUR LES MONTANTS
        # Pour les montants : identifier une RANGÉE HORIZONTALE de trous d'UNE SEULE étagère (alternance vis/tourillon)
        # Après rotation sur la feuille, les montants sont horizontaux, donc une rangée horizontale = une étagère
        if is_montant_panel and not is_montant_with_drawers:
            # Chercher une rangée Y (horizontale après rotation) qui contient à la fois des vis ET des tourillons
            # Cela indique l'assemblage d'une seule étagère avec alternance trou/tourillon
            y_with_both_types = {}
            for h in face_holes_list:
                y_pos = round(h['y'], 1)
                hole_type = h.get('type', 'autre')
                if y_pos not in y_with_both_types:
                    y_with_both_types[y_pos] = {'vis': False, 'tourillon': False, 'x_positions': []}
                
                if hole_type == 'vis':
                    y_with_both_types[y_pos]['vis'] = True
                elif hole_type == 'tourillon':
                    y_with_both_types[y_pos]['tourillon'] = True
                
                y_with_both_types[y_pos]['x_positions'].append(round(h['x'], 1))
            
            # Trouver la rangée qui a à la fois des vis ET des tourillons (assemblage d'une seule étagère)
            # PRIORITÉ : choisir la rangée la plus proche du centre de la face (milieu de W_actual)
            center_y = W_actual / 2.0
            best_y = None
            
            # D'abord, chercher les rangées avec vis ET tourillons
            candidates_with_both_types = []
            for y_pos, info in y_with_both_types.items():
                if info['vis'] and info['tourillon']:
                    unique_x_count = len(set(info['x_positions']))
                    distance_to_center = abs(y_pos - center_y)
                    candidates_with_both_types.append({
                        'y': y_pos,
                        'hole_count': unique_x_count,
                        'distance': distance_to_center
                    })
            
            if candidates_with_both_types:
                # Trier par nombre de trous (d'abord), puis par distance au centre
                candidates_with_both_types.sort(key=lambda c: (-c['hole_count'], c['distance']))
                best_y = candidates_with_both_types[0]['y']
                rack_zone_y = best_y
        
        # Pour les montants gauches et droits : AUCUNE cotation verticale sur la feuille d'usinage.
        # Pour le panneau arrière (fond) : cotations spéciales pour les trous de montants secondaires
        if is_montant_panel:
            vertical_dims_items = []
        elif is_door_with_hinges:
            # Pour les portes, on ne fait PAS de cotations verticales ici
            # Les cotations seront uniquement horizontales (voir section horizontale)
            pass
        else:
            # Comportement normal pour les autres panneaux (non-montants, non-portes)
            # NOUVELLE APPROCHE : Collecter toutes les cotes dans des DimItem avant de les dessiner
            vertical_dims_items = []
            
            # Pour les non-montants : pas besoin de détection spéciale de rack_zone_y
            # (elle a déjà été faite pour les montants plus haut si nécessaire)
            has_rack_holes = False
        
        # === CRÉATION DES DIMENSIONS D'ALTERNANCE VIS/TOURILLON POUR LES MONTANTS ===
        # Pour les montants : créer les cotations d'alternance selon les coordonnées X
        # SAUF si c'est un montant droit/gauche avec des tiroirs (dans ce cas, pas de cotations du tout)
        if is_montant_panel and rack_zone_y is not None and not is_montant_with_drawers:
            # Filtrer TOUS les trous qui sont dans la rangée rack_zone_y (vis ET tourillons)
            # Utiliser une tolérance plus large pour capturer tous les trous de la rangée
            rack_holes_x = []
            for h in face_holes_list:
                y_pos = round(h['y'], 1)
                # Tolérance augmentée à 1.0mm pour capturer tous les trous de la rangée
                if abs(y_pos - rack_zone_y) < 1.0:
                    rack_holes_x.append({
                        'x': round(h['x'], 1),
                        'y': round(h['y'], 1),  # Garder aussi Y pour référence
                        'type': h.get('type', 'autre')
                    })
            
            if rack_holes_x:
                # Trier les trous par position X
                rack_holes_x.sort(key=lambda h: h['x'])
                
                # Calculer la position Y des cotations internes (texte rouge) de façon
                # INDÉPENDANTE du zoom initial et stable avec l'autoscale.
                # Pour les montants, on place la ligne de cotes à une fraction fixe
                # de la hauteur de la face, pour qu'elle reste lisible quel que soit le dézoom.
                if panel_bounds is not None:
                    panel_y_min, panel_y_max = panel_bounds['y']
                    panel_h = panel_y_max - panel_y_min
                    # Ligne de cotes à ~70% de la hauteur depuis le bas de la face
                    zone_y_center = panel_y_min + 0.7 * panel_h
                elif W_actual:
                    # Fallback générique si jamais panel_bounds n'est pas dispo
                    zone_y_center = rack_zone_y + max(80.0, W_actual * 0.18)
                    zone_y_center = min(W_actual - 20.0, zone_y_center)
                else:
                    zone_y_center = rack_zone_y + 80.0
                
                # Position X des cotations verticales (Y) dans la zone rouge
                zone_x_pos = L_actual * 0.85 if L_actual else 0  # 85% de la largeur
                
                # Créer les cotations d'alternance des trous vis/tourillon selon les coordonnées X
                # Ces cotations sont tracées directement avec add_pro_dimension
                if len(rack_holes_x) > 0:
                    # Position Y pour tracer les cotations (dans la zone d'assemblage)
                    cote_y_pos = zone_y_center
                    
                    # Cotation du bord gauche au premier trou (si > 1mm)
                    x_first = rack_holes_x[0]['x']
                    dist_bord_gauche = x_first - 0
                    if dist_bord_gauche > 1.0:
                        add_pro_dimension(
                            fig, 0, cote_y_pos, x_first, cote_y_pos,
                            format_number_no_decimal(dist_bord_gauche),
                            0.0,  # offset_dist
                            axis='x', line_dash='dot',
                            color='blue', font_size=9,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
                    
                    # Espacement entre chaque trou consécutif (alternance vis/tourillon)
                    for i in range(len(rack_holes_x) - 1):
                        x_curr = rack_holes_x[i]['x']
                        x_next = rack_holes_x[i + 1]['x']
                        dist_between = x_next - x_curr
                        
                        hole_curr_type = rack_holes_x[i].get('type', 'autre')
                        hole_next_type = rack_holes_x[i + 1].get('type', 'autre')
                        
                        # Couleur selon le type de trou (vis ou tourillon)
                        cote_color = 'green' if hole_curr_type == 'vis' else 'orange'
                        
                        add_pro_dimension(
                            fig, x_curr, cote_y_pos, x_next, cote_y_pos,
                            format_number_no_decimal(dist_between),
                            0.0,  # offset_dist
                            axis='x', line_dash='dot',
                            color=cote_color, font_size=9,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
                    
                    # Cotation du dernier trou au bord droit (si > 1mm)
                    x_last = rack_holes_x[-1]['x']
                    if L_actual is not None:
                        dist_bord_droit = L_actual - x_last
                        if dist_bord_droit > 1.0:
                            add_pro_dimension(
                                fig, x_last, cote_y_pos, L_actual, cote_y_pos,
                                format_number_no_decimal(dist_bord_droit),
                                0.0,  # offset_dist
                                axis='x', line_dash='dot',
                                color='blue', font_size=9,
                                rotate_coords_fn=rotate_coords if needs_rotation else None,
                                vertical_dims_tracker=None,
                                cascade_tracker=None,
                                panel_bounds=None,
                                panel_L=L_actual,
                                panel_W=W_actual,
                                is_montant=("Montant" in panel_name),
                                panel_name=panel_name
                            )
        
        # === CRÉATION DES DIMENSIONS X POUR LES MONTANTS AVEC TIROIRS ===
        # Pour les montants ayant des trous de tiroirs : créer les cotations d'espacement selon les coordonnées X
        if is_montant_panel and is_montant_with_drawers and face_holes_list:
            drawer_slide_holes = drawer_slide_holes_for_panel
            if not drawer_slide_holes:
                drawer_slide_holes = face_holes_list
            # Grouper les trous par rangée Y (horizontale)
            holes_by_y = {}
            for h in drawer_slide_holes:
                y_pos = round(h['y'], 1)
                if y_pos not in holes_by_y:
                    holes_by_y[y_pos] = []
                holes_by_y[y_pos].append(h)
            
            # Pour chaque rangée de trous, créer les dimensions X
            drawer_dim_font_size = 14
            ghost_dim_mm = 25.0
            ghost_dim_tol = 0.6
            for y_pos in sorted(holes_by_y.keys()):
                holes_in_row = holes_by_y[y_pos]
                
                if len(holes_in_row) > 0:
                    # Trier les trous par position X
                    holes_in_row_sorted = sorted(holes_in_row, key=lambda h: h['x'])
                    x_positions = [round(h['x'], 1) for h in holes_in_row_sorted]
                    
                    # Calculer la position Y pour les dimensions (légèrement au-dessus de la rangée)
                    cote_y_pos = y_pos + max(20.0, W_actual * 0.04) if W_actual else y_pos + 25.0
                    
                    # Cotation du bord gauche au premier trou (si > 1mm)
                    x_first = x_positions[0]
                    dist_bord_gauche = x_first - 0
                    if dist_bord_gauche > 1.0 and abs(dist_bord_gauche - ghost_dim_mm) > ghost_dim_tol:
                        add_pro_dimension(
                            fig, 0, cote_y_pos, x_first, cote_y_pos,
                            format_number_no_decimal(dist_bord_gauche),
                            0.0,  # offset_dist
                            axis='x', line_dash='dot',
                            color='blue', font_size=drawer_dim_font_size,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
                    
                    # Espacement entre chaque trou consécutif
                    for i in range(len(x_positions) - 1):
                        x_curr = x_positions[i]
                        x_next = x_positions[i + 1]
                        dist_between = x_next - x_curr
                        
                        # Couleur : gris pour les trous de tiroirs
                        cote_color = 'gray'
                        
                        add_pro_dimension(
                            fig, x_curr, cote_y_pos, x_next, cote_y_pos,
                            format_number_no_decimal(dist_between),
                            0.0,  # offset_dist
                            axis='x', line_dash='dot',
                            color=cote_color, font_size=drawer_dim_font_size,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
                    
                    # Cotation du dernier trou au bord droit (si > 1mm)
                    x_last = x_positions[-1]
                    if L_actual is not None:
                        dist_bord_droit = L_actual - x_last
                        if dist_bord_droit > 1.0 and abs(dist_bord_droit - ghost_dim_mm) > ghost_dim_tol:
                            add_pro_dimension(
                                fig, x_last, cote_y_pos, L_actual, cote_y_pos,
                                format_number_no_decimal(dist_bord_droit),
                                0.0,  # offset_dist
                                axis='x', line_dash='dot',
                                color='blue', font_size=drawer_dim_font_size,
                                rotate_coords_fn=rotate_coords if needs_rotation else None,
                                vertical_dims_tracker=None,
                                cascade_tracker=None,
                                panel_bounds=None,
                                panel_L=L_actual,
                                panel_W=W_actual,
                                is_montant=("Montant" in panel_name),
                                panel_name=panel_name
                            )

                # Pour les montants secondaires avec tiroirs : ajouter les notations Y
                # des lignes de trous (chaîne verticale : bas -> lignes -> haut).
                if is_montant_secondaire and holes_by_y and W_actual is not None:
                    y_rows_sorted = sorted(holes_by_y.keys())
                    x_dim_y = L_actual + max(35.0, L_actual * 0.06) if L_actual is not None else 35.0

                    y_first = y_rows_sorted[0]
                    dist_bottom = y_first - 0
                    if dist_bottom > 1.0:
                        add_pro_dimension(
                            fig, x_dim_y, 0, x_dim_y, y_first,
                            format_number_no_decimal(dist_bottom),
                            0.0,
                            axis='y', line_dash='dot',
                            color='gray', font_size=drawer_dim_font_size,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )

                    for i in range(len(y_rows_sorted) - 1):
                        y_curr = y_rows_sorted[i]
                        y_next = y_rows_sorted[i + 1]
                        dist_between_rows = y_next - y_curr
                        if dist_between_rows <= 1.0:
                            continue
                        add_pro_dimension(
                            fig, x_dim_y, y_curr, x_dim_y, y_next,
                            format_number_no_decimal(dist_between_rows),
                            0.0,
                            axis='y', line_dash='dot',
                            color='gray', font_size=drawer_dim_font_size,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )

                    y_last = y_rows_sorted[-1]
                    dist_top = W_actual - y_last
                    if dist_top > 1.0:
                        add_pro_dimension(
                            fig, x_dim_y, y_last, x_dim_y, W_actual,
                            format_number_no_decimal(dist_top),
                            0.0,
                            axis='y', line_dash='dot',
                            color='gray', font_size=drawer_dim_font_size,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )

            # Cotes d'assemblage montant/traverse : espacement entre chaque trou (vis/tourillon)
            assembly_holes = [
                h for h in face_holes_list
                if h.get('type') in ('vis', 'tourillon') and not is_drawer_slide_hole(h)
            ]
            if assembly_holes and W_actual:
                edge_band = max(30.0, W_actual * 0.05)
                assembly_rows = {}
                for h in assembly_holes:
                    y_pos = round(h['y'], 1)
                    if y_pos <= edge_band or y_pos >= W_actual - edge_band:
                        assembly_rows.setdefault(y_pos, []).append(h)

                row_keys = sorted(assembly_rows.keys())
                # Montants: les lignées d'assemblage ont la même trame X.
                # On ne cote l'entraxe entre trous que sur UNE seule lignée de référence.
                if "Montant" in panel_name and row_keys:
                    row_keys = [max(row_keys, key=lambda y_key: len(assembly_rows.get(y_key, [])))]

                for y_pos in row_keys:
                    holes_in_row = sorted(assembly_rows[y_pos], key=lambda h: h['x'])
                    x_positions = [round(h['x'], 1) for h in holes_in_row]
                    if len(x_positions) < 2:
                        continue
                    cote_y_pos = y_pos + max(20.0, W_actual * 0.04)
                    for i in range(len(x_positions) - 1):
                        x_curr = x_positions[i]
                        x_next = x_positions[i + 1]
                        dist_between = x_next - x_curr
                        if dist_between <= 1.0:
                            continue
                        add_pro_dimension(
                            fig, x_curr, cote_y_pos, x_next, cote_y_pos,
                            format_number_no_decimal(dist_between),
                            0.0,
                            axis='x', line_dash='dot',
                            color='black', font_size=12,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=None,
                            panel_bounds=None,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
            
            # Détecter si TOUS les montants ont des trous de tiroirs
            has_drawer_holes_montant = False
            if "Montant" in panel_name:
                has_drawer_holes_montant = any(
                    ("5/12" in h.get('diam_str', '')) or 
                    (("⌀3" in h.get('diam_str', '') or "/3" in h.get('diam_str', '')) and "/10" not in h.get('diam_str', ''))
                    for h in face_holes_list
                )
            
            # Traiter les autres types de trous (non racks) normalement
            for idx, k in enumerate(sorted_keys):
                # Skip complètement si c'est un montant droit/gauche avec tiroirs
                if is_montant_with_drawers:
                    continue
                    
                if "traverse" in panel_lower and str(k).lower() == 'tourillon':
                    continue
                
                y_vals = holes_by_func[k]
                current_x_dim = x_dim_start - (idx * layer_width)
                groups = group_holes_for_dimensioning(y_vals)
                prev_end = 0
                
                # Vérifier si ce type de trou correspond à la rangée rack_zone_y
                # MAIS NE PAS IGNORER LES TROUS - ils doivent tous être dessinés
                # On ignore seulement la création de cotations verticales pour cette rangée
                # car les cotations horizontales sont déjà créées ci-dessus
                is_rack_row = False
                if "Montant" in panel_name and rack_zone_y is not None:
                    # Vérifier si des trous de ce type sont dans la rangée rack_zone_y
                    # Utiliser une tolérance plus large
                    for h in face_holes_list:
                        if h.get('type', 'autre') == k and abs(round(h['y'], 1) - rack_zone_y) < 1.0:
                            is_rack_row = True
                            break
                
                for grp in groups:
                    grp_type = grp.get('type', 'single')
                    is_shelf_rack = (grp_type == 'rack')

                    # Si c'est la rangée de l'étagère, les cotations horizontales sont déjà créées ci-dessus
                    # On ignore seulement la création de cotations verticales pour cette rangée
                    # MAIS LES TROUS SONT TOUJOURS DESSINÉS (voir ligne 1802)
                    if is_rack_row and "Montant" in panel_name:
                        # Ignorer seulement les cotations verticales pour cette rangée
                        # Les trous eux-mêmes seront dessinés normalement plus bas
                        if is_shelf_rack:
                            prev_end = grp['end']
                        else:
                            prev_end = grp['start']
                        continue

                    # Pour les étagères (mais pas les montants) : mesurer les espacements entre les trous consécutifs
                    if is_shelf and "Montant" not in panel_name:
                        positions = grp.get('positions', [])
                        if len(positions) > 1:
                            # Cotation : écart entre le bord bas et le premier trou
                            y_first = positions[0]
                            ecart_bord_bas = y_first - 0
                            if ecart_bord_bas > 1.0:
                                color_group_key = f"external_{k}"
                                color_group = get_color_for_group(color_group_key, is_montant=("Montant" in panel_name))
                                dim_item = DimItem(
                                    axis='y',
                                    p0=0,
                                    p1=y_first,
                                    text=format_number_no_decimal(ecart_bord_bas),
                                    base_offset=-20.0,
                                    layer=1,
                                    kind='hole',
                                    x_dim_pos=current_x_dim,
                                    chain_id=k,
                                    color_group=color_group
                                )
                                vertical_dims_items.append(dim_item)
                            
                            # Cotation : écart entre le dernier trou et le bord haut
                            y_last = positions[-1]
                            if W_actual is not None:
                                ecart_bord_haut = W_actual - y_last
                                if ecart_bord_haut > 1.0:
                                    color_group_key = f"external_{k}"
                                    color_group = get_color_for_group(color_group_key, is_montant=("Montant" in panel_name))
                                    dim_item = DimItem(
                                        axis='y',
                                        p0=y_last,
                                        p1=W_actual,
                                        text=format_number_no_decimal(ecart_bord_haut),
                                        base_offset=-20.0,
                                        layer=1,
                                        kind='hole',
                                        x_dim_pos=current_x_dim,
                                        chain_id=k,
                                        color_group=color_group
                                    )
                                    vertical_dims_items.append(dim_item)
                            
                            # Mesurer l'écart entre chaque trou consécutif
                            for i in range(len(positions) - 1):
                                dist_between = positions[i+1] - positions[i]
                                color_group_key = f"internal_shelf_{k}_{idx}"
                                color_group = get_color_for_group(color_group_key, is_montant=("Montant" in panel_name))
                                dim_item = DimItem(
                                    axis='y',
                                    p0=positions[i],
                                    p1=positions[i+1],
                                    text=format_number_no_decimal(dist_between),
                                    base_offset=0.0,
                                    layer=1,
                                    kind='hole',
                                    x_dim_pos=current_x_dim,
                                    chain_id=k,
                                    color_group=color_group
                                )
                                vertical_dims_items.append(dim_item)
                        prev_end = grp['end']
                    else:
                        # Autres usinages : cotes externes classiques, à gauche du panneau
                        base_offset_gap = -20.0  # ~20 unités vers la gauche (extérieur)
                        # Pour les montants, marquer explicitement la chaîne externe (côté droit après rotation)
                        if "Montant" in panel_name:
                            kind_gap = 'montant_external_right_chain'
                        else:
                            kind_gap = 'hole'
                        x_dim_gap = current_x_dim

                        dist_gap = grp['start'] - prev_end
                        # NE PAS créer de dimensions pour les montants avec trous de tiroirs
                        if dist_gap > 1.0 and not ("Montant" in panel_name and has_drawer_holes_montant):
                            # Créer un DimItem pour cette cote (palier 1 = perçages)
                            # Créer une clé de groupe pour cette cotation externe
                            color_group_key = f"external_{k}"  # Groupe par type de trou
                            color_group = get_color_for_group(color_group_key, is_montant=("Montant" in panel_name))
                            dim_item = DimItem(
                                axis='y',
                                p0=prev_end,
                                p1=grp['start'],
                                text=format_number_no_decimal(dist_gap),
                                base_offset=base_offset_gap,
                                layer=1,
                                kind=kind_gap,
                                x_dim_pos=x_dim_gap,
                                chain_id=k,  # regrouper toutes les cotes de ce type de trous
                                color_group=color_group
                            )
                            vertical_dims_items.append(dim_item)
                        
                        if is_shelf_rack:
                            # Racks d'étagères (autres colonnes) : cotes internes entre trous
                            # Créer une clé de groupe pour cette cotation interne shelf_rack
                            color_group_key = f"internal_shelf_rack_{k}_{idx}"  # Groupe par type et colonne
                            color_group = get_color_for_group(color_group_key, is_montant=("Montant" in panel_name))
                            positions = grp.get('positions', [])
                            for i in range(len(positions) - 1):
                                dist_between = positions[i+1] - positions[i]
                                dim_item = DimItem(
                                    axis='y',
                                    p0=positions[i],
                                    p1=positions[i+1],
                                    text=format_number_no_decimal(dist_between),
                                    base_offset=0.0,
                                    layer=1,
                                    kind='shelf_rack',
                                    x_dim_pos=current_x_dim,
                                    chain_id=k,
                                    color_group=color_group
                                )
                                vertical_dims_items.append(dim_item)
                            prev_end = grp['end']
                        else:
                            prev_end = grp['start']
            
            # EXCEPTION : Pour les faces de tiroir ET dos de tiroir, créer des cotations d'espacement verticales propres
            if (is_drawer_face or is_drawer_back) and not drawer_y_dims_done:
                vertical_dims_items = []  # Vider complètement la liste
                
                # Récupérer TOUS les trous sur la face/dos de tiroir (pas seulement vis)
                drawer_holes = merge_drawer_panel_holes(face_holes_list, tranche_longue_holes_list, tranche_cote_holes_list)
                
                if drawer_holes and len(drawer_holes) > 0:
                    # Étendre les bounds pour garder les cotes Y visibles (gauche + droite)
                    bounds_x.append(-80)
                    if L_actual is not None:
                        bounds_x.append(L_actual + 100)
                    # Grouper les trous par position X (colonnes verticales)
                    x_locs = sorted(list(set([round(h['x'], 1) for h in drawer_holes])))
                    tick_len = 5
                    line_width = 0.8
                    
                    # Pour chaque colonne de trous, créer une ligne de cotation verticale à l'extérieur
                    for x_pos in x_locs:
                        holes_at_x = [h for h in drawer_holes if round(h['x'], 1) == x_pos]
                        
                        if len(holes_at_x) >= 1:  # Au moins 1 trou
                            # Trier les trous par position Y
                            holes_at_x_sorted = sorted(holes_at_x, key=lambda h: h['y'])
                            y_positions = [h['y'] for h in holes_at_x_sorted]
                            
                            # Position de la ligne de cotation : à gauche de la figure
                            x_dim = -60  # En dehors à gauche

                            # Cotation du bord inférieur au premier trou (si > 1mm)
                            y_first = y_positions[0]
                            dist_to_first = y_first - 0
                            if dist_to_first > 1.0:
                                add_pro_dimension(fig, x_dim, 0, x_dim, y_first, format_number_no_decimal(dist_to_first), -15,
                                                axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                                cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                            
                            # Espacements entre trous consécutifs
                            for i in range(len(y_positions) - 1):
                                y_curr = y_positions[i]
                                y_next = y_positions[i + 1]
                                dist_between = y_next - y_curr
                                if dist_between > 1.0:
                                    add_pro_dimension(fig, x_dim, y_curr, x_dim, y_next, format_number_no_decimal(dist_between), -15,
                                                    axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                            
                            # Cotation du dernier trou au bord supérieur (si > 1mm)
                            y_last = y_positions[-1]
                            dist_to_top = W_actual - y_last
                            if dist_to_top > 1.0:
                                add_pro_dimension(fig, x_dim, y_last, x_dim, W_actual, format_number_no_decimal(dist_to_top), -15,
                                                axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                                cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)

                            # Ligne de cotation pleine à droite : écarts entre trous (convertir en dimensions AutoCAD)
                            x_dim_right = L_actual + 60
                            for i in range(len(y_positions) - 1):
                                y_curr = y_positions[i]
                                y_next = y_positions[i + 1]
                                dist_between = y_next - y_curr
                                if dist_between > 1.0:
                                    add_pro_dimension(fig, x_dim_right, y_curr, x_dim_right, y_next, format_number_no_decimal(dist_between), 15,
                                                    axis='y', xanchor='left', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
            
            # Appliquer l'empilement intelligent avec espacements adaptés et relatifs pour autoscale
            # Pour les cotations dans la zone colorée (shelf_rack), utiliser un espacement plus petit
            # car elles doivent rester dans la zone
            if L_actual and W_actual:
                min_dim = min(L_actual, W_actual)
                # Espacements relatifs au panneau pour fonctionner en autoscale
                montant_min_gap = max(25, min_dim * 0.025) if "Montant" in panel_name else max(30, min_dim * 0.03)
                montant_step = max(20, min_dim * 0.02) if "Montant" in panel_name else max(35, min_dim * 0.035)
            else:
                montant_min_gap = 30.0 if "Montant" in panel_name else 35.0
                montant_step = 25.0 if "Montant" in panel_name else 40.0
            vertical_dims_items = stack_dimensions_on_axis(vertical_dims_items, min_gap=montant_min_gap, step_per_conflict=montant_step, panel_L=L_actual, panel_W=W_actual)
            
            # Collecter les positions des cotations shelf_rack pour dessiner des traits entre elles
            shelf_rack_positions = []
            shelf_rack_y_positions = []  # Pour les cotations verticales (Y) dans la zone rouge
            # Collecter aussi les segments de la chaîne de cotes située en BAS du montant
            # (bord où pointe le triangle vide) pour pouvoir l'entourer visuellement.
            bottom_chain_segments = []
            
            # Dessiner toutes les cotes avec leurs offsets finaux
            for dim_item in vertical_dims_items:
                if dim_item.skip:
                    continue

                # Pour les montants : supprimer complètement la chaîne de cotes
                # externes "classiques" qui se retrouve à droite (côté rouge).
                if "Montant" in panel_name and dim_item.kind == "montant_external_right_chain":
                    continue
                
                # Pour les racks d'étagères : gérer les cotations horizontales (X) dans la zone colorée
                if dim_item.kind == 'shelf_rack' and dim_item.axis == 'x' and dim_item.y_dim_pos is not None:
                    # Cotation horizontale pour la rangée d'étagères
                    y_dim_to_use = dim_item.final_y_dim if dim_item.final_y_dim is not None else dim_item.y_dim_pos
                    # Position réelle du trou (pour les traits de rappel) - utiliser feature_pos
                    hole_y_pos = dim_item.feature_pos[1] if dim_item.feature_pos else dim_item.y_dim_pos
                    is_internal_dim = True
                    panel_bounds_arg = None
                    # Pour les cotations à côté des trous (p0 == p1), utiliser la position dans la zone décalée
                    if abs(dim_item.p0 - dim_item.p1) < 0.1:
                        # Cotation à côté d'un trou : placer la cotation dans la zone décalée (y_dim_to_use)
                        # mais utiliser hole_y_pos pour les traits de rappel depuis les trous.
                        # Couleur attribuée par groupe pour différencier chaque ligne de cotes sur les montants.
                        if "Montant" in panel_name:
                            dim_color_h = get_color_for_group(dim_item.chain_id or "rack_shelf_x", is_montant=True)
                        else:
                            dim_color_h = "blue"
                        add_pro_dimension(
                            fig, dim_item.p0, hole_y_pos, dim_item.p1, hole_y_pos,
                            dim_item.text, 0.0, axis='x', line_dash='dot', color=dim_color_h, font_size=6,
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=cascade_tracker,
                            panel_bounds=panel_bounds_arg,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name,
                            zone_y_dim=y_dim_to_use  # Position dans la zone décalée pour le texte
                        )
                        # Stocker la position X de la cotation (après cascade) pour dessiner des traits entre elles
                        # On récupère la dernière position cascade_tracker pour cette cotation
                        if cascade_tracker and len(cascade_tracker) > 0:
                            last_cascade = cascade_tracker[-1]
                            shelf_rack_positions.append({
                                'x': last_cascade[0],  # Position X après cascade
                                'y': y_dim_to_use,  # Position Y dans la zone
                                'hole_x': dim_item.p0  # Position X du trou
                            })
                    else:
                        # Cotation entre deux trous : utiliser y_dim_to_use pour la ligne de cotation
                        add_pro_dimension(
                            fig, dim_item.p0, y_dim_to_use, dim_item.p1, y_dim_to_use,
                            dim_item.text, dim_item.final_offset, axis='x', line_dash='solid',  # line_dash='solid' pour traits pro
                            rotate_coords_fn=rotate_coords if needs_rotation else None,
                            vertical_dims_tracker=None,
                            cascade_tracker=cascade_tracker,
                            panel_bounds=panel_bounds_arg,
                            panel_L=L_actual,
                            panel_W=W_actual,
                            is_montant=("Montant" in panel_name),
                            panel_name=panel_name
                        )
                else:
                    # Cotation verticale (Y) classique
                    x_dim_to_use = dim_item.final_x_dim if dim_item.final_x_dim is not None else dim_item.x_dim_pos
                    is_internal_dim = (dim_item.kind == 'shelf_rack' or dim_item.kind == 'montant_internal')
                    panel_bounds_arg = None if is_internal_dim else panel_bounds
                    
                    # Pour les cotations verticales shelf_rack dans la zone rouge : couleur ROUGE et taille réduite
                    is_rack_y_in_zone = (dim_item.kind == 'shelf_rack' and dim_item.chain_id == 'rack_shelf_y')
                    
                    # Filtrer les cotations selon leur position après rotation
                    # Pour les montants avec rotation : afficher seulement les cotations en dessous (après rotation)
                    # et retirer celles à droite (après rotation)
                    should_display_dim = True
                    if needs_rotation and "Montant" in panel_name and not is_rack_y_in_zone:
                        # Après rotation : (x, y) -> (W - y, x)
                        # Calculer la position après rotation de la ligne de cotation
                        y_center = (dim_item.p0 + dim_item.p1) / 2.0
                        x_after_rot, y_after_rot = rotate_coords(x_dim_to_use, y_center)
                        
                        # Les cotations à droite après rotation (x élevé) doivent être retirées
                        # Les cotations en dessous après rotation (y élevé) doivent être affichées
                        # Pour un montant avec rotation :
                        # - Les cotations externes à gauche (base_offset < 0) deviennent en bas après rotation
                        #   car x_dim_to_use est négatif, donc y_after_rot = x_dim_to_use (négatif = en bas)
                        # - On garde seulement celles qui sont en bas (y_after_rot négatif ou faible)
                        # - On retire celles qui sont à droite (x_after_rot élevé)
                        
                        if dim_item.base_offset < 0:  # Cotation externe à gauche (devient en bas après rotation)
                            # Après rotation : y_after_rot = x_dim_to_use (négatif = en bas)
                            # On garde seulement si elle est bien en bas (y_after_rot négatif ou faible)
                            if y_after_rot > W_actual * 0.4:  # Si pas assez en bas, retirer
                                should_display_dim = False
                        elif x_after_rot > L_actual * 0.7:  # Cotation à droite après rotation, retirer
                            should_display_dim = False
                        # Les cotations internes (base_offset == 0) dans la zone rouge sont gardées
                    
                    if should_display_dim:
                        # Utiliser la couleur du groupe si définie, sinon couleur par défaut.
                        # Pour les montants, on veut une couleur différente par chaîne de cotations,
                        # y compris pour les cotes externes en bas du montant (base_offset < 0).
                        if "Montant" in panel_name:
                            if dim_item.color_group:
                                dim_color = dim_item.color_group
                            else:
                                # Chaîne pour les cotes de la zone interne spéciale (rack_shelf_y)
                                if is_rack_y_in_zone:
                                    group_key = dim_item.chain_id or "montant_rack_zone"
                                # Chaîne pour les cotes externes vers le bas (bord où pointe le triangle vide)
                                elif dim_item.base_offset < 0:
                                    group_key = dim_item.chain_id or "montant_bottom_chain"
                                else:
                                    group_key = dim_item.chain_id or f"montant_chain_{id(dim_item)}"
                                dim_color = get_color_for_group(group_key, is_montant=True)
                            
                            # MARKERS pour tracer le code:
                            # Cotes internes (base_offset == 0) -> CYAN
                            if dim_item.base_offset == 0.0:
                                dim_color = "cyan"
                            # Cotes externes (base_offset < 0) -> MAGENTA
                            elif dim_item.base_offset < 0 and not is_rack_y_in_zone:
                                dim_color = "magenta"
                            # Taille réduite pour les cotes de la zone interne spéciale
                            dim_font_size = 6 if is_rack_y_in_zone else 11
                        else:
                            if dim_item.color_group:
                                dim_color = dim_item.color_group  # Utiliser la couleur du groupe
                                dim_font_size = 11
                            else:
                                dim_color = "black"  # Par défaut
                                dim_font_size = 11
                        
                        # Si c'est une cote appartenant à la chaîne "bas du montant",
                        # mémoriser la position de sa ligne de cote pour pouvoir
                        # entourer TOUT le groupe après coup.
                        if "Montant" in panel_name and dim_item.base_offset < 0:
                            if needs_rotation:
                                # Après rotation, on stocke les coordonnées dans le repère final.
                                x0_rot, y0_rot = rotate_coords(x_dim_to_use, dim_item.p0)
                                x1_rot, y1_rot = rotate_coords(x_dim_to_use, dim_item.p1)
                                x_store = x0_rot
                                y0_store = min(y0_rot, y1_rot)
                                y1_store = max(y0_rot, y1_rot)
                            else:
                                x_store = x_dim_to_use
                                y0_store = min(dim_item.p0, dim_item.p1)
                                y1_store = max(dim_item.p0, dim_item.p1)
                            bottom_chain_segments.append({
                                'x': x_store,
                                'y0': y0_store,
                                'y1': y1_store
                            })
                        
                        # Pour les étagères fixes : dessiner les cotations comme pour les traverses
                        # avec des traits pointillés depuis chaque trou vers la ligne de cotation
                        if is_shelf and "Montant" not in panel_name:
                            tick_len = 5
                            line_width = 0.8
                            y_dim_line = x_dim_to_use  # Position X de la ligne de cotation verticale
                            
                            # Colorer en ROUGE si c'est un montant avec trous de tiroirs
                            dim_line_color = "red" if has_drawer_holes_montant else dim_color
                            
                            if dim_item.base_offset == 0.0:
                                # Cotation interne entre deux trous pour étagère fixe
                                # Trait pointillé depuis le premier trou vers la ligne de cotation
                                if needs_rotation:
                                    x_trou1_rot, y_trou1_rot = rotate_coords(0, dim_item.p0)
                                    x_dim_rot1, y_dim_rot1 = rotate_coords(y_dim_line, dim_item.p0)
                                    fig.add_shape(type="line", x0=x_trou1_rot, y0=y_trou1_rot, x1=x_dim_rot1, y1=y_dim_rot1, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    # Tick sur la ligne de cotation au niveau du premier trou
                                    fig.add_shape(type="line", x0=x_dim_rot1-tick_len, y0=y_dim_rot1, x1=x_dim_rot1+tick_len, y1=y_dim_rot1, line=dict(color=dim_color, width=1.2, dash='solid'))
                                else:
                                    fig.add_shape(type="line", x0=0, y0=dim_item.p0, x1=y_dim_line, y1=dim_item.p0, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    # Tick sur la ligne de cotation au niveau du premier trou
                                    fig.add_shape(type="line", x0=y_dim_line-tick_len, y0=dim_item.p0, x1=y_dim_line+tick_len, y1=dim_item.p0, line=dict(color=dim_color, width=1.2, dash='solid'))
                                
                                # Trait pointillé depuis le deuxième trou vers la ligne de cotation
                                if needs_rotation:
                                    x_trou2_rot, y_trou2_rot = rotate_coords(0, dim_item.p1)
                                    x_dim_rot2, y_dim_rot2 = rotate_coords(y_dim_line, dim_item.p1)
                                    fig.add_shape(type="line", x0=x_trou2_rot, y0=y_trou2_rot, x1=x_dim_rot2, y1=y_dim_rot2, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    # Tick sur la ligne de cotation au niveau du deuxième trou
                                    fig.add_shape(type="line", x0=x_dim_rot2-tick_len, y0=y_dim_rot2, x1=x_dim_rot2+tick_len, y1=y_dim_rot2, line=dict(color=dim_color, width=1.2, dash='solid'))
                                else:
                                    fig.add_shape(type="line", x0=0, y0=dim_item.p1, x1=y_dim_line, y1=dim_item.p1, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    # Tick sur la ligne de cotation au niveau du deuxième trou
                                    fig.add_shape(type="line", x0=y_dim_line-tick_len, y0=dim_item.p1, x1=y_dim_line+tick_len, y1=dim_item.p1, line=dict(color=dim_color, width=1.2, dash='solid'))
                                
                                # Ligne de cotation verticale entre les deux trous
                                y_mid = (dim_item.p0 + dim_item.p1) / 2.0
                                if needs_rotation:
                                    x_dim_mid_rot, y_dim_mid_rot = rotate_coords(y_dim_line, y_mid)
                                    x_dim_rot1, y_dim_rot1 = rotate_coords(y_dim_line, dim_item.p0)
                                    x_dim_rot2, y_dim_rot2 = rotate_coords(y_dim_line, dim_item.p1)
                                    fig.add_shape(type="line", x0=x_dim_rot1, y0=y_dim_rot1, x1=x_dim_rot2, y1=y_dim_rot2, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    # Texte au milieu avec l'écart
                                    fig.add_annotation(x=x_dim_mid_rot + 15, y=y_dim_mid_rot, text=dim_item.text, showarrow=False, font=dict(color=dim_color, size=dim_font_size, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                                else:
                                    fig.add_shape(type="line", x0=y_dim_line, y0=dim_item.p0, x1=y_dim_line, y1=dim_item.p1, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_annotation(x=y_dim_line + 15, y=y_mid, text=dim_item.text, showarrow=False, font=dict(color=dim_color, size=dim_font_size, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                            else:
                                # Cotation externe (bord/premier trou ou dernier trou/bord)
                                if needs_rotation:
                                    x_bord_rot, y_bord_rot = rotate_coords(0, dim_item.p0)
                                    x_trou_rot, y_trou_rot = rotate_coords(0, dim_item.p1)
                                    x_dim_bord_rot, y_dim_bord_rot = rotate_coords(y_dim_line, dim_item.p0)
                                    x_dim_trou_rot, y_dim_trou_rot = rotate_coords(y_dim_line, dim_item.p1)
                                    fig.add_shape(type="line", x0=x_bord_rot, y0=y_bord_rot, x1=x_dim_bord_rot, y1=y_dim_bord_rot, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_shape(type="line", x0=x_trou_rot, y0=y_trou_rot, x1=x_dim_trou_rot, y1=y_dim_trou_rot, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_shape(type="line", x0=x_dim_bord_rot-tick_len, y0=y_dim_bord_rot, x1=x_dim_bord_rot+tick_len, y1=y_dim_bord_rot, line=dict(color=dim_color, width=1.2, dash='solid'))
                                    fig.add_shape(type="line", x0=x_dim_trou_rot-tick_len, y0=y_dim_trou_rot, x1=x_dim_trou_rot+tick_len, y1=y_dim_trou_rot, line=dict(color=dim_color, width=1.2, dash='solid'))
                                    y_mid = (dim_item.p0 + dim_item.p1) / 2.0
                                    x_dim_mid_rot, y_dim_mid_rot = rotate_coords(y_dim_line, y_mid)
                                    fig.add_shape(type="line", x0=x_dim_bord_rot, y0=y_dim_bord_rot, x1=x_dim_trou_rot, y1=y_dim_trou_rot, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_annotation(x=x_dim_mid_rot + 15, y=y_dim_mid_rot, text=dim_item.text, showarrow=False, font=dict(color=dim_color, size=dim_font_size, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                                else:
                                    fig.add_shape(type="line", x0=0, y0=dim_item.p0, x1=y_dim_line, y1=dim_item.p0, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_shape(type="line", x0=0, y0=dim_item.p1, x1=y_dim_line, y1=dim_item.p1, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_shape(type="line", x0=y_dim_line-tick_len, y0=dim_item.p0, x1=y_dim_line+tick_len, y1=dim_item.p0, line=dict(color=dim_color, width=1.2, dash='solid'))
                                    fig.add_shape(type="line", x0=y_dim_line-tick_len, y0=dim_item.p1, x1=y_dim_line+tick_len, y1=dim_item.p1, line=dict(color=dim_color, width=1.2, dash='solid'))
                                    y_mid = (dim_item.p0 + dim_item.p1) / 2.0
                                    fig.add_shape(type="line", x0=y_dim_line, y0=dim_item.p0, x1=y_dim_line, y1=dim_item.p1, line=dict(color=dim_color, width=line_width, dash='dot'))
                                    fig.add_annotation(x=y_dim_line + 15, y=y_mid, text=dim_item.text, showarrow=False, font=dict(color=dim_color, size=dim_font_size, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                        else:
                            # Pour les autres éléments : utiliser add_pro_dimension normalement
                            # MARKER: Colorer EN ORANGE pour montants pour repérer les dimensions
                            use_color = "orange" if "Montant" in panel_name else dim_color
                            add_pro_dimension(
                                fig, x_dim_to_use, dim_item.p0, x_dim_to_use, dim_item.p1,
                                dim_item.text, dim_item.final_offset, axis='y', line_dash='dot', color=use_color, font_size=dim_font_size,
                                rotate_coords_fn=rotate_coords if needs_rotation else None,
                                vertical_dims_tracker=vertical_dims_tracker,
                                cascade_tracker=cascade_tracker,
                                panel_bounds=panel_bounds_arg,
                                panel_L=L_actual,
                                panel_W=W_actual,
                                is_montant=("Montant" in panel_name),
                                panel_name=panel_name
                            )
                        
                        # Stocker la position Y de la cotation verticale pour dessiner des traits entre elles
                        # Seulement pour les cotations dans la zone rouge (bleues)
                        if is_rack_y_in_zone:
                            # Récupérer la position Y finale après cascade (dans le repère déjà pivoté,
                            # car add_pro_dimension a appliqué rotate_coords AVANT la cascade).
                            final_y_pos = cascade_tracker[-1][0] if cascade_tracker and len(cascade_tracker) > 0 else (dim_item.p0 + dim_item.p1) / 2.0
                            # Calculer la position X réelle de la ligne de cote dans le repère final.
                            # Pour axis='y' avec rotation 90° horaire (x, y) -> (W - y, x) et offset=0,
                            # la ligne de cote interne se trouve à x = W_actual - p0 (premier point).
                            if needs_rotation and "Montant" in panel_name:
                                x_store = W_actual - dim_item.p0
                            else:
                                x_store = x_dim_to_use
                            shelf_rack_y_positions.append({
                                'x': x_store,
                                'y': final_y_pos,
                                'y0': dim_item.p0,
                                'y1': dim_item.p1
                            })
                        # Pour toutes les cotations verticales rouges (pas seulement rack_shelf_y), 
                        # ajouter aussi les traits de construction si elles sont dans la zone rouge
                        elif dim_color == "red" and x_dim_to_use > L_actual * 0.7:
                            # Cotation rouge dans la zone rouge (mais pas rack_shelf_y)
                            final_y_pos = cascade_tracker[-1][0] if cascade_tracker and len(cascade_tracker) > 0 else (dim_item.p0 + dim_item.p1) / 2.0
                            if needs_rotation and "Montant" in panel_name:
                                x_store = W_actual - dim_item.p0
                            else:
                                x_store = x_dim_to_use
                            shelf_rack_y_positions.append({
                                'x': x_store,
                                'y': final_y_pos,
                                'y0': dim_item.p0,
                                'y1': dim_item.p1
                            })
            
            # Si on a détecté une chaîne de cotes "bas du montant", l'entourer
            # pour qu'elle soit parfaitement identifiable sur la feuille d'usinage.
            if bottom_chain_segments and "Montant" in panel_name:
                # On prend l'abscisse de la première cote (toutes partagent la même ligne de cote).
                x_chain = bottom_chain_segments[0]['x']
                y_min_chain = min(seg['y0'] for seg in bottom_chain_segments)
                y_max_chain = max(seg['y1'] for seg in bottom_chain_segments)
                
                # Un petit padding pour ne pas coller au trait ni au texte.
                pad_x = (L_actual + W_actual) * 0.005 if (L_actual and W_actual) else 10.0
                pad_y = (L_actual + W_actual) * 0.005 if (L_actual and W_actual) else 10.0
                
                fig.add_shape(
                    type="rect",
                    x0=x_chain - pad_x * 2.0,
                    x1=x_chain + pad_x * 0.5,
                    y0=y_min_chain - pad_y,
                    y1=y_max_chain + pad_y,
                    line=dict(color="red", width=1.5),
                    fillcolor="rgba(0,0,0,0)",
                    layer="above"
                )
            
            # Dessiner des traits de cotation en pointillés entre les chiffres de cotation dans la zone colorée
            if shelf_rack_positions and len(shelf_rack_positions) > 1:
                # Trier par position X
                shelf_rack_positions.sort(key=lambda p: p['x'])
                # Calculer la largeur de ligne relative
                if L_actual and W_actual:
                    min_dim = min(L_actual, W_actual)
                    line_width_dotted = max(0.6, min_dim * 0.0006)
                else:
                    line_width_dotted = 0.6

                if "Montant" in panel_name:
                    # Pour les montants : ticks entre les chiffres pour delimiter.
                    first = shelf_rack_positions[0]
                    y_line = first['y']  # même Y pour toutes les cotations dans la zone
                    tick_len = max(6.0, line_width_dotted * 10.0)
                    for pos in shelf_rack_positions:
                        fig.add_shape(
                            type="line",
                            x0=pos['x'],
                            y0=y_line - tick_len,
                            x1=pos['x'],
                            y1=y_line + tick_len,
                            line=dict(color="rgba(180,180,180,1.0)", width=max(0.8, line_width_dotted), dash='dash'),
                            layer="above"
                        )
                else:
                    # Autres panneaux : traits entre chaque paire consécutive
                    for i in range(len(shelf_rack_positions) - 1):
                        pos1 = shelf_rack_positions[i]
                        pos2 = shelf_rack_positions[i + 1]
                        y_line = pos1['y']  # Même Y pour toutes les cotations dans la zone
                        fig.add_shape(
                            type="line",
                            x0=pos1['x'],
                            y0=y_line,
                            x1=pos2['x'],
                            y1=y_line,
                            line=dict(color="blue", width=line_width_dotted, dash='dot'),
                            layer="above"
                        )
            
            # Dessiner des traits de construction en pointillés bleus pour les cotations verticales bleues
            if shelf_rack_y_positions and len(shelf_rack_y_positions) > 1:
                # Trier par position Y
                shelf_rack_y_positions.sort(key=lambda p: p['y'])
                # Calculer la largeur de ligne relative
                if L_actual and W_actual:
                    min_dim = min(L_actual, W_actual)
                    line_width_dotted_y = max(0.6, min_dim * 0.0006)
                else:
                    line_width_dotted_y = 0.6

                if "Montant" in panel_name:
                    # Pour les montants : un SEUL trait vertical qui longe l'ensemble,
                    # directement dans le repère final (les coordonnées stockées sont
                    # déjà éventuellement passées par rotate_coords ci‑dessus).
                    first = shelf_rack_y_positions[0]
                    last = shelf_rack_y_positions[-1]
                    x_line = first['x']
                    fig.add_shape(
                        type="line",
                        x0=x_line,
                        y0=first['y'],
                        x1=x_line,
                        y1=last['y'],
                        line=dict(color="blue", width=line_width_dotted_y, dash='dot'),
                        layer="above"
                    )
                else:
                    # Autres panneaux : traits entre chaque paire consécutive
                    for i in range(len(shelf_rack_y_positions) - 1):
                        pos1 = shelf_rack_y_positions[i]
                        pos2 = shelf_rack_y_positions[i + 1]
                        x_line = pos1['x']  # Même X pour toutes les cotations dans la zone
                        # Trait vertical en pointillés bleus entre les deux cotations (traits de construction)
                        fig.add_shape(
                            type="line",
                            x0=x_line,
                            y0=pos1['y'],
                            x1=x_line,
                            y1=pos2['y'],
                            line=dict(color="blue", width=line_width_dotted_y, dash='dot'),
                            layer="above"  # Au-dessus pour être bien visible
                        )
            
            # Mettre à jour bounds_x avec le minimum des x_dim utilisés
            if vertical_dims_items:
                x_dims_list = [d.final_x_dim if d.final_x_dim is not None else d.x_dim_pos 
                              for d in vertical_dims_items if d.x_dim_pos is not None]
                if x_dims_list:
                    min_x_used = min(x_dims_list)
                    bounds_x.append(min_x_used - 20)
                else:
                    bounds_x.append(x_dim_start - 20)
            else:
                bounds_x.append(x_dim_start - 20)

        # Cotations horizontales des trous de face
        # Pour les montants : on aura des cotes X spécifiques plus bas (dans la zone bleue),
        # donc ici on ne traite que les autres panneaux (portes, fonds, etc.).
        if "Montant" not in panel_name:
            # Pour les portes : RETIRER les cotations d'espacement entre les trous
            # On garde seulement les coordonnées exactes dans la zone rouge
            is_door_with_hinges = "Porte" in panel_name
            if is_door_with_hinges:
                # Pour les portes : pas de cotations horizontales d'espacement
                # Les coordonnées exactes sont déjà affichées dans la zone rouge
                pass
            elif not (is_drawer_face or is_drawer_back):
                # Sur TRAVERSES et PANNEAU ARRIÈRE : retirer toute cote liée aux montants secondaires (leurs trous sont des tourillons)
                face_holes_for_x = face_holes_list
                is_back_panel = "panneau arrière" in panel_lower or ("fond" in panel_lower and "tiroir" not in panel_lower)
                if "traverse" in panel_lower or is_back_panel:
                    face_holes_for_x = [h for h in face_holes_list if str(h.get('type', '')).lower() != 'tourillon']
                unique_x = sorted(list(set([round(h['x'], 1) for h in face_holes_for_x])))
                # Tracker pour éviter que les textes des cotes horizontales ne se chevauchent
                horiz_cascade_tracker = []
            
                # Cotations d'espacement entre trous pour le panneau arrière
                # Même style que les traverses : cotations d'espacement au lieu de coordonnées absolues
                y_dim_base = -30  # Position de la ligne de cotation
                
                if len(unique_x) > 0:
                    # Cotation : écart entre le bord gauche et le premier trou
                    x_first = unique_x[0]
                    ecart_bord_gauche = x_first - 0
                    add_pro_dimension(fig, 0, y_dim_base, x_first, y_dim_base, format_number_no_decimal(ecart_bord_gauche), -10,
                                    axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                    cascade_tracker=horiz_cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                    
                    # Cotations : espacements entre chaque paire de trous consécutifs
                    for i in range(len(unique_x) - 1):
                        x_curr = unique_x[i]
                        x_next = unique_x[i + 1]
                        ecart = x_next - x_curr
                        add_pro_dimension(fig, x_curr, y_dim_base, x_next, y_dim_base, format_number_no_decimal(ecart), -10,
                                        axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                        cascade_tracker=horiz_cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                    
                    # Cotation : écart entre le dernier trou et le bord droit
                    x_last = unique_x[-1]
                    ecart_bord_droit = L_actual - x_last
                    add_pro_dimension(fig, x_last, y_dim_base, L_actual, y_dim_base, format_number_no_decimal(ecart_bord_droit), -10,
                                    axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                    cascade_tracker=horiz_cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                    
                    # Mettre à jour bounds_y
                    bounds_y.append(y_dim_base - 30)

        # --- ZONE SPÉCIFIQUE : cotes Y des lignes de trous des MONTANTS ET TRAVERSES dans la zone BLEUE ---
        # Pour chaque rangée horizontale de trous présente sur un montant ou traverse, on ajoute une cote
        # professionnelle (trait + ticks) indiquant sa coordonnée Y depuis le bas,
        # placée EXACTEMENT sous la position des trous, dans le rectangle bleu.
        # Pour les traverses : RETIRER les cotations des trous de montants secondaires (tourillons non sur les bords)
        if ("Montant" in panel_name or "traverse" in panel_lower) and face_holes_list and panel_bounds:
            # Bords du panneau (repère déjà pivoté si besoin)
            panel_x_min, panel_x_max = panel_bounds['x']
            panel_y_min, panel_y_max = panel_bounds['y']
            
            # Pour les traverses : définir offset et rect_thickness si pas déjà définis (comme pour les montants)
            if "traverse" in panel_lower and "Montant" not in panel_name:
                panel_width_x = panel_x_max - panel_x_min
                panel_height_y = panel_y_max - panel_y_min
                rect_thickness = max(20.0, min(panel_width_x, panel_height_y) * 0.06)
                offset = rect_thickness * 0.8
            
            # Rectangle "EN DESSOUS" (repère 2) : bande bleue déjà tracée
            blue_y0 = panel_y_min - offset - rect_thickness
            blue_y1 = panel_y_min - offset
            blue_y_center = (blue_y0 + blue_y1) * 0.5
            
            filtered_face_holes = face_holes_list
            if "traverse" in panel_lower:
                filtered_face_holes = [h for h in face_holes_list if str(h.get('type','')).lower() != 'tourillon']

            # --- COTES INTERNES SPÉCIALES POUR LES CHARNIÈRES ---
            # Si le montant porte une porte avec charnières (trous de vis ⌀5/11.5),
            # on ajoute, à l'intérieur de la face du montant :
            # 1) la distance entre les 2 vis de chaque charnière
            # 2) la distance entre le bord du montant (x=0) et la première vis
            #
            # Les trous de charnière sont repérés par leur 'diam_str' spécifique "⌀5/11.5".
            hinge_rows = {}
            for h in face_holes_list:
                diam = str(h.get('diam_str', ''))
                if "11.5" not in diam:
                    continue
                y_row = round(float(h.get('y', 0.0)), 1)
                hinge_rows.setdefault(y_row, []).append(h)
            
            for y_row, holes_in_row in hinge_rows.items():
                if len(holes_in_row) < 2:
                    continue
                # Récupérer les positions X brutes des vis de charnière
                x_vals = sorted([float(h.get('x', 0.0)) for h in holes_in_row])
                first_x = x_vals[0]
                last_x = x_vals[-1]
                if last_x <= first_x:
                    continue
                # 1) Cote interne entre les 2 vis (distance last_x - first_x, arrondie à l'entier)
                dist_between = last_x - first_x
                dist_between_int = int(round(dist_between))
                # Nouvelle méthode : décaler la ligne de cotation elle-même légèrement vers le haut
                # et augmenter l'offset pour que le texte soit encore plus décalé
                y_line_offset = 50.0  # Décalage vertical de la ligne de cotation
                add_pro_dimension(
                    fig,
                    first_x, y_row + y_line_offset,
                    last_x, y_row + y_line_offset,
                    text_val=format_number_no_decimal(dist_between_int),
                    # Ligne de cote décalée légèrement vers le haut, offset augmenté pour le texte
                    offset_dist=100,  # Offset plus grand pour décaler le texte encore plus
                    axis='x',
                    color="black",  # Noir pour les cotes de charnières
                    font_size=10,
                    # Trait de cotation pointillé avec petits traits perpendiculaires
                    # aux extrémités (gérés dans add_pro_dimension).
                    line_dash='dot',
                    xanchor='center',
                    yanchor='middle',
                    rotate_coords_fn=rotate_coords if needs_rotation else None,
                    vertical_dims_tracker=None,
                    cascade_tracker=None,
                    panel_bounds=None,   # interne à la face
                    panel_L=L_actual,
                    panel_W=W_actual,
                    is_montant=True,
                    panel_name=panel_name
                )
                # 2) Cote interne entre le bord du montant (x=0) et la première vis
                first_x_int = int(round(first_x))
                add_pro_dimension(
                    fig,
                    0.0, y_row - y_line_offset,
                    first_x, y_row - y_line_offset,
                    text_val=format_number_no_decimal(first_x_int),
                    # Même principe : ligne décalée vers le bas, offset négatif plus grand
                    offset_dist=-100,  # Offset négatif plus grand pour décaler le texte
                    axis='x',
                    color="black",  # Noir pour les cotes de charnières
                    font_size=10,
                    # Même style de trait pointillé pour cette cote d'origine bord -> 1re vis.
                    line_dash='dot',
                    xanchor='center',
                    yanchor='middle',
                    rotate_coords_fn=rotate_coords if needs_rotation else None,
                    vertical_dims_tracker=None,
                    cascade_tracker=None,
                    panel_bounds=None,
                    panel_L=L_actual,
                    panel_W=W_actual,
                    is_montant=True,
                    panel_name=panel_name
                )
                
                # 3) Cote sous le bord bas du panneau : distance entre le premier trou de vis et le rebord droit du montant
                # Pour les montants droits uniquement (MAIS PAS si montant a des trous de tiroirs)
                if "Montant Droit" in panel_name and not has_drawer_holes_montant:
                    # Calculer la distance entre first_x et le rebord droit (L_actual)
                    dist_to_right_edge = L_actual - first_x
                    dist_to_right_edge_int = int(round(dist_to_right_edge))
                    
                    # Position Y sous le bord bas du panneau (y=0)
                    y_below_panel = -50.0  # Position sous le panneau
                    
                    # Dessiner un trait de cotation gris en pointillé de first_x à L_actual
                    # Ligne horizontale sous le panneau
                    if needs_rotation:
                        x0_rot, y0_rot = rotate_coords(first_x, 0)
                        x1_rot, y1_rot = rotate_coords(L_actual, 0)
                        x0_below_rot, y0_below_rot = rotate_coords(first_x, y_below_panel)
                        x1_below_rot, y1_below_rot = rotate_coords(L_actual, y_below_panel)
                        # Traits verticaux depuis les points jusqu'à la ligne de cotation
                        fig.add_shape(type="line", x0=x0_rot, y0=y0_rot, x1=x0_below_rot, y1=y0_below_rot, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        fig.add_shape(type="line", x0=x1_rot, y0=y1_rot, x1=x1_below_rot, y1=y1_below_rot, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        # Ligne horizontale de cotation
                        fig.add_shape(type="line", x0=x0_below_rot, y0=y0_below_rot, x1=x1_below_rot, y1=y1_below_rot, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        # Ticks aux extrémités
                        tick_len = 5
                        fig.add_shape(type="line", x0=x0_below_rot-tick_len, y0=y0_below_rot, x1=x0_below_rot+tick_len, y1=y0_below_rot, 
                                     line=dict(color="gray", width=1.2), layer="above")
                        fig.add_shape(type="line", x0=x1_below_rot-tick_len, y0=y1_below_rot, x1=x1_below_rot+tick_len, y1=y1_below_rot, 
                                     line=dict(color="gray", width=1.2), layer="above")
                        # Texte de la cote au centre de la ligne
                        text_x_center = (x0_below_rot + x1_below_rot) / 2
                        text_y_center = y0_below_rot - 15  # Légèrement en dessous de la ligne
                        fig.add_annotation(x=text_x_center, y=text_y_center, text=format_number_no_decimal(dist_to_right_edge_int), 
                                         showarrow=False, font=dict(size=10, color="black"), 
                                         xanchor='center', yanchor='middle', bgcolor='rgba(255,255,255,0.8)')
                    else:
                        # Traits verticaux depuis les points jusqu'à la ligne de cotation
                        fig.add_shape(type="line", x0=first_x, y0=0, x1=first_x, y1=y_below_panel, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        fig.add_shape(type="line", x0=L_actual, y0=0, x1=L_actual, y1=y_below_panel, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        # Ligne horizontale de cotation
                        fig.add_shape(type="line", x0=first_x, y0=y_below_panel, x1=L_actual, y1=y_below_panel, 
                                     line=dict(color="gray", width=0.8, dash='dot'), layer="above")
                        # Ticks aux extrémités
                        tick_len = 5
                        fig.add_shape(type="line", x0=first_x-tick_len, y0=y_below_panel, x1=first_x+tick_len, y1=y_below_panel, 
                                     line=dict(color="gray", width=1.2), layer="above")
                        fig.add_shape(type="line", x0=L_actual-tick_len, y0=y_below_panel, x1=L_actual+tick_len, y1=y_below_panel, 
                                     line=dict(color="gray", width=1.2), layer="above")
                        # Texte de la cote au centre de la ligne
                        text_x_center = (first_x + L_actual) / 2
                        text_y_center = y_below_panel - 15  # Légèrement en dessous de la ligne
                        fig.add_annotation(x=text_x_center, y=text_y_center, text=format_number_no_decimal(dist_to_right_edge_int), 
                                         showarrow=False, font=dict(size=10, color="black"), 
                                         xanchor='center', yanchor='middle', bgcolor='rgba(255,255,255,0.8)')

            # --- COTES X DANS LA ZONE VERTE POUR LES TROUS D'ASSEMBLAGE MONTANT / TRAVERSE ---
            # On ne garde QUE les trous d'assemblage vis / tourillons des traverses :
            # - type 'vis' ou 'tourillon'
            # - très proches des bords haut ou bas du montant (sur les tranches),
            #   i.e. y proche de 0 ou de W_actual.
            # SUPPRIMÉ : Cotations des trous d'assemblage aux extrémités
            # assembly_holes = []

            # (Les cotations X spécifiques de l'étagère centrale ont été retirées pour éviter
            #  les traits de cotations entre les trous vis/tourillon. On ne garde ici que
            #  les cotes Y dans la zone bleue, comme repère propre et lisible.)

        # --- COTATIONS X POUR LES TROUS DE TOURILLONS (MONTANTS SECONDAIRES) SUR LES TRAVERSES ---
        # Style identique aux cotations des tranches : ligne droite, non-cascade
        if "traverse" in panel_lower and face_holes_list:
            # Filtrer SEULEMENT les tourillons (trous de montants secondaires)
            tourillon_holes = [h for h in face_holes_list if str(h.get('type', '')).lower() == 'tourillon']
            
            if tourillon_holes:
                # Extraire les positions X uniques des tourillons
                x_tourillons = sorted(list(set([round(h['x'], 1) for h in tourillon_holes])))
                
                if len(x_tourillons) > 0:
                    # Ligne de cotation unique au-dessus de la face
                    tick_len = 5
                    line_width = 0.8
                    y_dim_tourillons = -60  # Plus haut que les autres cotations
                    
                    # Trait pointillé depuis le bord gauche
                    if needs_rotation:
                        x0_rot, y0_rot = rotate_coords(0, y_dim_tourillons)
                        fig.add_shape(type="line", x0=x0_rot-tick_len, y0=y0_rot, x1=x0_rot+tick_len, y1=y0_rot, 
                                     line=dict(color="black", width=1.2, dash='solid'))
                    else:
                        fig.add_shape(type="line", x0=0-tick_len, y0=y_dim_tourillons, x1=0+tick_len, y1=y_dim_tourillons, 
                                     line=dict(color="black", width=1.2, dash='solid'))
                    
                    # Trait pointillé depuis le bord droit
                    if needs_rotation:
                        xL_rot, yL_rot = rotate_coords(L_actual, y_dim_tourillons)
                        fig.add_shape(type="line", x0=xL_rot-tick_len, y0=yL_rot, x1=xL_rot+tick_len, y1=yL_rot, 
                                     line=dict(color="black", width=1.2, dash='solid'))
                    else:
                        fig.add_shape(type="line", x0=L_actual-tick_len, y0=y_dim_tourillons, x1=L_actual+tick_len, y1=y_dim_tourillons, 
                                     line=dict(color="black", width=1.2, dash='solid'))
                    
                    # Cotation bord gauche -> premier tourillon supprimée à la demande utilisateur.
                    
                    # Cotation : écart entre le dernier tourillon et le bord droit
                    x_last_tour = x_tourillons[-1]
                    ecart_droit = L_actual - x_last_tour
                    if needs_rotation:
                        x_last_tour_rot, y_last_tour_rot = rotate_coords(x_last_tour, y_dim_tourillons)
                        xL_rot, yL_rot = rotate_coords(L_actual, y_dim_tourillons)
                        fig.add_shape(type="line", x0=x_last_tour_rot, y0=y_last_tour_rot, x1=xL_rot, y1=yL_rot, 
                                     line=dict(color="black", width=line_width, dash='solid'))
                        # Tick marks perpendiculaires aux extrémités
                        fig.add_shape(type="line", x0=x_last_tour_rot, y0=y_last_tour_rot-tick_len, x1=x_last_tour_rot, y1=y_last_tour_rot+tick_len,
                                     line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=xL_rot, y0=yL_rot-tick_len, x1=xL_rot, y1=yL_rot+tick_len,
                                     line=dict(color="black", width=1.2, dash='solid'))
                        x_mid_rot, y_mid_rot = rotate_coords((x_last_tour + L_actual)/2, y_dim_tourillons)
                        fig.add_annotation(x=x_mid_rot, y=y_mid_rot + 15, text=format_number_no_decimal(ecart_droit), 
                                         showarrow=False, font=dict(color="black", size=11, family="Arial"), 
                                         bgcolor="white", yanchor='middle', xanchor='center')
                    else:
                        fig.add_shape(type="line", x0=x_last_tour, y0=y_dim_tourillons, x1=L_actual, y1=y_dim_tourillons, 
                                     line=dict(color="black", width=line_width, dash='solid'))
                        # Tick marks perpendiculaires aux extrémités
                        fig.add_shape(type="line", x0=x_last_tour, y0=y_dim_tourillons-tick_len, x1=x_last_tour, y1=y_dim_tourillons+tick_len,
                                     line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=L_actual, y0=y_dim_tourillons-tick_len, x1=L_actual, y1=y_dim_tourillons+tick_len,
                                     line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_annotation(x=(x_last_tour + L_actual)/2, y=y_dim_tourillons + 15, text=format_number_no_decimal(ecart_droit), 
                                         showarrow=False, font=dict(color="black", size=11, family="Arial"), 
                                         bgcolor="white", yanchor='middle', xanchor='center')
                    
                    # Traits pointillés depuis chaque tourillon vers la ligne de cotation + écarts entre tourillons
                    for i in range(len(x_tourillons)):
                        x_tour = x_tourillons[i]
                        # Trait pointillé depuis le trou vers la ligne
                        if needs_rotation:
                            x_tour_rot, y0_rot = rotate_coords(x_tour, 0)
                            x_tour_dim_rot, y_dim_rot = rotate_coords(x_tour, y_dim_tourillons)
                            fig.add_shape(type="line", x0=x_tour_rot, y0=y0_rot, x1=x_tour_dim_rot, y1=y_dim_rot, 
                                         line=dict(color="black", width=line_width, dash='dot'))
                            fig.add_shape(type="line", x0=x_tour_dim_rot-tick_len, y0=y_dim_rot, x1=x_tour_dim_rot+tick_len, y1=y_dim_rot, 
                                         line=dict(color="black", width=1.2, dash='solid'))
                        else:
                            fig.add_shape(type="line", x0=x_tour, y0=0, x1=x_tour, y1=y_dim_tourillons, 
                                         line=dict(color="black", width=line_width, dash='dot'))
                            fig.add_shape(type="line", x0=x_tour-tick_len, y0=y_dim_tourillons, x1=x_tour+tick_len, y1=y_dim_tourillons, 
                                         line=dict(color="black", width=1.2, dash='solid'))
                        
                        # Écart entre ce tourillon et le suivant
                        if i < len(x_tourillons) - 1:
                            x_next_tour = x_tourillons[i + 1]
                            ecart_entre = x_next_tour - x_tour
                            x_mid = (x_tour + x_next_tour) / 2
                            
                            if needs_rotation:
                                x_tour_dim_rot, y_dim_rot = rotate_coords(x_tour, y_dim_tourillons)
                                x_next_dim_rot, y_next_dim_rot = rotate_coords(x_next_tour, y_dim_tourillons)
                                fig.add_shape(type="line", x0=x_tour_dim_rot, y0=y_dim_rot, x1=x_next_dim_rot, y1=y_next_dim_rot, 
                                             line=dict(color="black", width=line_width, dash='solid'))
                                # Tick marks perpendiculaires pour ce segment
                                fig.add_shape(type="line", x0=x_tour_dim_rot, y0=y_dim_rot-tick_len, x1=x_tour_dim_rot, y1=y_dim_rot+tick_len,
                                             line=dict(color="black", width=1.2, dash='solid'))
                                fig.add_shape(type="line", x0=x_next_dim_rot, y0=y_next_dim_rot-tick_len, x1=x_next_dim_rot, y1=y_next_dim_rot+tick_len,
                                             line=dict(color="black", width=1.2, dash='solid'))
                                x_mid_rot, y_mid_rot = rotate_coords(x_mid, y_dim_tourillons)
                                fig.add_annotation(x=x_mid_rot, y=y_mid_rot + 15, text=format_number_no_decimal(ecart_entre), 
                                                 showarrow=False, font=dict(color="black", size=11, family="Arial"), 
                                                 bgcolor="white", yanchor='middle', xanchor='center')
                            else:
                                fig.add_shape(type="line", x0=x_tour, y0=y_dim_tourillons, x1=x_next_tour, y1=y_dim_tourillons, 
                                             line=dict(color="black", width=line_width, dash='solid'))
                                # Tick marks perpendiculaires pour ce segment
                                fig.add_shape(type="line", x0=x_tour, y0=y_dim_tourillons-tick_len, x1=x_tour, y1=y_dim_tourillons+tick_len,
                                             line=dict(color="black", width=1.2, dash='solid'))
                                fig.add_shape(type="line", x0=x_next_tour, y0=y_dim_tourillons-tick_len, x1=x_next_tour, y1=y_dim_tourillons+tick_len,
                                             line=dict(color="black", width=1.2, dash='solid'))
                                fig.add_annotation(x=x_mid, y=y_dim_tourillons + 15, text=format_number_no_decimal(ecart_entre), 
                                                 showarrow=False, font=dict(color="black", size=11, family="Arial"), 
                                                 bgcolor="white", yanchor='middle', xanchor='center')


        # --- ZONES SPÉCIFIQUES POUR LES PORTES ---
        # Zone jaune : coordonnées Y des VIS
        # Zone bleue : coordonnées Y des TOURILLONS
        # Zone rouge (saumon) : coordonnées X des vis et tourillons
        if "Porte" in panel_name and face_holes_list and panel_bounds:
            # Bords du panneau (repère déjà pivoté si besoin)
            panel_x_min, panel_x_max = panel_bounds['x']
            panel_y_min, panel_y_max = panel_bounds['y']
            
            # Calculer rect_thickness et offset pour les portes
            # TRIPLER la largeur des rectangles pour permettre la cascade sur 2 lignes
            panel_width_x = panel_x_max - panel_x_min
            panel_height_y = panel_y_max - panel_y_min
            base_rect_thickness = max(20.0, min(panel_width_x, panel_height_y) * 0.06)
            rect_thickness = base_rect_thickness * 3  # TRIPLÉ pour les portes
            offset = base_rect_thickness * 0.8
            
            # Zone jaune AU-DESSUS : coordonnées Y des VIS (une seule ligne, pas de cascade)
            yellow_y0 = panel_y_max + offset
            yellow_y1 = panel_y_max + offset + rect_thickness
            yellow_y_center = (yellow_y0 + yellow_y1) * 0.5
            
            line_spacing = 50.0  # pour zones bleue et rouge
            # Zone bleue EN DESSOUS : coordonnées Y des TOURILLONS
            blue_y0 = panel_y_min - offset - rect_thickness
            blue_y1 = panel_y_min - offset
            blue_y_center = (blue_y0 + blue_y1) * 0.5
            blue_line1 = blue_y_center - line_spacing / 2
            blue_line2 = blue_y_center + line_spacing / 2
            
            # Zone rouge (saumon) À DROITE : coordonnées X des vis et tourillons
            red_x0 = panel_x_max + offset
            red_x1 = panel_x_max + offset + rect_thickness
            red_x_center = (red_x0 + red_x1) * 0.5
            red_line1 = red_x_center - line_spacing / 2
            red_line2 = red_x_center + line_spacing / 2
            
            # Séparer les trous par type
            vis_holes = []
            tourillon_holes = []
            for h in face_holes_list:
                h_type = h.get('type', '')
                if h_type == 'vis':
                    vis_holes.append(h)
                elif h_type == 'tourillon':
                    tourillon_holes.append(h)
            
            # Zone jaune : coordonnées Y des VIS (un lot = 2 vis + gros trou par charnière)
            # Pour chaque charnière : cote Y de la 1re vis dans la zone jaune (à l'endroit habituel)
            # + une cote À L'INTÉRIEUR du panneau = distance entre la 1re et la 2e vis du lot
            vis_by_x = {}
            for h in vis_holes:
                x_key = round(float(h['x']), 1)
                vis_by_x.setdefault(x_key, []).append(h)
            tick_len = 4
            for x_key in sorted(vis_by_x.keys()):
                group = sorted(vis_by_x[x_key], key=lambda h: float(h['y']))
                group = [h for h in group if round(float(h['y'])) not in (24, 10)]
                if not group:
                    continue
                x_hole = float(group[0]['x'])
                # Traiter toutes les charnières : par paire (2 vis) ou vis seule en fin de liste
                idx = 0
                while idx < len(group):
                    if idx + 1 < len(group):
                        # Paire de vis (une charnière)
                        h1, h2 = group[idx], group[idx + 1]
                        y1, y2 = float(h1['y']), float(h2['y'])
                        y1_int = round(y1)
                        dist_int = round(abs(y2 - y1))
                        if needs_rotation:
                            x_pos, y1_fig = rotate_coords(x_hole, y1)
                            _, y2_fig = rotate_coords(x_hole, y2)
                        else:
                            x_pos, y1_fig = x_hole, y1
                            y2_fig = y2
                        # Lignes verticales pointillées des deux trous jusqu'à la zone jaune
                        for y_fig in (y1_fig, y2_fig):
                            fig.add_shape(
                                type="line",
                                x0=x_pos, y0=y_fig,
                                x1=x_pos, y1=yellow_y1,
                                line=dict(color="rgba(180,180,180,1.0)", width=0.6, dash="dot")
                            )
                            fig.add_shape(
                                type="line",
                                x0=x_pos - tick_len, y0=y_fig,
                                x1=x_pos + tick_len, y1=y_fig,
                                line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                            )
                        fig.add_shape(
                            type="line",
                            x0=x_pos - tick_len, y0=yellow_y1,
                            x1=x_pos + tick_len, y1=yellow_y1,
                            line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                        )
                        # Zone jaune : coordonnée Y de la 1re vis de cette charnière (une seule ligne)
                        fig.add_annotation(
                            x=x_pos, y=yellow_y_center,
                            text=format_number_no_decimal(y1_int),
                            showarrow=False,
                            font=dict(size=8, color="black"),
                            bgcolor="rgba(255,255,255,0.85)",
                            xanchor="center", yanchor="middle"
                        )
                        # Cote au plus près des deux trous : distance 1re→2e vis (écart 5× plus grand)
                        offset_cote = 50.0  # 5 fois la distance précédente (10 mm) entre la cote et les trous
                        x_dim_panel = x_hole + offset_cote
                        y_mid_panel = (y1 + y2) / 2
                        tick_cote = 3
                        if needs_rotation:
                            x1_fig, y1_dim_fig = rotate_coords(x_dim_panel, y1)
                            x2_fig, y2_dim_fig = rotate_coords(x_dim_panel, y2)
                            tx1, ty1 = rotate_coords(x_dim_panel - tick_cote, y1)
                            tx2, ty2 = rotate_coords(x_dim_panel + tick_cote, y1)
                            tx3, ty3 = rotate_coords(x_dim_panel - tick_cote, y2)
                            tx4, ty4 = rotate_coords(x_dim_panel + tick_cote, y2)
                            x_text_fig, y_mid_fig = rotate_coords(x_dim_panel + 60, y_mid_panel)  # écart chiffre / trait de cote (double de 30)
                        else:
                            x1_fig = x2_fig = x_dim_panel
                            y1_dim_fig, y2_dim_fig = y1, y2
                            y_mid_fig = y_mid_panel
                            x_text_fig = x_dim_panel + 60  # écart chiffre / trait de cote (double de 30)
                            tx1, ty1 = x_dim_panel - tick_cote, y1
                            tx2, ty2 = x_dim_panel + tick_cote, y1
                            tx3, ty3 = x_dim_panel - tick_cote, y2
                            tx4, ty4 = x_dim_panel + tick_cote, y2
                        fig.add_shape(
                            type="line",
                            x0=x1_fig, y0=y1_dim_fig,
                            x1=x2_fig, y1=y2_dim_fig,
                            line=dict(color="black", width=0.6, dash="dot")
                        )
                        fig.add_shape(
                            type="line",
                            x0=tx1, y0=ty1, x1=tx2, y1=ty2,
                            line=dict(color="black", width=1.0)
                        )
                        fig.add_shape(
                            type="line",
                            x0=tx3, y0=ty3, x1=tx4, y1=ty4,
                            line=dict(color="black", width=1.0)
                        )
                        fig.add_annotation(
                            x=x_text_fig,
                            y=y_mid_fig,
                            text=format_number_no_decimal(dist_int),
                            showarrow=False,
                            font=dict(size=8, color="black"),
                            bgcolor="rgba(255,255,255,0.9)",
                            xanchor="left" if not needs_rotation else "center",
                            yanchor="middle"
                        )
                        idx += 2
                    else:
                        # Une seule vis (orpheline ou dernière)
                        h = group[idx]
                        y_hole = float(h['y'])
                        y_val_int = round(y_hole)
                        if needs_rotation:
                            x_pos, y_pos_fig = rotate_coords(x_hole, y_hole)
                        else:
                            x_pos, y_pos_fig = x_hole, y_hole
                        main_y_bottom = y_pos_fig
                        main_y_top = yellow_y1
                        fig.add_shape(
                            type="line",
                            x0=x_pos, y0=main_y_bottom,
                            x1=x_pos, y1=main_y_top,
                            line=dict(color="rgba(180,180,180,1.0)", width=0.6, dash="dot")
                        )
                        fig.add_shape(
                            type="line",
                            x0=x_pos - tick_len, y0=main_y_bottom,
                            x1=x_pos + tick_len, y1=main_y_bottom,
                            line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                        )
                        fig.add_shape(
                            type="line",
                            x0=x_pos - tick_len, y0=main_y_top,
                            x1=x_pos + tick_len, y1=main_y_top,
                            line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                        )
                        fig.add_annotation(
                            x=x_pos, y=yellow_y_center,
                            text=format_number_no_decimal(y_val_int),
                            showarrow=False,
                            font=dict(size=8, color="black"),
                            bgcolor="rgba(255,255,255,0.85)",
                            xanchor="center", yanchor="middle"
                        )
                        idx += 1
            
            # Zone bleue : coordonnées Y des TOURILLONS
            # UNE SEULE LIGNE (pas de cascade)
            for h in tourillon_holes:
                x_hole, y_hole = h['x'], h['y']
                y_row = round(y_hole, 1)
                
                # Passer dans le repère final de la figure si rotation
                if needs_rotation:
                    x_pos, y_pos_fig = rotate_coords(x_hole, y_hole)
                else:
                    x_pos, y_pos_fig = x_hole, y_hole
                
                # Ligne de cotation verticale : depuis le trou jusqu'à la zone bleue (en bas)
                # Traits gris pointillés identiques aux montants
                main_y_top = y_pos_fig
                main_y_bottom = blue_y0
                fig.add_shape(
                    type="line",
                    x0=x_pos, y0=main_y_top,
                    x1=x_pos, y1=main_y_bottom,
                    line=dict(color="rgba(180,180,180,1.0)", width=0.6, dash="dot")
                )
                # Ticks au niveau du trou et à l'entrée de la zone bleue (en gris)
                tick_len = 4
                fig.add_shape(
                    type="line",
                    x0=x_pos - tick_len, y0=main_y_top,
                    x1=x_pos + tick_len, y1=main_y_top,
                    line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                )
                fig.add_shape(
                    type="line",
                    x0=x_pos - tick_len, y0=main_y_bottom,
                    x1=x_pos + tick_len, y1=main_y_bottom,
                    line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                )
                
                # Texte sur une seule ligne (pas de cascade)
                y_val = round(y_row)  # Arrondir à l'entier
                y_val_str = format_number_no_decimal(y_val)
                
                fig.add_annotation(
                    x=x_pos,
                    y=blue_y_center,
                    text=f"{y_val_str}",
                    showarrow=False,
                    font=dict(size=8, color="black"),
                    bgcolor="rgba(255,255,255,0.85)",
                    xanchor="center",
                    yanchor="middle"
                )
            
            # Zone rouge (saumon) : coordonnées X des vis ET tourillons
            # Marquer CHAQUE ESPACE entre trous consécutifs (l'alternance vis/tourillon)
            # sur UNE SEULE LIGNE de cotes
            all_holes = vis_holes + tourillon_holes
            
            # Trier tous les trous par position X pour obtenir l'ordre de succession
            all_holes_sorted = sorted(all_holes, key=lambda h: float(h['x']))
            
            if len(all_holes_sorted) > 1:
                # Obtenir les coordonnées X uniques et triées
                x_positions = sorted(list(set([float(h['x']) for h in all_holes_sorted])))
                
                # Pour chaque paire de trous consécutifs, marquer l'espace entre eux
                for i in range(len(x_positions) - 1):
                    x1 = x_positions[i]
                    x2 = x_positions[i + 1]
                    espacement = x2 - x1
                    
                    # Trouver un trou pour obtenir une coordonnée Y (on va utiliser le Y du premier trou)
                    h_for_y = next((h for h in all_holes_sorted if abs(float(h['x']) - x1) < 0.1), None)
                    if not h_for_y:
                        continue
                    
                    y_hole_raw = float(h_for_y['y'])
                    
                    # Position du trou dans le repère de la figure (avec rotation si besoin)
                    if needs_rotation:
                        x1_fig, y_hole_fig = rotate_coords(x1, y_hole_raw)
                        x2_fig, _ = rotate_coords(x2, y_hole_raw)
                    else:
                        x1_fig = x1
                        x2_fig = x2
                        y_hole_fig = y_hole_raw
                    
                    # Ligne de cote horizontale entre les deux trous
                    fig.add_shape(
                        type="line",
                        x0=x1_fig, y0=y_hole_fig,
                        x1=x2_fig, y1=y_hole_fig,
                        line=dict(color="rgba(180,180,180,1.0)", width=0.6, dash="dot")
                    )
                    
                    # Ticks aux extrémités
                    tick_len = 3
                    fig.add_shape(
                        type="line",
                        x0=x1_fig, y0=y_hole_fig - tick_len,
                        x1=x1_fig, y1=y_hole_fig + tick_len,
                        line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                    )
                    fig.add_shape(
                        type="line",
                        x0=x2_fig, y0=y_hole_fig - tick_len,
                        x1=x2_fig, y1=y_hole_fig + tick_len,
                        line=dict(color="rgba(180,180,180,1.0)", width=1.0)
                    )
                    
                    # Marquer l'espace dans la zone rouge sur UNE SEULE LIGNE
                    espacement_str = format_number_no_decimal(espacement)
                    
                    fig.add_annotation(
                        x=red_x_center,
                        y=y_hole_fig,
                        text=f"{espacement_str}",
                        showarrow=False,
                        font=dict(size=8, color="black"),
                        bgcolor="rgba(255,255,255,0.9)",
                        xanchor="center",
                        yanchor="middle"
                    )

    annotated_types = set()
    existing_labels = [] 
    for h in face_holes_list:
        x, y = h['x'], h['y']
        # Transformer les coordonnées si rotation nécessaire
        if needs_rotation:
            x, y = rotate_coords(x, y)
        diam_str = h.get('diam_str', '⌀8')
        r = 4.0
        try: r = float(re.findall(r"[\d\.]+", diam_str)[0])/2
        except: pass
        fill = "black" if 'vis' in h.get('type', '') else "white"
        fig.add_shape(type="circle", x0=x-r, y0=y-r, x1=x+r, y1=y+r, line_color="black", fillcolor=fill, layer="above")
        type_key = f"{h['type']}_{diam_str}"
        if type_key not in annotated_types:
            ax, ay, final_pos = get_smart_label_pos(x, y, r, existing_labels)
            if not is_fond_sheet:
                fig.add_annotation(x=x, y=y, text=f"<b>{diam_str}</b>", showarrow=True, arrowwidth=1, arrowhead=2, ax=ax, ay=ay, font=dict(size=12, color="black"), bgcolor="white", bordercolor="black")
            annotated_types.add(type_key)
            existing_labels.append(final_pos)

    # --- TROUS DE TRANCHE LONGUE (Haut et Bas) ---
    # Pour les traverses : traits pointillés depuis chaque trou vers la ligne de cotation,
    # chiffres alignés avec les trous, espacements entre trous consécutifs.
    # Pour les montants principaux (Gauche/Droit) : cotations uniquement (pas de trous dessinés, ils sont sur la face)
    # Pour les montants secondaires : dessiner les trous (ils servent à les fixer au panneau arrière)
    # Pour les autres éléments : cote depuis le bord (x=0) jusqu'à chaque trou.
    tranche_diam_cascade_tracker = []
    is_traverse = "traverse" in panel_lower
    is_montant = "Montant" in panel_name
    is_montant_principal = ("Montant Gauche" in panel_name or "Montant Droit" in panel_name)
    # Détection des étagères (y compris groupées) - utiliser la même variable que définie plus haut
    # is_shelf est déjà défini plus haut, mais on le redéfinit ici pour être sûr
    panel_normalized_tranche = panel_lower.replace("é", "e").replace("è", "e").replace("ê", "e")
    is_shelf = "etagere" in panel_normalized_tranche or "étagère" in panel_lower
    
    # Détecter si le montant a des trous de tiroirs (⌀5/12 ou ⌀5/11.5)
    has_drawer_holes = False
    if is_montant and face_holes_list:
        for h in face_holes_list:
            diam = h.get('diam_str', '')
            if '⌀5/12' in diam or '⌀5/11.5' in diam:
                has_drawer_holes = True
                break
    
    if tranche_longue_holes_list:
        x_locs = sorted(list(set([round(h['x'], 1) for h in tranche_longue_holes_list])))
        tick_len = 5
        line_width = 0.8
        
        # Position de la ligne de cotation : pour les montants, à l'extérieur (en bas)
        is_montant_secondaire = is_montant and not is_montant_principal
        
        if is_montant_secondaire:
            # Pour les montants secondaires : ligne courte juste sous les trous
            short_distance = 20
            y_dim = y_th_1 + short_distance
        elif is_montant:
            # Pour les montants principaux : ligne éloignée
            y_dim = -60  # À l'extérieur, en dessous de la face
        else:
            y_dim = y_th_1 + 40  # Position UNIQUE de la ligne de cotation (au-dessus de la tranche)
        
        # Vérifier si c'est l'alternance vis/tourillon sur montant principal
        has_both_vis_and_tourillon = False
        is_alternance_row = False
        alternance_y_pos = None
        
        if is_montant_principal:
            has_vis = any(h.get('type') == 'vis' for h in tranche_longue_holes_list)
            has_tourillon = any(h.get('type') == 'tourillon' for h in tranche_longue_holes_list)
            has_both_vis_and_tourillon = has_vis and has_tourillon
            
            # Trouver la rangée Y qui a l'alternance vis/tourillon
            if has_both_vis_and_tourillon:
                y_positions_with_types = {}
                for h in tranche_longue_holes_list:
                    y_pos = round(h['y'], 1)
                    if y_pos not in y_positions_with_types:
                        y_positions_with_types[y_pos] = {'vis': False, 'tourillon': False}
                    if h.get('type') == 'vis':
                        y_positions_with_types[y_pos]['vis'] = True
                    elif h.get('type') == 'tourillon':
                        y_positions_with_types[y_pos]['tourillon'] = True
                
                # Trouver la rangée qui a À LA FOIS vis et tourillons
                for y_pos, types in y_positions_with_types.items():
                    if types['vis'] and types['tourillon']:
                        is_alternance_row = True
                        alternance_y_pos = y_pos
                        break
        
        # Ne pas créer la cotation X horizontale pour l'alternance si elle est sur montant principal
        # SAUF si c'est la rangée d'alternance : on crée une UNIQUE cotation
        # NE PAS créer ANY cotations si montant principal avec trous de tiroirs
        if (is_traverse or is_shelf or not is_montant_principal) and len(x_locs) > 1 and not (is_montant_principal and has_drawer_holes):
            # POUR LES TRAVERSES, ÉTAGÈRES ET MONTANTS : mesurer l'écart entre chaque trou consécutif + écarts avec les bords
            # Trait pointillé depuis le bord gauche vers la ligne de cotation
            if needs_rotation:
                x0_rot, y_th_1_rot = rotate_coords(0, y_th_1)
                x0_dim_rot, y_dim_rot0 = rotate_coords(0, y_dim)
                fig.add_shape(type="line", x0=x0_rot, y0=y_th_1_rot, x1=x0_dim_rot, y1=y_dim_rot0, line=dict(color="black", width=line_width, dash='dot'))
                fig.add_shape(type="line", x0=x0_dim_rot-tick_len, y0=y_dim_rot0, x1=x0_dim_rot+tick_len, y1=y_dim_rot0, line=dict(color="black", width=1.2, dash='solid'))
            else:
                fig.add_shape(type="line", x0=0, y0=y_th_1, x1=0, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                fig.add_shape(type="line", x0=0-tick_len, y0=y_dim, x1=0+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
            
            # Trait pointillé depuis le bord droit vers la ligne de cotation
            if needs_rotation:
                x_L_rot, y_th_1_rot = rotate_coords(L_actual, y_th_1)
                x_L_dim_rot, y_dim_rot_L = rotate_coords(L_actual, y_dim)
                fig.add_shape(type="line", x0=x_L_rot, y0=y_th_1_rot, x1=x_L_dim_rot, y1=y_dim_rot_L, line=dict(color="black", width=line_width, dash='dot'))
                fig.add_shape(type="line", x0=x_L_dim_rot-tick_len, y0=y_dim_rot_L, x1=x_L_dim_rot+tick_len, y1=y_dim_rot_L, line=dict(color="black", width=1.2, dash='solid'))
            else:
                fig.add_shape(type="line", x0=L_actual, y0=y_th_1, x1=L_actual, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                fig.add_shape(type="line", x0=L_actual-tick_len, y0=y_dim, x1=L_actual+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
            
            # Cotation : écart entre le bord gauche et le premier trou (trait plein)
            x_first = x_locs[0]
            ecart_bord_gauche = x_first - 0
            add_pro_dimension(fig, 0, y_dim, x_first, y_dim, format_number_no_decimal(ecart_bord_gauche), 15,
                            axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                            cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
            
            # Cotation : écart entre le dernier trou et le bord droit (trait plein)
            x_last = x_locs[-1]
            ecart_bord_droit = L_actual - x_last
            add_pro_dimension(fig, x_last, y_dim, L_actual, y_dim, format_number_no_decimal(ecart_bord_droit), 15,
                            axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                            cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
            
            # Mesurer l'écart entre chaque trou consécutif
            for i in range(len(x_locs)):
                x_pos = x_locs[i]
                # Trait pointillé depuis chaque trou vers la ligne de cotation
                
                if is_montant_secondaire:
                    # Pour les montants secondaires : trait court juste sous les trous (ne traverse pas la figure)
                    if needs_rotation:
                        x_pos_rot, y_th_1_rot = rotate_coords(x_pos, y_th_1)
                        x_pos_dim_rot, y_dim_rot = rotate_coords(x_pos, y_dim)
                        # Trait pointillé court depuis le trou
                        fig.add_shape(type="line", x0=x_pos_rot, y0=y_th_1_rot, x1=x_pos_dim_rot, y1=y_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne courte
                        fig.add_shape(type="line", x0=x_pos_dim_rot-tick_len, y0=y_dim_rot, x1=x_pos_dim_rot+tick_len, y1=y_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    else:
                        # Trait pointillé court depuis le trou
                        fig.add_shape(type="line", x0=x_pos, y0=y_th_1, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne courte
                        fig.add_shape(type="line", x0=x_pos-tick_len, y0=y_dim, x1=x_pos+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
                elif not is_montant_principal:
                    # Pour les traverses et étagères : trait pointillé complet
                    if needs_rotation:
                        x_pos_rot, y_th_1_rot = rotate_coords(x_pos, y_th_1)
                        x_pos_dim_rot, y_dim_rot = rotate_coords(x_pos, y_dim)
                        # Trait pointillé depuis le trou vers la ligne de cotation horizontale
                        fig.add_shape(type="line", x0=x_pos_rot, y0=y_th_1_rot, x1=x_pos_dim_rot, y1=y_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne de cotation au niveau du trou
                        fig.add_shape(type="line", x0=x_pos_dim_rot-tick_len, y0=y_dim_rot, x1=x_pos_dim_rot+tick_len, y1=y_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    else:
                        # Trait pointillé depuis le trou vers la ligne de cotation horizontale
                        fig.add_shape(type="line", x0=x_pos, y0=y_th_1, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne de cotation au niveau du trou
                        fig.add_shape(type="line", x0=x_pos-tick_len, y0=y_dim, x1=x_pos+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
                
                # Mesurer l'écart entre ce trou et le suivant
                if i < len(x_locs) - 1:
                    x_next = x_locs[i + 1]
                    ecart = x_next - x_pos
                    add_pro_dimension(fig, x_pos, y_dim, x_next, y_dim, format_number_no_decimal(ecart), 15,
                                    axis='x', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
        elif is_traverse or is_shelf or is_montant:
            # Cas avec un seul trou : mesurer depuis le bord
            x_pos = x_locs[0]
            if needs_rotation:
                x_pos_rot, y_th_1_rot = rotate_coords(x_pos, y_th_1)
                x_pos_dim_rot, y_dim_rot = rotate_coords(x_pos, y_dim)
                x0_dim_rot, y_dim_rot0 = rotate_coords(0, y_dim)
                # Trait pointillé depuis le trou vers la ligne de cotation horizontale
                fig.add_shape(type="line", x0=x_pos_rot, y0=y_th_1_rot, x1=x_pos_dim_rot, y1=y_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                # Ligne de cotation depuis le bord jusqu'au trou
                fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot0, x1=x_pos_dim_rot, y1=y_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                # Ticks
                fig.add_shape(type="line", x0=x0_dim_rot-tick_len, y0=y_dim_rot0, x1=x0_dim_rot+tick_len, y1=y_dim_rot0, line=dict(color="black", width=1.2, dash='solid'))
                fig.add_shape(type="line", x0=x_pos_dim_rot-tick_len, y0=y_dim_rot, x1=x_pos_dim_rot+tick_len, y1=y_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                # Texte
                mid_x_rot, mid_y_rot = rotate_coords(x_pos/2, y_dim)
                fig.add_annotation(x=mid_x_rot, y=mid_y_rot + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
            else:
                # Trait pointillé depuis le trou vers la ligne de cotation horizontale
                fig.add_shape(type="line", x0=x_pos, y0=y_th_1, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                # Ligne de cotation depuis le bord jusqu'au trou
                fig.add_shape(type="line", x0=0, y0=y_dim, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                # Ticks
                fig.add_shape(type="line", x0=0-tick_len, y0=y_dim, x1=0+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
                fig.add_shape(type="line", x0=x_pos-tick_len, y0=y_dim, x1=x_pos+tick_len, y1=y_dim, line=dict(color="black", width=1.2, dash='solid'))
                # Texte
                fig.add_annotation(x=x_pos/2, y=y_dim + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
        elif not is_shelf:
            # POUR LES AUTRES ÉLÉMENTS (mais pas les étagères) : cote depuis le bord gauche (x=0) jusqu'à ce trou
            for x_pos in x_locs:
                if needs_rotation:
                    x0_rot, y_th_1_rot = rotate_coords(0, y_th_1)
                    x_pos_rot, y_th_1_rot2 = rotate_coords(x_pos, y_th_1)
                    x0_dim_rot, y_dim_rot = rotate_coords(0, y_dim)
                    x_pos_dim_rot, y_dim_rot2 = rotate_coords(x_pos, y_dim)
                    # Lignes courtes depuis les trous vers l'extérieur
                    ext_y_pos = y_th_1_rot2 + max_extension_length
                    x_pos_ext_rot, y_ext_pos_rot = rotate_coords(x_pos, ext_y_pos)
                    fig.add_shape(type="line", x0=x_pos_rot, y0=y_th_1_rot2, x1=x_pos_ext_rot, y1=y_ext_pos_rot, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation horizontale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot, x1=x_pos_dim_rot, y1=y_dim_rot2, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks aux extrémités
                    fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot-tick_len, x1=x0_dim_rot, y1=y_dim_rot+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_pos_dim_rot, y0=y_dim_rot2-tick_len, x1=x_pos_dim_rot, y1=y_dim_rot2+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne horizontale (pas de cascade)
                    mid_x_rot, mid_y_rot = rotate_coords(x_pos/2, y_dim)
                    fig.add_annotation(x=mid_x_rot, y=mid_y_rot + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    # Ligne courte depuis le trou vers l'extérieur
                    ext_y = y_th_1 + max_extension_length
                    fig.add_shape(type="line", x0=x_pos, y0=y_th_1, x1=x_pos, y1=ext_y, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation horizontale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=0, y0=y_dim, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks verticaux aux extrémités
                    fig.add_shape(type="line", x0=0, y0=y_dim-tick_len, x1=0, y1=y_dim+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_pos, y0=y_dim-tick_len, x1=x_pos, y1=y_dim+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne horizontale (pas de cascade)
                    fig.add_annotation(x=x_pos/2, y=y_dim + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
            else:
                # POUR LES AUTRES ÉLÉMENTS : cote depuis le bord gauche (x=0) jusqu'à ce trou
                if needs_rotation:
                    x0_rot, y_th_1_rot = rotate_coords(0, y_th_1)
                    x_pos_rot, y_th_1_rot2 = rotate_coords(x_pos, y_th_1)
                    x0_dim_rot, y_dim_rot = rotate_coords(0, y_dim)
                    x_pos_dim_rot, y_dim_rot2 = rotate_coords(x_pos, y_dim)
                    # Lignes courtes depuis les trous vers l'extérieur
                    ext_y_pos = y_th_1_rot2 + max_extension_length
                    x_pos_ext_rot, y_ext_pos_rot = rotate_coords(x_pos, ext_y_pos)
                    fig.add_shape(type="line", x0=x_pos_rot, y0=y_th_1_rot2, x1=x_pos_ext_rot, y1=y_ext_pos_rot, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation horizontale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot, x1=x_pos_dim_rot, y1=y_dim_rot2, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks aux extrémités
                    fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot-tick_len, x1=x0_dim_rot, y1=y_dim_rot+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_pos_dim_rot, y0=y_dim_rot2-tick_len, x1=x_pos_dim_rot, y1=y_dim_rot2+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne horizontale (pas de cascade)
                    mid_x_rot, mid_y_rot = rotate_coords(x_pos/2, y_dim)
                    fig.add_annotation(x=mid_x_rot, y=mid_y_rot + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    # Ligne courte depuis le trou vers l'extérieur
                    ext_y = y_th_1 + max_extension_length
                    fig.add_shape(type="line", x0=x_pos, y0=y_th_1, x1=x_pos, y1=ext_y, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation horizontale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=0, y0=y_dim, x1=x_pos, y1=y_dim, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks verticaux aux extrémités
                    fig.add_shape(type="line", x0=0, y0=y_dim-tick_len, x1=0, y1=y_dim+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_pos, y0=y_dim-tick_len, x1=x_pos, y1=y_dim+tick_len, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne horizontale (pas de cascade)
                    fig.add_annotation(x=x_pos/2, y=y_dim + 15, text=format_number_no_decimal(x_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
        
        # COTATIONS POUR L'ALTERNANCE VIS/TOURILLON SUR MONTANTS PRINCIPAUX
        # Désactivé : la section normale gère maintenant tous les montants principaux
        if False:  # was: is_montant_principal and is_alternance_row and alternance_y_pos is not None and len(x_locs) > 1 and not has_drawer_holes:
            # Filtrer les trous de la rangée d'alternance
            alternance_holes = [h for h in tranche_longue_holes_list if round(h['y'], 1) == alternance_y_pos]
            alternance_holes_sorted = sorted(alternance_holes, key=lambda h: h['x'])
            
            if len(alternance_holes_sorted) > 1:
                y_dim_alternance = -60  # À l'extérieur, en dessous de la face
                
                # Trait pointillé depuis le bord gauche vers la ligne de cotation
                if needs_rotation:
                    x0_rot, y_th_1_rot = rotate_coords(0, y_th_1)
                    x0_dim_rot, y_dim_rot0 = rotate_coords(0, y_dim_alternance)
                    fig.add_shape(type="line", x0=x0_rot, y0=y_th_1_rot, x1=x0_dim_rot, y1=y_dim_rot0, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x0_dim_rot-tick_len, y0=y_dim_rot0, x1=x0_dim_rot+tick_len, y1=y_dim_rot0, line=dict(color="black", width=1.2, dash='solid'))
                else:
                    fig.add_shape(type="line", x0=0, y0=y_th_1, x1=0, y1=y_dim_alternance, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=0-tick_len, y0=y_dim_alternance, x1=0+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                
                # Trait pointillé depuis le bord droit vers la ligne de cotation
                if needs_rotation:
                    x_L_rot, y_th_1_rot = rotate_coords(L_actual, y_th_1)
                    x_L_dim_rot, y_dim_rot_L = rotate_coords(L_actual, y_dim_alternance)
                    fig.add_shape(type="line", x0=x_L_rot, y0=y_th_1_rot, x1=x_L_dim_rot, y1=y_dim_rot_L, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x_L_dim_rot-tick_len, y0=y_dim_rot_L, x1=x_L_dim_rot+tick_len, y1=y_dim_rot_L, line=dict(color="black", width=1.2, dash='solid'))
                else:
                    fig.add_shape(type="line", x0=L_actual, y0=y_th_1, x1=L_actual, y1=y_dim_alternance, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=L_actual-tick_len, y0=y_dim_alternance, x1=L_actual+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                
                # Cotation : écart entre le bord gauche et le premier trou
                x_first = alternance_holes_sorted[0]['x']
                ecart_bord_gauche = x_first - 0
                if needs_rotation:
                    x_first_dim_rot, y_first_dim_rot = rotate_coords(x_first, y_dim_alternance)
                    x0_dim_rot, y_dim_rot0 = rotate_coords(0, y_dim_alternance)
                    fig.add_shape(type="line", x0=x0_dim_rot, y0=y_dim_rot0, x1=x_first_dim_rot, y1=y_first_dim_rot, line=dict(color="black", width=line_width, dash='solid'))
                    fig.add_shape(type="line", x0=x0_dim_rot-tick_len, y0=y_dim_rot0, x1=x0_dim_rot+tick_len, y1=y_dim_rot0, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_first_dim_rot-tick_len, y0=y_first_dim_rot, x1=x_first_dim_rot+tick_len, y1=y_first_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    x_mid_rot, y_mid_rot = rotate_coords(x_first/2, y_dim_alternance)
                    fig.add_annotation(x=x_mid_rot, y=y_mid_rot + 15, text=format_number_no_decimal(ecart_bord_gauche), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    fig.add_shape(type="line", x0=0, y0=y_dim_alternance, x1=x_first, y1=y_dim_alternance, line=dict(color="black", width=line_width, dash='solid'))
                    fig.add_shape(type="line", x0=0-tick_len, y0=y_dim_alternance, x1=0+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_first-tick_len, y0=y_dim_alternance, x1=x_first+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_annotation(x=x_first/2, y=y_dim_alternance + 15, text=format_number_no_decimal(ecart_bord_gauche), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                
                # Cotations entre chaque paire de trous consécutifs (tous les espacements)
                for i in range(len(alternance_holes_sorted) - 1):
                    x_curr = alternance_holes_sorted[i]['x']
                    x_next = alternance_holes_sorted[i + 1]['x']
                    
                    # Afficher tous les espacements entre chaque trou consécutif
                    should_show_spacing = True
                    
                    ecart = x_next - x_curr
                    x_mid = (x_curr + x_next) / 2
                    
                    if needs_rotation:
                        x_curr_dim_rot, y_curr_dim_rot = rotate_coords(x_curr, y_dim_alternance)
                        x_next_dim_rot, y_next_dim_rot = rotate_coords(x_next, y_dim_alternance)
                        fig.add_shape(type="line", x0=x_curr_dim_rot, y0=y_curr_dim_rot, x1=x_next_dim_rot, y1=y_next_dim_rot, line=dict(color="black", width=line_width, dash='solid'))
                        fig.add_shape(type="line", x0=x_curr_dim_rot-tick_len, y0=y_curr_dim_rot, x1=x_curr_dim_rot+tick_len, y1=y_curr_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=x_next_dim_rot-tick_len, y0=y_next_dim_rot, x1=x_next_dim_rot+tick_len, y1=y_next_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                        if should_show_spacing:
                            x_mid_rot, y_mid_rot = rotate_coords(x_mid, y_dim_alternance)
                            fig.add_annotation(x=x_mid_rot, y=y_mid_rot + 15, text=format_number_no_decimal(ecart), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                    else:
                        fig.add_shape(type="line", x0=x_curr, y0=y_dim_alternance, x1=x_next, y1=y_dim_alternance, line=dict(color="black", width=line_width, dash='solid'))
                        fig.add_shape(type="line", x0=x_curr-tick_len, y0=y_dim_alternance, x1=x_curr+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=x_next-tick_len, y0=y_dim_alternance, x1=x_next+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                        if should_show_spacing:
                            fig.add_annotation(x=x_mid, y=y_dim_alternance + 15, text=format_number_no_decimal(ecart), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                
                # Cotation : écart entre le dernier trou et le bord droit
                x_last = alternance_holes_sorted[-1]['x']
                ecart_bord_droit = L_actual - x_last
                if needs_rotation:
                    x_last_dim_rot, y_last_dim_rot = rotate_coords(x_last, y_dim_alternance)
                    x_L_dim_rot, y_L_dim_rot = rotate_coords(L_actual, y_dim_alternance)
                    fig.add_shape(type="line", x0=x_last_dim_rot, y0=y_last_dim_rot, x1=x_L_dim_rot, y1=y_L_dim_rot, line=dict(color="black", width=line_width, dash='solid'))
                    fig.add_shape(type="line", x0=x_last_dim_rot-tick_len, y0=y_last_dim_rot, x1=x_last_dim_rot+tick_len, y1=y_last_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_L_dim_rot-tick_len, y0=y_L_dim_rot, x1=x_L_dim_rot+tick_len, y1=y_L_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    x_mid_rot, y_mid_rot = rotate_coords((x_last + L_actual)/2, y_dim_alternance)
                    fig.add_annotation(x=x_mid_rot, y=y_mid_rot + 15, text=format_number_no_decimal(ecart_bord_droit), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    fig.add_shape(type="line", x0=x_last, y0=y_dim_alternance, x1=L_actual, y1=y_dim_alternance, line=dict(color="black", width=line_width, dash='solid'))
                    fig.add_shape(type="line", x0=x_last-tick_len, y0=y_dim_alternance, x1=x_last+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=L_actual-tick_len, y0=y_dim_alternance, x1=L_actual+tick_len, y1=y_dim_alternance, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_annotation(x=(x_last + L_actual)/2, y=y_dim_alternance + 15, text=format_number_no_decimal(ecart_bord_droit), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
        
        annotated_tranche_longue_types = set()
        
        # Ajouter les cotations sur l'épaisseur des tranches pour les trous
        # Cotation à la moitié de l'épaisseur qui pointe le centre des trous
        # NE PAS DESSINER LES TROUS POUR LES MONTANTS (ils sont sur la face, pas sur les tranches)
        y_tb_center_epaisseur = (y_tb_0 + y_tb_1) / 2  # Moitié de l'épaisseur tranche bas
        y_th_center_epaisseur = (y_th_0 + y_th_1) / 2  # Moitié de l'épaisseur tranche haut
        
        # Dessiner d'abord tous les trous (SAUF pour les montants principaux)
        if not is_montant_principal:
            for h in tranche_longue_holes_list:
                x = h['x']
                # Position Y pour les tranches haut et bas
                y_tb_center = (y_tb_0 + y_tb_1) / 2  # Centre de la tranche bas
                y_th_center = (y_th_0 + y_th_1) / 2  # Centre de la tranche haut
                
                # Cotation sur l'épaisseur : ligne pointillée depuis le centre du trou jusqu'à la moitié de l'épaisseur
                # Pour la tranche bas
                if needs_rotation:
                    x_tb_rot, y_tb_center_rot = rotate_coords(x, y_tb_center)
                    x_tb_epaisseur_rot, y_tb_epaisseur_rot = rotate_coords(x, y_tb_center_epaisseur)
                else:
                    x_tb_rot, y_tb_center_rot = x, y_tb_center
                    x_tb_epaisseur_rot, y_tb_epaisseur_rot = x, y_tb_center_epaisseur
                
                # Ligne pointillée depuis le centre du trou jusqu'à la moitié de l'épaisseur
                fig.add_shape(type="line", x0=x_tb_rot, y0=y_tb_center_rot, x1=x_tb_epaisseur_rot, y1=y_tb_epaisseur_rot, line=dict(color="black", width=0.5, dash='dot'))
                
                # Pour la tranche haut
                if needs_rotation:
                    x_th_rot, y_th_center_rot = rotate_coords(x, y_th_center)
                    x_th_epaisseur_rot, y_th_epaisseur_rot = rotate_coords(x, y_th_center_epaisseur)
                else:
                    x_th_rot, y_th_center_rot = x, y_th_center
                    x_th_epaisseur_rot, y_th_epaisseur_rot = x, y_th_center_epaisseur
                
                # Ligne pointillée depuis le centre du trou jusqu'à la moitié de l'épaisseur
                fig.add_shape(type="line", x0=x_th_rot, y0=y_th_center_rot, x1=x_th_epaisseur_rot, y1=y_th_epaisseur_rot, line=dict(color="black", width=0.5, dash='dot'))
                
                # Transformer les coordonnées si rotation nécessaire
                if needs_rotation:
                    x_tb_rot, y_tb_center_rot = rotate_coords(x, y_tb_center)
                    x_th_rot, y_th_center_rot = rotate_coords(x, y_th_center)
                else:
                    x_tb_rot, y_tb_center_rot = x, y_tb_center
                    x_th_rot, y_th_center_rot = x, y_th_center
                
                diam_str = h.get('diam_str', '⌀8')
                r = 3.0
                try: r = float(re.findall(r"[\d\.]+", diam_str)[0])/2
                except: pass
                
                fill = "black" if 'vis' in h.get('type', '') else "white"
                
                # Dessiner sur les deux tranches (haut et bas)
                fig.add_shape(type="circle", x0=x_tb_rot-r, y0=y_tb_center_rot-r, x1=x_tb_rot+r, y1=y_tb_center_rot+r, line_color="black", fillcolor=fill, layer="above")
                fig.add_shape(type="circle", x0=x_th_rot-r, y0=y_th_center_rot-r, x1=x_th_rot+r, y1=y_th_center_rot+r, line_color="black", fillcolor=fill, layer="above")
                
                type_key = f"tranche_longue_{h.get('type','unk')}_{diam_str}"
                if type_key not in annotated_tranche_longue_types:
                    # Pas de cascade : position fixe pour tous les callouts de diamètre
                    # Offset vertical fixe de -40 pour tous
                    ay_offset = -40
                    if not is_fond_sheet:
                        fig.add_annotation(
                            x=x_th_rot, y=y_th_center_rot, 
                            text=f"<b>{diam_str}</b>", 
                            showarrow=True, 
                            arrowwidth=1, arrowhead=2,
                            ax=0, ay=ay_offset, 
                            font=dict(size=12, color="black"), 
                            bgcolor="white", 
                            bordercolor="black"
                        )
                    annotated_tranche_longue_types.add(type_key)
        
        # Cotation sous la tranche : distance entre le centre du trou le plus proche d'un bord et le bord de l'épaisseur
        if not is_montant_principal:
            # Utiliser le même style que la cotation d'épaisseur des tranches (add_pro_dimension)
            if tranche_longue_holes_list:
                x_locs = sorted(list(set([round(h['x'], 1) for h in tranche_longue_holes_list])))
                if x_locs:
                    dist_rebord = T / 2.0  # Moitié de l'épaisseur réelle
                    
                    # Trouver le trou le plus proche d'un bord
                    # Pour les tranches longues, mesurer verticalement depuis le centre du trou jusqu'au bord haut ou bas de l'épaisseur
                    x_min_hole = min(x_locs)
                    # Utiliser le premier trou comme référence
                    x_ref = x_min_hole
                    
                    # Pour la tranche bas : cotation verticale depuis le bord bas de l'épaisseur jusqu'au centre du trou
                    y_tb_center = (y_tb_0 + y_tb_1) / 2
                    y_bord_bas = y_tb_1  # Bord bas de l'épaisseur
                    
                    # Utiliser add_pro_dimension avec le même style que l'épaisseur des tranches
                    # Réduire l'offset pour rapprocher la cotation 9.5 de la tranche et laisser un espace avec le 19
                    offset_base = offset_epaisseur if not is_montant_or_porte_rotated else offset_tranche_hb
                    offset_t2 = offset_base - 20  # Réduire de 20mm pour rapprocher de la tranche
                    add_pro_dimension(fig, x_ref, y_bord_bas, x_ref, y_tb_center, format_number_no_decimal(dist_rebord), -offset_t2, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)
                    
                    # Pour la tranche haut : cotation verticale depuis le bord haut de l'épaisseur jusqu'au centre du trou
                    y_th_center = (y_th_0 + y_th_1) / 2
                    y_bord_haut = y_th_0  # Bord haut de l'épaisseur
                    
                    # Utiliser add_pro_dimension avec le même style que l'épaisseur des tranches
                    add_pro_dimension(fig, x_ref, y_bord_haut, x_ref, y_th_center, format_number_no_decimal(dist_rebord), -offset_t2, axis='y', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual, panel_name=panel_name)

    # --- TROUS DE TRANCHE (Traverses & Etagères Fixes) ---
    # Pour les traverses : traits pointillés depuis chaque trou vers la ligne de cotation,
    # chiffres alignés avec les trous.
    # Pour les autres éléments : cote depuis le bord bas (y=0) jusqu'à chaque trou.
    # Pour les traverses : garder les trous, y compris ceux des montants secondaires,
    # et aligner les cotes d'espacement sur une seule ligne.
    # NE PAS CRÉER ces cotations pour les montants (traits pointillés et dimensions)
    if tranche_cote_holes_list and "Montant" not in panel_name:
        filtered_tranche_holes = tranche_cote_holes_list
        
        # Pour les COTATIONS uniquement : sur TRAVERSES, garder aussi les tourillons,
        # mais les aligner sur une seule ligne de cote (pas de cascade).
        filtered_tranche_holes_for_dims = filtered_tranche_holes
        if is_traverse and filtered_tranche_holes:
            tol_x = max(0.5, T * 0.3)
            filtered_tranche_holes_for_dims = []
            for h in filtered_tranche_holes:
                try:
                    x_raw = float(h.get('x', T / 2.0))
                except:
                    x_raw = T / 2.0
                if abs(x_raw - (T / 2.0)) <= tol_x:
                    filtered_tranche_holes_for_dims.append(h)
        
        if not filtered_tranche_holes_for_dims:
            # Pas de trous après filtrage, on sort
            pass
        else:
            y_locs = sorted(list(set([round(h['y'], 1) for h in filtered_tranche_holes_for_dims])))
            tourillon_y_locs = sorted(
                list(
                    set(
                        [
                            round(h['y'], 1)
                            for h in filtered_tranche_holes_for_dims
                            if str(h.get('type', '')).lower() == 'tourillon'
                        ]
                    )
                )
            )
            tick_len = 5
            line_width = 0.8
            x_dim = x_td_1 + 40  # Position UNIQUE de la ligne de cotation (à droite de la tranche)

            # Cote explicite demandée: sur traverses/étagères, les deux tourillons extrêmes
            # sont cotés depuis les bords (typiquement 25 mm).
            if (is_traverse or is_shelf) and len(tourillon_y_locs) >= 2:
                y_tour_first = tourillon_y_locs[0]
                y_tour_last = tourillon_y_locs[-1]
                dist_bottom_tour = y_tour_first
                dist_top_tour = W_actual - y_tour_last
                txt_bottom_tour = "25" if abs(dist_bottom_tour - 25.0) <= 2.0 else format_number_no_decimal(dist_bottom_tour)
                txt_top_tour = "25" if abs(dist_top_tour - 25.0) <= 2.0 else format_number_no_decimal(dist_top_tour)
                x_dim_tourillon = x_dim + 35

                add_pro_dimension(
                    fig, x_dim_tourillon, 0, x_dim_tourillon, y_tour_first, txt_bottom_tour, 15,
                    axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual
                )
                add_pro_dimension(
                    fig, x_dim_tourillon, y_tour_last, x_dim_tourillon, W_actual, txt_top_tour, 15,
                    axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                    cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual
                )
            
            if (is_traverse or is_shelf) and len(y_locs) > 1 and "Montant" not in panel_name:
                # POUR LES TRAVERSES ET ÉTAGÈRES : mesurer l'écart entre chaque trou consécutif + écarts avec les bords
                # Trait pointillé depuis le bord bas vers la ligne de cotation
                if needs_rotation:
                    x_td_1_rot0, y0_rot = rotate_coords(x_td_1, 0)
                    x_dim_rot0, y0_dim_rot = rotate_coords(x_dim, 0)
                    fig.add_shape(type="line", x0=x_td_1_rot0, y0=y0_rot, x1=x_dim_rot0, y1=y0_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x_dim_rot0-tick_len, y0=y0_dim_rot, x1=x_dim_rot0+tick_len, y1=y0_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                else:
                    fig.add_shape(type="line", x0=x_td_1, y0=0, x1=x_dim, y1=0, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=0, x1=x_dim+tick_len, y1=0, line=dict(color="black", width=1.2, dash='solid'))
                
                # Trait pointillé depuis le bord haut vers la ligne de cotation
                if needs_rotation:
                    x_td_1_rot_W, y_W_rot = rotate_coords(x_td_1, W_actual)
                    x_dim_rot_W, y_W_dim_rot = rotate_coords(x_dim, W_actual)
                    fig.add_shape(type="line", x0=x_td_1_rot_W, y0=y_W_rot, x1=x_dim_rot_W, y1=y_W_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x_dim_rot_W-tick_len, y0=y_W_dim_rot, x1=x_dim_rot_W+tick_len, y1=y_W_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                else:
                    fig.add_shape(type="line", x0=x_td_1, y0=W_actual, x1=x_dim, y1=W_actual, line=dict(color="black", width=line_width, dash='dot'))
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=W_actual, x1=x_dim+tick_len, y1=W_actual, line=dict(color="black", width=1.2, dash='solid'))
                
                # Cotation : écart entre le bord bas et le premier trou
                y_first = y_locs[0]
                ecart_bord_bas = y_first - 0
                add_pro_dimension(fig, x_dim, 0, x_dim, y_first, format_number_no_decimal(ecart_bord_bas), 15,
                                axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                
                # Cotation : écart entre le dernier trou et le bord haut
                y_last = y_locs[-1]
                ecart_bord_haut = W_actual - y_last
                add_pro_dimension(fig, x_dim, y_last, x_dim, W_actual, format_number_no_decimal(ecart_bord_haut), 15,
                                axis='y', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
                
                # Mesurer l'écart entre chaque trou consécutif
                # Pour les traverses : toutes les cotations d'écart sur la même ligne X (pas de cascade)
                for i in range(len(y_locs)):
                    y_pos = y_locs[i]
                    # Trait pointillé depuis chaque trou vers la ligne de cotation
                    if needs_rotation:
                        x_td_1_rot, y_pos_rot = rotate_coords(x_td_1, y_pos)
                        x_dim_rot, y_pos_dim_rot = rotate_coords(x_dim, y_pos)
                        # Trait pointillé depuis le trou vers la ligne de cotation verticale
                        if i != 0 and i != len(y_locs) - 1:
                            fig.add_shape(type="line", x0=x_td_1_rot, y0=y_pos_rot, x1=x_dim_rot, y1=y_pos_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne de cotation au niveau du trou
                        fig.add_shape(type="line", x0=x_dim_rot-tick_len, y0=y_pos_dim_rot, x1=x_dim_rot+tick_len, y1=y_pos_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    else:
                        # Trait pointillé depuis le trou vers la ligne de cotation verticale
                        if i != 0 and i != len(y_locs) - 1:
                            fig.add_shape(type="line", x0=x_td_1, y0=y_pos, x1=x_dim, y1=y_pos, line=dict(color="black", width=line_width, dash='dot'))
                        # Tick sur la ligne de cotation au niveau du trou
                        fig.add_shape(type="line", x0=x_dim-tick_len, y0=y_pos, x1=x_dim+tick_len, y1=y_pos, line=dict(color="black", width=1.2, dash='solid'))
                    
                    # Mesurer l'écart entre ce trou et le suivant
                    if i < len(y_locs) - 1:
                        y_next = y_locs[i + 1]
                        ecart = y_next - y_pos
                        add_pro_dimension(fig, x_dim, y_pos, x_dim, y_next, format_number_no_decimal(ecart), 15,
                                        axis='y', xanchor='left', rotate_coords_fn=rotate_coords if needs_rotation else None,
                                        cascade_tracker=cascade_tracker, panel_bounds=None, panel_L=L_actual, panel_W=W_actual)
            elif is_traverse or is_shelf:
                # Cas avec un seul trou : mesurer depuis le bord
                y_pos = y_locs[0]
                if needs_rotation:
                    x_td_1_rot, y_pos_rot = rotate_coords(x_td_1, y_pos)
                    x_dim_rot, y_pos_dim_rot = rotate_coords(x_dim, y_pos)
                    x_dim_rot0, y0_dim_rot = rotate_coords(x_dim, 0)
                    # Trait pointillé depuis le trou vers la ligne de cotation verticale
                    fig.add_shape(type="line", x0=x_td_1_rot, y0=y_pos_rot, x1=x_dim_rot, y1=y_pos_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                    # Ligne de cotation depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x_dim_rot0, y0=y0_dim_rot, x1=x_dim_rot, y1=y_pos_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks
                    fig.add_shape(type="line", x0=x_dim_rot0-tick_len, y0=y0_dim_rot, x1=x_dim_rot0+tick_len, y1=y0_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_dim_rot-tick_len, y0=y_pos_dim_rot, x1=x_dim_rot+tick_len, y1=y_pos_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte
                    x_mid_rot, y_mid_rot = rotate_coords(x_dim, y_pos/2)
                    fig.add_annotation(x=x_mid_rot + 15, y=y_mid_rot, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    # Trait pointillé depuis le trou vers la ligne de cotation verticale
                    fig.add_shape(type="line", x0=x_td_1, y0=y_pos, x1=x_dim, y1=y_pos, line=dict(color="black", width=line_width, dash='dot'))
                    # Ligne de cotation depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x_dim, y0=0, x1=x_dim, y1=y_pos, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=0, x1=x_dim+tick_len, y1=0, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=y_pos, x1=x_dim+tick_len, y1=y_pos, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte
                    fig.add_annotation(x=x_dim + 15, y=y_pos/2, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
            elif not is_shelf:
                # POUR LES AUTRES ÉLÉMENTS (mais pas les étagères) : cote depuis le bord bas (y=0) jusqu'à ce trou
                for y_pos in y_locs:
                    if needs_rotation:
                        x_td_1_rot, y0_rot = rotate_coords(x_td_1, 0)
                        x_td_1_rot2, y_pos_rot = rotate_coords(x_td_1, y_pos)
                        x_dim_rot, y0_dim_rot = rotate_coords(x_dim, 0)
                        x_dim_rot2, y_pos_dim_rot = rotate_coords(x_dim, y_pos)
                        # Lignes courtes depuis les trous vers l'extérieur
                        ext_x_pos = x_td_1_rot2 + max_extension_length
                        x_ext_pos_rot, y_ext_pos_rot = rotate_coords(ext_x_pos, y_pos)
                        fig.add_shape(type="line", x0=x_td_1_rot2, y0=y_pos_rot, x1=x_ext_pos_rot, y1=y_ext_pos_rot, line=dict(color="black", width=0.5, dash='dot'))
                        # Ligne de cotation verticale depuis le bord jusqu'au trou
                        fig.add_shape(type="line", x0=x_dim_rot, y0=y0_dim_rot, x1=x_dim_rot2, y1=y_pos_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                        # Ticks aux extrémités
                        fig.add_shape(type="line", x0=x_dim_rot-tick_len, y0=y0_dim_rot, x1=x_dim_rot+tick_len, y1=y0_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=x_dim_rot2-tick_len, y0=y_pos_dim_rot, x1=x_dim_rot2+tick_len, y1=y_pos_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                        # Texte sur la même ligne verticale (pas de cascade)
                        mid_x_rot, mid_y_rot = rotate_coords(x_dim, y_pos/2)
                        fig.add_annotation(x=mid_x_rot + 15, y=mid_y_rot, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                    else:
                        # Ligne courte depuis le trou vers l'extérieur
                        ext_x = x_td_1 + max_extension_length
                        fig.add_shape(type="line", x0=x_td_1, y0=y_pos, x1=ext_x, y1=y_pos, line=dict(color="black", width=0.5, dash='dot'))
                        # Ligne de cotation verticale depuis le bord jusqu'au trou
                        fig.add_shape(type="line", x0=x_dim, y0=0, x1=x_dim, y1=y_pos, line=dict(color="black", width=line_width, dash='dot'))
                        # Ticks horizontaux aux extrémités
                        fig.add_shape(type="line", x0=x_dim-tick_len, y0=0, x1=x_dim+tick_len, y1=0, line=dict(color="black", width=1.2, dash='solid'))
                        fig.add_shape(type="line", x0=x_dim-tick_len, y0=y_pos, x1=x_dim+tick_len, y1=y_pos, line=dict(color="black", width=1.2, dash='solid'))
                        # Texte sur la même ligne verticale (pas de cascade)
                        fig.add_annotation(x=x_dim + 15, y=y_pos/2, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
            else:
                # POUR LES AUTRES ÉLÉMENTS : cote depuis le bord bas (y=0) jusqu'à ce trou
                if needs_rotation:
                    x_td_1_rot, y0_rot = rotate_coords(x_td_1, 0)
                    x_td_1_rot2, y_pos_rot = rotate_coords(x_td_1, y_pos)
                    x_dim_rot, y0_dim_rot = rotate_coords(x_dim, 0)
                    x_dim_rot2, y_pos_dim_rot = rotate_coords(x_dim, y_pos)
                    # Lignes courtes depuis les trous vers l'extérieur
                    ext_x_pos = x_td_1_rot2 + max_extension_length
                    x_ext_pos_rot, y_ext_pos_rot = rotate_coords(ext_x_pos, y_pos)
                    fig.add_shape(type="line", x0=x_td_1_rot2, y0=y_pos_rot, x1=x_ext_pos_rot, y1=y_ext_pos_rot, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation verticale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x_dim_rot, y0=y0_dim_rot, x1=x_dim_rot2, y1=y_pos_dim_rot, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks aux extrémités
                    fig.add_shape(type="line", x0=x_dim_rot-tick_len, y0=y0_dim_rot, x1=x_dim_rot+tick_len, y1=y0_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_dim_rot2-tick_len, y0=y_pos_dim_rot, x1=x_dim_rot2+tick_len, y1=y_pos_dim_rot, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne verticale (pas de cascade)
                    mid_x_rot, mid_y_rot = rotate_coords(x_dim, y_pos/2)
                    fig.add_annotation(x=mid_x_rot + 15, y=mid_y_rot, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
                else:
                    # Ligne courte depuis le trou vers l'extérieur
                    ext_x = x_td_1 + max_extension_length
                    fig.add_shape(type="line", x0=x_td_1, y0=y_pos, x1=ext_x, y1=y_pos, line=dict(color="black", width=0.5, dash='dot'))
                    # Ligne de cotation verticale depuis le bord jusqu'au trou
                    fig.add_shape(type="line", x0=x_dim, y0=0, x1=x_dim, y1=y_pos, line=dict(color="black", width=line_width, dash='dot'))
                    # Ticks horizontaux aux extrémités
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=0, x1=x_dim+tick_len, y1=0, line=dict(color="black", width=1.2, dash='solid'))
                    fig.add_shape(type="line", x0=x_dim-tick_len, y0=y_pos, x1=x_dim+tick_len, y1=y_pos, line=dict(color="black", width=1.2, dash='solid'))
                    # Texte sur la même ligne verticale (pas de cascade)
                    fig.add_annotation(x=x_dim + 15, y=y_pos/2, text=format_number_no_decimal(y_pos), showarrow=False, font=dict(color="black", size=11, family="Arial"), bgcolor="white", yanchor='middle', xanchor='center')
            
            annotated_tranche_types = set()
            
            # Ajouter les cotations sur l'épaisseur des tranches pour les trous
            # Cotation à la moitié de l'épaisseur qui pointe le centre des trous
            x_tg_center_epaisseur = (x_tg_0 + x_tg_1) / 2  # Moitié de l'épaisseur tranche gauche
            x_td_center_epaisseur = (x_td_0 + x_td_1) / 2  # Moitié de l'épaisseur tranche droite
            
            # Dessiner d'abord tous les trous (utiliser filtered_tranche_holes qui est défini dans le bloc if tranche_cote_holes_list ci-dessus)
            for h in filtered_tranche_holes:
                y = h['y']
                gx = (x_tg_0 + x_tg_1) / 2
                dx = (x_td_0 + x_td_1) / 2
                
                # Transformer les coordonnées si rotation nécessaire
                if needs_rotation:
                    gx_rot, y_g_rot = rotate_coords(gx, y)
                    dx_rot, y_d_rot = rotate_coords(dx, y)
                    gx_epaisseur_rot, y_g_epaisseur_rot = rotate_coords(x_tg_center_epaisseur, y)
                    dx_epaisseur_rot, y_d_epaisseur_rot = rotate_coords(x_td_center_epaisseur, y)
                else:
                    gx_rot, y_g_rot = gx, y
                    dx_rot, y_d_rot = dx, y
                    gx_epaisseur_rot, y_g_epaisseur_rot = x_tg_center_epaisseur, y
                    dx_epaisseur_rot, y_d_epaisseur_rot = x_td_center_epaisseur, y
                
                # Cotation sur l'épaisseur : ligne pointillée depuis le centre du trou jusqu'à la moitié de l'épaisseur
                # Pour la tranche gauche
                fig.add_shape(type="line", x0=gx_rot, y0=y_g_rot, x1=gx_epaisseur_rot, y1=y_g_epaisseur_rot, line=dict(color="black", width=0.5, dash='dot'))
                
                # Pour la tranche droite
                fig.add_shape(type="line", x0=dx_rot, y0=y_d_rot, x1=dx_epaisseur_rot, y1=y_d_epaisseur_rot, line=dict(color="black", width=0.5, dash='dot'))
                
                diam_str = h.get('diam_str', '⌀8')
                r = 3.0
                try: r = float(re.findall(r"[\d\.]+", diam_str)[0])/2
                except: pass
                
                fill = "black" if 'vis' in h.get('type', '') else "white"
                
                # Utiliser les coordonnées transformées
                fig.add_shape(type="circle", x0=gx_rot-r, y0=y_g_rot-r, x1=gx_rot+r, y1=y_g_rot+r, line_color="black", fillcolor=fill, layer="above")
                fig.add_shape(type="circle", x0=dx_rot-r, y0=y_d_rot-r, x1=dx_rot+r, y1=y_d_rot+r, line_color="black", fillcolor=fill, layer="above")
                
                type_key = f"tranche_{h.get('type','unk')}_{diam_str}"
                if type_key not in annotated_tranche_types:
                    # STYLE "PRO" APPLIQUÉ ICI (Cadre + Flèche Fine)
                    # Pas de cascade : position fixe pour tous les callouts de diamètre
                    # Offset horizontal fixe de -40 pour tous
                    ax_offset = -40
                    if not is_fond_sheet:
                        fig.add_annotation(
                            x=gx_rot, y=y_g_rot, 
                            text=f"<b>{diam_str}</b>", 
                            showarrow=True, 
                            arrowwidth=1, arrowhead=2,
                            ax=ax_offset, ay=0, 
                            font=dict(size=12, color="black"), 
                            bgcolor="white", 
                            bordercolor="black"
                        )
                    annotated_tranche_types.add(type_key)
            
            # Cotation sous la tranche : distance entre le centre du trou le plus proche d'un bord et le bord de l'épaisseur
            # Utiliser le même style que la cotation d'épaisseur des tranches (add_pro_dimension)
            # Utiliser filtered_tranche_holes qui est défini dans le bloc if tranche_cote_holes_list ci-dessus
            if filtered_tranche_holes:
                y_locs = sorted(list(set([round(h['y'], 1) for h in filtered_tranche_holes])))
                if y_locs:
                    dist_rebord = T / 2.0
                    
                    # Trouver le trou le plus proche d'un bord
                    # Pour les tranches latérales, mesurer horizontalement depuis le centre du trou jusqu'au bord gauche ou droit de l'épaisseur
                    y_min_hole = min(y_locs)
                    # Utiliser le premier trou comme référence
                    y_ref = y_min_hole
                    
                    # Pour la tranche gauche : cotation horizontale depuis le bord gauche de l'épaisseur jusqu'au centre du trou
                    gx = (x_tg_0 + x_tg_1) / 2
                    x_bord_gauche = x_tg_0
                    
                    # Utiliser add_pro_dimension avec le même style que l'épaisseur des tranches
                    # Réduire l'offset pour rapprocher la cotation 9.5 de la tranche et laisser un espace avec le 19
                    offset_base = offset_epaisseur if not is_montant_or_porte_rotated else offset_tranche_gd
                    offset_t2 = offset_base - 20
                    add_pro_dimension(fig, x_bord_gauche, y_ref, gx, y_ref, format_number_no_decimal(dist_rebord), offset_t2, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)
                    
                    # Pour la tranche droite : cotation horizontale depuis le bord droit de l'épaisseur jusqu'au centre du trou
                    dx = (x_td_0 + x_td_1) / 2
                    x_bord_droit = x_td_1
                    
                    # Utiliser add_pro_dimension avec le même style que l'épaisseur des tranches
                    add_pro_dimension(fig, x_bord_droit, y_ref, dx, y_ref, format_number_no_decimal(dist_rebord), offset_t2, axis='x', xanchor='center', yanchor='middle', rotate_coords_fn=rotate_coords if needs_rotation else None, cascade_tracker=cascade_tracker, panel_bounds=panel_bounds, panel_L=L_actual, panel_W=W_actual)

    CART_Y_MIN = 0.01
    CART_Y_MAX = 0.095 
    CART_BG_COLOR = "#f9f9f0"
    LINE_COLOR = "black"
    
    # Rallonger la largeur globale de la bande de légende (réduire les marges)
    CART_X_START = 0.03  # Marge gauche réduite (au lieu de 0.05)
    CART_X_END = 0.97    # Marge droite réduite (au lieu de 0.95)
    CART_WIDTH = CART_X_END - CART_X_START  # Largeur totale : 0.94 (au lieu de 0.90)
    
    fig.add_shape(type="rect", xref="paper", yref="paper", x0=CART_X_START, x1=CART_X_END, y0=CART_Y_MIN, y1=CART_Y_MAX, line=dict(color=LINE_COLOR, width=1), fillcolor=CART_BG_COLOR, layer="below")
    
    col_pcts = [0.15, 0.35, 0.55, 0.66, 0.75, 0.9]  # Réduire Quantité (0.66) et Date (0.75), agrandir Légende (0.75-0.9)
    for pct in col_pcts:
        x_pos = CART_X_START + (CART_WIDTH * pct)
        fig.add_shape(type="line", xref="paper", yref="paper", x0=x_pos, x1=x_pos, y0=CART_Y_MIN, y1=CART_Y_MAX, line=dict(color=LINE_COLOR, width=0.5))
    
    def wrap_text(text, max_chars=50):
        """Découpe le texte en plusieurs lignes uniquement quand il dépasse max_chars."""
        if not text:
            return ""
        text_str = str(text)
        if len(text_str) <= max_chars:
            return text_str
        
        # Découper uniquement si nécessaire, en préservant les mots
        words = text_str.split()
        lines = []
        current_line = ""
        
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            if len(test_line) <= max_chars:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                # Si le mot seul dépasse max_chars, on le coupe
                if len(word) > max_chars:
                    while len(word) > max_chars:
                        lines.append(word[:max_chars])
                        word = word[max_chars:]
                    current_line = word
                else:
                    current_line = word
        
        if current_line:
            lines.append(current_line)
        
        return "<br>".join(lines)
    
    def limit_to_4_words(text):
        """Limite le texte à 4 mots maximum."""
        if not text:
            return ""
        words = str(text).split()
        return " ".join(words[:4])
    
    def add_paper_txt(pct_center, title, val, max_chars_title=10, max_chars_val=12):
        # Calculer les positions des colonnes
        col_pcts = [0.15, 0.35, 0.55, 0.66, 0.75, 0.9]  # Inclure la colonne Légende (agrandie)
        col_positions = [CART_X_START]  # Début
        for pct in col_pcts:
            col_positions.append(CART_X_START + CART_WIDTH * pct)
        col_positions.append(CART_X_END)  # Fin
        
        # Mapper pct_center aux indices de case
        case_map = {
            0.1: 0,    # Case 1 (Projet)
            0.25: 1,   # Case 2 (Corps de meuble)
            0.45: 2,   # Case 3 (Désignation)
            0.65: 3,   # Case 4 (Quantité)
            0.75: 4    # Case 5 (Date) - ajusté pour correspondre à la nouvelle position
            # Case 6 (Légende) gérée séparément
        }
        case_index = case_map.get(pct_center, 0)
        
        # Calculer le centre réel de la case
        case_start = col_positions[case_index]
        case_end = col_positions[case_index + 1]
        x_c = (case_start + case_end) / 2  # Centre exact de la case
        case_width_pct = case_end - case_start
        
        # Estimer le nombre de caractères qui tiennent dans la case (largeur en pixels)
        # Largeur totale = 1123 pixels, case_width_pct est en pourcentage de cette largeur
        case_width_px = 1123 * case_width_pct
        # Estimation: ~0.6 pixels par point de taille de police par caractère
        max_chars_title_case = int(case_width_px / (11 * 0.6))
        max_chars_val_case = int(case_width_px / (10 * 0.6))
        
        # Centrer verticalement le texte dans la case
        y_center = (CART_Y_MIN + CART_Y_MAX) / 2  # Centre vertical de la case
        # Titre : limiter à 4 mots, découper uniquement si dépasse la largeur de la case
        title_str = limit_to_4_words(title)
        title_wrapped = wrap_text(title_str, max_chars=max_chars_title_case)
        # Valeur : limiter à 4 mots, découper uniquement si dépasse la largeur de la case
        val_str = limit_to_4_words(val)
        val_wrapped = wrap_text(val_str, max_chars=max_chars_val_case)
        # Ajouter les annotations centrées verticalement et horizontalement
        fig.add_annotation(
            xref="paper", yref="paper", 
            x=x_c, y=y_center + 0.015, 
            text=f"<b>{title_wrapped}</b>", 
            showarrow=False, 
            font=dict(size=11, color="black"), 
            xanchor="center", 
            yanchor="middle"
        )
        fig.add_annotation(
            xref="paper", yref="paper", 
            x=x_c, y=y_center - 0.015, 
            text=val_wrapped, 
            showarrow=False, 
            font=dict(size=10, color="black"), 
            xanchor="center", 
            yanchor="middle"
        )

    add_paper_txt(0.1, "Projet", project_info['project_name'], max_chars_title=8, max_chars_val=10)
    add_paper_txt(0.25, "Corps de meuble", project_info.get('corps_meuble', ''), max_chars_title=10, max_chars_val=12)
    add_paper_txt(0.45, "Désignation", panel_name, max_chars_title=10, max_chars_val=12)
    add_paper_txt(0.65, "Quantité", project_info['quantity'], max_chars_title=8, max_chars_val=8)
    add_paper_txt(0.75, "Date", project_info['date'], max_chars_title=6, max_chars_val=10)
    
    # Colonne dédiée "Légende" avec titre et définitions des symboles
    # Ajuster les colonnes pour inclure la légende avant le logo
    col_pcts = [0.15, 0.35, 0.55, 0.66, 0.75, 0.9]  # Ajout de la colonne Légende (agrandie : 0.75-0.9)
    col_positions = [CART_X_START]
    for pct in col_pcts:
        col_positions.append(CART_X_START + CART_WIDTH * pct)
    col_positions.append(CART_X_END)
    
    # Position de la case Légende (entre Date et Logo)
    legend_start = col_positions[5]  # Après Date (index 5)
    legend_end = col_positions[6]     # Avant Logo (index 6)
    legend_x_center = (legend_start + legend_end) / 2  # Centre horizontal de la case
    legend_y_center = (CART_Y_MIN + CART_Y_MAX) / 2
    
    # Titre "Légende" en haut de la case, centré
    fig.add_annotation(
        xref="paper", yref="paper",
        x=legend_x_center, y=CART_Y_MAX - 0.008,
        text="<b>Légende</b>",
        showarrow=False,
        font=dict(size=10, color="black"),
        xanchor="center",
        yanchor="top"
    )
    
    # Dessiner les triangles dans la légende (coordonnées en pourcentage du papier)
    # Layout : triangle + espace + texte sur la même ligne, une ligne par paire, centré horizontalement
    legend_tri_size_pct = 0.006  # Taille du triangle
    legend_spacing_from_title = 0.020  # Espace entre le titre et le contenu (augmenté)
    legend_text_offset = 0.015  # Espace entre triangle et texte (augmenté)
    legend_y_line1 = CART_Y_MAX - 0.008 - legend_spacing_from_title - 0.012  # Première ligne (triangle vide)
    legend_y_line2 = CART_Y_MAX - 0.008 - legend_spacing_from_title - 0.032  # Deuxième ligne (triangle noir)
    
    # Pour centrer l'ensemble : calculer la largeur approximative du contenu
    # Triangle ~0.012, espace ~0.015, texte "Corps meuble inférieur" ~0.09
    content_width_line1 = 0.117  # Largeur approximative ligne 1 (triangle + espace + texte)
    content_width_line2 = 0.113  # Largeur approximative ligne 2 (texte plus court)
    
    # Ligne 1 : Triangle vide + espace + texte "Corps meuble inférieur" - centré
    # Positionner le triangle à gauche du centre pour centrer l'ensemble
    tri_vide_x = legend_x_center - content_width_line1 / 2 + legend_tri_size_pct
    tri_vide_top_y = legend_y_line1  # Triangle pointant vers le bas (retourné 180°)
    tri_vide_left_x = tri_vide_x - legend_tri_size_pct
    tri_vide_right_x = tri_vide_x + legend_tri_size_pct
    tri_vide_bottom_y = legend_y_line1 + legend_tri_size_pct * 1.2  # Base du triangle en bas
    
    fig.add_shape(
        type="path",
        path=f"M {tri_vide_x},{tri_vide_top_y} L {tri_vide_left_x},{tri_vide_bottom_y} L {tri_vide_right_x},{tri_vide_bottom_y} Z",
        xref="paper", yref="paper",
        line=dict(color="black", width=1.0),
        fillcolor="white",
        layer="above"
    )
    
    # Texte avec espace après le triangle - centré dans la case
    text_x_line1 = tri_vide_x + legend_tri_size_pct + legend_text_offset
    fig.add_annotation(
        xref="paper", yref="paper",
        x=text_x_line1, y=legend_y_line1 + legend_tri_size_pct * 0.6,
        text="Corps meuble inférieur",
        showarrow=False,
        font=dict(size=7, color="black"),
        xanchor="left",
        yanchor="middle"
    )
    
    # Ligne 2 : Triangle noir + espace + texte "Avant corps du meuble" - centré
    tri_noir_x = legend_x_center - content_width_line2 / 2 + legend_tri_size_pct
    tri_noir_top_y = legend_y_line2  # Triangle pointant vers le bas (retourné 180°)
    tri_noir_left_x = tri_noir_x - legend_tri_size_pct
    tri_noir_right_x = tri_noir_x + legend_tri_size_pct
    tri_noir_bottom_y = legend_y_line2 + legend_tri_size_pct * 1.2  # Base du triangle en bas
    
    fig.add_shape(
        type="path",
        path=f"M {tri_noir_x},{tri_noir_top_y} L {tri_noir_left_x},{tri_noir_bottom_y} L {tri_noir_right_x},{tri_noir_bottom_y} Z",
        xref="paper", yref="paper",
        line=dict(color="black", width=1.0),
        fillcolor="black",
        layer="above"
    )
    
    # Texte avec espace après le triangle - centré dans la case
    text_x_line2 = tri_noir_x + legend_tri_size_pct + legend_text_offset
    fig.add_annotation(
        xref="paper", yref="paper",
        x=text_x_line2, y=legend_y_line2 + legend_tri_size_pct * 0.6,
        text="Avant corps du meuble",
        showarrow=False,
        font=dict(size=7, color="black"),
        xanchor="left",
        yanchor="middle"
    )
    
    # Logo centré dans sa case (dernière colonne)
    logo_col_start = CART_X_START + (CART_WIDTH * 0.9)  # Début de la colonne logo
    logo_col_end = CART_X_END  # Fin de la colonne logo
    logo_x_c = (logo_col_start + logo_col_end) / 2  # Centre horizontal de la colonne
    logo_y_c = (CART_Y_MIN + CART_Y_MAX) / 2  # Centre vertical de la légende
    logo_file = "logo.png"
    logo_base64 = load_image_base64(logo_file)
    
    if logo_base64:
        # Calculer la taille du logo pour qu'il reste dans la case
        logo_width = logo_col_end - logo_col_start - 0.01  # Largeur avec marge
        logo_height = CART_Y_MAX - CART_Y_MIN - 0.01  # Hauteur avec marge
        # Garder le ratio mais s'assurer qu'il rentre dans la case
        logo_size_x = min(logo_width, logo_height * 1.4)  # Ratio approximatif
        logo_size_y = min(logo_height, logo_width / 1.4)
        fig.add_layout_image(dict(
            source=logo_base64, 
            xref="paper", 
            yref="paper", 
            x=logo_x_c, 
            y=logo_y_c, 
            sizex=logo_size_x, 
            sizey=logo_size_y, 
            xanchor="center", 
            yanchor="middle", 
            layer="above"
        ))
    else:
        fig.add_annotation(
            xref="paper", 
            yref="paper", 
            x=logo_x_c, 
            y=logo_y_c, 
            text="LOGO<br>MANQUANT", 
            showarrow=False, 
            font=dict(color="red", size=8), 
            xanchor="center", 
            yanchor="middle"
        )

    margin_val = 50
    # Les tranches sont déjà incluses dans bounds_x et bounds_y (lignes 1193-1197)
    # Calculer les limites finales avec marges
    f_min_x, f_max_x = min(bounds_x) - margin_val, max(bounds_x) + margin_val
    f_min_y, f_max_y = min(bounds_y) - margin_val, max(bounds_y) + margin_val
    
    # AUTOSCALE : on veut que toute la figure (panneau + 4 tranches + cotes)
    # soit toujours CENTRÉE au milieu de la zone de dessin,
    # et qu'elle tienne dans la zone située au-dessus de la légende.
    # La légende occupe les 10% du bas (domain=[0.10, 1.0] pour yaxis).
    # Dimensions du canvas : 1123 x 794 pixels
    available_height_px = 794 * 0.90  # 90% pour le graphique, 10% pour la légende
    available_width_px = 1123 - 2 * margin_val
    
    # Dimensions réelles du contenu (en unités de données)
    content_width_data = f_max_x - f_min_x
    content_height_data = f_max_y - f_min_y
    
    # Facteurs d'échelle nécessaires pour faire rentrer le contenu
    scale_x = available_width_px / content_width_data if content_width_data > 0 else 1.0
    scale_y = available_height_px / content_height_data if content_height_data > 0 else 1.0
    
    # On prend le plus petit facteur pour que ça tienne dans les deux sens
    scale_factor = min(scale_x, scale_y)
    # Légère marge pour que la figure ne colle pas aux bords
    scale_factor *= 0.97
    
    # Taille visible en unités de données après ce zoom
    visible_width_data = available_width_px / scale_factor
    visible_height_data = available_height_px / scale_factor
    
    # Centre de la figure (en coordonnées données)
    center_x = (f_min_x + f_max_x) / 2
    center_y = (f_min_y + f_max_y) / 2
    
    # Règle spéciale : zoom général un peu plus fort pour Portes & Montants
    if ("Montant" in panel_name) or ("Porte" in panel_name):
        visible_width_data *= 0.90
        visible_height_data *= 0.90
    
    # Ranges centrés horizontalement ET verticalement
    f_min_x_zoomed = center_x - visible_width_data / 2
    f_max_x_zoomed = center_x + visible_width_data / 2
    f_min_y_zoomed = center_y - visible_height_data / 2
    f_max_y_zoomed = center_y + visible_height_data / 2
    
    # Sur les feuilles de fond/panneau arriere, retirer les traits blancs residuels
    # qui peuvent apparaitre en haut a droite dans certains viewers DXF/HTML.
    if is_fond_sheet and fig.layout.shapes:
        cleaned_shapes = []
        for shp in fig.layout.shapes:
            color = str(getattr(getattr(shp, "line", None), "color", "")).replace(" ", "").lower()
            if color in {"white", "#fff", "#ffffff", "rgb(255,255,255)", "rgba(255,255,255,1)", "rgba(255,255,255,1.0)"}:
                continue
            cleaned_shapes.append(shp)
        fig.update_layout(shapes=cleaned_shapes)

    # Regle stricte demandee pour les feuilles du fond:
    # aucun element en coordonnees panneau ne doit depasser au-dessus de la tranche superieure.
    if is_fond_sheet:
        if fig.layout.shapes:
            capped_shapes = []
            for shp in fig.layout.shapes:
                yref = getattr(shp, "yref", None)
                # Garder les formes en coordonnees papier (cartouche/legende).
                if yref == "paper":
                    capped_shapes.append(shp)
                    continue
                y0 = getattr(shp, "y0", None)
                y1 = getattr(shp, "y1", None)
                if y0 is not None and y1 is not None:
                    try:
                        if max(float(y0), float(y1)) > float(y_th_1) + 0.01:
                            continue
                    except Exception:
                        pass
                capped_shapes.append(shp)
            fig.update_layout(shapes=capped_shapes)

        if fig.layout.annotations:
            capped_annotations = []
            for ann in fig.layout.annotations:
                yref = getattr(ann, "yref", None)
                # Garder les annotations papier (cartouche).
                if yref == "paper":
                    capped_annotations.append(ann)
                    continue
                y = getattr(ann, "y", None)
                if y is not None:
                    try:
                        if float(y) > float(y_th_1) + 0.01:
                            continue
                    except Exception:
                        pass
                capped_annotations.append(ann)
            fig.update_layout(annotations=capped_annotations)

    fig.update_layout(
        title=dict(text=f"FEUILLE D'USINAGE : {panel_name}", x=0.5, y=0.98),
        plot_bgcolor="white", paper_bgcolor="white",
        width=1123, height=794,
        margin=dict(l=margin_val, r=margin_val, t=50, b=30),
        # xaxis/yaxis: zoom automatique pour que tout tienne au-dessus de la légende
        # domain=[0.10, 1.0] signifie que le graphique occupe 90% de la hauteur (les 10% du bas sont pour la légende)
        xaxis=dict(visible=False, range=[f_min_x_zoomed, f_max_x_zoomed], fixedrange=False),
        yaxis=dict(visible=False, range=[f_min_y_zoomed, f_max_y_zoomed], scaleanchor="x", scaleratio=1, domain=[0.10, 1.0], fixedrange=False),
        showlegend=False
    )
    return fig
