import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import datetime
from io import BytesIO 
import math
import copy
import json
import hashlib
import hmac
import base64
import os
import sys
import importlib 
import zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def resolve_logo_path(filename: str = "logo.png") -> str:
    candidates = [
        os.path.join(APP_DIR, filename),
        os.path.join(os.path.dirname(APP_DIR), filename),
        filename,
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return os.path.join(APP_DIR, filename)


from utils import calculate_available_space_between_horizontal_shelves
from geometry_helpers import cuboid_mesh_for, cylinder_mesh_for, add_zone_annotations_to_figure, add_hatched_zones_3d, add_zone_outlines_3d, add_zone_debug_boxes_3d, check_element_placement_validity
from excel_export import create_styled_excel
from project_definitions import get_default_dims_19, get_default_door_props_19, get_default_drawer_props_19, get_legrabox_specs, get_default_joue_props
from machining_logic import (
    calculate_origins_recursively, get_hinge_y_positions, get_mobile_shelf_holes, 
    calculate_back_panel_holes, detect_collisions, calculate_zones_from_dividers, 
    get_vertical_divider_tranche_holes, get_traverse_holes_for_divider, get_traverse_face_holes_for_divider, get_mounting_holes_for_zone_element,
    get_vertical_shelf_tranche_holes, calculate_vertical_zones_in_x_zone, calculate_all_zones_2d,
    calculate_hole_positions
)
from drawing_interface import draw_machining_view_pro_final
from state_manager import (
    initialize_session_state, get_selected_cabinet, load_save_state, add_cabinet, clear_scene, delete_selected_cabinet,
    update_selected_cabinet_dim, update_selected_cabinet_door, update_selected_cabinet_drawer,
    add_shelf_callback, update_shelf_prop, delete_shelf_callback,
    add_fixed_shelves_stack_callback,
    update_selected_cabinet_material, update_selected_cabinet_door_material, 
    update_selected_cabinet_drawer_material, update_shelf_material, update_hinge_count, update_hinge_position,
    add_vertical_divider_callback, add_vertical_divider_double_callback,
    update_vertical_divider_prop, delete_vertical_divider_callback, update_vertical_divider_material,
    add_vertical_shelf_callback, update_vertical_shelf_prop, delete_vertical_shelf_callback, update_vertical_shelf_material,
    add_drawer_callback, add_drawers_stack_callback, update_drawer_prop, delete_drawer_callback, update_drawer_material,
    get_default_debit_data, update_selected_cabinet_base_element, update_hinge_count, update_hinge_position
)
from export_manager import generate_stacked_html_plans  # import for machining plans export

st.set_page_config(page_title="KoboMeuble", layout="wide")
initialize_session_state()

# ---------------------------------------------------------------------------
# Sérialisation JSON stable (clé de détection de changement de scène)
# ---------------------------------------------------------------------------
class _DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        return super().default(obj)

def _scene_to_json(scene):
    """Retourne une chaîne JSON stable pour scene_cabinets – sert de clé de comparaison."""
    return json.dumps(scene, cls=_DateEncoder, sort_keys=True)


def _normalize_materials_list(materials):
    """Normalise une liste de matières (trim, unicité, ordre stable)."""
    if not materials:
        return []
    out = []
    seen = set()
    for mat in materials:
        mat_txt = str(mat).strip()
        if not mat_txt or mat_txt in seen:
            continue
        out.append(mat_txt)
        seen.add(mat_txt)
    return out


def _build_material_filter_key(selected_materials):
    mats = _normalize_materials_list(selected_materials)
    if not mats:
        return "__ALL__"
    return "||".join(mats)


def _sanitize_export_name(value):
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or ""))
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "Projet"


def _build_deliverables_archive(project_name, xls_data, dxf_data, dxf_filename=None, selected_materials=None):
    folder_name = f"Livrables_{_sanitize_export_name(project_name)}"
    if selected_materials:
        folder_name += "_matieres_selectionnees"

    xlsx_name = f"Projet_{_sanitize_export_name(project_name)}.xlsx"
    dxf_name = dxf_filename or f"Plans_{_sanitize_export_name(project_name)}.dxf"

    archive_buffer = BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        if xls_data:
            zip_file.writestr(f"{folder_name}/{xlsx_name}", xls_data)
        if dxf_data:
            zip_file.writestr(f"{folder_name}/{dxf_name}", dxf_data)
    archive_buffer.seek(0)
    archive_name = f"{folder_name}.zip"
    return archive_buffer.getvalue(), archive_name


def _generate_and_store_exports(all_parts, selected_materials=None):
    """Génère les exports HTML, DXF, XLS et les stocke dans session_state."""
    scene = st.session_state['scene_cabinets']
    selected_materials = _normalize_materials_list(selected_materials)
    selected_materials_set = set(selected_materials)
    parts_for_filtered_exports = all_parts
    if selected_materials_set:
        parts_for_filtered_exports = [
            part for part in all_parts
            if str(part.get("Matière", "")).strip() in selected_materials_set
        ]

    # Export HTML
    from export_manager import generate_stacked_html_plans
    html_data, html_ok = generate_stacked_html_plans(scene, list(range(len(scene))))
    st.session_state['exports_html_data'] = html_data
    st.session_state['exports_html_ok'] = html_ok

    # Export DXF
    try:
        from dxf_export import export_project_to_dxf
        logo_path = resolve_logo_path("logo.png")
        dxf_result = export_project_to_dxf(
            {
                "cabinets_data": scene,
                "indices": list(range(len(scene))),
                "project_name": st.session_state.project_name,
                "client": st.session_state.client,
                "comments": st.session_state.ref_chantier,
                "version": "V1",
                "paper_width_mm": 420.0,
                "paper_height_mm": 297.0,
                "page_margin_mm": 10.0,
                "bbox_margin_factor": 1.05,
                "text_height": 2.5,
                "dimensions_text_height": 10.0,
                "triangle_size": 8.0,
                "logo_path": logo_path,
                "presentation_cover_enabled": True,
                "presentation_cover_title": st.session_state.get("presentation_cover_title", st.session_state.project_name),
                "selected_materials": selected_materials,
            },
            mode="editable",
            force_primitives_dims=False,
            debug=False,
            debug_stage="all",
        )
        st.session_state['exports_dwg_data'] = dxf_result.dxf_bytes
        st.session_state['exports_dwg_ok'] = dxf_result.ok
        st.session_state['exports_dwg_filename'] = f"usinage_{st.session_state.project_name.replace(' ', '_')}.dxf"
    except Exception as e:
        st.session_state['exports_dwg_data'] = f"Erreur export DXF: {str(e)}".encode('utf-8')
        st.session_state['exports_dwg_ok'] = False
        st.session_state['exports_dwg_filename'] = None

    # Export XLS
    project_info_export = {
        "project_name": st.session_state.project_name,
        "client": st.session_state.client,
        "adresse_chantier": st.session_state.adresse_chantier,
        "ref_chantier": st.session_state.ref_chantier,
        "telephone": st.session_state.telephone,
        "date_souhaitee": st.session_state.date_souhaitee,
        "panneau_decor": st.session_state.panneau_decor,
        "chant_mm": st.session_state.chant_mm,
        "decor_chant": st.session_state.decor_chant,
        "corps_meuble": "Ensemble",
        "quantity": 1,
        "date": datetime.date.today().strftime("%Y-%m-%d"),
    }
    save_data_export = {
        'project_name': st.session_state.project_name,
        'scene_cabinets': scene,
    }
    st.session_state['exports_xls_data'] = create_styled_excel(
        project_info_export,
        pd.DataFrame(parts_for_filtered_exports) if parts_for_filtered_exports else pd.DataFrame(),
        save_data_export,
    )

    # Export SketchUp (Collada .dae)
    try:
        from sketchup_export import generate_sketchup_collada
        has_doors = any(bool(cab.get('door_props', {}).get('has_door', False)) for cab in scene)

        skp_closed_bytes = generate_sketchup_collada(scene, door_mode="closed")
        st.session_state['exports_skp_closed_data'] = skp_closed_bytes
        st.session_state['exports_skp_closed_ok'] = bool(skp_closed_bytes)

        if has_doors:
            skp_open_bytes = generate_sketchup_collada(scene, door_mode="ajar", door_angle_deg=50.0)
            st.session_state['exports_skp_open_data'] = skp_open_bytes
            st.session_state['exports_skp_open_ok'] = bool(skp_open_bytes)
        else:
            st.session_state['exports_skp_open_data'] = None
            st.session_state['exports_skp_open_ok'] = False

        st.session_state['exports_skp_has_doors'] = has_doors
        # Compatibilité avec l'ancien état (utilisé ailleurs)
        st.session_state['exports_skp_data'] = skp_closed_bytes
        st.session_state['exports_skp_ok'] = bool(skp_closed_bytes)
    except Exception:
        st.session_state['exports_skp_data'] = None
        st.session_state['exports_skp_ok'] = False
        st.session_state['exports_skp_has_doors'] = False
        st.session_state['exports_skp_closed_data'] = None
        st.session_state['exports_skp_closed_ok'] = False
        st.session_state['exports_skp_open_data'] = None
        st.session_state['exports_skp_open_ok'] = False

    # Marquer la scène comme générée
    st.session_state['exports_scene_json'] = _scene_to_json(scene)
    st.session_state['exports_material_filter_key'] = _build_material_filter_key(selected_materials)


def _generate_and_store_hole_counts():
    """Génère le comptage vis/tourillons par scène d'usinage et le stocke en session."""
    scene = st.session_state['scene_cabinets']
    from export_manager import get_all_machining_plans_figures

    _, hole_counts = get_all_machining_plans_figures(
        scene,
        list(range(len(scene))),
        include_hole_counts=True,
    )
    st.session_state['hole_counts_rows'] = hole_counts
    st.session_state['hole_counts_scene_json'] = _scene_to_json(scene)


def _render_exports(all_parts):
    st.markdown("---")
    st.header("Export des livrables")
    if not st.session_state['scene_cabinets']:
        st.info("Ajoutez au moins un caisson pour pouvoir générer les livrables.")
        return

    available_materials = sorted({
        str(part.get("Matière", "")).strip()
        for part in (all_parts or [])
        if str(part.get("Matière", "")).strip()
    })

    current_scene_json = _scene_to_json(st.session_state['scene_cabinets'])

    st.markdown(
        """
        <style>
        div[data-testid="stDownloadButton"] > button {
            min-height: 52px;
            font-size: 0.98rem;
            font-weight: 700;
            color: #111111;
            border-radius: 12px;
            border: 1px solid #c7d9ea;
            background: linear-gradient(180deg, #ffffff, #f4f8fc);
            box-shadow: 0 6px 18px rgba(14, 47, 79, 0.08);
        }
        div[data-testid="stDownloadButton"] > button p {
            color: #111111;
        }
        div[data-testid="stDownloadButton"] > button[kind="primary"] {
            color: #111111;
        }
        div[data-testid="stDownloadButton"] > button[kind="primary"] p {
            color: #111111;
        }
        div[data-testid="stDownloadButton"] > button:hover {
            border-color: #8fb2cf;
            background: linear-gradient(180deg, #ffffff, #edf5fc);
        }
        div[data-testid="stDownloadButton"] {
            margin-bottom: 0.45rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        top_left, top_right = st.columns([1.3, 1], gap="large")
        with top_left:
            st.text_input(
                "Titre page de garde",
                key="presentation_cover_title",
                value=st.session_state.get("presentation_cover_title", st.session_state.project_name),
                placeholder="Titre affiche sous PLANS D'USINAGES",
            )
        with top_right:
            material_scope = st.radio(
                "Téléchargement",
                options=["Toutes les matières", "Sélectionner des matières"],
                key="exports_material_scope",
                horizontal=True,
            )

        selected_export_materials = []
        if material_scope == "Sélectionner des matières":
            default_mats = _normalize_materials_list(st.session_state.get("exports_selected_materials", available_materials))
            default_mats = [m for m in default_mats if m in available_materials]
            selected_export_materials = st.multiselect(
                "Matières",
                options=available_materials,
                default=default_mats,
                key="exports_selected_materials",
                placeholder="Choisir une ou plusieurs matières",
                help="Seules ces matières seront incluses dans l'Excel et le DXF.",
            )
            if not selected_export_materials:
                st.warning("Sélectionnez au moins une matière pour générer un export filtré.")

    selected_export_materials = _normalize_materials_list(selected_export_materials)
    selected_materials_for_generation = None if material_scope == "Toutes les matières" else selected_export_materials
    selected_materials_key = _build_material_filter_key(selected_materials_for_generation)

    already_generated = (
        st.session_state.get('exports_scene_json') == current_scene_json
        and st.session_state.get('exports_material_filter_key') == selected_materials_key
    )
    scene_changed = (
        st.session_state.get('exports_scene_json') is not None
        and st.session_state.get('exports_scene_json') != current_scene_json
    )
    material_filter_changed = (
        st.session_state.get('exports_material_filter_key') is not None
        and st.session_state.get('exports_material_filter_key') != selected_materials_key
    )

    if scene_changed:
        st.warning("⚠️ Le projet a été modifié depuis la dernière génération. Regénérez les livrables pour obtenir des fichiers à jour.")
    if material_filter_changed:
        st.warning("⚠️ Le filtre matière a changé. Regénérez les livrables pour mettre à jour l'Excel et le DXF.")

    btn_label = "🔄 Regénérer les livrables" if (already_generated or scene_changed or material_filter_changed) else "⚙️ Générer les livrables"
    generated_now = False
    if st.button(btn_label, type="primary", use_container_width=True):
        if material_scope == "Sélectionner des matières" and not selected_export_materials:
            st.error("Impossible de générer: aucune matière sélectionnée.")
        else:
            with st.spinner("Génération des livrables en cours…"):
                # Recalcul forcé à partir de l'état courant (sans dépendre du cache débit)
                # pour garantir que les livrables reflètent la dernière version visible.
                fresh_parts, _ = calculate_all_project_parts()
                _generate_and_store_exports(fresh_parts, selected_materials=selected_materials_for_generation)
                generated_now = True

    if generated_now:
        already_generated = True

    if not already_generated:
        st.caption("Les livrables seront calculés uniquement au clic sur le bouton ci-dessus.")
    
    st.markdown("---")
    st.subheader("Comptage vis / tourillons")
    st.caption("Calculez le décompte à la demande, sans génération automatique.")

    hole_counts_ready = st.session_state.get('hole_counts_scene_json') == current_scene_json
    hole_counts_changed = (
        st.session_state.get('hole_counts_scene_json') is not None
        and not hole_counts_ready
    )
    if hole_counts_changed:
        st.warning("⚠️ Le projet a été modifié depuis le dernier comptage. Relancez le bouton pour mettre à jour les quantités.")

    count_btn_label = "🔄 Recalculer le comptage" if (hole_counts_ready or hole_counts_changed) else "🧮 Générer le comptage"
    if st.button(count_btn_label, key="btn_generate_hole_counts", use_container_width=True):
        with st.spinner("Calcul du nombre de vis et de tourillons..."):
            _generate_and_store_hole_counts()
            hole_counts_ready = True

    if hole_counts_ready:
        hole_rows = st.session_state.get('hole_counts_rows', [])
        if hole_rows:
            df_counts = pd.DataFrame(hole_rows)
            df_counts = df_counts.rename(columns={
                'scene': 'Scène',
                'vis': 'Vis',
                'tourillon': 'Tourillons',
            })
            total_vis = int(df_counts['Vis'].sum())
            total_tourillons = int(df_counts['Tourillons'].sum())
            st.dataframe(df_counts, hide_index=True, use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                st.metric("Total vis", total_vis)
            with c2:
                st.metric("Total tourillons", total_tourillons)
        else:
            st.info("Aucune scène d'usinage trouvée pour ce projet.")
    else:
        st.caption("Le comptage n'est lancé qu'au clic sur le bouton ci-dessus.")

    if not already_generated:
        return

    # Afficher les boutons de téléchargement avec les données stockées
    html_data = st.session_state['exports_html_data']
    html_ok = st.session_state['exports_html_ok']
    dwg_data = st.session_state['exports_dwg_data']
    dwg_ok = st.session_state['exports_dwg_ok']
    dwg_filename = st.session_state['exports_dwg_filename']
    xls_data = st.session_state['exports_xls_data']
    skp_has_doors = st.session_state.get('exports_skp_has_doors', False)
    skp_closed_data = st.session_state.get('exports_skp_closed_data')
    skp_closed_ok = st.session_state.get('exports_skp_closed_ok', False)
    skp_open_data = st.session_state.get('exports_skp_open_data')
    skp_open_ok = st.session_state.get('exports_skp_open_ok', False)
    deliverables_zip_data = None
    deliverables_zip_name = None
    if xls_data and dwg_ok and dwg_data:
        deliverables_zip_data, deliverables_zip_name = _build_deliverables_archive(
            st.session_state.project_name,
            xls_data,
            dwg_data,
            dxf_filename=dwg_filename,
            selected_materials=selected_materials_for_generation,
        )

    with st.container(border=True):
        if material_scope == "Toutes les matières":
            st.caption("Téléchargement configure pour toutes les matières.")
        else:
            st.caption(f"Téléchargement configure pour : {', '.join(selected_export_materials)}")

        if deliverables_zip_data and deliverables_zip_name:
            st.download_button(
                "⬇️ Télécharger les livrables (.zip)",
                deliverables_zip_data,
                deliverables_zip_name,
                "application/zip",
                use_container_width=True,
                type="primary",
            )
        else:
            st.info("Le téléchargement groupé sera disponible dès que l'Excel et le DXF seront tous les deux générés.")

        dl_col1, dl_col2, dl_col3, dl_col4 = st.columns([1, 1, 1, 1], gap="small")
        with dl_col1:
            if html_ok and html_data:
                st.download_button(
                    "📄 Dossier Plans (HTML)", html_data,
                    f"Dossier_{st.session_state.project_name.replace(' ', '_')}.html",
                    "text/html", use_container_width=True,
                )
            else:
                if html_data:
                    try:
                        error_html = html_data.decode('utf-8') if isinstance(html_data, bytes) else html_data
                        st.error("⚠️ Erreur lors de la génération du fichier HTML.")
                        with st.expander("Détails de l'erreur"):
                            st.markdown(error_html, unsafe_allow_html=True)
                    except Exception:
                        st.error("⚠️ Erreur lors de la génération du fichier HTML. Vérifiez la console pour plus de détails.")
                else:
                    st.error("⚠️ Erreur lors de la génération du fichier HTML. Aucune donnée générée.")

        with dl_col2:
            if xls_data:
                st.download_button(
                    "📥 Fiche de Débit (.xlsx)", xls_data,
                    f"Projet_{st.session_state.project_name.replace(' ', '_')}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        with dl_col3:
            if dwg_ok and dwg_data:
                st.download_button(
                    "📐 Plans AutoCAD (.dxf)", dwg_data,
                    dwg_filename or f"Plans_{st.session_state.project_name.replace(' ', '_')}.dxf",
                    "application/dxf", use_container_width=True,
                )
            else:
                if dwg_data:
                    try:
                        dwg_error = dwg_data.decode('utf-8') if isinstance(dwg_data, bytes) else str(dwg_data)
                        st.warning(f"⚠️ Export DXF indisponible : {dwg_error}")
                    except Exception:
                        st.warning("⚠️ Export DXF indisponible.")
                else:
                    st.warning("⚠️ Export DXF indisponible.")

        with dl_col4:
            if skp_closed_ok and skp_closed_data:
                st.download_button(
                    "🏗️ SketchUp portes fermées (.dae)",
                    skp_closed_data,
                    f"Scene3D_Ferme_{st.session_state.project_name.replace(' ', '_')}.dae",
                    "model/vnd.collada+xml",
                    use_container_width=True,
                )
            else:
                st.warning("⚠️ Export SketchUp indisponible.")

            if skp_has_doors:
                if skp_open_ok and skp_open_data:
                    st.download_button(
                        "🚪 SketchUp portes entre-ouvertes (.dae)",
                        skp_open_data,
                        f"Scene3D_Entreouverte_{st.session_state.project_name.replace(' ', '_')}.dae",
                        "model/vnd.collada+xml",
                        use_container_width=True,
                    )
                else:
                    st.warning("⚠️ Export SketchUp portes entre-ouvertes indisponible.")


def get_automatic_edge_banding(part_name):
    name = part_name.lower()
    if "etagère" in name or "etagere" in name: return True, False, False, False
    elif "fond" in name or "dos" in name:
        if "façade" in name or "face" in name: return True, True, True, True
        return False, False, False, False
    elif "traverse" in name: return True, True, False, False
    else: return True, True, True, True

def has_holes_for_piece(ref_key, cabinet, piece_data=None):
    """
    Vérifie si une pièce a des trous sur sa feuille d'usinage.
    Retourne True si la pièce a au moins un trou.
    """
    dims = cabinet['dims']
    
    # Montants principaux : toujours des trous (vis et tourillons)
    if "Montant" in ref_key and ("Gauche" in ref_key or "Droit" in ref_key):
        return True
    
    # Traverses : toujours des trous (vis et tourillons pour assemblage)
    if "Traverse" in ref_key:
        return True
    
    # Portes : toujours des trous (charnières)
    if "Porte" in ref_key:
        return True
    
    # Étagères fixes : toujours des trous (assemblage)
    if "Etagère" in ref_key and "Fixe" in ref_key:
        return True
    
    # Étagères mobiles : vérifier si elles ont des trous de taquets
    if "Etagère" in ref_key and "Mobile" in ref_key:
        if 'shelves' in cabinet:
            for s in cabinet['shelves']:
                if s.get('shelf_type') == 'mobile':
                    # Les étagères mobiles ont toujours des trous pour les taquets
                    return True
    
    # Montants secondaires : toujours des trous
    if "Montant Secondaire" in ref_key or "Divider" in ref_key:
        return True
    
    # Tiroirs : toujours des trous (assemblage)
    if "Tiroir" in ref_key or "Façade" in ref_key:
        return True
    
    # Fonds : vérifier s'ils ont des trous (vis de fixation)
    if "Fond" in ref_key:
        # Les fonds ont généralement des trous pour la fixation
        return True
    
    # Par défaut, pas de trous
    return False

def calculate_all_project_parts():
    all_parts = []
    lettre_code = 65 
    shelf_dims_cache = {} 

    for i, cabinet in enumerate(st.session_state['scene_cabinets']):
        dims = cabinet['dims']
        debit_data = cabinet['debit_data']
        fileur_w = float(cabinet.get('door_props', {}).get('fileur_width', 0) or 0.0)
        L_eff = max(0.0, float(dims['L_raw']) - fileur_w)

        # Les zones de calcul (portes/tiroirs/étagères) doivent suivre la largeur
        # effective du caisson quand un fileur est posé.
        cabinet_for_zones = copy.deepcopy(cabinet)
        cabinet_for_zones['dims']['L_raw'] = L_eff
        zones_for_parts = calculate_all_zones_2d(cabinet_for_zones)
        
        t_lr, t_tb, t_fb = dims['t_lr_raw'], dims['t_tb_raw'], dims['t_fb_raw']
        h_side = dims['H_raw'] 
        L_traverse = max(0.0, L_eff - 2 * t_lr)
        dim_fond_vertical = dims['H_raw'] - 2.0
        dim_fond_horizontal = max(0.0, L_eff - 2.0)
        
        panel_dims = {
            "Traverse Bas": (L_traverse, dims['W_raw'], t_tb),
            "Traverse Haut": (L_traverse, dims['W_raw'], t_tb),
            "Montant Gauche": (h_side, dims['W_raw'], t_lr),
            "Montant Droit": (h_side, dims['W_raw'], t_lr),
            "Fond": (dim_fond_vertical, dim_fond_horizontal, t_fb)
        }
        
        # Récupérer les préférences des éléments de base (par défaut tous activés)
        base_el = cabinet.get('base_elements', {
            'has_back_panel': True,
            'has_left_upright': True,
            'has_right_upright': True,
            'has_bottom_traverse': True,
            'has_top_traverse': True
        })
        
        # 1. Structure
        for piece in debit_data:
            ref_full = piece.get("Référence Pièce", "")
            ref_key = ref_full.split(' (')[0].strip()
            
            # Vérifier si l'élément doit être inclus
            should_include = True
            if "Traverse Bas" in ref_key and not base_el.get('has_bottom_traverse', True):
                should_include = False
            elif "Traverse Haut" in ref_key and not base_el.get('has_top_traverse', True):
                should_include = False
            elif "Montant Gauche" in ref_key and not base_el.get('has_left_upright', True):
                should_include = False
            elif "Montant Droit" in ref_key and not base_el.get('has_right_upright', True):
                should_include = False
            elif "Fond" in ref_key and not base_el.get('has_back_panel', True):
                should_include = False
            
            if not should_include:
                continue
            
            new_piece = piece.copy()
            new_piece['Lettre'] = f"C{i}-{chr(lettre_code)}"
            lettre_code += 1
            new_piece["Référence Pièce"] = ref_full 
            new_piece["Matière"] = cabinet.get('material_body', 'Matière Corps')
            new_piece["Caisson"] = f"C{i}"
            # Quantité : par défaut 1 si non précisé
            new_piece["Qté"] = piece.get("Qté", 1)
            # Pour le fond : mettre "CF plan" (le fond a toujours des trous de fixation)
            if "Fond" in ref_key:
                new_piece["Usinage"] = "CF plan"
            # Vérifier si la pièce a des trous pour mettre "CF plan"
            elif has_holes_for_piece(ref_key, cabinet, new_piece):
                new_piece["Usinage"] = "CF plan"
            else:
                new_piece["Usinage"] = new_piece.get("Usinage", "")
            # Chant : privilégier les choix utilisateur s'ils existent, sinon utiliser l'automatique
            cav_auto, car_auto, cg_auto, cd_auto = get_automatic_edge_banding(ref_key)
            cav = piece.get("Chant Avant", cav_auto)
            car = piece.get("Chant Arrière", car_auto)
            cg  = piece.get("Chant Gauche", cg_auto)
            cd  = piece.get("Chant Droit", cd_auto)
            new_piece["Chant Avant"] = bool(cav)
            new_piece["Chant Arrière"] = bool(car)
            new_piece["Chant Gauche"] = bool(cg)
            new_piece["Chant Droit"] = bool(cd)

            match_found = False
            for key, dims_tuple in panel_dims.items():
                if key in ref_key:
                    new_piece["Longueur (mm)"] = dims_tuple[0]; new_piece["Largeur (mm)"] = dims_tuple[1]; new_piece["Epaisseur"] = dims_tuple[2]
                    match_found = True; break
            if not match_found and "Fond" in ref_key:
                    new_piece["Longueur (mm)"] = dim_fond_vertical; new_piece["Largeur (mm)"] = dim_fond_horizontal; new_piece["Epaisseur"] = t_fb
            all_parts.append(new_piece)

        # Plinthe (débit uniquement) si l'option pieds est activée.
        if st.session_state.get('has_feet', False):
            plinthe_longueur = max(0.0, L_traverse + 100.0)
            all_parts.append({
                "Lettre": f"C{i}-PL",
                "Référence Pièce": f"Plinthe (C{i})",
                "Matière": cabinet.get('material_body', 'Matière Corps'),
                "Caisson": f"C{i}",
                "Qté": 1,
                "Longueur (mm)": plinthe_longueur,
                "Largeur (mm)": 80.0,
                "Epaisseur": 19.0,
                "Chant Avant": False,
                "Chant Arrière": False,
                "Chant Gauche": False,
                "Chant Droit": False,
                "Usinage": "",
            })

        # Joues manuelles: présentes dans le débit, jamais dans les exportations AutoCAD.
        joues = cabinet.get('joues', {}) or {}
        for joue_key, joue_label in [
            ('gauche', 'Joue gauche'),
            ('droite', 'Joue droite'),
            ('dessus', 'Joue dessus'),
            ('dessous', 'Joue dessous'),
        ]:
            joue = joues.get(joue_key, get_default_joue_props())
            if not bool(joue.get('enabled', False)):
                continue
            all_parts.append({
                "Lettre": f"C{i}-J{joue_key[:1].upper()}",
                "Référence Pièce": f"{joue_label} (C{i})",
                "Matière": str(joue.get('material', 'Matière Corps')),
                "Caisson": f"C{i}",
                "Qté": 1,
                "Longueur (mm)": float(joue.get('length', 0.0) or 0.0),
                "Largeur (mm)": float(joue.get('width', 0.0) or 0.0),
                "Epaisseur": float(joue.get('thickness', 0.0) or 0.0),
                "Chant Avant": False,
                "Chant Arrière": False,
                "Chant Gauche": False,
                "Chant Droit": False,
                "Usinage": "",
            })
        
        # 2. Porte
        if cabinet['door_props']['has_door']:
            dp = cabinet['door_props']
            dH = dims['H_raw'] - (2 * dp['door_gap']) 
            if dp.get('door_model') == 'floor_length':
                dH += 80.0
            
            # Vérifier si une zone est assignée
            zone_id = dp.get('zone_id', None)
            if zone_id is not None and zone_id < len(zones_for_parts):
                zone = zones_for_parts[zone_id]
                dW = (zone['x_max'] - zone['x_min']) - (2 * dp['door_gap'])
            else:
                dW = L_eff - (2 * dp['door_gap']) if dp.get('door_type') == 'single' else (L_eff - 2*dp['door_gap'])/2
            
            cav, car, cg, cd = get_automatic_edge_banding("Porte")
            porte_ref = f"Porte (C{i})"
            usinage_porte = "CF plan" if has_holes_for_piece("Porte", cabinet) else ""
            all_parts.append({"Lettre": f"C{i}-P", "Référence Pièce": porte_ref, "Matière": dp.get('material', 'Matière Porte'), "Caisson": f"C{i}", "Qté": 1 if dp.get('door_type')=='single' else 2, "Longueur (mm)": dH, "Largeur (mm)": dW, "Epaisseur": dp.get('door_thickness', 19.0), "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd, "Usinage": usinage_porte})

        # 3. Tiroirs (tous les tiroirs de la liste) - dimensions adaptées à la zone
        if 'drawers' in cabinet and cabinet['drawers']:
            legrabox_specs = get_legrabox_specs()
            for drawer_idx, drp in enumerate(cabinet['drawers']):
                drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                tech_type = drp.get('drawer_tech_type', 'K')
                
                # Largeur utile du tiroir en fonction de la zone
                zone_id = drp.get('zone_id', None)
                gap_mm = drp.get('drawer_gap', 2.0)
                t_lr = dims['t_lr_raw']
                
                if zone_id is not None and zone_id < len(zones_for_parts):
                    zone = zones_for_parts[zone_id]
                    zone_width_total = zone['x_max'] - zone['x_min']  # Largeur totale incluant chants
                    zone_width_interior = zone_width_total - (2 * t_lr)  # Largeur intérieure
                else:
                    zone_width_total = L_eff
                    zone_width_interior = L_eff - (2 * t_lr)
                
                # Dimensionnement selon le système
                if drawer_system == 'LÉGRABOX':
                    # LÉGRABOX : Face = largeur totale de la zone (incluant chants)
                    drawer_face_width = zone_width_total - (2 * gap_mm)
                    # Dos : largeur intérieure - 38mm
                    drawer_back_width = max(0.0, zone_width_interior - 38.0)
                    # Fond : largeur intérieure - 35mm, profondeur intérieure - 10mm
                    t_fb_raw = float(dims.get('t_fb_raw', 0.0))
                    zone_depth_interior = dims['W_raw'] - (2 * t_lr)  # Profondeur intérieure
                    drawer_bottom_width = max(0.0, zone_width_interior - 35.0)
                    drawer_bottom_depth = max(0.0, zone_depth_interior - 10.0)
                    # Hauteur dos selon modèle LÉGRABOX
                    legrabox_spec = legrabox_specs.get(tech_type, legrabox_specs['K'])
                    fixed_back_h = legrabox_spec['back_height']
                elif drawer_system == 'ANGLAISE':
                    # ANGLAISE : 2 mm de jeu de chaque côté par rapport aux montants adjacents
                    drawer_face_width = max(0.0, zone_width_total - 4.0)
                    drawer_back_width = max(0.0, drawer_face_width - 40.0)
                    back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                    fixed_back_h = back_height_map.get(tech_type, 116.0)
                    t_fb_raw = float(dims.get('t_fb_raw', 0.0))
                    drawer_bottom_width = max(0.0, drawer_face_width - 49.0)
                    drawer_bottom_depth = float(dims['W_raw']) - (20.0 + t_fb_raw)
                else:
                    # TANDEMBOX : logique existante
                    drawer_face_width = zone_width_total - (2 * gap_mm)
                    drawer_back_width = max(0.0, drawer_face_width - 40.0)
                    back_height_map = {'N': 69.0, 'M': 84.0, 'K': 116.0, 'D': 199.0}
                    fixed_back_h = back_height_map.get(tech_type, 116.0)
                    t_fb_raw = float(dims.get('t_fb_raw', 0.0))
                    drawer_bottom_width = max(0.0, drawer_face_width - 49.0)
                    drawer_bottom_depth = float(dims['W_raw']) - (20.0 + t_fb_raw)
                
                cav, car, cg, cd = get_automatic_edge_banding("Façade")
                facade_ref = f"Façade Tiroir {drawer_idx+1} (C{i})"
                usinage_facade = "CF plan" if has_holes_for_piece("Façade Tiroir", cabinet) else ""
                all_parts.append({
                    "Lettre": f"C{i}-TF{drawer_idx+1}",
                    "Référence Pièce": facade_ref,
                    "Matière": drp.get('material', 'Matière Tiroir'),
                    "Caisson": f"C{i}",
                    "Qté": 1,
                    "Longueur (mm)": drp.get('drawer_face_H_raw', 150.0),
                    "Largeur (mm)": drawer_face_width,
                    "Epaisseur": drp.get('drawer_face_thickness', 19.0),
                    "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd,
                    "Usinage": usinage_facade
                })
                
                # Dos du tiroir
                cav, car, cg, cd = get_automatic_edge_banding("Tiroir Dos")
                dos_ref = f"Tiroir Dos {drawer_idx+1} (C{i})"
                usinage_dos = "CF plan" if has_holes_for_piece("Tiroir Dos", cabinet) else ""
                all_parts.append({
                    "Lettre": f"C{i}-TD{drawer_idx+1}",
                    "Référence Pièce": dos_ref,
                    "Matière": drp.get('material_inner', cabinet.get('material_body', 'Matière Corps')),
                    "Caisson": f"C{i}",
                    "Qté": 1,
                    "Longueur (mm)": fixed_back_h,
                    "Largeur (mm)": drawer_back_width,
                    "Epaisseur": float(drp.get('inner_thickness', 16.0)),
                    "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd,
                    "Usinage": usinage_dos
                })
                
                # Fond du tiroir
                cav, car, cg, cd = get_automatic_edge_banding("Tiroir Fond")
                fond_ref = f"Tiroir Fond {drawer_idx+1} (C{i})"
                usinage_fond_base = "Feuillure G/D" if drawer_system == 'LÉGRABOX' else ""
                usinage_fond = "CF plan" if has_holes_for_piece("Tiroir Fond", cabinet) else usinage_fond_base
                all_parts.append({
                    "Lettre": f"C{i}-TFD{drawer_idx+1}",
                    "Référence Pièce": fond_ref,
                    "Matière": drp.get('material_inner', cabinet.get('material_body', 'Matière Corps')),
                    "Caisson": f"C{i}",
                    "Qté": 1,
                    "Longueur (mm)": drawer_bottom_width,
                    "Largeur (mm)": drawer_bottom_depth,
                    "Epaisseur": float(drp.get('inner_thickness', 16.0)),
                    "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd,
                    "Usinage": usinage_fond
                })
            
        # 4. Étagères (CORRIGÉ ICI POUR USINAGE ET REGROUPEMENT)
        # Dictionnaire pour regrouper les étagères identiques : clé = (dim_L, dim_W, épaisseur, matière, type, usinage)
        shelves_grouped = {}
        
        if 'shelves' in cabinet:
            for s_idx, s in enumerate(cabinet['shelves']):
                s_type = s.get('shelf_type', 'mobile')
                s_th = float(s.get('thickness', 19.0))
                # Règle demandée: étagère = dimensions exactes de traverse du caisson.
                dim_L = L_traverse
                dim_W = dims['W_raw']
                
                shelf_dims_cache[f"C{i}_S{s_idx}"] = (dim_L, dim_W)
                
                cav, car, cg, cd = get_automatic_edge_banding("Etagère")
                
                # --- MODIFICATION DEMANDÉE : USINAGE ---
                # Vérifier si l'étagère a des trous
                shelf_ref = f"Etagère {s_type.capitalize()}"
                usinage_txt = "CF plan" if has_holes_for_piece(shelf_ref, cabinet) else ""
                
                # Clé pour regrouper : dimensions, épaisseur, matière, type, usinage
                shelf_key = (round(dim_L, 1), round(dim_W, 1), round(s_th, 1), 
                           s.get('material', 'Matière Étagère'), s_type, usinage_txt)
                
                if shelf_key in shelves_grouped:
                    # Incrémenter la quantité
                    shelves_grouped[shelf_key]['Qté'] += 1
                    # Ajouter le caisson à la liste des caissons si pas déjà présent
                    if f"C{i}" not in shelves_grouped[shelf_key]['Caissons']:
                        shelves_grouped[shelf_key]['Caissons'].append(f"C{i}")
                else:
                    # Première occurrence de cette étagère
                    shelves_grouped[shelf_key] = {
                        "Lettre": f"C{i}-E{s_idx+1}",  # Garder la première lettre rencontrée
                        "Référence Pièce": f"Etagère {s_type.capitalize()}",
                        "Matière": s.get('material', 'Matière Étagère'),
                        "Caissons": [f"C{i}"],
                        "Qté": 1,
                        "Longueur (mm)": dim_L,
                        "Largeur (mm)": dim_W,
                        "Epaisseur": s_th,
                        "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd,
                        "Usinage": usinage_txt
                    }
        
        # Ajouter les étagères regroupées à all_parts
        for shelf_key, shelf_data in shelves_grouped.items():
            # Construire la référence pièce avec les caissons
            shelf_type = shelf_data['Référence Pièce'].split()[-1]  # "Mobile" ou "Fixe"
            caissons_list = shelf_data['Caissons']
            if len(caissons_list) > 1:
                shelf_data["Référence Pièce"] = f"Etagère {shelf_type} ({', '.join(caissons_list)})"
                shelf_data["Caisson"] = ', '.join(caissons_list)  # Champ "Caisson" avec tous les caissons
            else:
                shelf_data["Référence Pièce"] = f"Etagère {shelf_type} ({caissons_list[0]})"
                shelf_data["Caisson"] = caissons_list[0]  # Champ "Caisson" avec un seul caisson
            
            # Retirer 'Caissons' du dictionnaire avant d'ajouter à all_parts
            shelf_data.pop('Caissons')
            all_parts.append(shelf_data)
        
        # 5. Montants verticaux secondaires
        if 'vertical_dividers' in cabinet and cabinet['vertical_dividers']:
            for div_idx, div in enumerate(cabinet['vertical_dividers']):
                div_th = div.get('thickness', 19.0)
                div_h = h_side - 2 * t_tb  # Hauteur entre traverses
                div_w = dims['W_raw']  # Largeur (profondeur)
                
                cav, car, cg, cd = get_automatic_edge_banding("Montant")
                divider_ref = f"Montant Secondaire {div_idx+1} (C{i})"
                usinage_divider = "CF plan" if has_holes_for_piece("Montant Secondaire", cabinet) else ""
                all_parts.append({
                    "Lettre": f"C{i}-MS{div_idx+1}",
                    "Référence Pièce": divider_ref,
                    "Matière": div.get('material', cabinet.get('material_body', 'Matière Corps')),
                    "Caisson": f"C{i}",
                    "Qté": 1,
                    "Longueur (mm)": div_h,
                    "Largeur (mm)": div_w,
                    "Epaisseur": div_th,
                    "Chant Avant": cav, "Chant Arrière": car, "Chant Gauche": cg, "Chant Droit": cd,
                    "Usinage": usinage_divider
                })
            
    return all_parts, shelf_dims_cache


def get_cached_project_parts():
    """Calcule la feuille de debit seulement quand la scene change."""
    PARTS_CACHE_SCHEMA_VERSION = "v3_plinthe_cachepied_80"
    scene_json = _scene_to_json(st.session_state['scene_cabinets'])
    if (st.session_state.get('parts_scene_json') != scene_json
            or st.session_state.get('parts_cache_schema_version') != PARTS_CACHE_SCHEMA_VERSION):
        st.session_state['parts_cache'] = calculate_all_project_parts()
        st.session_state['parts_scene_json'] = scene_json
        st.session_state['parts_cache_schema_version'] = PARTS_CACHE_SCHEMA_VERSION
    return st.session_state.get('parts_cache', ([], {}))

# Fonction pour charger le logo en base64
def load_image_base64(filename):
    """Charge une image et la convertit en base64"""
    try:
        from PIL import Image
        import io
        candidates = [filename]
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, filename))
        candidates.append(os.path.join(os.path.dirname(script_dir), filename))
        final_path = None
        for path in candidates:
            if os.path.exists(path):
                final_path = path
                break
        if not final_path:
            return None
        img = Image.open(final_path)
        output_buffer = io.BytesIO()
        img.save(output_buffer, format="PNG")
        encoded = base64.b64encode(output_buffer.getvalue()).decode()
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None

# Charger le logo
logo_base64 = load_image_base64("logo.png")


def _get_expected_password() -> str:
    """Lit le mot de passe depuis secrets, sinon utilise la valeur par défaut."""
    secret_pwd = None
    if hasattr(st, "secrets"):
        try:
            secret_pwd = st.secrets.get("access_password")
        except FileNotFoundError:
            secret_pwd = None
        except Exception:
            secret_pwd = None
    return str(secret_pwd).strip() if secret_pwd else "pascal"


def _is_password_valid(typed_password: str) -> bool:
    expected_password = _get_expected_password()
    typed_hash = hashlib.sha256((typed_password or "").encode("utf-8")).hexdigest()
    expected_hash = hashlib.sha256(expected_password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(typed_hash, expected_hash)


def _render_login_page(logo_data: str | None) -> None:
    st.markdown(
        """
        <style>
        .main .block-container {
            padding-top: 1rem;
        }
        .stApp {
            background:
                radial-gradient(1200px 500px at 5% -10%, rgba(20, 68, 106, 0.26), transparent 60%),
                radial-gradient(900px 500px at 95% 0%, rgba(152, 104, 47, 0.2), transparent 55%),
                linear-gradient(180deg, #f3f7fb 0%, #e8eef5 100%);
        }
        .kb-auth-badge {
            display: inline-block;
            padding: 0.3rem 0.65rem;
            border-radius: 999px;
            background: #dce9f7;
            color: #1a4a73;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .kb-auth-title {
            color: #14324c;
            margin: 0.65rem 0 0.3rem 0;
            font-size: 2.05rem;
            font-weight: 800;
            line-height: 1.1;
        }
        .kb-auth-sub {
            color: #37526a;
            margin-bottom: 1.1rem;
            font-size: 0.98rem;
        }
        .kb-auth-logo {
            width: 94px;
            height: auto;
            display: block;
            margin: 0 auto 0.5rem auto;
            margin-bottom: 0.5rem;
            filter: drop-shadow(0 7px 14px rgba(13, 47, 80, 0.16));
        }
        .kb-auth-offset {
            margin-top: 8vh;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    left, center, right = st.columns([1, 1.35, 1])
    with center:
        st.markdown('<div class="kb-auth-offset">', unsafe_allow_html=True)
        if logo_data:
            st.markdown(f'<img class="kb-auth-logo" src="{logo_data}" alt="Logo" />', unsafe_allow_html=True)
        st.markdown('<span class="kb-auth-badge">Acces securise</span>', unsafe_allow_html=True)
        st.markdown('<h1 class="kb-auth-title">KoboMeuble Studio</h1>', unsafe_allow_html=True)
        st.markdown(
            '<p class="kb-auth-sub">Plateforme de conception et de preparation de plans techniques. Entrez le mot de passe pour acceder a l\'interface.</p>',
            unsafe_allow_html=True,
        )

        with st.form("kb_login_form", clear_on_submit=False):
            typed_password = st.text_input("Mot de passe", type="password", placeholder="Saisir le mot de passe")
            submit = st.form_submit_button("Acceder a l'interface", use_container_width=True, type="primary")

        if submit:
            if _is_password_valid(typed_password):
                st.session_state["auth_granted"] = True
                st.session_state["auth_error"] = False
                st.rerun()
            else:
                st.session_state["auth_error"] = True

        if st.session_state.get("auth_error", False):
            st.error("Mot de passe incorrect.")
        st.markdown('</div>', unsafe_allow_html=True)


def ensure_authenticated(logo_data: str | None) -> None:
    if "auth_granted" not in st.session_state:
        st.session_state["auth_granted"] = False
    if "auth_error" not in st.session_state:
        st.session_state["auth_error"] = False

    if not st.session_state["auth_granted"]:
        _render_login_page(logo_data)
        st.stop()


ensure_authenticated(logo_base64)

# En-tête et styles UI
if logo_base64:
    header_html = f"""
    <style>
    :root {{
        --kb-navy: #0e2f4f;
        --kb-blue: #1f5d8c;
        --kb-ice: #f4f8fc;
        --kb-border: #cddceb;
        --kb-text: #14324c;
    }}
    .main-header {{
        background: linear-gradient(120deg, rgba(22, 58, 92, 0.62), rgba(47, 94, 131, 0.54));
        padding: 1.1rem 1.4rem;
        border-radius: 14px;
        margin-bottom: 0.9rem;
        color: white;
        text-align: center;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.9rem;
        box-shadow: 0 10px 24px rgba(14, 47, 79, 0.18);
    }}
    .main-header h1 {{
        color: white;
        margin: 0;
        font-size: 2.1rem;
        font-weight: bold;
    }}
    .main-header img {{
        height: 52px;
        width: auto;
    }}
    .kb-roadmap {{
        background: var(--kb-ice);
        border: 1px solid var(--kb-border);
        border-radius: 12px;
        padding: 0.75rem 0.95rem;
        margin-bottom: 1rem;
        color: var(--kb-text);
    }}
    .kb-roadmap h3 {{
        margin: 0 0 0.45rem 0;
        color: var(--kb-navy);
        font-size: 1.02rem;
    }}
    .kb-roadmap p {{
        margin: 0;
        font-size: 0.92rem;
        line-height: 1.4;
    }}
    .kb-step {{
        background: linear-gradient(180deg, #ffffff, #f8fbff);
        border: 1px solid var(--kb-border);
        border-left: 5px solid var(--kb-blue);
        border-radius: 10px;
        padding: 0.55rem 0.75rem;
        margin: 0.55rem 0 0.45rem 0;
        color: var(--kb-text);
        font-weight: 700;
        font-size: 0.95rem;
    }}
    .kb-note {{
        color: #35536e;
        font-size: 0.86rem;
        margin: 0.1rem 0 0.55rem 0;
    }}
    .stTabs [data-baseweb="tab"] {{
        font-weight: 700;
    }}
    </style>
    <div class="main-header">
        <img src="{logo_base64}" alt="Logo KoboMeuble" />
        <h1>KoboMeuble</h1>
    </div>
    """
else:
    header_html = """
    <style>
    :root {
        --kb-navy: #0e2f4f;
        --kb-blue: #1f5d8c;
        --kb-ice: #f4f8fc;
        --kb-border: #cddceb;
        --kb-text: #14324c;
    }
    .main-header {
        background: linear-gradient(120deg, rgba(22, 58, 92, 0.62), rgba(47, 94, 131, 0.54));
        padding: 1.1rem 1.4rem;
        border-radius: 14px;
        margin-bottom: 0.9rem;
        color: white;
        text-align: center;
        box-shadow: 0 10px 24px rgba(14, 47, 79, 0.18);
    }
    .main-header h1 {
        color: white;
        margin: 0;
        font-size: 2.1rem;
        font-weight: bold;
    }
    .kb-roadmap {
        background: var(--kb-ice);
        border: 1px solid var(--kb-border);
        border-radius: 12px;
        padding: 0.75rem 0.95rem;
        margin-bottom: 1rem;
        color: var(--kb-text);
    }
    .kb-roadmap h3 {
        margin: 0 0 0.45rem 0;
        color: var(--kb-navy);
        font-size: 1.02rem;
    }
    .kb-roadmap p {
        margin: 0;
        font-size: 0.92rem;
        line-height: 1.4;
    }
    .kb-step {
        background: linear-gradient(180deg, #ffffff, #f8fbff);
        border: 1px solid var(--kb-border);
        border-left: 5px solid var(--kb-blue);
        border-radius: 10px;
        padding: 0.55rem 0.75rem;
        margin: 0.55rem 0 0.45rem 0;
        color: var(--kb-text);
        font-weight: 700;
        font-size: 0.95rem;
    }
    .kb-note {
        color: #35536e;
        font-size: 0.86rem;
        margin: 0.1rem 0 0.55rem 0;
    }
    .stTabs [data-baseweb="tab"] {
        font-weight: 700;
    }
    </style>
    <div class="main-header">
        <h1>KoboMeuble</h1>
    </div>
    """

st.markdown(header_html, unsafe_allow_html=True)

col1, col2 = st.columns([1, 2])
selected_cab = get_selected_cabinet()

with col1:
    st.header("Projet et caissons")
    st.caption("Cette colonne sert a renseigner le projet, assembler les caissons, puis editer leurs options.")
    tab_assembly, tab_edit = st.tabs(["Etape 1 - Projet et Assemblage", "Etape 2 - Edition détaillée"])

    with tab_assembly:
        st.markdown('<div class="kb-step">Etape 1A - Informations générales du projet</div>', unsafe_allow_html=True)
        st.markdown('<p class="kb-note">Ces informations servent au dossier de plans et à la fiche de débit.</p>', unsafe_allow_html=True)
        # CSS pour égaliser les largeurs des champs
        st.markdown("""
        <style>
        div[data-testid="column"]:nth-of-type(1) input,
        div[data-testid="column"]:nth-of-type(2) input,
        div[data-testid="column"]:nth-of-type(1) div[data-baseweb="input"],
        div[data-testid="column"]:nth-of-type(2) div[data-baseweb="input"] {
            width: 100% !important;
        }
        </style>
        """, unsafe_allow_html=True)
        with st.form("project_info_form"):
            c1, c2 = st.columns(2)
            with c1:
                st.text_input("Nom du Projet", key='project_name')
            with c2:
                st.date_input("Date souhaitée", key='date_souhaitee', value=datetime.date.today())
            st.text_input("Client", key='client')
            st.text_input("Adresse Chantier", key='adresse_chantier')
            st.text_input("Réf. Chantier", key='ref_chantier')
            st.text_input("Téléphone / Mail", key='telephone')
            st.markdown("##### Matériaux (Défaut)")
            st.text_input("Panneau / Décor", key='panneau_decor')
            st.text_input("Chant (mm)", key='chant_mm')
            st.text_input("Décor Chant", key='decor_chant')
            st.form_submit_button("💾 Enregistrer les informations projet", use_container_width=True)
        st.markdown("---")
        st.markdown('<div class="kb-step">Etape 1B - Charger un projet existant (optionnel)</div>', unsafe_allow_html=True)
        st.markdown('<p class="kb-note">Si vous avez déjà un projet, chargez votre fichier XLS pour reprendre le travail.</p>', unsafe_allow_html=True)
        st.info("La sauvegarde du projet reste incluse dans le téléchargement XLS.")
        st.file_uploader("Charger un Projet (.xlsx)", type=["xlsx"], key="file_loader", on_change=load_save_state)
        st.markdown("---")
        st.markdown('<div class="kb-step">Etape 1C - Assemblage de la scène</div>', unsafe_allow_html=True)
        st.markdown('<p class="kb-note">Commencez par le caisson principal puis ajoutez les caissons secondaires avec le panneau interactif.</p>', unsafe_allow_html=True)
        st.button("1. Ajouter le Caisson Central", on_click=add_cabinet, args=('central',), disabled=bool(st.session_state['scene_cabinets']), use_container_width=True)

        st.markdown("##### Ajout rapide des caissons secondaires")
        st.caption("Sélectionnez un caisson de référence puis cliquez sur une direction autour du carré central.")
        if st.session_state['scene_cabinets']:
            opts = [f"{i}: {c['name']}" for i, c in enumerate(st.session_state['scene_cabinets'])]
            st.selectbox("Caisson de référence", options=range(len(opts)), format_func=lambda x: opts[x], key='base_cabinet_index')

            with st.container(border=True):
                top_cols = st.columns([1, 1, 1])
                with top_cols[1]:
                    st.button("⬆️ Ajouter en haut", key="quick_add_up", on_click=add_cabinet, args=('up',), use_container_width=True)

                mid_cols = st.columns([1, 1, 1])
                with mid_cols[0]:
                    st.button("⬅️ Ajouter à gauche", key="quick_add_left", on_click=add_cabinet, args=('left',), use_container_width=True)
                with mid_cols[1]:
                    st.markdown(
                        """
                        <div style="
                            height: 76px;
                            border: 2px solid #1f5d8c;
                            border-radius: 10px;
                            background: linear-gradient(180deg, #ffffff, #eef4fb);
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            text-align: center;
                            color: #1f5d8c;
                            font-weight: 700;
                        ">
                            <span style="display:block; width:100%;">Caisson sélectionné</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with mid_cols[2]:
                    st.button("➡️ Ajouter à droite", key="quick_add_right", on_click=add_cabinet, args=('right',), use_container_width=True)

        st.button("Vider la scène 🗑️", on_click=clear_scene, use_container_width=True)
        st.markdown("---")
        st.markdown('<div class="kb-step">Etape 1D - Options globales</div>', unsafe_allow_html=True)
        st.markdown('<p class="kb-note">Activez les pieds uniquement si votre meuble est posé au sol.</p>', unsafe_allow_html=True)
        st.toggle("Ajouter des pieds", key='has_feet')
        if st.session_state.has_feet:
            feet_map = {"20": 20.0, "80-100": 100.0, "110-120": 120.0}
            sel_feet = st.selectbox("Hauteur (mm)", options=["20", "80-100", "110-120"], index=1)
            st.session_state.foot_height = feet_map[sel_feet]
            st.number_input("Diamètre pieds (mm)", min_value=10.0, key='foot_diameter', value=50.0, format="%.0f", step=1.0)

    with tab_edit:
        st.markdown('<div class="kb-step">Etape 2A - Choix du caisson a modifier</div>', unsafe_allow_html=True)
        st.markdown('<p class="kb-note">Selectionnez un caisson puis reglez ses dimensions, ses portes, ses tiroirs et ses etageres.</p>', unsafe_allow_html=True)
        if not st.session_state['scene_cabinets']:
            st.info("Ajoutez un caisson central pour commencer l'édition.")
        else:
            opts = [f"{i}: {c['name']}" for i, c in enumerate(st.session_state['scene_cabinets'])]
            st.selectbox("Éditer le caisson :", options=range(len(opts)), format_func=lambda x: opts[x], key='selected_cabinet_index')
            st.button("Supprimer le Caisson", on_click=delete_selected_cabinet, use_container_width=True, type="primary")
            
            if selected_cab:
                idx = st.session_state.selected_cabinet_index
                t_dims, t_acc, t_sh, t_div, t_deb = st.tabs(["Dimensions", "Porte/Tiroir", "Étagères", "Montants Secondaires", "Feuille de Débit"])
                with t_dims:
                    st.markdown(f"#### Matières et Dimensions du Corps")
                    st.text_input(f"Matière Corps", value=selected_cab.get('material_body', 'Matière Corps'), key=f"material_body_{idx}", on_change=lambda: update_selected_cabinet_material('material_body'))
                    st.markdown("##### Dimensions Externes")
                    dims = selected_cab['dims']
                    st.number_input("Longueur (X)", value=dims['L_raw'], key=f"L_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('L_raw'), format="%.0f", step=1.0)
                    st.number_input("Largeur (Y - Profondeur)", value=dims['W_raw'], key=f"W_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('W_raw'), format="%.0f", step=1.0)
                    st.number_input("Hauteur (Z)", value=dims['H_raw'], key=f"H_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('H_raw'), format="%.0f", step=1.0)
                    st.markdown("##### Épaisseurs des Panneaux")
                    st.number_input("Parois latérales (Montants)", value=dims['t_lr_raw'], key=f"t_lr_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('t_lr_raw'), format="%.0f", step=1.0)
                    st.number_input("Arrière (Fond)", value=dims['t_fb_raw'], key=f"t_fb_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('t_fb_raw'), format="%.0f", step=1.0)
                    st.number_input("Haut/Bas (Traverses)", value=dims['t_tb_raw'], key=f"t_tb_raw_{idx}", on_change=lambda: update_selected_cabinet_dim('t_tb_raw'), format="%.0f", step=1.0)
                    
                    st.markdown("##### Éléments de Base")
                    st.markdown("Cocher les éléments à inclure dans le caisson :")
                    # Initialiser les préférences si elles n'existent pas
                    if 'base_elements' not in selected_cab:
                        selected_cab['base_elements'] = {
                            'has_back_panel': True,
                            'has_left_upright': True,
                            'has_right_upright': True,
                            'has_bottom_traverse': True,
                            'has_top_traverse': True
                        }
                    base_el = selected_cab['base_elements']
                    st.toggle("Panneau Arrière (Fond)", value=base_el.get('has_back_panel', True), key=f"base_element_has_back_panel_{idx}", on_change=lambda: update_selected_cabinet_base_element('has_back_panel'))
                    st.toggle("Montant Gauche", value=base_el.get('has_left_upright', True), key=f"base_element_has_left_upright_{idx}", on_change=lambda: update_selected_cabinet_base_element('has_left_upright'))
                    st.toggle("Montant Droit", value=base_el.get('has_right_upright', True), key=f"base_element_has_right_upright_{idx}", on_change=lambda: update_selected_cabinet_base_element('has_right_upright'))
                    st.toggle("Traverse Bas", value=base_el.get('has_bottom_traverse', True), key=f"base_element_has_bottom_traverse_{idx}", on_change=lambda: update_selected_cabinet_base_element('has_bottom_traverse'))
                    st.toggle("Traverse Haut", value=base_el.get('has_top_traverse', True), key=f"base_element_has_top_traverse_{idx}", on_change=lambda: update_selected_cabinet_base_element('has_top_traverse'))

                with t_acc:
                    d_p = selected_cab['door_props']; dr_p = selected_cab['drawer_props']
                    # Calculer les zones disponibles (SANS inclure les éléments sans zone_id)
                    all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                    zone_options = [None] + [z['id'] for z in all_zones_2d]
                    zone_labels = ["Tout le caisson"] + [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d]
                    
                    st.markdown("#### Porte (Façade)")
                    st.toggle("Ajouter une porte", value=d_p['has_door'], key=f"has_door_{idx}", on_change=lambda: update_selected_cabinet_door('has_door'))
                    if d_p['has_door']:
                        # Sélection de zone pour la porte
                        if len(all_zones_2d) > 1:
                            current_zone = d_p.get('zone_id', None)
                            zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                            st.selectbox(
                                "Zone d'emplacement",
                                options=zone_options,
                                index=zone_index,
                                format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Tout le caisson",
                                key=f"door_zone_{idx}",
                                on_change=lambda: update_selected_cabinet_door('zone_id')
                            )
                        st.selectbox("Type de porte", options=['single', 'double'], index=0 if d_p.get('door_type')=='single' else 1, format_func=lambda x: 'Simple' if x=='single' else 'Double', key=f"door_type_{idx}", on_change=lambda: update_selected_cabinet_door('door_type'))
                        if d_p.get('door_type')=='single': st.selectbox("Sens d'ouverture", options=['right', 'left'], index=0 if d_p.get('door_opening')=='right' else 1, format_func=lambda x: 'Droite' if x=='right' else 'Gauche', key=f"door_opening_{idx}", on_change=lambda: update_selected_cabinet_door('door_opening'))
                        st.number_input("Épaisseur (mm)", value=d_p.get('door_thickness', 19.0), key=f"door_thickness_{idx}", on_change=lambda: update_selected_cabinet_door('door_thickness'), format="%.0f", step=1.0)
                        st.selectbox("Modèle", options=['standard', 'floor_length'], index=0 if d_p.get('door_model')=='standard' else 1, format_func=lambda x: 'Standard' if x=='standard' else 'Cache-pied', key=f"door_model_{idx}", on_change=lambda: update_selected_cabinet_door('door_model'))
                        st.number_input("Jeu extérieur (mm)", value=d_p.get('door_gap', 2.0), key=f"door_gap_{idx}", on_change=lambda: update_selected_cabinet_door('door_gap'), format="%.1f", step=0.1)
                        st.text_input("Matière Porte", value=d_p.get('material', 'Matière Porte'), key=f"door_material_{idx}", on_change=lambda: update_selected_cabinet_door_material('material'))
                        
                        # Configuration des charnières
                        st.markdown("##### Charnières")
                        hinge_mode = d_p.get('hinge_mode', 'default')
                        hinge_mode_index = 0 if hinge_mode == 'default' else 1
                        selected_hinge_mode = st.selectbox(
                            "Mode de charnières",
                            options=['default', 'custom'],
                            index=hinge_mode_index,
                            format_func=lambda x: 'Par défaut' if x=='default' else 'Personnalisé',
                            key=f"hinge_mode_{idx}",
                            on_change=lambda: update_selected_cabinet_door('hinge_mode')
                        )
                        
                        if selected_hinge_mode == 'custom':
                            # Mode personnalisé : permettre à l'utilisateur de définir le nombre et les positions
                            custom_positions = d_p.get('custom_hinge_positions', [])
                            num_hinges = st.number_input(
                                "Nombre de charnières",
                                value=len(custom_positions) if custom_positions else 3,
                                min_value=1,
                                max_value=10,
                                step=1,
                                key=f"num_hinges_{idx}",
                                on_change=lambda: update_hinge_count(idx)
                            )
                            
                            # Ajuster la liste si le nombre a changé
                            if len(custom_positions) != num_hinges:
                                if len(custom_positions) < num_hinges:
                                    # Ajouter des positions par défaut
                                    door_height = selected_cab['dims']['H_raw']
                                    for i in range(len(custom_positions), num_hinges):
                                        # Répartir équitablement
                                        pos = (i + 1) * door_height / (num_hinges + 1)
                                        custom_positions.append(pos)
                                else:
                                    # Retirer les positions en trop
                                    custom_positions = custom_positions[:num_hinges]
                                d_p['custom_hinge_positions'] = custom_positions
                            
                            # Afficher les champs pour chaque charnière
                            for i in range(num_hinges):
                                current_pos = custom_positions[i] if i < len(custom_positions) else (i + 1) * selected_cab['dims']['H_raw'] / (num_hinges + 1)
                                new_pos = st.number_input(
                                    f"Position charnière {i+1} (mm depuis le bas)",
                                    value=float(current_pos),
                                    min_value=0.0,
                                    max_value=float(selected_cab['dims']['H_raw']),
                                    step=1.0,
                                    format="%.0f",
                                    key=f"hinge_pos_{idx}_{i}",
                                    on_change=lambda idx_cab=idx, idx_hinge=i: update_hinge_position(idx_cab, idx_hinge)
                                )
                                if i < len(custom_positions):
                                    custom_positions[i] = new_pos
                                else:
                                    custom_positions.append(new_pos)
                            d_p['custom_hinge_positions'] = custom_positions[:num_hinges]

                    st.markdown("#### Fileur")
                    current_fileur = int(d_p.get('fileur_width', 0))
                    has_fileur = current_fileur > 0
                    fileur_toggle = st.toggle(
                        "Avec fileur",
                        value=has_fileur,
                        key=f"has_fileur_{idx}"
                    )
                    if fileur_toggle:
                        fileur_choice = st.radio(
                            "Largeur du fileur",
                            options=[50, 100],
                            index=0 if current_fileur != 100 else 1,
                            format_func=lambda x: "5 cm" if x == 50 else "10 cm",
                            key=f"fileur_width_{idx}",
                            horizontal=True
                        )
                        d_p['fileur_width'] = fileur_choice
                    else:
                        d_p['fileur_width'] = 0

                    st.markdown("#### Joues")
                    joues = d_p.setdefault('joues', {
                        'gauche': get_default_joue_props(),
                        'droite': get_default_joue_props(),
                        'dessus': get_default_joue_props(),
                        'dessous': get_default_joue_props(),
                    })

                    for joue_key, joue_label in [
                        ('gauche', 'Joue gauche'),
                        ('droite', 'Joue droite'),
                        ('dessus', 'Joue dessus'),
                        ('dessous', 'Joue dessous'),
                    ]:
                        joue = joues.setdefault(joue_key, get_default_joue_props())
                        st.markdown(f"##### {joue_label}")
                        enabled = st.checkbox(
                            f"Activer {joue_label.lower()}",
                            value=bool(joue.get('enabled', False)),
                            key=f"joue_enabled_{joue_key}_{idx}"
                        )
                        joue['enabled'] = enabled
                        if enabled:
                            cols = st.columns(4)
                            with cols[0]:
                                joue['width'] = st.number_input(
                                    "Largeur du panneau (mm)",
                                    value=float(joue.get('width', 0.0) or 0.0),
                                    min_value=0.0,
                                    step=1.0,
                                    format="%.0f",
                                    key=f"joue_width_{joue_key}_{idx}"
                                )
                            with cols[1]:
                                joue['length'] = st.number_input(
                                    "Longueur du panneau (mm)",
                                    value=float(joue.get('length', 0.0) or 0.0),
                                    min_value=0.0,
                                    step=1.0,
                                    format="%.0f",
                                    key=f"joue_length_{joue_key}_{idx}"
                                )
                            with cols[2]:
                                joue['thickness'] = st.number_input(
                                    "Épaisseur du panneau (mm)",
                                    value=float(joue.get('thickness', 0.0) or 0.0),
                                    min_value=0.0,
                                    step=1.0,
                                    format="%.0f",
                                    key=f"joue_thickness_{joue_key}_{idx}"
                                )
                            with cols[3]:
                                joue['material'] = st.text_input(
                                    "Matière",
                                    value=str(joue.get('material', 'Matière Corps')),
                                    key=f"joue_material_{joue_key}_{idx}"
                                )

                    st.markdown("#### Configuration des Tiroirs")
                    st.button("➕ Ajouter un Tiroir", key=f"add_drawer_{idx}", on_click=add_drawer_callback)
                    st.button("🧱 Ajouter plusieurs tiroirs (empiler)", key=f"add_drawers_stack_{idx}", on_click=add_drawers_stack_callback)
                    
                    # --- POSE EN 2 TEMPS (APERÇU -> VALIDER) : TIROIR ---
                    pending = st.session_state.get('pending_placement')
                    if pending and pending.get('cabinet_index') == idx and pending.get('kind') in ('drawer', 'drawer_stack'):
                        p = pending.get('props', {})
                        is_stack_mode = pending.get('kind') == 'drawer_stack' or bool(p.get('_stack_mode'))
                        st.warning("Pose en cours : le tiroir est en prévisualisation. Cliquez sur **Valider la position** pour le poser définitivement.")
                        with st.expander("✅ Valider la position (Tiroir)" if not is_stack_mode else "✅ Valider la position (Tiroirs empilés)"):
                            all_zones_2d_sel = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                            zone_options = [None] + [z['id'] for z in all_zones_2d_sel]
                            zone_labels = ["Tout le caisson"] + [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_sel]
                            current_zone = p.get('zone_id', None)
                            zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                            p['zone_id'] = st.selectbox(
                                "Zone d'emplacement",
                                options=zone_options,
                                index=zone_index,
                                format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Tout le caisson",
                                key=f"pending_drawer_zone_{idx}",
                            )
                            
                            if is_stack_mode:
                                # Empilement : l'utilisateur ne saisit PAS de dimensions (ni hauteur, ni offset).
                                p['stack_count'] = int(st.number_input(
                                    "Nombre de tiroirs dans la zone",
                                    min_value=1,
                                    max_value=12,
                                    value=int(p.get('stack_count', 3)),
                                    step=1,
                                    key=f"pending_drawer_stack_{idx}",
                                    help="Les hauteurs et positions sont calculées automatiquement pour remplir toute la zone (2mm entre tiroirs)."
                                ))
                                
                                # Choix : Encastré ou En applique
                                is_applique_current = bool(p.get('_applique_mode', False))
                                mount_type = st.selectbox(
                                    "Mode de montage LEGRABOX",
                                    options=['Encastré', 'En applique'],
                                    index=1 if is_applique_current else 0,
                                    key=f"pending_drawer_mount_type_{idx}",
                                    help="Encastré: tiroirs restent ENTRE les traverses | En applique: faces sortent du caisson"
                                )
                                p['_applique_mode'] = (mount_type == 'En applique')
                            
                            # Calculer la hauteur maximale disponible dans la zone sélectionnée
                            if p.get('zone_id') is not None and p['zone_id'] < len(all_zones_2d_sel):
                                zone = all_zones_2d_sel[p['zone_id']]
                                max_height_in_zone = zone['y_max'] - zone['y_min']
                            else:
                                dims = selected_cab['dims']
                                max_height_in_zone = dims['H_raw'] - dims['t_tb_raw'] * 2
                            
                        # Sélection du système (TANDEMBOX, LÉGRABOX ou ANGLAISE)
                        drawer_system = p.get('drawer_system', 'TANDEMBOX')
                        _system_opts = ['TANDEMBOX', 'LÉGRABOX', 'ANGLAISE']
                        system_idx = _system_opts.index(drawer_system) if drawer_system in _system_opts else 0
                        p['drawer_system'] = st.selectbox(
                            "Système de Tiroir",
                            options=_system_opts,
                            index=system_idx,
                            key=f"pending_drawer_system_{idx}",
                            help="ANGLAISE : tiroir posé 40mm à l'intérieur, face centrée dans la zone"
                        )
                        
                        # Sélection du type selon le système
                        if p['drawer_system'] == 'LÉGRABOX':
                            tech_opts = ['N', 'M', 'K', 'C']
                        else:  # TANDEMBOX et ANGLAISE partagent les mêmes types
                            tech_opts = ['K', 'M', 'N', 'D']
                        curr_tech = p.get('drawer_tech_type', 'K')
                        idx_tech = tech_opts.index(curr_tech) if curr_tech in tech_opts else 0
                        p['drawer_tech_type'] = st.selectbox(
                            f"Type ({p['drawer_system']})",
                            options=tech_opts,
                            index=idx_tech,
                            key=f"pending_drawer_tech_type_{idx}",
                        )
                        
                        if not is_stack_mode:
                            # Mode tiroir unique : on garde les champs manuels
                            face_h_default = float(p.get('drawer_face_H_raw', 150.0))
                            face_h_min = 50.0
                            face_h_max = float(max_height_in_zone)
                            face_h_value = max(face_h_min, min(face_h_default, face_h_max))
                            
                            p['drawer_face_H_raw'] = st.number_input(
                                "Hauteur Face (mm)",
                                value=face_h_value,
                                key=f"pending_drawer_face_H_raw_{idx}",
                                format="%.0f",
                                step=1.0,
                                min_value=face_h_min,
                                max_value=face_h_max,
                            )
                            
                            # Position Y du bas du tiroir dans la zone
                            if p.get('zone_id') is not None and p['zone_id'] < len(all_zones_2d_sel):
                                zone = all_zones_2d_sel[p['zone_id']]
                                bottom_offset_min = 0.0
                                bottom_offset_max = zone['y_max'] - zone['y_min'] - p['drawer_face_H_raw']
                            else:
                                dims = selected_cab['dims']
                                bottom_offset_min = 0.0
                                bottom_offset_max = dims['H_raw'] - dims['t_tb_raw'] * 2 - p['drawer_face_H_raw']
                            
                            bottom_offset_default = float(p.get('drawer_bottom_offset', 0.0))
                            bottom_offset_value = max(bottom_offset_min, min(bottom_offset_default, bottom_offset_max))
                            
                            p['drawer_bottom_offset'] = st.number_input(
                                "Position Y - Offset depuis le bas de la zone (mm)",
                                value=bottom_offset_value,
                                key=f"pending_drawer_bottom_offset_{idx}",
                                format="%.0f",
                                step=1.0,
                                min_value=bottom_offset_min,
                                max_value=bottom_offset_max,
                                help="Position verticale du bas du tiroir dans la zone sélectionnée"
                            )
                        
                        p['drawer_face_thickness'] = st.number_input(
                            "Épaisseur Face (mm)",
                            value=float(p.get('drawer_face_thickness', 19.0)),
                            key=f"pending_drawer_face_thickness_{idx}",
                            format="%.0f",
                            step=1.0,
                            min_value=10.0,
                        )
                        p['inner_thickness'] = st.number_input(
                            "Épaisseur Intérieur (dos/fond) (mm)",
                            value=float(p.get('inner_thickness', 16.0)),
                            key=f"pending_drawer_inner_thickness_{idx}",
                            format="%.0f",
                            step=1.0,
                            min_value=5.0,
                        )
                        p['drawer_gap'] = st.number_input(
                            "Jeu extérieur (mm)",
                            value=float(p.get('drawer_gap', 2.0)),
                            key=f"pending_drawer_gap_{idx}",
                            format="%.1f",
                            step=0.1,
                        )
                        _drawer_handle_options = ['none', 'integrated_cutout', 'finger_pull']
                        _drawer_handle_current = p.get('drawer_handle_type', 'none')
                        if _drawer_handle_current not in _drawer_handle_options:
                            _drawer_handle_current = 'none'
                        p['drawer_handle_type'] = st.selectbox(
                            "Poignée",
                            options=_drawer_handle_options,
                            index=_drawer_handle_options.index(_drawer_handle_current),
                            format_func=lambda x: 'Aucune' if x=='none' else ('Intégrée (Découpe)' if x=='integrated_cutout' else 'Passe-doigt'),
                            key=f"pending_drawer_handle_type_{idx}",
                        )
                        if p.get('drawer_handle_type') == 'integrated_cutout':
                            p['drawer_handle_width'] = st.number_input(
                                "Largeur Poignée",
                                value=float(p.get('drawer_handle_width', 150.0)),
                                key=f"pending_drawer_handle_width_{idx}",
                                format="%.0f",
                                step=1.0,
                            )
                            p['drawer_handle_height'] = st.number_input(
                                "Hauteur Poignée",
                                value=float(p.get('drawer_handle_height', 40.0)),
                                key=f"pending_drawer_handle_height_{idx}",
                                format="%.0f",
                                step=1.0,
                            )
                            p['drawer_handle_offset_top'] = st.number_input(
                                "Offset Haut",
                                value=float(p.get('drawer_handle_offset_top', 10.0)),
                                key=f"pending_drawer_handle_offset_top_{idx}",
                                format="%.0f",
                                step=1.0,
                            )
                        elif p.get('drawer_handle_type') == 'finger_pull':
                            p['drawer_handle_offset_top'] = st.number_input(
                                "Offset Haut Passe-doigt",
                                value=float(p.get('drawer_handle_offset_top', 10.0)),
                                key=f"pending_drawer_finger_pull_offset_top_{idx}",
                                format="%.0f",
                                step=1.0,
                                min_value=0.0,
                            )
                            p['drawer_finger_pull_depth'] = st.number_input(
                                "Profondeur Passe-doigt (mm)",
                                value=float(p.get('drawer_finger_pull_depth', 12.0)),
                                key=f"pending_drawer_finger_pull_depth_{idx}",
                                format="%.1f",
                                step=0.5,
                                min_value=1.0,
                            )
                            p['drawer_finger_pull_drop'] = st.number_input(
                                "Hauteur Passe-doigt sur tranche (mm)",
                                value=float(p.get('drawer_finger_pull_drop', 35.0)),
                                key=f"pending_drawer_finger_pull_drop_{idx}",
                                format="%.1f",
                                step=0.5,
                                min_value=5.0,
                            )
                        p['material'] = st.text_input(
                            "Matière Face Tiroir",
                            value=p.get('material', 'Matière Tiroir'),
                            key=f"pending_drawer_material_{idx}",
                        )
                        p['material_inner'] = st.text_input(
                            "Matière Intérieur Tiroir (Dos/Fond)",
                            value=p.get('material_inner', p.get('material', 'Matière Tiroir')),
                            key=f"pending_drawer_material_inner_{idx}",
                        )
                        c_ok, c_cancel = st.columns(2)
                        if c_ok.button("Valider la position", key=f"pending_drawer_validate_{idx}", use_container_width=True, type="primary"):
                            selected_cab.setdefault('drawers', [])
                            # IMPORTANT : Recalculer les zones au moment de la validation (elles ont pu changer)
                            all_zones_2d_sel_final = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                            stack_count = int(p.get('stack_count', 1)) if is_stack_mode else 1
                            
                            # Cas 1 : tiroir unique (comportement identique à avant)
                            if (not is_stack_mode) or stack_count <= 1 or p.get('zone_id') is None or p['zone_id'] >= len(all_zones_2d_sel_final):

                                # Stocker les coordonnées de la zone avec le tiroir pour référence future
                                if p.get('zone_id') is not None:
                                    all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=True)
                                    if p['zone_id'] < len(all_zones_2d):
                                        zone = all_zones_2d[p['zone_id']]
                                        p['stored_zone_coords'] = {
                                            'x_min': zone['x_min'],
                                            'x_max': zone['x_max'],
                                            'y_min': zone['y_min'],
                                            'y_max': zone['y_max']
                                        }
                                selected_cab['drawers'].append(copy.deepcopy(p))
                            else:
                                # Cas 2 : empilement automatique de plusieurs tiroirs dans la zone
                                zone = all_zones_2d_sel_final[p['zone_id']]
                                dims = selected_cab['dims']
                                t_tb_mm = float(dims.get('t_tb_raw', 19.0))
                                H_raw = float(dims.get('H_raw', 1000.0))
                                
                                # Vérifier le mode (encastré ou appliqué)
                                is_applique = bool(p.get('_applique_mode', False))
                                
                                if is_applique:
                                    # Mode APPLIQUE : formule H_raw - n*2mm - 2x1mm (jeu de 1mm haut/bas)
                                    # Les faces dépassent du meuble et recouvrent les montants + 1mm de jeu de chaque côté
                                    n_junctions = stack_count - 1
                                    total_face_height = H_raw - (n_junctions * 2.0) - 2.0  # -2 pour les 2x1mm de jeu
                                    face_h = total_face_height / float(stack_count) if stack_count > 0 else 0.0
                                    if face_h < 10.0:
                                        face_h = 10.0
                                    # Position : début du caisson avec 1mm de jeu
                                    current_z_offset = -1.0  # Commence 1mm avant le bas (o[2])
                                else:
                                    # Mode ENCASTE : formule H_raw - 2*t_tb - 4mm - n*2mm
                                    # Les tiroirs restent à l'intérieur, avec marges
                                    n_junctions = stack_count - 1
                                    total_face_height = H_raw - 2.0 * t_tb_mm - 4.0 - (n_junctions * 2.0)
                                    face_h = total_face_height / float(stack_count) if stack_count > 0 else 0.0
                                    if face_h < 10.0:
                                        face_h = 10.0
                                    # Position du premier tiroir : 2mm après traverse basse
                                    current_z_offset = t_tb_mm + 2.0
                                
                                for k in range(stack_count):
                                    d_copy = copy.deepcopy(p)
                                    d_copy.pop('stack_count', None)
                                    d_copy.pop('_stack_mode', None)
                                    # GARDER _applique_mode pour que les tiroirs validés restent en applique
                                    
                                    d_copy['drawer_face_H_raw'] = face_h
                                    d_copy['drawer_bottom_offset'] = current_z_offset
                                    # Préparer offset pour próchain tiroir (hauteur + 2mm gap)
                                    current_z_offset += face_h + 2.0
                                    # Stocker les coordonnées de la zone pour ce tiroir
                                    d_copy['stored_zone_coords'] = {
                                        'x_min': zone['x_min'],
                                        'x_max': zone['x_max'],
                                        'y_min': zone['y_min'],
                                        'y_max': zone['y_max']
                                    }
                                    selected_cab['drawers'].append(d_copy)
                            st.session_state['pending_placement'] = None
                            st.rerun()
                        if c_cancel.button("Annuler", key=f"pending_drawer_cancel_{idx}", use_container_width=True):
                            st.session_state['pending_placement'] = None
                            st.rerun()
                    
                    # Afficher la liste des tiroirs existants
                    if 'drawers' in selected_cab and selected_cab['drawers']:
                        # Calculer toutes les zones 2D disponibles (SANS inclure les éléments sans zone_id)
                        all_zones_2d_drawers = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                        zone_options_drawers = [None] + [z['id'] for z in all_zones_2d_drawers]
                        zone_labels_drawers = ["Tout le caisson"] + [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_drawers]
                        
                        for i, d in enumerate(selected_cab['drawers']):
                            current_zone = d.get('zone_id', None)
                            zone_index = zone_options_drawers.index(current_zone) if current_zone in zone_options_drawers else 0
                            
                            with st.expander(f"⚙️ Tiroir {i+1}"):
                                # Sélection de zone
                                if len(all_zones_2d_drawers) > 1:
                                    st.selectbox(
                                        "Zone d'emplacement",
                                        options=zone_options_drawers,
                                        index=zone_index,
                                        format_func=lambda x: zone_labels_drawers[zone_options_drawers.index(x)] if x in zone_options_drawers else "Tout le caisson",
                                        key=f"drawer_zone_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'zone_id')
                                    )
                                    
                                    # Si une zone est sélectionnée, afficher les limites de la zone
                                    if current_zone is not None and current_zone < len(all_zones_2d_drawers):
                                        zone = all_zones_2d_drawers[current_zone]
                                        st.caption(f"Zone sélectionnée : Largeur X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm, Hauteur Y = {zone['y_min']:.0f}-{zone['y_max']:.0f}mm")
                                
                                # Sélection du système (TANDEMBOX, LÉGRABOX ou ANGLAISE)
                                drawer_system = d.get('drawer_system', 'TANDEMBOX')
                                _system_opts_edit = ['TANDEMBOX', 'LÉGRABOX', 'ANGLAISE']
                                system_idx = _system_opts_edit.index(drawer_system) if drawer_system in _system_opts_edit else 0
                                st.selectbox(
                                    "Système de Tiroir",
                                    options=_system_opts_edit,
                                    index=system_idx,
                                    key=f"drawer_system_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_system'),
                                    help="ANGLAISE : tiroir posé 40mm à l'intérieur, face centrée dans la zone"
                                )
                                
                                # Sélection du type selon le système
                                if drawer_system == 'LÉGRABOX':
                                    tech_opts = ['N', 'M', 'K', 'C']
                                else:  # TANDEMBOX et ANGLAISE partagent les mêmes types
                                    tech_opts = ['K', 'M', 'N', 'D']
                                curr_tech = d.get('drawer_tech_type', 'K')
                                idx_tech = tech_opts.index(curr_tech) if curr_tech in tech_opts else 0
                                st.selectbox(
                                    f"Type ({drawer_system})",
                                    options=tech_opts,
                                    index=idx_tech,
                                    key=f"drawer_tech_type_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_tech_type')
                                )
                                
                                st.number_input(
                                    "Hauteur Face (mm)",
                                    value=d.get('drawer_face_H_raw', 150.0),
                                    key=f"drawer_face_H_raw_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_face_H_raw'),
                                    format="%.0f",
                                    step=1.0,
                                )
                                st.number_input(
                                    "Position Y - Offset depuis le bas de la zone (mm)",
                                    value=d.get('drawer_bottom_offset', 0.0),
                                    key=f"drawer_bottom_offset_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_bottom_offset'),
                                    format="%.0f",
                                    step=1.0,
                                    help="Position verticale du bas du tiroir dans la zone sélectionnée"
                                )
                                st.number_input(
                                    "Épaisseur Face (mm)",
                                    value=d.get('drawer_face_thickness', 19.0),
                                    key=f"drawer_face_thickness_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_face_thickness'),
                                    format="%.0f",
                                    step=1.0,
                                )
                                st.number_input(
                                    "Jeu extérieur (mm)",
                                    value=d.get('drawer_gap', 2.0),
                                    key=f"drawer_gap_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_gap'),
                                    format="%.1f",
                                    step=0.1,
                                )
                                st.number_input(
                                    "Épaisseur Intérieur (dos/fond) (mm)",
                                    value=d.get('inner_thickness', 16.0),
                                    key=f"drawer_inner_thickness_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'inner_thickness'),
                                    format="%.0f",
                                    step=1.0,
                                )
                                _drawer_handle_options_edit = ['none', 'integrated_cutout', 'finger_pull']
                                _drawer_handle_current_edit = d.get('drawer_handle_type', 'none')
                                if _drawer_handle_current_edit not in _drawer_handle_options_edit:
                                    _drawer_handle_current_edit = 'none'
                                st.selectbox(
                                    "Poignée",
                                    options=_drawer_handle_options_edit,
                                    index=_drawer_handle_options_edit.index(_drawer_handle_current_edit),
                                    format_func=lambda x: 'Aucune' if x=='none' else ('Intégrée (Découpe)' if x=='integrated_cutout' else 'Passe-doigt'),
                                    key=f"drawer_handle_type_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'drawer_handle_type')
                                )
                                if d.get('drawer_handle_type') == 'integrated_cutout':
                                    st.number_input(
                                        "Largeur Poignée",
                                        value=d.get('drawer_handle_width', 150.0),
                                        key=f"drawer_handle_width_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_handle_width'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                    st.number_input(
                                        "Hauteur Poignée",
                                        value=d.get('drawer_handle_height', 40.0),
                                        key=f"drawer_handle_height_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_handle_height'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                    st.number_input(
                                        "Offset Haut",
                                        value=d.get('drawer_handle_offset_top', 10.0),
                                        key=f"drawer_handle_offset_top_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_handle_offset_top'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                elif d.get('drawer_handle_type') == 'finger_pull':
                                    st.number_input(
                                        "Offset Haut Passe-doigt",
                                        value=float(d.get('drawer_handle_offset_top', 10.0)),
                                        key=f"drawer_finger_pull_offset_top_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_handle_offset_top'),
                                        format="%.0f",
                                        step=1.0,
                                        min_value=0.0,
                                    )
                                    st.number_input(
                                        "Profondeur Passe-doigt (mm)",
                                        value=float(d.get('drawer_finger_pull_depth', 12.0)),
                                        key=f"drawer_finger_pull_depth_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_finger_pull_depth'),
                                        format="%.1f",
                                        step=0.5,
                                        min_value=1.0,
                                    )
                                    st.number_input(
                                        "Hauteur Passe-doigt sur tranche (mm)",
                                        value=float(d.get('drawer_finger_pull_drop', 35.0)),
                                        key=f"drawer_finger_pull_drop_{idx}_{i}",
                                        on_change=lambda x=i: update_drawer_prop(x, 'drawer_finger_pull_drop'),
                                        format="%.1f",
                                        step=0.5,
                                        min_value=5.0,
                                    )
                                st.text_input(
                                    "Matière Face Tiroir",
                                    value=d.get('material', 'Matière Tiroir'),
                                    key=f"drawer_material_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_material(x)
                                )
                                st.text_input(
                                    "Matière Intérieur Tiroir (Dos/Fond)",
                                    value=d.get('material_inner', d.get('material', 'Matière Tiroir')),
                                    key=f"drawer_material_inner_{idx}_{i}",
                                    on_change=lambda x=i: update_drawer_prop(x, 'material_inner')
                                )
                                st.button("Supprimer ce tiroir 🗑️", key=f"del_drawer_{idx}_{i}", on_click=lambda x=i: delete_drawer_callback(x))

                with t_sh:
                    st.markdown("#### Configuration des Étagères")
                    st.button("Ajouter une étagère au Caisson", key=f"add_shelf_{idx}", on_click=add_shelf_callback)
                    st.button("🧱 Ajouter plusieurs étagères fixes (empiler)", key=f"add_shelf_stack_{idx}", on_click=add_fixed_shelves_stack_callback)
                    
                    # --- POSE EN 2 TEMPS (APERÇU -> VALIDER) : ÉTAGÈRE ---
                    pending = st.session_state.get('pending_placement')
                    if pending and pending.get('cabinet_index') == idx and pending.get('kind') in ('shelf', 'shelf_stack'):
                        p = pending.get('props', {})
                        is_stack_mode = pending.get('kind') == 'shelf_stack' or bool(p.get('_stack_mode'))
                        st.warning("Pose en cours : l'étagère est en prévisualisation. Cliquez sur **Valider la position** pour la poser définitivement.")
                        with st.expander("✅ Valider la position (Étagères fixes empilées)" if is_stack_mode else "✅ Valider la position (Étagère)"):
                            all_zones_2d_sel = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                            zone_options = [None] + [z['id'] for z in all_zones_2d_sel]
                            zone_labels = ["Tout le caisson"] + [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_sel]
                            current_zone = p.get('zone_id', None)
                            zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                            p['zone_id'] = st.selectbox(
                                "Zone d'emplacement",
                                options=zone_options,
                                index=zone_index,
                                format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Tout le caisson",
                                key=f"pending_shelf_zone_{idx}",
                            )

                            if is_stack_mode:
                                p['shelf_type'] = 'fixe'
                                p['stack_count'] = int(st.number_input(
                                    "Nombre d'étagères fixes dans la zone",
                                    min_value=1,
                                    max_value=20,
                                    value=int(p.get('stack_count', 3)),
                                    step=1,
                                    key=f"pending_shelf_stack_{idx}",
                                    help="Les étagères seront placées automatiquement à distance égale sur la hauteur de la zone."
                                ))
                                st.caption("Type imposé: Fixe (assemblage automatique)")
                            else:
                                p['shelf_type'] = st.selectbox(
                                    "Type",
                                    options=['mobile', 'fixe'],
                                    index=0 if p.get('shelf_type', 'mobile') == 'mobile' else 1,
                                    format_func=lambda x: 'Mobile (Taquets)' if x=='mobile' else 'Fixe',
                                    key=f"pending_shelf_type_{idx}",
                                )
                                # Clamping pour éviter StreamlitAPIException
                                height_default = float(p.get('height', 200.0))
                                height_min = 0.0
                                height_max = float(selected_cab['dims']['H_raw'] - selected_cab['dims']['t_tb_raw'] * 2)
                                height_value = max(height_min, min(height_default, height_max))
                                
                                p['height'] = st.number_input(
                                    "Position Y - Hauteur (mm depuis traverse inférieure)",
                                    value=height_value,
                                    key=f"pending_shelf_height_{idx}",
                                    format="%.0f",
                                    step=1.0,
                                    min_value=height_min,
                                    max_value=height_max,
                                )
                            p['thickness'] = st.number_input(
                                "Épaisseur (mm)",
                                value=float(p.get('thickness', 19.0)),
                                key=f"pending_shelf_thickness_{idx}",
                                format="%.0f",
                                step=1.0,
                                min_value=10.0,
                            )
                            p['material'] = st.text_input(
                                "Matière",
                                value=p.get('material', 'Matière Étagère'),
                                key=f"pending_shelf_material_{idx}",
                            )
                            c_ok, c_cancel = st.columns(2)
                            if c_ok.button("Valider la position", key=f"pending_shelf_validate_{idx}", use_container_width=True, type="primary"):
                                selected_cab.setdefault('shelves', [])

                                all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=True)
                                zone_obj = None
                                if p.get('zone_id') is not None and p['zone_id'] < len(all_zones_2d):
                                    zone_obj = all_zones_2d[p['zone_id']]

                                def _apply_zone_storage(shelf_obj, zone):
                                    if zone is None:
                                        return
                                    shelf_obj['stored_zone_coords'] = {
                                        'x_min': zone['x_min'],
                                        'x_max': zone['x_max'],
                                        'y_min': zone['y_min'],
                                        'y_max': zone['y_max']
                                    }
                                    zone_width_mm = zone['x_max'] - zone['x_min']
                                    shelf_obj['stored_shelf_width_mm'] = zone_width_mm
                                    shelf_obj['stored_shelf_x_start_mm'] = zone['x_min']

                                if is_stack_mode:
                                    stack_count = max(1, int(p.get('stack_count', 3)))
                                    dims = selected_cab['dims']
                                    t_tb_mm = float(dims.get('t_tb_raw', 19.0))
                                    y_min = 0.0
                                    y_max = float(dims['H_raw'] - 2.0 * dims['t_tb_raw'])
                                    if zone_obj is not None:
                                        y_min = max(0.0, float(zone_obj['y_min']) - t_tb_mm)
                                        y_max = max(y_min, float(zone_obj['y_max']) - t_tb_mm)

                                    zone_height = max(0.0, y_max - y_min)
                                    spacing = zone_height / float(stack_count + 1)

                                    for k in range(stack_count):
                                        s_copy = copy.deepcopy(p)
                                        s_copy.pop('stack_count', None)
                                        s_copy.pop('_stack_mode', None)
                                        s_copy['shelf_type'] = 'fixe'
                                        s_copy['height'] = y_min + (k + 1) * spacing
                                        _apply_zone_storage(s_copy, zone_obj)
                                        selected_cab['shelves'].append(s_copy)
                                else:
                                    _apply_zone_storage(p, zone_obj)
                                    selected_cab['shelves'].append(copy.deepcopy(p))

                                st.session_state['pending_placement'] = None
                                st.rerun()
                            if c_cancel.button("Annuler", key=f"pending_shelf_cancel_{idx}", use_container_width=True):
                                st.session_state['pending_placement'] = None
                                st.rerun()
                    if 'shelves' in selected_cab:
                        # Calculer toutes les zones 2D disponibles (SANS inclure les éléments sans zone_id)
                        # Pour le choix de zone, on veut voir les zones existantes AVANT le placement
                        all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                        zone_options = [None] + [z['id'] for z in all_zones_2d]
                        zone_labels = ["Tout le caisson"] + [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d]
                        
                        for i, s in enumerate(selected_cab['shelves']):
                            s_type = s.get('shelf_type', 'mobile')
                            current_zone = s.get('zone_id', None)
                            zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                            
                            with st.expander(f"⚙️ Étagère {i+1} ({'Mobile' if s_type=='mobile' else 'Fixe'})"):
                                # Sélection de zone (parmi les zones existantes AVANT placement)
                                if len(all_zones_2d) > 1:
                                    st.selectbox(
                                        "Zone d'emplacement",
                                        options=zone_options,
                                        index=zone_index,
                                        format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Tout le caisson",
                                        key=f"shelf_zone_{idx}_{i}",
                                        on_change=lambda x=i: update_shelf_prop(x, 'zone_id')
                                    )
                                    
                                    # Si une zone est sélectionnée, afficher les limites de la zone
                                    if current_zone is not None and current_zone < len(all_zones_2d):
                                        zone = all_zones_2d[current_zone]
                                        st.caption(f"Zone sélectionnée : Largeur X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm, Hauteur Y = {zone['y_min']:.0f}-{zone['y_max']:.0f}mm")
                                
                                st.selectbox("Type", options=['mobile', 'fixe'], index=0 if s_type=='mobile' else 1, format_func=lambda x: 'Mobile (Taquets)' if x=='mobile' else 'Fixe', key=f"shelf_t_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'shelf_type'))
                                st.number_input("Position Y - Hauteur (mm depuis traverse inférieure)", value=s['height'], key=f"shelf_h_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'height'), format="%.0f", step=1.0, help="Hauteur de l'étagère dans la zone (modifiable)")
                                st.number_input("Épaisseur (mm)", value=s['thickness'], key=f"shelf_e_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'thickness'), format="%.0f", step=1.0)
                                st.text_input("Matière", value=s.get('material', 'Matière Étagère'), key=f"shelf_m_{idx}_{i}", on_change=lambda x=i: update_shelf_material(x, 'material'))
                                if s_type == 'mobile':
                                    st.selectbox("Motif Trous", options=['full_height', '5_holes_centered', 'custom_n_m'], index=['full_height', '5_holes_centered', 'custom_n_m'].index(s.get('mobile_machining_type', 'full_height')), format_func=lambda x: {'full_height':'Toute hauteur', '5_holes_centered':'5 Trous Centrés', 'custom_n_m':'Personnalisé'}.get(x, x), key=f"shelf_m_type_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'mobile_machining_type'))
                                    if s.get('mobile_machining_type') == 'custom_n_m':
                                        st.number_input("Trous au-dessus (N)", value=s.get('custom_holes_above', 0), key=f"shelf_c_above_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'custom_holes_above'), step=1)
                                        st.number_input("Trous en-dessous (M)", value=s.get('custom_holes_below', 0), key=f"shelf_c_below_{idx}_{i}", on_change=lambda x=i: update_shelf_prop(x, 'custom_holes_below'), step=1)
                                st.button("Supprimer cette étagère 🗑️", key=f"del_shelf_{idx}_{i}", on_click=lambda x=i: delete_shelf_callback(x))
                    
                    st.markdown("---")

                with t_div:
                    st.markdown("#### Montants Verticaux Secondaires (Séparations)")
                    st.info("Les montants secondaires divisent le caisson en plusieurs zones. Les étagères, portes et tiroirs peuvent être assignés à des zones spécifiques.")
                    c_div_add1, c_div_add2 = st.columns(2)
                    c_div_add1.button("➕ Ajouter un Montant Secondaire", key=f"add_divider_{idx}", on_click=add_vertical_divider_callback, use_container_width=True)
                    c_div_add2.button("➕ Double Montants Secondaires", key=f"add_double_divider_{idx}", on_click=add_vertical_divider_double_callback, use_container_width=True)
                    
                    # --- POSE EN 2 TEMPS (APERÇU -> VALIDER) : MONTANT SECONDAIRE ---
                    pending = st.session_state.get('pending_placement')
                    if pending and pending.get('cabinet_index') == idx and pending.get('kind') in ('vertical_divider', 'vertical_divider_double'):
                        p = pending.get('props', {})
                        is_double = pending.get('kind') == 'vertical_divider_double' or bool(p.get('double'))
                        txt_title = "✅ Valider la position (Double montant secondaire)" if is_double else "✅ Valider la position (Montant secondaire)"
                        st.warning("Pose en cours : le montant secondaire est en prévisualisation. Cliquez sur **Valider la position** pour le poser définitivement.")
                        with st.expander(txt_title):
                            existing_count = len(selected_cab.get('vertical_dividers', []))
                            is_first = (existing_count == 0)
                            all_zones_2d_sel = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                            
                            if is_first:
                                p['zone_id'] = None
                                st.info("💡 Premier montant : placement libre (il créera ensuite 2 zones).")
                            else:
                                zone_options = [z['id'] for z in all_zones_2d_sel] if all_zones_2d_sel else []
                                zone_labels = [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_sel]
                                if zone_options:
                                    current_zone = p.get('zone_id', zone_options[0])
                                    zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                                    p['zone_id'] = st.selectbox(
                                        "Zone d'emplacement",
                                        options=zone_options,
                                        index=zone_index,
                                        format_func=lambda x: zone_labels[zone_options.index(x)],
                                        key=f"pending_divider_zone_{idx}",
                                    )
                            
                            dims = selected_cab['dims']
                            min_x = float(dims['t_lr_raw'] + 50)
                            max_x = float(dims['L_raw'] - dims['t_lr_raw'] - 50)

                            # Option de placement : manuel ou milieu de la zone (pour les doubles)
                            placement_mode = "Milieu de la zone" if (is_double and p.get('zone_id') is not None) else "Manuel"
                            if is_double:
                                placement_mode = st.radio(
                                    "Position X",
                                    options=["Manuel", "Milieu de la zone"],
                                    index=1 if p.get('zone_id') is not None else 0,
                                    key=f"pending_divider_place_mode_{idx}",
                                )

                            if is_double and placement_mode == "Milieu de la zone" and p.get('zone_id') is not None and all_zones_2d_sel:
                                # Centre automatiquement le double montant au milieu de la zone choisie
                                z_mid = next((z for z in all_zones_2d_sel if z['id'] == p['zone_id']), None)
                                if z_mid:
                                    p['position_x'] = (z_mid['x_min'] + z_mid['x_max']) / 2.0
                                    st.info(f"Position X au milieu de la zone sélectionnée (X ≈ {p['position_x']:.0f} mm).")
                            else:
                                p['position_x'] = st.number_input(
                                    "Position X (mm depuis le montant gauche)",
                                    value=float(p.get('position_x', (min_x + max_x) / 2.0)),
                                    key=f"pending_divider_position_x_{idx}",
                                    format="%.0f",
                                    step=1.0,
                                    min_value=min_x,
                                    max_value=max_x,
                                )
                            p['thickness'] = st.number_input(
                                "Épaisseur (mm)",
                                value=float(p.get('thickness', 19.0)),
                                key=f"pending_divider_thickness_{idx}",
                                format="%.0f",
                                step=1.0,
                                min_value=10.0,
                            )
                            p['material'] = st.text_input(
                                "Matière",
                                value=p.get('material', 'Matière Corps'),
                                key=f"pending_divider_material_{idx}",
                            )
                            c_ok, c_cancel = st.columns(2)
                            if c_ok.button("Valider la position", key=f"pending_divider_validate_{idx}", use_container_width=True, type="primary"):
                                selected_cab.setdefault('vertical_dividers', [])
                                # Stocker les coordonnées de la zone avec le montant pour référence future
                                stored_zone = None
                                if p.get('zone_id') is not None:
                                    all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=True)
                                    if p['zone_id'] < len(all_zones_2d):
                                        stored_zone = all_zones_2d[p['zone_id']]
                                if is_double:
                                    # Créer 2 montants centrés sur center_x (bords se touchent).
                                    center_x = float(p.get('position_x', (min_x + max_x) / 2.0))
                                    th = float(p.get('thickness', 19.0))
                                    pos_left = center_x - th / 2.0
                                    pos_right = center_x + th / 2.0
                                    for px in (pos_left, pos_right):
                                        d_copy = copy.deepcopy(p)
                                        d_copy['position_x'] = px
                                        d_copy.pop('double', None)
                                        if stored_zone is not None:
                                            d_copy['stored_zone_coords'] = {
                                                'x_min': stored_zone['x_min'],
                                                'x_max': stored_zone['x_max'],
                                                'y_min': stored_zone['y_min'],
                                                'y_max': stored_zone['y_max']
                                            }
                                        selected_cab['vertical_dividers'].append(d_copy)
                                else:
                                    if stored_zone is not None:
                                        p['stored_zone_coords'] = {
                                            'x_min': stored_zone['x_min'],
                                            'x_max': stored_zone['x_max'],
                                            'y_min': stored_zone['y_min'],
                                            'y_max': stored_zone['y_max']
                                        }
                                    selected_cab['vertical_dividers'].append(copy.deepcopy(p))
                                st.session_state['pending_placement'] = None
                                st.rerun()
                            if c_cancel.button("Annuler", key=f"pending_divider_cancel_{idx}", use_container_width=True):
                                st.session_state['pending_placement'] = None
                                st.rerun()
                    
                    # Calculer toutes les zones 2D (X et Y combinés) - SANS inclure les éléments sans zone_id
                    all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                    st.markdown("##### Zones disponibles :")
                    for zone in all_zones_2d:
                        st.caption(f"{zone['label']}: X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm, Y = {zone['y_min']:.0f}-{zone['y_max']:.0f}mm")
                    
                    if 'vertical_dividers' in selected_cab and selected_cab['vertical_dividers']:
                        for i, div in enumerate(selected_cab['vertical_dividers']):
                            with st.expander(f"🔧 Montant Secondaire {i+1}"):
                                # Le premier montant (i == 0) peut toujours être placé librement dans la Zone 0 originale
                                # Les montants suivants doivent être placés dans une zone existante
                                is_first_divider = (i == 0)
                                
                                if is_first_divider:
                                    # Premier montant : pas de sélection de zone, placement libre dans Zone 0 originale
                                    st.info("💡 Premier montant : vous pouvez le placer librement dans tout le caisson (Zone 0). Il créera ensuite 2 zones.")
                                    # S'assurer que zone_id est None pour le premier montant
                                    if div.get('zone_id') is not None:
                                        div['zone_id'] = None
                                else:
                                    # Montants suivants : sélection de zone
                                    current_zone_id = div.get('zone_id', None)
                                    zone_options = [z['id'] for z in all_zones_2d]
                                    zone_labels = [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d]
                                    zone_index = zone_options.index(current_zone_id) if current_zone_id in zone_options else 0
                                    
                                    selected_zone_id = st.selectbox(
                                        "Zone d'emplacement",
                                        options=zone_options,
                                        index=zone_index,
                                        format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Zone inconnue",
                                        key=f"divider_zone_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_divider_prop(x, 'zone_id')
                                    )
                                    
                                    # Ne PAS ajuster automatiquement - l'utilisateur doit placer manuellement
                                    if selected_zone_id < len(all_zones_2d):
                                        zone = all_zones_2d[selected_zone_id]
                                        # Afficher les limites de la zone mais ne pas modifier la position
                                        st.caption(f"Zone sélectionnée : X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm (largeur: {zone['x_max'] - zone['x_min']:.0f}mm)")
                                
                                st.number_input(
                                    "Position X (mm depuis le montant gauche)",
                                    value=div['position_x'],
                                    key=f"divider_position_x_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_divider_prop(x, 'position_x'),
                                    format="%.0f",
                                    step=1.0,
                                    min_value=float(selected_cab['dims']['t_lr_raw'] + 50),
                                    max_value=float(selected_cab['dims']['L_raw'] - selected_cab['dims']['t_lr_raw'] - 50),
                                    help="Position du montant dans le caisson" if is_first_divider else "Ajustement fin de la position"
                                )
                                st.number_input(
                                    "Épaisseur (mm)",
                                    value=div['thickness'],
                                    key=f"divider_thickness_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_divider_prop(x, 'thickness'),
                                    format="%.0f",
                                    step=1.0,
                                    min_value=10.0
                                )
                                st.text_input(
                                    "Matière",
                                    value=div.get('material', 'Matière Corps'),
                                    key=f"divider_material_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_divider_material(x)
                                )
                                st.button(
                                    "🗑️ Supprimer ce montant",
                                    key=f"del_divider_{idx}_{i}",
                                    on_click=lambda x=i: delete_vertical_divider_callback(x),
                                    use_container_width=True
                                )
                    
                    st.markdown("---")
                    st.markdown("#### Étagères Verticales")
                    st.info("Les étagères verticales créent des séparations partielles (pas de la traverse inf à la traverse sup). Elles peuvent lier étagère/traverse, traverse/étagère ou étagère/étagère.")
                    st.button("➕ Ajouter une Étagère Verticale", key=f"add_vertical_shelf_{idx}", on_click=add_vertical_shelf_callback, use_container_width=True)
                    
                    # --- POSE EN 2 TEMPS (APERÇU -> VALIDER) : ÉTAGÈRE VERTICALE ---
                    pending = st.session_state.get('pending_placement')
                    if pending and pending.get('cabinet_index') == idx and pending.get('kind') == 'vertical_shelf':
                        p = pending.get('props', {})
                        st.warning("Pose en cours : l'étagère verticale est en prévisualisation. Cliquez sur **Valider la position** pour la poser définitivement.")
                        with st.expander("✅ Valider la position (Étagère verticale)"):
                            all_zones_2d_sel = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                            if all_zones_2d_sel:
                                zone_options = [z['id'] for z in all_zones_2d_sel]
                                zone_labels = [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_sel]
                                current_zone = p.get('zone_id', zone_options[0])
                                zone_index = zone_options.index(current_zone) if current_zone in zone_options else 0
                                p['zone_id'] = st.selectbox(
                                    "Zone d'emplacement",
                                    options=zone_options,
                                    index=zone_index,
                                    format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Zone inconnue",
                                    key=f"pending_vs_zone_{idx}",
                                )
                                # Pendant la pose, on cale Y sur la zone sélectionnée (prévisualisation)
                                zone = all_zones_2d_sel[zone_index]
                                # S'assurer que les valeurs sont dans les limites valides avec clamping
                                dims = selected_cab['dims']
                                zone_y_min = float(zone['y_min'])
                                zone_y_max = float(zone['y_max'])
                                
                                # Calculer l'espace disponible entre les planches horizontales en tenant compte des épaisseurs
                                vs_thickness = float(p.get('thickness', 19.0))
                                position_x = float(p.get('position_x', (zone['x_min'] + zone['x_max']) / 2.0))
                                available_spaces, blocking_shelves = calculate_available_space_between_horizontal_shelves(
                                    selected_cab, zone['x_min'], zone['x_max'], position_x, vs_thickness
                                )
                                
                                # Trouver l'espace disponible qui correspond à la zone Y
                                matching_space = None
                                for space in available_spaces:
                                    if space['y_min'] <= zone_y_min and space['y_max'] >= zone_y_max:
                                        matching_space = space
                                        break
                                
                                if matching_space:
                                    # Utiliser les faces intérieures des planches comme limites
                                    bottom_y_min = max(0.0, matching_space['y_min_face'])
                                    bottom_y_max = min(float(dims['H_raw'] - 50), matching_space['y_max_face'])
                                    p['bottom_y'] = max(bottom_y_min, min(zone_y_min, bottom_y_max))
                                    top_y_min = float(p['bottom_y'] + 50)
                                    top_y_max = min(float(dims['H_raw']), matching_space['y_max_face'])
                                    p['top_y'] = max(top_y_min, min(zone_y_max, top_y_max))
                                else:
                                    # Fallback : utiliser les limites de la zone avec clamping
                                    bottom_y_min = 0.0
                                    bottom_y_max = float(dims['H_raw'] - 50)
                                    p['bottom_y'] = max(bottom_y_min, min(zone_y_min, bottom_y_max))
                                    top_y_min = float(p['bottom_y'] + 50)
                                    top_y_max = float(dims['H_raw'])
                                    p['top_y'] = max(top_y_min, min(zone_y_max, top_y_max))
                                
                                st.info(f"Hauteur Y : {p['bottom_y']:.0f}mm à {p['top_y']:.0f}mm (déterminée par la zone pendant la pose).")
                            else:
                                p['zone_id'] = None
                            
                            dims = selected_cab['dims']
                            # Retirer toutes les contraintes min/max pour éviter les erreurs StreamlitAPIException
                            # L'utilisateur peut entrer n'importe quelle valeur
                            p['thickness'] = st.number_input(
                                "Épaisseur (mm)",
                                value=float(p.get('thickness', 19.0)),
                                key=f"pending_vs_thickness_{idx}",
                                format="%.0f",
                                step=1.0,
                            )
                            p['position_x'] = st.number_input(
                                "Position X (mm depuis le montant gauche)",
                                value=float(p.get('position_x', 300.0)),
                                key=f"pending_vs_position_x_{idx}",
                                format="%.0f",
                                step=1.0,
                            )
                            # Si aucune zone n'est sélectionnée, permettre la saisie libre de bottom_y et top_y
                            if p.get('zone_id', None) is None:
                                p['bottom_y'] = st.number_input(
                                    "Position Y Bas - Hauteur bas (mm depuis traverse inférieure)",
                                    value=float(p.get('bottom_y', 100.0)),
                                    key=f"pending_vs_bottom_y_{idx}",
                                    format="%.0f",
                                    step=1.0,
                                )
                                
                                p['top_y'] = st.number_input(
                                    "Position Y Haut - Hauteur haut (mm depuis traverse inférieure)",
                                    value=float(p.get('top_y', float(p.get('bottom_y', 100.0)) + 200.0)),
                                    key=f"pending_vs_top_y_{idx}",
                                    format="%.0f",
                                    step=1.0,
                                )
                            
                            p['material'] = st.text_input(
                                "Matière",
                                value=p.get('material', 'Matière Corps'),
                                key=f"pending_vs_material_{idx}",
                            )
                            c_ok, c_cancel = st.columns(2)
                            if c_ok.button("Valider la position", key=f"pending_vs_validate_{idx}", use_container_width=True, type="primary"):
                                selected_cab.setdefault('vertical_shelves', [])
                                # Stocker les coordonnées de la zone avec l'étagère verticale pour référence future
                                if p.get('zone_id') is not None:
                                    all_zones_2d = calculate_all_zones_2d(selected_cab, include_all_elements=True)
                                    if p['zone_id'] < len(all_zones_2d):
                                        zone = all_zones_2d[p['zone_id']]
                                        p['stored_zone_coords'] = {
                                            'x_min': zone['x_min'],
                                            'x_max': zone['x_max'],
                                            'y_min': zone['y_min'],
                                            'y_max': zone['y_max']
                                        }
                                selected_cab['vertical_shelves'].append(copy.deepcopy(p))
                                st.session_state['pending_placement'] = None
                                st.rerun()
                            if c_cancel.button("Annuler", key=f"pending_vs_cancel_{idx}", use_container_width=True):
                                st.session_state['pending_placement'] = None
                                st.rerun()
                    
                    if 'vertical_shelves' in selected_cab and selected_cab['vertical_shelves']:
                        for i, vs in enumerate(selected_cab['vertical_shelves']):
                            with st.expander(f"📐 Étagère Verticale {i+1}"):
                                # Calculer les zones disponibles (SANS inclure les éléments sans zone_id)
                                all_zones_2d_for_selection = calculate_all_zones_2d(selected_cab, include_all_elements=False)
                                
                                # Sélection de la zone 2D (parmi les zones existantes AVANT placement)
                                current_zone_id = vs.get('zone_id', None)
                                zone_options = [z['id'] for z in all_zones_2d_for_selection]
                                zone_labels = [f"{z['label']} (X:{z['x_min']:.0f}-{z['x_max']:.0f}mm, Y:{z['y_min']:.0f}-{z['y_max']:.0f}mm)" for z in all_zones_2d_for_selection]
                                zone_index = zone_options.index(current_zone_id) if current_zone_id in zone_options else 0
                                
                                selected_zone_id = st.selectbox(
                                    "Zone d'emplacement",
                                    options=zone_options,
                                    index=zone_index,
                                    format_func=lambda x: zone_labels[zone_options.index(x)] if x in zone_options else "Zone inconnue",
                                    key=f"vertical_shelf_zone_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_shelf_prop(x, 'zone_id')
                                )
                                
                                # Si une zone est sélectionnée, afficher les limites mais NE PAS modifier les positions stockées
                                # Les éléments validés gardent leurs positions fixes
                                if selected_zone_id is not None and selected_zone_id < len(all_zones_2d_for_selection):
                                    zone = all_zones_2d_for_selection[selected_zone_id]
                                    # Afficher les limites de la zone
                                    st.caption(f"Zone sélectionnée : X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm, Y = {zone['y_min']:.0f}-{zone['y_max']:.0f}mm")
                                    
                                    # NE PAS modifier les positions stockées pour les éléments validés
                                    # Les positions sont modifiables uniquement via les number_input ci-dessous
                                    vs_th = vs.get('thickness', 19.0)
                                    min_x_in_zone = zone['x_min'] + vs_th / 2.0  # Au moins la moitié de l'épaisseur depuis le bord gauche
                                    max_x_in_zone = zone['x_max'] - vs_th / 2.0  # Au moins la moitié de l'épaisseur depuis le bord droit
                                    
                                    # Retirer toutes les contraintes min/max pour éviter les erreurs StreamlitAPIException
                                    # L'utilisateur peut entrer n'importe quelle valeur
                                    st.number_input(
                                        "Position X (mm depuis le montant gauche) - Déplacement gauche/droite",
                                        value=float(vs['position_x']),
                                        key=f"vertical_shelf_position_x_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'position_x'),
                                        format="%.0f",
                                        step=1.0,
                                        help=f"Zone sélectionnée : X = {zone['x_min']:.0f}-{zone['x_max']:.0f}mm"
                                    )
                                    
                                    st.number_input(
                                        "Position Y Bas - Hauteur bas (mm depuis traverse inférieure)",
                                        value=float(vs.get('bottom_y', 0.0)),
                                        key=f"vertical_shelf_bottom_y_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'bottom_y'),
                                        format="%.0f",
                                        step=1.0,
                                        help=f"Zone Y : {zone['y_min']:.0f}-{zone['y_max']:.0f}mm"
                                    )
                                    
                                    st.number_input(
                                        "Position Y Haut - Hauteur haut (mm depuis traverse inférieure)",
                                        value=float(vs.get('top_y', 100.0)),
                                        key=f"vertical_shelf_top_y_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'top_y'),
                                        format="%.0f",
                                        step=1.0,
                                        help=f"Zone Y : {zone['y_min']:.0f}-{zone['y_max']:.0f}mm"
                                    )
                                else:
                                    # Pas de zone sélectionnée : permettre tous les ajustements sans contraintes
                                    st.number_input(
                                        "Position X (mm depuis le montant gauche)",
                                        value=float(vs['position_x']),
                                        key=f"vertical_shelf_position_x_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'position_x'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                    
                                    st.number_input(
                                        "Position Y Bas - Hauteur bas (mm depuis traverse inférieure)",
                                        value=float(vs.get('bottom_y', 0.0)),
                                        key=f"vertical_shelf_bottom_y_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'bottom_y'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                    
                                    st.number_input(
                                        "Position Y Haut - Hauteur haut (mm depuis traverse inférieure)",
                                        value=float(vs.get('top_y', 100.0)),
                                        key=f"vertical_shelf_top_y_{idx}_{i}",
                                        on_change=lambda x=i: update_vertical_shelf_prop(x, 'top_y'),
                                        format="%.0f",
                                        step=1.0,
                                    )
                                st.number_input(
                                    "Épaisseur (mm)",
                                    value=vs['thickness'],
                                    key=f"vertical_shelf_thickness_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_shelf_prop(x, 'thickness'),
                                    format="%.0f",
                                    step=1.0,
                                )
                                st.text_input(
                                    "Matière",
                                    value=vs.get('material', 'Matière Corps'),
                                    key=f"vertical_shelf_material_{idx}_{i}",
                                    on_change=lambda x=i: update_vertical_shelf_material(x)
                                )
                                st.button(
                                    "🗑️ Supprimer cette étagère verticale",
                                    key=f"del_vertical_shelf_{idx}_{i}",
                                    on_click=lambda x=i: delete_vertical_shelf_callback(x),
                                    use_container_width=True
                                )

                with t_deb:
                    st.markdown(f"#### Feuille de Débit (Caisson {idx})")
                    debit_rows = selected_cab.get('debit_data', [])
                    
                    # Si debit_data est vide ou n'existe pas, l'initialiser avec les pièces de base
                    if not debit_rows:
                        debit_rows = get_default_debit_data()
                        selected_cab['debit_data'] = debit_rows
                    
                    df = pd.DataFrame(debit_rows)
                    
                    # S'assurer que toutes les colonnes nécessaires existent
                    required_cols = ["Référence Pièce", "Longueur (mm)", "Largeur (mm)", "Epaisseur", "Qté", "Usinage"]
                    chant_cols = ["Chant Avant", "Chant Arrière", "Chant Gauche", "Chant Droit"]
                    
                    # Ajouter les colonnes manquantes
                    for col in required_cols:
                        if col not in df.columns:
                            if col == "Usinage":
                                df[col] = ""
                            elif col == "Qté":
                                df[col] = 1
                            else:
                                df[col] = 0
                    
                    # Recalculer automatiquement Longueur/Largeur/Epaisseur pour les pièces structurelles
                    dims = selected_cab['dims']
                    fileur_w = float(selected_cab.get('door_props', {}).get('fileur_width', 0) or 0.0)
                    L_eff = max(0.0, float(dims['L_raw']) - fileur_w)
                    t_lr, t_tb, t_fb = dims['t_lr_raw'], dims['t_tb_raw'], dims['t_fb_raw']
                    h_side = dims['H_raw']
                    L_traverse = max(0.0, L_eff - 2 * t_lr)
                    dim_fond_vertical = dims['H_raw'] - 2.0
                    dim_fond_horizontal = max(0.0, L_eff - 2.0)
                    panel_dims = {
                        "Traverse Bas": (L_traverse, dims['W_raw'], t_tb),
                        "Traverse Haut": (L_traverse, dims['W_raw'], t_tb),
                        "Montant Gauche": (h_side, dims['W_raw'], t_lr),
                        "Montant Droit": (h_side, dims['W_raw'], t_lr),
                        "Fond": (dim_fond_vertical, dim_fond_horizontal, t_fb),
                    }
                    for row_idx, row in df.iterrows():
                        ref = str(row.get("Référence Pièce", ""))
                        ref_key = ref.split(" (")[0].strip()
                        if "Etagère" in ref_key or "Étagère" in ref_key:
                            # Règle demandée : étagères fixes et mobiles = dimensions traverses.
                            df.at[row_idx, "Longueur (mm)"] = L_traverse
                            df.at[row_idx, "Largeur (mm)"] = dims['W_raw']
                            continue
                        for key, dims_tuple in panel_dims.items():
                            if key in ref_key:
                                df.at[row_idx, "Longueur (mm)"] = dims_tuple[0]
                                df.at[row_idx, "Largeur (mm)"] = dims_tuple[1]
                                df.at[row_idx, "Epaisseur"] = dims_tuple[2]
                                break
                    
                    # S'assurer que les colonnes de chant existent et sont bien booléennes
                    for col in chant_cols:
                        if col not in df.columns:
                            df[col] = False
                        else:
                            # Convertir en booléen si ce n'est pas déjà le cas
                            df[col] = df[col].fillna(False).astype(bool)
                    
                    # Réorganiser les colonnes pour un meilleur affichage
                    display_cols = required_cols + chant_cols
                    df = df[[col for col in display_cols if col in df.columns] + [col for col in df.columns if col not in display_cols]]
                    
                    edited_df = st.data_editor(
                        df,
                        key=f"editor_{idx}",
                        hide_index=True,
                        column_config={
                            "Chant Avant": st.column_config.CheckboxColumn("Chant Avant", default=False),
                            "Chant Arrière": st.column_config.CheckboxColumn("Chant Arrière", default=False),
                            "Chant Gauche": st.column_config.CheckboxColumn("Chant Gauche", default=False),
                            "Chant Droit": st.column_config.CheckboxColumn("Chant Droit", default=False),
                        },
                        num_rows="dynamic"
                    )
                    
                    # Écrire les modifications dans l'état du caisson
                    st.session_state['scene_cabinets'][idx]['debit_data'] = edited_df.to_dict(orient="records")

all_calculated_parts, shelf_dims_cache = get_cached_project_parts()

with col2:
    sel_idx = st.session_state.get('selected_cabinet_index')
    if sel_idx is None and st.session_state['scene_cabinets']: sel_idx = 0
    cab_for_check = st.session_state['scene_cabinets'][sel_idx] if sel_idx is not None and 0 <= sel_idx < len(st.session_state['scene_cabinets']) else None
    
    # Désactivation des alertes de collision d'usinage
    # collisions = []
    # (Code de détection de collision désactivé)

    st.header("Prévisualisation 3D")
    st.caption("Cette zone sert a verifier visuellement le meuble avant export.")
    st.markdown('<div class="kb-step">Etape 3 - Controle de la previsualisation 3D</div>', unsafe_allow_html=True)
    st.markdown('<p class="kb-note">Tournez la vue et verifiez la coherence de la structure avant export.</p>', unsafe_allow_html=True)
    fig3d = go.Figure()
    scene = st.session_state['scene_cabinets']
    unit_factor = {"mm":0.001,"cm":0.01,"m":1.0}[st.session_state.unit_select]
    abs_origins = calculate_origins_recursively(st.session_state.scene_cabinets, unit_factor)
    
    BODY_COLOR = "#D6C098"
    ACCESSORY_COLOR = "#B8A078"
    BODY_OPACITY = 1.0
    ACCESSORY_OPACITY = 1.0
    
    if not st.session_state['scene_cabinets']:
        st.info("La scène est vide.")
    else:
        pending = st.session_state.get('pending_placement')
        for i, cab in enumerate(st.session_state['scene_cabinets']):
            # IMPORTANT: rendre à partir d'une copie pour éviter toute mutation involontaire pendant l'affichage
            cab_render = copy.deepcopy(cab)
            if pending and pending.get('cabinet_index') == i:
                kind = pending.get('kind')
                p = pending.get('props', {})
                if kind == 'vertical_divider':
                    cab_render.setdefault('vertical_dividers', []).append({**copy.deepcopy(p), '_preview': True})
                elif kind == 'vertical_divider_double':
                    # Double montant : 2 montants centrés sur position_x (bords se touchent).
                    th = float(p.get('thickness', 19.0))
                    L_raw = cab.get('dims', {}).get('L_raw', 0.0)
                    center_x = float(p.get('position_x', (L_raw / 2.0 if L_raw else 0.0)))
                    pos_left = center_x - th / 2.0
                    pos_right = center_x + th / 2.0
                    div_left = {**copy.deepcopy(p), 'position_x': pos_left, '_preview': True}
                    div_right = {**copy.deepcopy(p), 'position_x': pos_right, '_preview': True}
                    cab_render.setdefault('vertical_dividers', []).extend([div_left, div_right])
                elif kind == 'vertical_shelf':
                    cab_render.setdefault('vertical_shelves', []).append({**copy.deepcopy(p), '_preview': True})
                elif kind == 'shelf':
                    # Ajouter l'étagère en preview avec le flag _preview
                    shelf_preview = copy.deepcopy(p)
                    shelf_preview['_preview'] = True
                    cab_render.setdefault('shelves', []).append(shelf_preview)
                elif kind == 'shelf_stack':
                    stack_count = int(p.get('stack_count', 3))
                    zones_for_preview = calculate_all_zones_2d(cab, include_all_elements=False)
                    zone_id = p.get('zone_id', None)
                    dims_prev = cab.get('dims', {})
                    t_tb_mm = float(dims_prev.get('t_tb_raw', 19.0))
                    H_raw = float(dims_prev.get('H_raw', 1000.0))

                    if zone_id is not None and 0 <= int(zone_id) < len(zones_for_preview) and stack_count >= 1:
                        z = zones_for_preview[int(zone_id)]
                        y_min = max(0.0, float(z['y_min']) - t_tb_mm)
                        y_max = max(y_min, float(z['y_max']) - t_tb_mm)
                    else:
                        y_min = 0.0
                        y_max = max(0.0, H_raw - 2.0 * t_tb_mm)

                    zone_height = max(0.0, y_max - y_min)
                    spacing = zone_height / float(stack_count + 1) if stack_count > 0 else 0.0

                    for k in range(stack_count):
                        s_prev = copy.deepcopy(p)
                        s_prev['_preview'] = True
                        s_prev['shelf_type'] = 'fixe'
                        s_prev['height'] = y_min + (k + 1) * spacing
                        cab_render.setdefault('shelves', []).append(s_prev)
                elif kind in ('drawer', 'drawer_stack'):
                    # Ajouter le(s) tiroir(s) en preview avec le flag _preview
                    # - drawer : tiroir unique
                    # - drawer_stack : tiroirs empilés automatiquement (sans demander de dimensions)
                    if kind == 'drawer_stack':
                        # Construire une preview d'empilement si une zone est sélectionnée
                        stack_count = int(p.get('stack_count', 3))
                        # Zones calculées SANS inclure les tiroirs
                        zones_for_preview = calculate_all_zones_2d(cab, include_all_elements=False)
                        zone_id = p.get('zone_id', None)
                        dims = cab['dims']
                        t_tb_mm = float(dims.get('t_tb_raw', 19.0))
                        H_raw = float(dims.get('H_raw', 1000.0))
                        
                        if zone_id is not None and 0 <= int(zone_id) < len(zones_for_preview) and stack_count >= 1:
                            z = zones_for_preview[int(zone_id)]
                            
                            # Vérifier le mode (encastré ou appliqué)
                            is_applique = bool(p.get('_applique_mode', False))
                            
                            if is_applique:
                                # Mode APPLIQUE : formule H_raw - n*2mm - 2x1mm (jeu de 1mm haut/bas)
                                n_junctions = stack_count - 1
                                total_face_height = H_raw - (n_junctions * 2.0) - 2.0
                                face_h = total_face_height / float(stack_count) if stack_count > 0 else 0.0
                                if face_h < 10.0:
                                    face_h = 10.0
                                # Position : début du caisson avec 1mm de jeu
                                current_z_offset = -1.0
                            else:
                                # Mode ENCASTE : formule H_raw - 2*t_tb - 4mm - n*2mm
                                n_junctions = stack_count - 1
                                total_face_height = H_raw - 2.0 * t_tb_mm - 4.0 - (n_junctions * 2.0)
                                face_h = total_face_height / float(stack_count) if stack_count > 0 else 0.0
                                if face_h < 10.0:
                                    face_h = 10.0
                                current_z_offset = t_tb_mm + 2.0
                            
                            for k in range(stack_count):
                                d_prev = copy.deepcopy(p)
                                d_prev['_preview'] = True
                                d_prev['drawer_face_H_raw'] = face_h
                                d_prev['drawer_bottom_offset'] = current_z_offset
                                cab_render.setdefault('drawers', []).append(d_prev)
                                # Préparer offset pour prochain tiroir (hauteur + 2mm gap)
                                current_z_offset += face_h + 2.0
                        else:
                            # Pas de zone : fallback sur un tiroir unique preview
                            drawer_preview = copy.deepcopy(p)
                            drawer_preview['_preview'] = True
                            cab_render.setdefault('drawers', []).append(drawer_preview)
                    else:
                        drawer_preview = copy.deepcopy(p)
                        drawer_preview['_preview'] = True
                        cab_render.setdefault('drawers', []).append(drawer_preview)
            o = abs_origins[i]; d = cab['dims']; L, W, H = d['L_raw']*unit_factor, d['W_raw']*unit_factor, d['H_raw']*unit_factor
            tl, tb, tt = d['t_lr_raw']*unit_factor, d['t_fb_raw']*unit_factor, d['t_tb_raw']*unit_factor

            # Fileur : le caisson perd la largeur du fileur côté ouverture
            _dp_f = cab.get('door_props', {})
            _fileur_mm = float(_dp_f.get('fileur_width', 0)) * unit_factor
            if _fileur_mm > 0:
                L = L - _fileur_mm
                if _dp_f.get('door_opening', 'right') == 'left':
                    o = (o[0] + _fileur_mm, o[1], o[2])

            # Récupérer les préférences des éléments de base (par défaut tous activés)
            base_el = cab.get('base_elements', {
                'has_back_panel': True,
                'has_left_upright': True,
                'has_right_upright': True,
                'has_bottom_traverse': True,
                'has_top_traverse': True
            })
            
            # Traverse Bas
            if base_el.get('has_bottom_traverse', True):
                fig3d.add_trace(cuboid_mesh_for(L-2*tl, W, tt, (o[0]+tl, o[1], o[2]), color=BODY_COLOR, opacity=BODY_OPACITY, showlegend=False))
            # Traverse Haut
            if base_el.get('has_top_traverse', True):
                fig3d.add_trace(cuboid_mesh_for(L-2*tl, W, tt, (o[0]+tl, o[1], o[2]+H-tt), color=BODY_COLOR, opacity=BODY_OPACITY, showlegend=False))
            # Montant Gauche
            if base_el.get('has_left_upright', True):
                fig3d.add_trace(cuboid_mesh_for(tl, W, H, (o[0], o[1], o[2]), color=BODY_COLOR, opacity=BODY_OPACITY, showlegend=False))
            # Montant Droit
            if base_el.get('has_right_upright', True):
                fig3d.add_trace(cuboid_mesh_for(tl, W, H, (o[0]+L-tl, o[1], o[2]), color=BODY_COLOR, opacity=BODY_OPACITY, showlegend=False))
            # Panneau Arrière (Fond)
            if base_el.get('has_back_panel', True):
                fig3d.add_trace(cuboid_mesh_for(L-2*tl, tb, H-2*tt, (o[0]+tl, o[1]+W-tb, o[2]+tt), color=BODY_COLOR, opacity=BODY_OPACITY, showlegend=False))
            
            # Rendu des montants verticaux secondaires AVANT TOUS les autres éléments pour qu'ils soient visibles
            if 'vertical_dividers' in cab_render and cab_render['vertical_dividers']:
                DIVIDER_COLOR = "#8B7355"
                for div in cab_render['vertical_dividers']:
                    # position_x est en mm depuis le début du caisson (o[0])
                    div_x_mm = div['position_x']
                    div_th_mm = div.get('thickness', 19.0)
                    div_x = div_x_mm * unit_factor
                    div_th = div_th_mm * unit_factor
                    is_preview = bool(div.get('_preview'))
                    # Montant vertical : position X = o[0] + div_x_mm (en unités)
                    # Le montant va de (div_x_mm - div_th_mm/2) à (div_x_mm + div_th_mm/2) en mm
                    # Profondeur : W - tb pour ne pas traverser le panneau arrière
                    fig3d.add_trace(cuboid_mesh_for(
                        div_th, W - tb, H-2*tt,
                        (o[0] + div_x - div_th/2, o[1], o[2] + tt),
                        color=("#666666" if is_preview else DIVIDER_COLOR),
                        opacity=(0.35 if is_preview else BODY_OPACITY),
                        showlegend=False
                    ))
            
            # Rendu des étagères verticales (après les montants secondaires mais avant les autres éléments)
            if 'vertical_shelves' in cab_render and cab_render['vertical_shelves']:
                VERTICAL_SHELF_COLOR = "#A0826D"
                all_zones_2d = calculate_all_zones_2d(cab_render)
                
                for vs in cab_render['vertical_shelves']:
                    vs_th_mm = vs.get('thickness', 19.0)
                    is_preview = bool(vs.get('_preview'))
                    
                    # Utiliser TOUJOURS les positions stockées (ne JAMAIS recalculer)
                    # Cela garantit que l'étagère validée ne déplace pas d'autres éléments
                    # Pour les éléments en prévisualisation, utiliser les valeurs du pending
                    vs_x_mm = vs.get('position_x', 300.0)
                    vs_bottom_y_mm = vs.get('bottom_y', 0.0)
                    vs_top_y_mm = vs.get('top_y', 100.0)
                    
                    # NE JAMAIS modifier les positions stockées - utiliser telles quelles pour le rendu
                    # Les éléments validés gardent leurs positions fixes, même si les zones changent
                    
                    vs_height_mm = vs_top_y_mm - vs_bottom_y_mm
                    vs_x = vs_x_mm * unit_factor
                    vs_th = vs_th_mm * unit_factor
                    vs_height = vs_height_mm * unit_factor
                    vs_bottom_z = o[2] + tt + (vs_bottom_y_mm * unit_factor)
                    
                    # Validation du placement pour les étagères verticales en preview
                    is_valid_placement = True
                    if is_preview:
                        all_zones_2d_for_validation = calculate_all_zones_2d(cab_render, include_all_elements=True)
                        is_valid_placement, validation_reason = check_element_placement_validity(vs, all_zones_2d_for_validation, cab_render, element_type='vertical_shelf')
                    
                    # Choisir la couleur selon la validité du placement
                    if is_preview and not is_valid_placement:
                        vs_color = "rgba(255, 0, 0, 1.0)"  # Rouge vif pour placement invalide
                        vs_opacity = 0.8
                    else:
                        vs_color = "#666666" if is_preview else VERTICAL_SHELF_COLOR
                        vs_opacity = 0.35 if is_preview else BODY_OPACITY
                    
                    # Étagère verticale : position X = o[0] + vs_x_mm (en unités)
                    # L'étagère va de (vs_x_mm - vs_th_mm/2) à (vs_x_mm + vs_th_mm/2) en mm
                    fig3d.add_trace(cuboid_mesh_for(
                        vs_th, W, vs_height,
                        (o[0] + vs_x - vs_th/2, o[1], vs_bottom_z),
                        color=vs_color,
                        opacity=vs_opacity,
                        showlegend=False
                    ))

            zones_render_all = calculate_all_zones_2d(cab_render, include_all_elements=True)
            zones_render_without_elements = calculate_all_zones_2d(cab_render, include_all_elements=False)
            
            if cab_render['door_props']['has_door']:
                dp = cab_render['door_props']; gap = dp['door_gap'] * unit_factor; thk = dp.get('door_thickness', 19.0) * unit_factor; dy = o[1] - thk
                if dp.get('door_model') == 'floor_length':
                    cache_pied_drop = 80.0 * unit_factor
                    # La porte cache-pieds descend de 80 mm sous le meuble.
                    dH = H + cache_pied_drop - gap
                    dz = o[2] - cache_pied_drop
                else:
                    dH = H - 2*gap
                    dz = o[2] + gap
                rot_angle = 45 if dp.get('door_opening')=='right' else -45
                
                # Vérifier si une zone est assignée
                zone_id = dp.get('zone_id', None)
                
                if zone_id is not None and zone_id < len(zones_render_all):
                    # Porte dans une zone spécifique - strictement limitée à la zone
                    zone = zones_render_all[zone_id]
                    zone_x_min_mm = zone['x_min']
                    zone_x_max_mm = zone['x_max']
                    zone_x_min_abs = o[0] + (zone_x_min_mm * unit_factor)
                    zone_x_max_abs = o[0] + (zone_x_max_mm * unit_factor)
                    zone_width_abs = zone_x_max_abs - zone_x_min_abs
                    # La porte doit être dans la zone, avec le gap + marge de sécurité
                    safety_margin_mm = 2.0  # 2mm pour éviter de toucher les montants
                    safety_margin = safety_margin_mm * unit_factor
                    dW_zone = zone_width_abs - 2*gap - 2*safety_margin
                    # Position de départ de la porte dans la zone
                    door_x_start = zone_x_min_abs + gap + safety_margin
                    pivot_x = zone_x_max_abs - gap - safety_margin if dp.get('door_opening')=='right' else zone_x_min_abs + gap + safety_margin
                    if dW_zone > 0:
                        fig3d.add_trace(cuboid_mesh_for(dW_zone, thk, dH, (door_x_start, dy, dz), color=ACCESSORY_COLOR, opacity=ACCESSORY_OPACITY, name=f"Porte {i}", rotation_angle=rot_angle, rotation_axis='z', rotation_pivot=(pivot_x, dy, dz)))
                else:
                    # Porte sur tout le caisson
                    if dp.get('door_type') == 'single':
                        pivot_x = o[0] + L - gap if dp.get('door_opening')=='right' else o[0] + gap
                        fig3d.add_trace(cuboid_mesh_for(L-2*gap, thk, dH, (o[0]+gap, dy, dz), color=ACCESSORY_COLOR, opacity=ACCESSORY_OPACITY, name=f"Porte {i}", rotation_angle=rot_angle, rotation_axis='z', rotation_pivot=(pivot_x, dy, dz)))
                    else:
                        dl_half = (L-2*gap)/2; pivot_g = o[0] + gap; pivot_d = o[0] + L - gap
                        fig3d.add_trace(cuboid_mesh_for(dl_half, thk, dH, (o[0]+gap, dy, dz), color=ACCESSORY_COLOR, opacity=ACCESSORY_OPACITY, name=f"Porte G {i}", rotation_angle=-45, rotation_axis='z', rotation_pivot=(pivot_g, dy, dz)))
                        fig3d.add_trace(cuboid_mesh_for(dl_half, thk, dH, (o[0]+L-gap-dl_half, dy, dz), color=ACCESSORY_COLOR, opacity=ACCESSORY_OPACITY, name=f"Porte D {i}", rotation_angle=45, rotation_axis='z', rotation_pivot=(pivot_d, dy, dz)))

                # ── Fileur ─────────────────────────────────────────────
                fileur_w_mm = float(dp.get('fileur_width', 0))
                if fileur_w_mm > 0:
                    fil_w  = fileur_w_mm * unit_factor
                    fil_th = 19.0 * unit_factor          # épaisseur 19 mm
                    fil_H  = dH                          # même hauteur que la porte
                    fil_y  = dy                          # même plan façade
                    fil_z  = dz
                    # Côté du fileur : côté ouverture (côté libre de la porte)
                    if dp.get('door_opening', 'right') == 'right':
                        # Ouverture à droite → fileur à droite du caisson
                        fil_x = o[0] + L
                    else:
                        # Ouverture à gauche → fileur à gauche du caisson
                        fil_x = o[0] - fil_w
                    fig3d.add_trace(cuboid_mesh_for(
                        fil_w, fil_th, fil_H,
                        (fil_x, fil_y, fil_z),
                        color="#C8A96E",   # bois naturel distinct
                        opacity=BODY_OPACITY,
                        name=f"Fileur {i}"
                    ))

            # Rendu de tous les tiroirs
            if 'drawers' in cab_render and cab_render['drawers']:
                # IMPORTANT : Calculer les zones SANS inclure les tiroirs (include_all_elements=False)
                # Les tiroirs ne créent pas de zones, ils sont placés dans des zones existantes
                all_zones_2d_drawers = zones_render_without_elements

                # Identifier les zone_ids occupées par des tiroirs à l'anglaise
                # (pour rendre les tiroirs classiques de la même zone semi-transparents)
                anglaise_zone_ids = set()
                for _drp_check in cab_render['drawers']:
                    if _drp_check.get('drawer_system') == 'ANGLAISE':
                        _zid = _drp_check.get('zone_id')
                        if _zid is not None:
                            anglaise_zone_ids.add(_zid)
                
                for drawer_idx, drp in enumerate(cab_render['drawers']):
                    gap = drp.get('drawer_gap', 2.0) * unit_factor
                    thk = drp.get('drawer_face_thickness', 19.0) * unit_factor
                    is_preview = bool(drp.get('_preview'))
                    drawer_system = drp.get('drawer_system', 'TANDEMBOX')
                    
                    # Vérifier si une zone est assignée
                    zone_id = drp.get('zone_id', None)
                    
                    if zone_id is not None and zone_id < len(all_zones_2d_drawers):
                        # Tiroir dans une zone spécifique
                        zone = all_zones_2d_drawers[zone_id]
                        zone_x_min_mm = zone['x_min']
                        zone_x_max_mm = zone['x_max']
                        
                        zone_x_min_abs = o[0] + (zone_x_min_mm * unit_factor)
                        zone_x_max_abs = o[0] + (zone_x_max_mm * unit_factor)
                        
                        zone_width_abs = zone_x_max_abs - zone_x_min_abs
                        interior_depth = W - 2 * tb
                        
                        # Hauteur du tiroir : utiliser directement la hauteur stockée
                        drawer_height = drp.get('drawer_face_H_raw', 150.0) * unit_factor
                        
                        # Vérifier le mode (encastré ou en applique)
                        is_applique = bool(drp.get('_applique_mode', False))
                        
                        if drawer_system == 'ANGLAISE':
                            # TIROIR À L'ANGLAISE : face posée 40mm à l'intérieur du caisson
                            # Largeur face = largeur zone - 4mm (2mm de jeu de chaque côté)
                            anglaise_inset_depth = 40.0 * unit_factor
                            anglaise_side_gap = 2.0 * unit_factor
                            dW_zone = max(0.0, zone_width_abs - 2.0 * anglaise_side_gap)
                            drawer_x_start = zone_x_min_abs + anglaise_side_gap
                            # Face à 40mm de profondeur depuis la façade
                            drawer_depth = drp.get('drawer_face_thickness', 19.0) * unit_factor
                            drawer_y_pos = o[1] + anglaise_inset_depth
                        elif is_applique:
                            # Mode EN APPLIQUE : recouvrir les montants adjacents des deux côtés
                            # Avec jeu de pose latéral: 2 mm de chaque côté
                            # Largeur façade = largeur zone + 2x épaisseur montant - 2x jeu
                            t_montant_mm = float(cab_render['dims'].get('t_lr_raw', 19.0))
                            side_clearance = 2.0 * unit_factor
                            dW_zone = max(0.0, zone_width_abs + (2.0 * t_montant_mm * unit_factor) - (2.0 * side_clearance))
                            drawer_x_start = zone_x_min_abs - (t_montant_mm * unit_factor) + side_clearance
                            # Profondeur : juste l'épaisseur de la face du tiroir
                            drawer_depth = drp.get('drawer_face_thickness', 19.0) * unit_factor
                            # Position Y : les tiroirs sortent AVANT le panneau avant
                            drawer_y_pos = o[1] - drawer_depth
                        else:
                            # Mode ENCASTE : largeur zone - jeux
                            dW_zone = zone_width_abs - 2 * gap
                            drawer_x_start = zone_x_min_abs + gap
                            # Mode ENCASTE : profondeur interieure du caisson avec retrait avant.
                            drawer_depth = interior_depth - (19.0 * unit_factor)
                            drawer_y_pos = o[1] + tb
                        
                        # Position Z (hauteur) : drawer_bottom_offset incluit déjà t_tb depuis le fond du caisson
                        drawer_z_pos = o[2] + (drp.get('drawer_bottom_offset', 0.0) * unit_factor)
                        
                        if dW_zone > 0 and drawer_depth > 0:
                            drawer_color = "#666666" if is_preview else (
                                "#8B4513" if drawer_system == 'ANGLAISE' else ACCESSORY_COLOR
                            )
                            # Tiroir classique dans la même zone qu'un tiroir à l'anglaise → semi-transparent
                            same_zone_as_anglaise = (zone_id in anglaise_zone_ids) and drawer_system != 'ANGLAISE'
                            if is_preview:
                                drawer_opacity = 0.35
                            elif same_zone_as_anglaise:
                                drawer_opacity = 0.25
                            else:
                                drawer_opacity = ACCESSORY_OPACITY
                            fig3d.add_trace(cuboid_mesh_for(
                                dW_zone, drawer_depth, drawer_height,
                                (drawer_x_start, drawer_y_pos, drawer_z_pos),
                                color=drawer_color,
                                opacity=drawer_opacity,
                                name=f"Tiroir {'Anglaise' if drawer_system == 'ANGLAISE' else ''} {i}-{drawer_idx}"
                            ))
                    else:
                        # Tiroir sur tout le caisson
                        # Vérifier le mode (encastré ou en applique)
                        is_applique = bool(drp.get('_applique_mode', False))
                        
                        # Hauteur et position Z du tiroir
                        drawer_height = drp.get('drawer_face_H_raw', 150.0) * unit_factor
                        drawer_z_pos = o[2] + (drp.get('drawer_bottom_offset', 0.0) * unit_factor)
                        
                        if drawer_system == 'ANGLAISE':
                            # TIROIR À L'ANGLAISE : 40mm inside, 2mm de jeu latéral de chaque côté
                            anglaise_inset_depth = 40.0 * unit_factor
                            anglaise_side_gap = 2.0 * unit_factor
                            dW_zone = max(0.0, L - 2.0 * anglaise_side_gap)
                            drawer_x_start = o[0] + anglaise_side_gap
                            drawer_depth = drp.get('drawer_face_thickness', 19.0) * unit_factor
                            drawer_y_pos = o[1] + anglaise_inset_depth
                        elif is_applique:
                            # Mode EN APPLIQUE : recouvrir toute la largeur + épaisseurs montants + 1mm jeu
                            t_montant_mm = float(cab_render['dims'].get('t_lr_raw', 19.0))
                            dW_zone = L + (2.0 * t_montant_mm * unit_factor) + (2.0 * unit_factor)
                            drawer_x_start = o[0] - (t_montant_mm * unit_factor) - (1.0 * unit_factor)
                            # Profondeur : juste l'épaisseur de la face
                            drawer_depth = drp.get('drawer_face_thickness', 19.0) * unit_factor
                            drawer_y_pos = o[1] - drawer_depth
                        else:
                            # Mode ENCASTE : profondeur = intérieur caisson - retrait 19mm
                            dW_zone = L - 2 * gap
                            drawer_x_start = o[0] + gap
                            # Profondeur intérieure du caisson
                            interior_depth = W - 2 * tb
                            # Tiroir : profondeur intérieure - 19mm retrait
                            drawer_depth = interior_depth - (19.0 * unit_factor)
                            # Position : commencer après la traverse avant
                            drawer_y_pos = o[1] + tb
                        
                        drawer_color = "#666666" if is_preview else (
                            "#8B4513" if drawer_system == 'ANGLAISE' else ACCESSORY_COLOR
                        )
                        drawer_opacity = 0.35 if is_preview else ACCESSORY_OPACITY
                        fig3d.add_trace(cuboid_mesh_for(
                            dW_zone, drawer_depth, drawer_height,
                            (drawer_x_start, drawer_y_pos, drawer_z_pos),
                            color=drawer_color,
                            opacity=drawer_opacity,
                            name=f"Tiroir {'Anglaise' if drawer_system == 'ANGLAISE' else ''} {i}-{drawer_idx}"
                        ))

            if 'shelves' in cab_render:
                for s in cab_render['shelves']:
                    sh_z = o[2] + tt + (s['height'] * unit_factor)
                    is_preview = bool(s.get('_preview'))
                    s_type = s.get('shelf_type', 'mobile')
                    # IMPORTANT : Utiliser TOUJOURS les positions stockées pour les étagères validées
                    # Ne JAMAIS recalculer la position à partir de la zone pour les éléments validés
                    # Les zones peuvent changer, mais les positions stockées restent fixes
                    zone_id = s.get('zone_id', None)
                    
                    # Rechercher la zone correspondante
                    zone = None
                    if zone_id is not None:
                        all_zones_2d = zones_render_all
                        
                        if is_preview:
                            # Pour les éléments en preview, utiliser zone_id directement
                            if zone_id < len(all_zones_2d):
                                zone = all_zones_2d[zone_id]
                        else:
                            # Pour les éléments validés, essayer d'abord de trouver par coordonnées stockées
                            stored_zone_coords = s.get('stored_zone_coords', None)
                            if stored_zone_coords:
                                # Chercher la zone correspondante par coordonnées
                                for z in all_zones_2d:
                                    if (abs(z['x_min'] - stored_zone_coords['x_min']) < 0.1 and
                                        abs(z['x_max'] - stored_zone_coords['x_max']) < 0.1 and
                                        abs(z['y_min'] - stored_zone_coords['y_min']) < 0.1 and
                                        abs(z['y_max'] - stored_zone_coords['y_max']) < 0.1):
                                        zone = z
                                        break
                            
                            # Si pas trouvé par coordonnées, utiliser zone_id (comportement legacy ou fallback)
                            if zone is None and zone_id < len(all_zones_2d):
                                zone = all_zones_2d[zone_id]
                    
                    if zone is not None:
                        # IMPORTANT : Pour les étagères validées, utiliser la largeur et position stockées
                        # pour éviter que leur largeur change quand de nouveaux éléments sont ajoutés
                        if not is_preview and s.get('stored_shelf_width_mm') is not None and s.get('stored_shelf_x_start_mm') is not None:
                            # Utiliser les valeurs stockées directement pour les éléments validés
                            shelf_width = s['stored_shelf_width_mm'] * unit_factor
                            shelf_x_start = o[0] + (s['stored_shelf_x_start_mm'] * unit_factor)
                        else:
                            # Pour les éléments en preview, recalculer à partir de la zone
                            zone_x_min_mm = zone['x_min']  # En mm
                            zone_x_max_mm = zone['x_max']  # En mm
                            
                            # Convertir en coordonnées absolues pour la 3D
                            zone_x_min_abs = o[0] + (zone_x_min_mm * unit_factor)
                            zone_x_max_abs = o[0] + (zone_x_max_mm * unit_factor)
                            zone_width_abs = zone_x_max_abs - zone_x_min_abs
                            
                            # L'étagère doit être STRICTEMENT dans la zone, sans toucher les montants
                            # Calculer les positions des montants verticaux (principaux et secondaires) pour vérification
                            # IMPORTANT : Les étagères horizontales doivent toucher les montants principaux OU secondaires,
                            # mais PAS les étagères verticales
                            divider_bounds = []
                            # Montant gauche (principal)
                            divider_bounds.append((o[0], o[0] + tl))
                            # Montant droit (principal)
                            divider_bounds.append((o[0] + L - tl, o[0] + L))
                            # Montants secondaires
                            if 'vertical_dividers' in cab_render:
                                for div in cab_render['vertical_dividers']:
                                    div_x_mm = div['position_x']
                                    div_th_mm = div.get('thickness', 19.0)
                                    div_x_min_abs = o[0] + (div_x_mm - div_th_mm/2.0) * unit_factor
                                    div_x_max_abs = o[0] + (div_x_mm + div_th_mm/2.0) * unit_factor
                                    divider_bounds.append((div_x_min_abs, div_x_max_abs))
                            # Les étagères verticales NE sont PAS ajoutées - elles ne sont pas des montants
                            
                            # PLUS DE JEU : l'étagère suit exactement la largeur de la zone en X
                            if s_type == 'mobile':
                                shelf_width = zone_width_abs
                                shelf_x_start = zone_x_min_abs
                            else:
                                shelf_width = zone_width_abs
                                shelf_x_start = zone_x_min_abs
                        
                        # Calculer les positions des montants verticaux pour vérification de collision
                        divider_bounds = []
                        # Montant gauche (principal)
                        divider_bounds.append((o[0], o[0] + tl))
                        # Montant droit (principal)
                        divider_bounds.append((o[0] + L - tl, o[0] + L))
                        # Montants secondaires
                        if 'vertical_dividers' in cab_render:
                            for div in cab_render['vertical_dividers']:
                                div_x_mm = div['position_x']
                                div_th_mm = div.get('thickness', 19.0)
                                div_x_min_abs = o[0] + (div_x_mm - div_th_mm/2.0) * unit_factor
                                div_x_max_abs = o[0] + (div_x_mm + div_th_mm/2.0) * unit_factor
                                divider_bounds.append((div_x_min_abs, div_x_max_abs))
                        # Les étagères verticales NE sont PAS ajoutées - elles ne sont pas des montants
                            
                            # Vérification de sécurité : s'assurer que l'étagère ne dépasse pas
                            shelf_x_end = shelf_x_start + shelf_width
                            
                        # Pour les éléments validés avec valeurs stockées, on considère qu'ils sont toujours valides
                        # Pour les éléments en preview, vérifier qu'ils sont dans la zone
                        if not is_preview and s.get('stored_shelf_width_mm') is not None:
                            in_zone = True  # Les éléments validés sont toujours considérés comme valides
                            touches_divider = False  # Pas de vérification de collision pour les éléments validés
                        else:
                            # Pour les éléments en preview, vérifier qu'ils sont dans la zone
                            zone_x_min_abs = o[0] + (zone['x_min'] * unit_factor)
                            zone_x_max_abs = o[0] + (zone['x_max'] * unit_factor)
                            in_zone = shelf_width > 0 and shelf_x_start >= zone_x_min_abs and shelf_x_end <= zone_x_max_abs
                            touches_divider = False
                            
                            if in_zone and divider_bounds:
                                for div_min, div_max in divider_bounds:
                                    # Vérifier si l'étagère chevauche le montant (avec une petite tolérance)
                                    if (shelf_x_start <= div_max + 0.001 and shelf_x_end >= div_min - 0.001):
                                        touches_divider = True
                                        break
                            
                        # Validation du placement : vérifier si l'élément est dans une zone valide
                        is_valid_placement = True
                        if is_preview:
                            # Pour les éléments en preview, vérifier la validité du placement
                            all_zones_2d_for_validation = zones_render_all
                            is_valid_placement, validation_reason = check_element_placement_validity(s, all_zones_2d_for_validation, cab_render, element_type='shelf')
                            
                            # Choisir la couleur selon la validité du placement
                            if in_zone and not touches_divider and not is_valid_placement:
                                shelf_color = "rgba(255, 0, 0, 1.0)"  # Rouge vif pour placement invalide
                                shelf_opacity = 0.8
                            else:
                                shelf_color = "#666666"
                                shelf_opacity = 0.35
                        else:
                            shelf_color = BODY_COLOR
                            shelf_opacity = BODY_OPACITY

                        # Règle demandée: étagère = traverse (même largeur X et même profondeur Y).
                        shelf_width = max(0.0, L - 2 * tl)
                        shelf_x_start = o[0] + tl
                        shelf_depth = W
                        
                        fig3d.add_trace(cuboid_mesh_for(
                            shelf_width, max(0.0, shelf_depth - 0.01), s['thickness']*unit_factor, (shelf_x_start, o[1], sh_z),
                            color=shelf_color,
                            opacity=shelf_opacity,
                            showlegend=False
                        ))
                    else:
                        # Pas de zone ou zone_id invalide : étagère = traverse
                        shelf_depth = W
                        fig3d.add_trace(cuboid_mesh_for(
                            L-2*tl, max(0.0, shelf_depth - 0.01), s['thickness']*unit_factor, (o[0]+tl, o[1], sh_z),
                            color=("#666666" if is_preview else BODY_COLOR),
                            opacity=(0.35 if is_preview else BODY_OPACITY),
                            showlegend=False
                        ))
            
            # Ajouter les annotations de toutes les zones 2D (après tous les éléments)
            # NE PAS afficher les labels noirs pendant la prévisualisation (seulement les labels bleus des zones existantes)
            # Utiliser include_all_elements=True pour voir toutes les zones créées
            if not (pending and pending.get('cabinet_index') == i):
                all_zones_2d = zones_render_all

                add_zone_outlines_3d(
                    fig3d,
                    all_zones_2d,
                    o,
                    cab['dims'],
                    unit_factor,
                    zone_ids_to_show=None,
                    fill_color="rgba(0,100,200,0.3)",
                    line_color="rgba(0,100,200,0.95)",
                    line_width=4,
                    y_plane_offset=-0.015,
                )

                # Les boites wireframe de debug peuvent perturber le rendu WebGL sur certains navigateurs.
                # On les desactive par defaut pour stabiliser la previsualisation 3D utilisateur.
                SHOW_ZONE_DEBUG_BOXES = False
                if SHOW_ZONE_DEBUG_BOXES:
                    add_zone_debug_boxes_3d(
                        fig3d,
                        all_zones_2d,
                        o,
                        cab['dims'],
                        unit_factor,
                        wireframe=True,
                        opacity=0.4,
                        y_plane_offset=-0.008,
                    )
                
            for zone in all_zones_2d:
                zone_center_x_mm = (zone['x_min'] + zone['x_max']) / 2.0
                zone_center_y_mm = (zone['y_min'] + zone['y_max']) / 2.0
                annot_x = o[0] + (zone_center_x_mm * unit_factor)
                # Meme plan Y que les zones remplies/contours pour eviter la parallaxe au dezoom.
                annot_y = o[1] + W / 2.0 - 0.015
                annot_z = o[2] + tt + ((zone_center_y_mm - cab['dims']['t_tb_raw']) * unit_factor)
                fig3d.add_trace(go.Scatter3d(
                    x=[annot_x],
                    y=[annot_y],
                    z=[annot_z],
                    mode='text',
                    text=[f"<b>Zone {zone['id']}</b>"],
                    textposition='middle center',
                    textfont=dict(size=14, color="black", family="Arial Black"),
                    showlegend=False,
                    hoverinfo='skip'
                ))
            
            # --- AFFICHAGE DES ZONES AVANT/APRÈS (pendant la pose) ---
            if pending and pending.get('cabinet_index') == i:
                # 1. Calculer les zones AVANT la pose (sans l'élément pending)
                zones_before = calculate_all_zones_2d(cab, include_all_elements=True)
                
                # 2. Calculer les zones APRÈS la pose (avec l'élément pending dans cab_render)
                zones_after = calculate_all_zones_2d(cab_render, include_all_elements=True)
                
                # 3. Identifier les nouvelles zones (celles qui n'existent pas avant)
                before_sig = {
                    (round(z['x_min'], 3), round(z['x_max'], 3), round(z['y_min'], 3), round(z['y_max'], 3))
                    for z in zones_before
                }
                new_zone_ids = set()
                for z in zones_after:
                    sig = (round(z['x_min'], 3), round(z['x_max'], 3), round(z['y_min'], 3), round(z['y_max'], 3))
                    if sig not in before_sig:
                        new_zone_ids.add(z['id'])
                
                # 4. Afficher les zones AVANT (remplissage bleu + contours épais + labels)
                if zones_before:
                    add_zone_outlines_3d(fig3d, zones_before, o, cab['dims'], unit_factor, 
                                         zone_ids_to_show=None, 
                                         fill_color="rgba(0,100,200,0.3)", 
                                         line_color="rgba(0,100,200,0.95)", 
                                         line_width=4, 
                                         y_plane_offset=-0.015)
                    
                    # Ajouter les labels des zones existantes
                    W = cab['dims']['W_raw'] * unit_factor
                    t_tb = cab['dims']['t_tb_raw'] * unit_factor
                    # Utiliser exactement le même plan Y que les boîtes de debug pour éviter la parallaxe
                    y_plane_debug_before = o[1] + W / 2.0 - 0.015  # Même offset que les zones existantes (-0.015)
                    
                    for zone_before in zones_before:
                        zone_center_x_mm = (zone_before['x_min'] + zone_before['x_max']) / 2.0
                        zone_center_y_mm = (zone_before['y_min'] + zone_before['y_max']) / 2.0
                        annot_x = o[0] + (zone_center_x_mm * unit_factor)
                        annot_z = o[2] + t_tb + ((zone_center_y_mm - cab['dims']['t_tb_raw']) * unit_factor)
                        fig3d.add_trace(go.Scatter3d(
                            x=[annot_x],
                            y=[y_plane_debug_before],
                            z=[annot_z],
                            mode='text',
                            text=[f"<b>Zone {zone_before['id']}</b>"],
                            textposition='middle center',
                            textfont=dict(size=16, color="blue", family="Arial Black"),
                            showlegend=False,
                            hoverinfo='skip'
                        ))
                
                # 5. Afficher les nouvelles zones APRÈS (hachures grises)
                if new_zone_ids:
                    add_hatched_zones_3d(fig3d, zones_after, o, cab['dims'], unit_factor, 
                                        zone_ids_to_hatch=new_zone_ids, color="rgba(200,100,0,0.7)", line_width=2, y_plane_offset=-0.01)

        if st.session_state.has_feet:
            l_coords = [abs_origins[i][0] for i in range(len(scene))]; min_L = min(l_coords); max_L = max([abs_origins[i][0] + scene[i]['dims']['L_raw']*unit_factor for i in range(len(scene))])
            min_W = min([abs_origins[i][1] for i in range(len(scene))]); max_W = max([abs_origins[i][1] + scene[i]['dims']['W_raw']*unit_factor for i in range(len(scene))])
            fh = st.session_state.foot_height * unit_factor
            for x in [min_L+0.05, max_L-0.05]:
                for y in [min_W+0.05, max_W-0.05]:
                    fig3d.add_trace(cylinder_mesh_for((x, y, -fh), fh, 0.02, color='#333', showlegend=False))

    # Caméra fixée pour voir de face (côté intérieur du caisson)
    # eye=dict(x=0, y=-2, z=1.4) : voir depuis l'avant (y négatif = depuis l'avant)
    fig3d.update_layout(scene=dict(aspectmode='data', xaxis=dict(visible=True, showgrid=True, title="X"), yaxis=dict(visible=True, showgrid=True, title="Y"), zaxis=dict(visible=True, showgrid=True, title="Z"), camera=dict(eye=dict(x=0, y=-2, z=1.4), center=dict(x=0, y=0, z=0), up=dict(x=0, y=0, z=1))), margin=dict(l=0,r=0,t=0,b=0), uirevision='constant') 
    st.plotly_chart(fig3d, use_container_width=True)
    _render_exports(all_calculated_parts)
    st.markdown("---")
    st.subheader("📋 Feuille de Débit")
    if all_calculated_parts:
        df_debit = pd.DataFrame(all_calculated_parts)
        # Afficher systématiquement les 4 chants dans un ordre fixe.
        for chant_col in ["Chant Avant", "Chant Arrière", "Chant Gauche", "Chant Droit"]:
            if chant_col not in df_debit.columns:
                df_debit[chant_col] = False
        preferred_cols = [
            "Lettre", "Référence Pièce", "Matière", "Caisson", "Qté",
            "Longueur (mm)", "Largeur (mm)", "Epaisseur",
            "Chant Avant", "Chant Arrière", "Chant Gauche", "Chant Droit",
            "Usinage",
        ]
        ordered_cols = [c for c in preferred_cols if c in df_debit.columns] + [c for c in df_debit.columns if c not in preferred_cols]
        df_debit = df_debit[ordered_cols]
        # Formater les colonnes de dimensions : pas de virgule pour les nombres >= 1000
        dim_columns = ["Longueur (mm)", "Largeur (mm)", "Epaisseur"]
        for col in dim_columns:
            if col in df_debit.columns:
                def format_dimension(val):
                    if pd.isna(val) or val == "":
                        return ""
                    try:
                        num_val = float(val)
                        # Si >= 1000, format entier sans virgule
                        if num_val >= 1000:
                            return str(int(num_val))
                        else:
                            # Format décimal si nécessaire, sinon entier
                            if num_val == int(num_val):
                                return str(int(num_val))
                            return str(num_val)
                    except:
                        return str(val) if val else ""
                df_debit[col] = df_debit[col].apply(format_dimension)
        st.dataframe(df_debit, hide_index=True, use_container_width=True)

        # --- Métriques globales de la scène ---
        st.markdown("---")
        st.subheader("📐 Métriques de la scène")
        st.caption("Calculées à partir de la feuille de débit ci-dessus (toutes les pièces, tous les caissons), avec conservation de la précision pendant tout le calcul et affichage à 3 décimales.")

        total_m2 = 0.0
        linear_avec_chant = 0.0
        linear_sans_chant = 0.0
        total_area_mm2 = 0.0
        edge_with_count = 0
        edge_without_count = 0
        edge_with_total_mm = 0.0
        edge_without_total_mm = 0.0
        area_detail_terms = []
        edge_with_detail_terms = []
        edge_without_detail_terms = []

        for _part in all_calculated_parts:
            try:
                _ref = str(_part.get("Référence Pièce", "Pièce"))
                _longueur = float(_part.get("Longueur (mm)", 0) or 0)
                _largeur  = float(_part.get("Largeur (mm)", 0) or 0)
                _qte      = int(_part.get("Qté", 1) or 1)

                # m² de panneaux sans arrondi intermédiaire
                _surface_mm2 = _longueur * _largeur * _qte
                total_area_mm2 += _surface_mm2
                total_m2 += _surface_mm2 / 1_000_000
                area_detail_terms.append(
                    f"{_ref}: {_longueur:.3f} × {_largeur:.3f} × {_qte} = {_surface_mm2:.3f} mm²"
                )

                # Mètres linéaires des 4 tranches sans arrondi intermédiaire
                # Chant Avant / Arrière → dimension Longueur
                # Chant Gauche / Droit  → dimension Largeur
                for _chant_key, _edge_mm in [
                    ("Chant Avant",    _longueur),
                    ("Chant Arrière",  _longueur),
                    ("Chant Gauche",   _largeur),
                    ("Chant Droit",    _largeur),
                ]:
                    _total_edge_mm = _edge_mm * _qte
                    _meters = _total_edge_mm / 1000
                    if bool(_part.get(_chant_key, False)):
                        edge_with_count += _qte
                        edge_with_total_mm += _total_edge_mm
                        linear_avec_chant += _meters
                        edge_with_detail_terms.append(
                            f"{_ref} - {_chant_key}: {_edge_mm:.3f} × {_qte} = {_total_edge_mm:.3f} mm"
                        )
                    else:
                        edge_without_count += _qte
                        edge_without_total_mm += _total_edge_mm
                        linear_sans_chant += _meters
                        edge_without_detail_terms.append(
                            f"{_ref} - {_chant_key}: {_edge_mm:.3f} × {_qte} = {_total_edge_mm:.3f} mm"
                        )
            except (ValueError, TypeError):
                pass

        area_detail_text = " +\n".join(area_detail_terms) if area_detail_terms else "Aucune pièce prise en compte."
        area_detail_text += f"\n= {total_area_mm2:.3f} mm²\n= {total_area_mm2:.3f} / 1 000 000\n= {total_m2:.3f} m²"

        edge_with_detail_text = " +\n".join(edge_with_detail_terms) if edge_with_detail_terms else "Aucune tranche avec chant prise en compte."
        edge_with_detail_text += f"\n= {edge_with_total_mm:.3f} mm\n= {edge_with_total_mm:.3f} / 1000\n= {linear_avec_chant:.3f} ml"

        edge_without_detail_text = " +\n".join(edge_without_detail_terms) if edge_without_detail_terms else "Aucune tranche sans chant prise en compte."
        edge_without_detail_text += f"\n= {edge_without_total_mm:.3f} mm\n= {edge_without_total_mm:.3f} / 1000\n= {linear_sans_chant:.3f} ml"

        _mc1, _mc2, _mc3 = st.columns(3)
        with _mc1:
            st.metric("Surface totale panneaux", f"{total_m2:.3f} m²")
            st.text_area(
                "Détail calcul m²",
                value=area_detail_text,
                height=220,
                disabled=True,
                key="scene_metrics_area_detail",
            )
        with _mc2:
            st.metric("Tranches AVEC chant", f"{linear_avec_chant:.3f} ml")
            st.text_area(
                "Détail tranches AVEC chant",
                value=edge_with_detail_text,
                height=220,
                disabled=True,
                key="scene_metrics_edge_with_detail",
            )
        with _mc3:
            st.metric("Tranches SANS chant", f"{linear_sans_chant:.3f} ml")
            st.text_area(
                "Détail tranches SANS chant",
                value=edge_without_detail_text,
                height=220,
                disabled=True,
                key="scene_metrics_edge_without_detail",
            )
