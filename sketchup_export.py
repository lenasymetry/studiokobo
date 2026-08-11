"""Export SketchUp – génère un fichier Collada (.dae) importable dans SketchUp.

Le format Collada (.dae) est un standard XML ouvert que SketchUp importe
nativement avec les unités correctes (millimètres).

Chaque panneau de la scène est modélisé comme un parallélépipède rectangle
aux dimensions exactes indiquées dans l'interface Streamlit.

Unités : millimètres  (déclaration <unit meter="0.001"> dans l'en-tête Collada).
Système de coordonnées : Z_UP (identique à SketchUp).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List, Tuple, Dict


# ---------------------------------------------------------------------------
# Géométrie : construction d'une boîte (parallélépipède rectangle)
# ---------------------------------------------------------------------------

def _box_vertices(ox: float, oy: float, oz: float,
                  w: float, d: float, h: float,
                  rotation_deg: float = 0.0,
                  rotation_pivot_xy: Tuple[float, float] | None = None) -> List[float]:
    """Retourne 24 floats (8 sommets × 3 coordonnées) pour la boîte."""
    x0, y0, z0 = ox, oy, oz
    x1, y1, z1 = ox + w, oy + d, oz + h
    verts = [
        [x0, y0, z0],   # 0 – avant bas gauche
        [x1, y0, z0],   # 1 – avant bas droit
        [x1, y1, z0],   # 2 – arrière bas droit
        [x0, y1, z0],   # 3 – arrière bas gauche
        [x0, y0, z1],   # 4 – avant haut gauche
        [x1, y0, z1],   # 5 – avant haut droit
        [x1, y1, z1],   # 6 – arrière haut droit
        [x0, y1, z1],   # 7 – arrière haut gauche
    ]

    if abs(float(rotation_deg)) > 1e-9 and rotation_pivot_xy is not None:
        pivot_x, pivot_y = float(rotation_pivot_xy[0]), float(rotation_pivot_xy[1])
        a = math.radians(float(rotation_deg))
        cos_a = math.cos(a)
        sin_a = math.sin(a)
        for pt in verts:
            dx = pt[0] - pivot_x
            dy = pt[1] - pivot_y
            pt[0] = pivot_x + dx * cos_a - dy * sin_a
            pt[1] = pivot_y + dx * sin_a + dy * cos_a

    flat: List[float] = []
    for vx, vy, vz in verts:
        flat.extend([vx, vy, vz])
    return flat


# 12 triangles couvrant les 6 faces d'une boîte (indices dans les 8 sommets)
_BOX_TRIANGLES = [
    # Face bas   (z=z0) : 0-1-2, 0-2-3
    0, 1, 2,  0, 2, 3,
    # Face haut  (z=z1) : 4-7-6, 4-6-5
    4, 7, 6,  4, 6, 5,
    # Face avant (y=y0) : 0-4-5, 0-5-1
    0, 4, 5,  0, 5, 1,
    # Face arrière (y=y1) : 2-6-7, 2-7-3
    2, 6, 7,  2, 7, 3,
    # Face gauche (x=x0) : 0-3-7, 0-7-4
    0, 3, 7,  0, 7, 4,
    # Face droite (x=x1) : 1-5-6, 1-6-2
    1, 5, 6,  1, 6, 2,
]
_BOX_TRIANGLES_STR = " ".join(str(v) for v in _BOX_TRIANGLES)


# ---------------------------------------------------------------------------
# Construction XML Collada
# ---------------------------------------------------------------------------

def _add_box_geometry(lib_geom: ET.Element, geom_id: str, geom_name: str,
                      ox: float, oy: float, oz: float,
                      w: float, d: float, h: float,
                      material_symbol: str,
                      rotation_deg: float = 0.0,
                      rotation_pivot_xy: Tuple[float, float] | None = None) -> None:
    """Ajoute un élément <geometry> (boîte) dans library_geometries."""
    verts = _box_vertices(ox, oy, oz, w, d, h, rotation_deg=rotation_deg, rotation_pivot_xy=rotation_pivot_xy)
    verts_str = " ".join(f"{v:.4f}" for v in verts)

    geom = ET.SubElement(lib_geom, "geometry", id=geom_id, name=geom_name)
    mesh = ET.SubElement(geom, "mesh")

    # Source positions
    src_id = f"{geom_id}-positions"
    arr_id = f"{src_id}-array"
    src = ET.SubElement(mesh, "source", id=src_id)
    fa = ET.SubElement(src, "float_array", id=arr_id, count="24")
    fa.text = verts_str
    tc = ET.SubElement(src, "technique_common")
    acc = ET.SubElement(tc, "accessor", source=f"#{arr_id}", count="8", stride="3")
    ET.SubElement(acc, "param", name="X", type="float")
    ET.SubElement(acc, "param", name="Y", type="float")
    ET.SubElement(acc, "param", name="Z", type="float")

    # Vertices
    verts_elem = ET.SubElement(mesh, "vertices", id=f"{geom_id}-vertices")
    ET.SubElement(verts_elem, "input", semantic="POSITION", source=f"#{src_id}")

    # Triangles
    tris = ET.SubElement(mesh, "triangles", count="12", material=material_symbol)
    ET.SubElement(tris, "input", semantic="VERTEX",
                  source=f"#{geom_id}-vertices", offset="0")
    p_elem = ET.SubElement(tris, "p")
    p_elem.text = _BOX_TRIANGLES_STR


def _add_node(parent: ET.Element, node_id: str, node_name: str,
              geom_id: str, material_id: str, material_symbol: str) -> ET.Element:
    """Ajoute un <node> qui instancie une géométrie et le retourne."""
    node = ET.SubElement(parent, "node", id=node_id,
                         name=node_name, type="NODE")
    ig = ET.SubElement(node, "instance_geometry", url=f"#{geom_id}")
    bm = ET.SubElement(ig, "bind_material")
    tc = ET.SubElement(bm, "technique_common")
    ET.SubElement(tc, "instance_material",
                  symbol=material_symbol, target=f"#{material_id}")
    return node


def _add_group_node(parent: ET.Element, node_id: str, node_name: str) -> ET.Element:
    """Ajoute un nœud groupe (bloc) sans géométrie."""
    return ET.SubElement(parent, "node", id=node_id, name=node_name, type="NODE")


def _add_instance_node(parent: ET.Element, node_id: str, node_name: str, ref_node_id: str) -> ET.Element:
    """Ajoute un nœud qui instancie un nœud de library_nodes."""
    node = ET.SubElement(parent, "node", id=node_id, name=node_name, type="NODE")
    ET.SubElement(node, "instance_node", url=f"#{ref_node_id}")
    return node


def _add_material(lib_effects: ET.Element, lib_materials: ET.Element,
                  mat_id: str, mat_name: str,
                  r: float, g: float, b: float, a: float = 1.0) -> None:
    """Ajoute un effet Lambert + matériau dans les bibliothèques Collada."""
    eff_id = f"{mat_id}-effect"

    eff = ET.SubElement(lib_effects, "effect", id=eff_id, name=f"{mat_name}-effect")
    profile = ET.SubElement(eff, "profile_COMMON")
    tech = ET.SubElement(profile, "technique", sid="common")
    lambert = ET.SubElement(tech, "lambert")
    diffuse = ET.SubElement(lambert, "diffuse")
    color_elem = ET.SubElement(diffuse, "color", sid="diffuse")
    color_elem.text = f"{r:.4f} {g:.4f} {b:.4f} {a:.4f}"

    mat = ET.SubElement(lib_materials, "material", id=mat_id, name=mat_name)
    ET.SubElement(mat, "instance_effect", url=f"#{eff_id}")


def _safe_xml_id(text: str) -> str:
    """Transforme une chaîne quelconque en identifiant XML valide."""
    result = ""
    for ch in str(text):
        if ch.isalnum() or ch in ("-", "_", "."):
            result += ch
        else:
            result += "_"
    # Les IDs XML ne peuvent pas commencer par un chiffre
    if result and result[0].isdigit():
        result = "_" + result
    return result or "_id"


# ---------------------------------------------------------------------------
# Calcul des origines absolues (en mm, unit_factor=1.0)
# ---------------------------------------------------------------------------

def _calculate_origins(scene_cabinets: List[Dict]) -> List[Tuple[float, float, float]]:
    """Calcule les origines absolues de chaque caisson en mm."""
    from machining_logic import calculate_origins_recursively
    return calculate_origins_recursively(scene_cabinets, unit_factor=1.0)


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def generate_sketchup_collada(scene_cabinets: List[Dict], door_mode: str = "closed", door_angle_deg: float = 50.0) -> bytes:
    """Génère un fichier Collada (.dae) représentant la scène 3D.

    Chaque panneau (montants, traverses, fond, étagères, portes, façades
    tiroir) est modélisé à ses dimensions exactes en millimètres.

    Args:
        scene_cabinets: Données de scène issues de Streamlit.
        door_mode: "closed" (portes fermées) ou "ajar" (portes entre-ouvertes).
        door_angle_deg: Angle d'entre-ouverture utilisé quand door_mode="ajar".

    Returns:
        bytes – contenu UTF-8 du fichier .dae prêt à télécharger.
    """
    if not scene_cabinets:
        return b""

    # ------------------------------------------------------------------ #
    #  Racine COLLADA                                                      #
    # ------------------------------------------------------------------ #
    root = ET.Element("COLLADA")
    root.set("xmlns", "http://www.collada.org/2005/11/COLLADASchema")
    root.set("version", "1.4.1")

    asset = ET.SubElement(root, "asset")
    ET.SubElement(asset, "created").text = "2026-01-01T00:00:00"
    ET.SubElement(asset, "modified").text = "2026-01-01T00:00:00"
    ET.SubElement(asset, "unit", meter="0.001", name="millimeter")
    ET.SubElement(asset, "up_axis").text = "Z_UP"

    lib_effects = ET.SubElement(root, "library_effects")
    lib_materials = ET.SubElement(root, "library_materials")
    lib_geom = ET.SubElement(root, "library_geometries")
    lib_vis = ET.SubElement(root, "library_visual_scenes")
    lib_nodes = ET.SubElement(root, "library_nodes")
    visual_scene = ET.SubElement(lib_vis, "visual_scene",
                                 id="Scene", name="Scene")

    # ------------------------------------------------------------------ #
    #  Matériaux                                                           #
    # ------------------------------------------------------------------ #
    _add_material(lib_effects, lib_materials,
                  "mat_body",   "Corps",   0.859, 0.725, 0.553)   # bois clair
    _add_material(lib_effects, lib_materials,
                  "mat_door",   "Porte",   0.600, 0.600, 0.650)   # gris bleuté
    _add_material(lib_effects, lib_materials,
                  "mat_drawer", "Tiroir",  0.545, 0.698, 0.749)   # bleu clair

    # ------------------------------------------------------------------ #
    #  Origines des caissons (en mm)                                       #
    # ------------------------------------------------------------------ #
    origins = _calculate_origins(scene_cabinets)

    # Compteur global pour des IDs XML uniques
    _counter = [0]

    def _uid() -> str:
        _counter[0] += 1
        return str(_counter[0])

    meuble_node = _add_group_node(visual_scene, "node_meuble", "Meuble")

    def add_panel(parent_node: ET.Element,
                  label: str,
                  ox: float, oy: float, oz: float,
                  w: float, d: float, h: float,
                  mat: str = "mat_body",
                  rotation_deg: float = 0.0,
                  rotation_pivot_xy: Tuple[float, float] | None = None) -> None:
        """Crée un panneau (boîte) dans la scène si ses dimensions sont valides."""
        if w <= 0.0 or d <= 0.0 or h <= 0.0:
            return
        uid = _uid()
        geom_id = f"geom_{uid}"
        comp_node_id = f"comp_elem_{uid}"
        instance_node_id = f"node_elem_{uid}"
        safe_name = _safe_xml_id(label)
        _add_box_geometry(lib_geom, geom_id, safe_name,
                          ox, oy, oz, w, d, h, mat,
                          rotation_deg=rotation_deg,
                          rotation_pivot_xy=rotation_pivot_xy)
        # Définition composant + instance : SketchUp préserve mieux
        # chaque élément comme bloc indépendant avec ce schéma Collada.
        _add_node(lib_nodes, comp_node_id, f"{label} Component", geom_id, mat, mat)
        _add_instance_node(parent_node, instance_node_id, label, comp_node_id)

    # ------------------------------------------------------------------ #
    #  Génération de la géométrie pour chaque caisson                      #
    # ------------------------------------------------------------------ #
    for i, cabinet in enumerate(scene_cabinets):
        if not isinstance(cabinet, dict):
            continue

        dims = cabinet.get("dims", {}) or {}
        L  = float(dims.get("L_raw", 0.0) or 0.0)    # largeur totale
        W  = float(dims.get("W_raw", 0.0) or 0.0)    # profondeur totale
        H  = float(dims.get("H_raw", 0.0) or 0.0)    # hauteur totale
        tl = float(dims.get("t_lr_raw", 19.0) or 19.0) # épaisseur montants gauche/droit
        tb = float(dims.get("t_fb_raw", 3.0) or 3.0)   # épaisseur fond (panneau arrière)
        tt = float(dims.get("t_tb_raw", 19.0) or 19.0) # épaisseur traverses haut/bas

        if L <= 0.0 or W <= 0.0 or H <= 0.0:
            continue

        ox, oy, oz = origins[i]

        # Fileur : réduction de la largeur effective du caisson
        _dp_f = cabinet.get("door_props", {}) or {}
        _fileur_w_skp = float(_dp_f.get("fileur_width", 0))
        if _fileur_w_skp > 0:
            L = L - _fileur_w_skp
            if _dp_f.get("door_opening", "right") == "left":
                ox = ox + _fileur_w_skp

        base_el = cabinet.get("base_elements", {
            "has_back_panel":       True,
            "has_left_upright":     True,
            "has_right_upright":    True,
            "has_bottom_traverse":  True,
            "has_top_traverse":     True,
        })

        cab_label = cabinet.get("name", f"C{i+1}")

        cab_node_id = f"node_caisson_{_safe_xml_id(cab_label)}_{i+1}"
        cab_node = _add_group_node(meuble_node, cab_node_id, f"Caisson {cab_label}")

        web_geometry = cabinet.get("web_render_geometry")
        if isinstance(web_geometry, dict) and web_geometry.get("boxes"):
            for j, box in enumerate(web_geometry.get("boxes", []), start=1):
                color_group = str(box.get("color_group", "body"))
                mat = "mat_body" if color_group == "body" else "mat_drawer"
                add_panel(
                    cab_node,
                    f"Import Web {j} – {cab_label}",
                    ox + float(box.get("x_mm", 0.0)),
                    oy + float(box.get("y_mm", 0.0)),
                    oz + float(box.get("z_mm", 0.0)),
                    float(box.get("w_mm", 0.0)),
                    float(box.get("d_mm", 0.0)),
                    float(box.get("h_mm", 0.0)),
                    mat=mat,
                )
            continue

        # ── Structure principale ────────────────────────────────────────
        if base_el.get("has_bottom_traverse", True):
            add_panel(cab_node,
                      f"Traverse Bas – {cab_label}",
                      ox + tl, oy, oz,
                      L - 2*tl, W, tt)

        if base_el.get("has_top_traverse", True):
            add_panel(cab_node,
                      f"Traverse Haut – {cab_label}",
                      ox + tl, oy, oz + H - tt,
                      L - 2*tl, W, tt)

        if base_el.get("has_left_upright", True):
            add_panel(cab_node,
                      f"Montant Gauche – {cab_label}",
                      ox, oy, oz,
                      tl, W, H)

        if base_el.get("has_right_upright", True):
            add_panel(cab_node,
                      f"Montant Droit – {cab_label}",
                      ox + L - tl, oy, oz,
                      tl, W, H)

        if base_el.get("has_back_panel", True):
            add_panel(cab_node,
                      f"Fond – {cab_label}",
                      ox + tl, oy + W - tb, oz + tt,
                      L - 2*tl, tb, H - 2*tt)

        # ── Séparateurs verticaux (montants secondaires) ────────────────
        for j, div in enumerate(cabinet.get("vertical_dividers", [])):
            div_x  = float(div["position_x"])
            div_th = float(div.get("thickness", 19.0))
            add_panel(cab_node,
                      f"Séparateur {j+1} – {cab_label}",
                      ox + div_x - div_th / 2, oy, oz + tt,
                      div_th, W - tb, H - 2*tt)

        # ── Étagères verticales ─────────────────────────────────────────
        for j, vs in enumerate(cabinet.get("vertical_shelves", [])):
            vs_x    = float(vs.get("position_x", 300.0))
            vs_th   = float(vs.get("thickness",   19.0))
            vs_bot  = float(vs.get("bottom_y",     0.0))
            vs_top  = float(vs.get("top_y",       100.0))
            vs_h    = vs_top - vs_bot
            add_panel(cab_node,
                      f"Étagère Verticale {j+1} – {cab_label}",
                      ox + vs_x - vs_th / 2, oy, oz + tt + vs_bot,
                      vs_th, W, vs_h)

        # ── Étagères horizontales ───────────────────────────────────────
        for j, s in enumerate(cabinet.get("shelves", [])):
            sh_z      = oz + tt + float(s["height"])
            sh_th     = float(s.get("thickness", 19.0))
            # Règle demandée: étagère = longueur de la traverse, profondeur de la
            # traverse - 19mm (pour ne pas transpercer le Fond).
            sh_w = L - 2 * tl
            sh_x = ox + tl
            sh_depth = max(0.0, W - 19.0)

            add_panel(cab_node,
                      f"Étagère {j+1} – {cab_label}",
                      sh_x, oy, sh_z,
                      sh_w, max(0.0, sh_depth), sh_th)

        # ── Porte(s) importee(s) explicites ─────────────────────────────
        door_props_for_fileur = cabinet.get("door_props", {}) or {}
        imported_doors = cabinet.get("imported_doors", []) or []
        dp = None
        if imported_doors:
            is_ajar = str(door_mode).lower() == "ajar"
            ajar_angle = abs(float(door_angle_deg)) if is_ajar else 0.0
            for door_idx, imp_door in enumerate(imported_doors, start=1):
                gap = float(imp_door.get("door_gap", 2.0))
                door_th = float(imp_door.get("door_thickness", 19.0))
                d_y = oy - door_th
                d_z = oz + float(imp_door.get("door_bottom_offset", 0.0))
                d_H = float(imp_door.get("door_face_H_raw", 0.0))
                x_left = ox + float(imp_door.get("x_left_mm", 0.0))
                x_right = ox + float(imp_door.get("x_right_mm", 0.0))
                d_w = max(0.0, x_right - x_left)
                if d_H <= 0.0 or d_w <= 0.0:
                    continue

                is_double_leaf = bool(imp_door.get("is_double_leaf", False))
                if is_double_leaf:
                    half = d_w / 2.0
                    leaf_w = max(0.0, half - gap)
                    if leaf_w <= 0.0:
                        continue
                    add_panel(
                        cab_node,
                        f"Porte Import G {door_idx} – {cab_label}",
                        x_left + gap,
                        d_y,
                        d_z,
                        leaf_w,
                        door_th,
                        d_H,
                        mat="mat_door",
                        rotation_deg=-ajar_angle,
                        rotation_pivot_xy=(x_left + gap, d_y),
                    )
                    add_panel(
                        cab_node,
                        f"Porte Import D {door_idx} – {cab_label}",
                        x_left + half,
                        d_y,
                        d_z,
                        leaf_w,
                        door_th,
                        d_H,
                        mat="mat_door",
                        rotation_deg=ajar_angle,
                        rotation_pivot_xy=(x_right - gap, d_y),
                    )
                    continue

                opening = str(imp_door.get("door_opening", "right")).lower()
                if opening == "left":
                    rot = -ajar_angle
                    pivot_x = x_left + gap
                else:
                    rot = ajar_angle
                    pivot_x = x_right - gap

                add_panel(
                    cab_node,
                    f"Porte Import {door_idx} – {cab_label}",
                    x_left + gap,
                    d_y,
                    d_z,
                    max(0.0, d_w - 2.0 * gap),
                    door_th,
                    d_H,
                    mat="mat_door",
                    rotation_deg=rot,
                    rotation_pivot_xy=(pivot_x, d_y),
                )

        # ── Porte(s) natives ────────────────────────────────────────────
        else:
            dp = cabinet.get("door_props", {})
            if not dp.get("has_door", False):
                dp = None
        if dp is not None:
            gap      = float(dp.get("door_gap",       2.0))
            door_th  = float(dp.get("door_thickness", 19.0))
            # Porte cache-pieds: descend de 80 mm sous le meuble.
            if dp.get("door_model") == "floor_length":
                d_H = H + 80.0 - gap
                d_z = oz - 80.0
            else:
                d_H = H - 2 * gap
                d_z = oz + gap
            # Porte positionnée en façade (y = oy − épaisseur), représentation fermée
            d_y   = oy - door_th
            is_ajar = str(door_mode).lower() == "ajar"
            ajar_angle = abs(float(door_angle_deg)) if is_ajar else 0.0

            if dp.get("door_type", "single") == "single":
                opening = str(dp.get("door_opening", "right")).lower()
                if opening == "left":
                    rot = -ajar_angle
                    pivot_x = ox + gap
                else:
                    rot = ajar_angle
                    pivot_x = ox + L - gap
                add_panel(cab_node,
                          f"Porte – {cab_label}",
                          ox + gap, d_y, d_z,
                          L - 2 * gap, door_th, d_H,
                          mat="mat_door",
                          rotation_deg=rot,
                          rotation_pivot_xy=(pivot_x, d_y))
            else:
                half = (L - 2 * gap) / 2.0
                add_panel(cab_node,
                          f"Porte Gauche – {cab_label}",
                          ox + gap, d_y, d_z,
                          half, door_th, d_H, mat="mat_door",
                          rotation_deg=-ajar_angle,
                          rotation_pivot_xy=(ox + gap, d_y))
                add_panel(cab_node,
                          f"Porte Droite – {cab_label}",
                          ox + gap + half, d_y, d_z,
                          half, door_th, d_H, mat="mat_door",
                          rotation_deg=ajar_angle,
                          rotation_pivot_xy=(ox + L - gap, d_y))

        # ── Façades de tiroir ───────────────────────────────────────────
        for j, drp in enumerate(cabinet.get("drawers", [])):
            gap      = float(drp.get("drawer_gap",          2.0))
            face_h   = float(drp.get("drawer_face_H_raw", 150.0))
            face_th  = float(drp.get("drawer_face_thickness", 19.0))
            bot_z    = oz + float(drp.get("drawer_bottom_offset", 0.0))
            d_y      = oy - face_th   # en façade, avant le caisson

            is_applique = bool(drp.get("_applique_mode", False))

            if bool(drp.get("_use_explicit_x_bounds")) and drp.get("x_left_mm") is not None and drp.get("x_right_mm") is not None:
                d_x = ox + float(drp.get("x_left_mm", 0.0))
                d_w = max(0.0, float(drp.get("x_right_mm", 0.0)) - float(drp.get("x_left_mm", 0.0)))
            elif is_applique:
                # Mode EN APPLIQUE : la façade recouvre toute la largeur du caisson (- 4 mm de jeu)
                d_w = max(0.0, L - 4.0)
                d_x = ox + 2.0
            else:
                # Largeur : utiliser la largeur intérieure par défaut
                d_w = L - 2 * tl - 2 * gap
                d_x = ox + tl + gap

            add_panel(cab_node,
                      f"Façade Tiroir {j+1} – {cab_label}",
                      d_x, d_y, bot_z,
                      d_w, face_th, face_h,
                      mat="mat_drawer")

        # ── Fileur (élément indépendant à côté du caisson) ─────────────
        fileur_w = float(door_props_for_fileur.get("fileur_width", 0) or 0)
        if fileur_w > 0:
            fil_th   = 19.0              # épaisseur panneau fileur
            fil_gap  = float(door_props_for_fileur.get("door_gap", 2.0) or 2.0)
            # Hauteur = même que la porte (H - 2*gap standard)
            fil_H    = H - 2.0 * fil_gap
            fil_z    = oz + fil_gap
            # Côté ouverture (côté libre de la porte)
            # ox et L sont déjà ajustés → le fileur est adjacent au caisson réduit
            if door_props_for_fileur.get("door_opening", "right") == "right":
                # Ouverture à droite → fileur à droite du caisson réduit
                fil_x = ox + L
            else:
                # Ouverture à gauche → fileur à gauche (ox déjà décalé)
                fil_x = ox - fileur_w
            # Le fileur est une planche de 19 mm posée en façade (même plan que la porte).
            # oy = face avant du caisson → on place la planche devant (oy - fil_th).
            fil_y = oy - fil_th
            fil_node_id = f"node_fileur_{_safe_xml_id(cab_label)}_{i+1}"
            fil_node = _add_group_node(meuble_node, fil_node_id, f"Fileur {cab_label}")
            add_panel(fil_node,
                      f"Fileur – {cab_label}",
                      fil_x, fil_y, fil_z,
                      fileur_w, fil_th, fil_H,
                      mat="mat_body")

    # ------------------------------------------------------------------ #
    #  Instance de scène                                                   #
    # ------------------------------------------------------------------ #
    scene_elem = ET.SubElement(root, "scene")
    ET.SubElement(scene_elem, "instance_visual_scene", url="#Scene")

    # ------------------------------------------------------------------ #
    #  Sérialisation XML                                                   #
    # ------------------------------------------------------------------ #
    raw_xml = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw_xml).toprettyxml(indent="  ", encoding=None)

    # minidom génère <?xml version="1.0" ?> – on ajoute l'encodage UTF-8
    lines = pretty.splitlines(keepends=True)
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="utf-8"?>\n'

    return "".join(lines).encode("utf-8")
