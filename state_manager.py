# Contenu de state_manager.py
# Gestion des callbacks et de l'état de session

import streamlit as st
import openpyxl
import json
import datetime
import copy
from project_definitions import get_default_dims_19, get_default_door_props_19, get_default_drawer_props_19, get_default_drawer_props, get_default_vertical_divider_props, get_default_vertical_shelf_props

# Fonctions de compatibilité pour les valeurs par défaut
def get_default_debit_data():
    """Retourne les données de débit par défaut avec les pièces de base."""
    return [
        {
            "Référence Pièce": "Traverse Bas",
            "Longueur (mm)": 0,
            "Largeur (mm)": 0,
            "Epaisseur": 0,
            "Chant Avant": True,
            "Chant Arrière": True,
            "Chant Gauche": False,
            "Chant Droit": False,
            "Usinage": ""
        },
        {
            "Référence Pièce": "Traverse Haut",
            "Longueur (mm)": 0,
            "Largeur (mm)": 0,
            "Epaisseur": 0,
            "Chant Avant": True,
            "Chant Arrière": True,
            "Chant Gauche": False,
            "Chant Droit": False,
            "Usinage": ""
        },
        {
            "Référence Pièce": "Montant Gauche",
            "Longueur (mm)": 0,
            "Largeur (mm)": 0,
            "Epaisseur": 0,
            "Chant Avant": True,
            "Chant Arrière": True,
            "Chant Gauche": True,
            "Chant Droit": True,
            "Usinage": ""
        },
        {
            "Référence Pièce": "Montant Droit",
            "Longueur (mm)": 0,
            "Largeur (mm)": 0,
            "Epaisseur": 0,
            "Chant Avant": True,
            "Chant Arrière": True,
            "Chant Gauche": True,
            "Chant Droit": True,
            "Usinage": ""
        },
        {
            "Référence Pièce": "Fond",
            "Longueur (mm)": 0,
            "Largeur (mm)": 0,
            "Epaisseur": 0,
            "Chant Avant": False,
            "Chant Arrière": False,
            "Chant Gauche": False,
            "Chant Droit": False,
            "Usinage": ""
        }
    ]

def get_default_shelf_props():
    """Retourne les propriétés par défaut pour une étagère."""
    return {
        'height': 200.0,
        'thickness': 19.0,
        'shelf_type': 'mobile',
        'zone_id': None,
        'material': 'Matière Corps'
    }

def get_selected_cabinet():
    idx = st.session_state.get('selected_cabinet_index')
    if idx is not None and idx < len(st.session_state['scene_cabinets']): return st.session_state['scene_cabinets'][idx]
    return None

def initialize_session_state():
    """Initialise l'état de session global."""
    st.session_state.setdefault('scene_cabinets', [])
    st.session_state.setdefault('selected_cabinet_index', None)
    st.session_state.setdefault('base_cabinet_index', 0)
    st.session_state.setdefault('unit_select', 'mm')

    # Infos Globales du Projet
    st.session_state.setdefault('project_name', "Nouveau Projet")
    st.session_state.setdefault('corps_meuble', "Caisson 1")
    st.session_state.setdefault('quantity', 1)
    st.session_state.setdefault('client', "CLIENT NOM")
    st.session_state.setdefault('adresse_chantier', "") # AJOUTÉ
    st.session_state.setdefault('ref_chantier', "")
    st.session_state.setdefault('telephone', "")
    st.session_state.setdefault('date_souhaitee', datetime.date.today())
    st.session_state.setdefault('panneau_decor', "BLANC")
    st.session_state.setdefault('chant_mm', "1mm")
    st.session_state.setdefault('decor_chant', "BLANC")
    
    # Propriétés des pieds
    st.session_state.setdefault('has_feet', False)
    st.session_state.setdefault('foot_height', 80.0) 
    st.session_state.setdefault('foot_diameter', 30.0)
    
    # Pose en 2 temps (prévisualisation -> validation)
    # pending_placement: { kind: 'shelf'|'vertical_divider'|'vertical_shelf', cabinet_index: int, props: dict }
    st.session_state.setdefault('pending_placement', None)

    # Exports : mémorise le JSON de scène au moment de la dernière génération
    # None = jamais généré, chaîne = scène déjà générée
    st.session_state.setdefault('exports_scene_json', None)
    # Résultats stockés des derniers exports générés
    st.session_state.setdefault('exports_html_data', None)
    st.session_state.setdefault('exports_html_ok', False)
    st.session_state.setdefault('exports_dwg_data', None)
    st.session_state.setdefault('exports_dwg_ok', False)
    st.session_state.setdefault('exports_dwg_filename', None)
    st.session_state.setdefault('exports_xls_data', None)
    st.session_state.setdefault('exports_material_filter_key', None)
    st.session_state.setdefault('exports_material_scope', "Toutes les matières")
    st.session_state.setdefault('exports_selected_materials', [])
    st.session_state.setdefault('exports_skp_data', None)
    st.session_state.setdefault('exports_skp_ok', False)
    st.session_state.setdefault('exports_skp_has_doors', False)
    st.session_state.setdefault('exports_skp_closed_data', None)
    st.session_state.setdefault('exports_skp_closed_ok', False)
    st.session_state.setdefault('exports_skp_open_data', None)
    st.session_state.setdefault('exports_skp_open_ok', False)

def load_save_state():
    if 'file_loader' in st.session_state and st.session_state.file_loader is not None:
        uploaded_file = st.session_state.file_loader
        try:
            workbook = openpyxl.load_workbook(uploaded_file)
            if 'SaveData' in workbook.sheetnames:
                save_sheet = workbook['SaveData']
                json_data_str = save_sheet['A1'].value 
                if json_data_str:
                    loaded_data = json.loads(json_data_str)
                    st.session_state['project_name'] = loaded_data.get('project_name', 'Nouveau Projet')
                    st.session_state['client'] = loaded_data.get('client', '')
                    st.session_state['adresse_chantier'] = loaded_data.get('adresse_chantier', '') # AJOUTÉ
                    st.session_state['ref_chantier'] = loaded_data.get('ref_chantier', '')
                    st.session_state['telephone'] = loaded_data.get('telephone', '')
                    if 'date_souhaitee' in loaded_data:
                         st.session_state['date_souhaitee'] = datetime.date.fromisoformat(loaded_data['date_souhaitee'])
                    st.session_state['panneau_decor'] = loaded_data.get('panneau_decor', '')
                    st.session_state['chant_mm'] = loaded_data.get('chant_mm', '')
                    st.session_state['decor_chant'] = loaded_data.get('decor_chant', '')
                    st.session_state['has_feet'] = loaded_data.get('has_feet', False)
                    st.session_state['foot_height'] = loaded_data.get('foot_height', 80.0)
                    st.session_state['foot_diameter'] = loaded_data.get('foot_diameter', 50.0)
                    st.session_state['scene_cabinets'] = loaded_data.get('scene_cabinets', [])
                    if st.session_state['scene_cabinets']:
                        st.session_state['selected_cabinet_index'] = 0
                        st.session_state['base_cabinet_index'] = 0
                    else:
                        st.session_state['selected_cabinet_index'] = None
                        st.session_state['base_cabinet_index'] = 0
                    st.success("Projet chargé.")
                    st.rerun()
        except Exception as e:
            st.error(f"Erreur chargement : {e}")

# Callbacks
def update_selected_cabinet_dim(key):
    cabinet = get_selected_cabinet()
    widget_key = f"{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state: cabinet['dims'][key] = st.session_state[widget_key]

def update_selected_cabinet_base_element(element_key):
    """Met à jour l'état d'un élément de base du caisson (fond, montants, traverses)."""
    cabinet = get_selected_cabinet()
    idx = st.session_state.selected_cabinet_index
    widget_key = f"base_element_{element_key}_{idx}"
    if cabinet and widget_key in st.session_state:
        if 'base_elements' not in cabinet:
            cabinet['base_elements'] = {
                'has_back_panel': True,
                'has_left_upright': True,
                'has_right_upright': True,
                'has_bottom_traverse': True,
                'has_top_traverse': True
            }
        cabinet['base_elements'][element_key] = st.session_state[widget_key]

def update_selected_cabinet_door(key):
    cabinet = get_selected_cabinet()
    widget_key = f"{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state:
        if 'door_props' not in cabinet: cabinet['door_props'] = get_default_door_props_19()
        cabinet['door_props'][key] = st.session_state[widget_key]
        if key == 'has_door' and st.session_state[widget_key] is True:
            if 'drawer_props' in cabinet: cabinet['drawer_props']['has_drawer'] = False

def update_selected_cabinet_drawer(key):
    # Ancien système (compatibilité) - à supprimer progressivement
    cabinet = get_selected_cabinet()
    # Pour zone_id, la clé du widget est "drawer_zone_{idx}" et non "zone_id_{idx}"
    if key == 'zone_id':
        widget_key = f"drawer_zone_{st.session_state.selected_cabinet_index}"
    else:
        widget_key = f"drawer_{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state:
        if 'drawer_props' not in cabinet: cabinet['drawer_props'] = get_default_drawer_props_19()
        cabinet['drawer_props'][key] = st.session_state[widget_key]
        if key == 'has_drawer' and st.session_state[widget_key] is True:
            if 'door_props' in cabinet: cabinet['door_props']['has_door'] = False

def add_drawer_callback():
    """Ajoute un nouveau tiroir en mode preview (pose en 2 temps)."""
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'drawers' not in cabinet: cabinet['drawers'] = []
        # Pose en 2 temps: on crée un "pending" au lieu d'ajouter directement
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        st.session_state['pending_placement'] = {
            'kind': 'drawer',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': get_default_drawer_props()
        }

def add_drawers_stack_callback():
    """Ajoute plusieurs tiroirs empilés (pose en 2 temps), sans demander de dimensions à l'utilisateur."""
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'drawers' not in cabinet: cabinet['drawers'] = []
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        p = get_default_drawer_props()
        # Mode empilement : dimensions (hauteur/offset) seront calculées automatiquement à partir de la zone
        p['stack_count'] = 3
        p['_stack_mode'] = True
        st.session_state['pending_placement'] = {
            'kind': 'drawer_stack',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': p
        }

def update_drawer_prop(drawer_index, key):
    """Met à jour une propriété d'un tiroir existant."""
    cabinet = get_selected_cabinet()
    if key == 'drawer_tech_type': widget_key = f"drawer_tech_type_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_system': widget_key = f"drawer_system_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_handle_type': widget_key = f"drawer_handle_type_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'zone_id': widget_key = f"drawer_zone_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_bottom_offset': widget_key = f"drawer_bottom_offset_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_face_H_raw': widget_key = f"drawer_face_H_raw_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_face_thickness': widget_key = f"drawer_face_thickness_{st.session_state.selected_cabinet_index}_{drawer_index}"
    elif key == 'drawer_gap': widget_key = f"drawer_gap_{st.session_state.selected_cabinet_index}_{drawer_index}"
    else: widget_key = f"drawer_{key}_{st.session_state.selected_cabinet_index}_{drawer_index}"
    if cabinet and widget_key in st.session_state:
        if 'drawers' in cabinet and drawer_index < len(cabinet['drawers']): 
            cabinet['drawers'][drawer_index][key] = st.session_state[widget_key]

def delete_drawer_callback(drawer_index):
    """Supprime un tiroir."""
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'drawers' in cabinet and drawer_index < len(cabinet['drawers']):
            cabinet['drawers'].pop(drawer_index)
            st.rerun()

def update_drawer_material(drawer_index):
    """Met à jour la matière d'un tiroir."""
    widget_key = f"drawer_material_{st.session_state.selected_cabinet_index}_{drawer_index}"
    cabinet = get_selected_cabinet()
    if cabinet and widget_key in st.session_state:
        if 'drawers' in cabinet and drawer_index < len(cabinet['drawers']): 
            cabinet['drawers'][drawer_index]['material'] = st.session_state[widget_key]

def add_shelf_callback():
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'shelves' not in cabinet: cabinet['shelves'] = []
        # Pose en 2 temps: on crée un "pending" au lieu d'ajouter directement
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        st.session_state['pending_placement'] = {
            'kind': 'shelf',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': get_default_shelf_props()
        }


def add_fixed_shelves_stack_callback():
    """Ajoute un assemblage d'etageres fixes (pose en 2 temps)."""
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'shelves' not in cabinet:
            cabinet['shelves'] = []
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return

        p = get_default_shelf_props()
        p['shelf_type'] = 'fixe'
        p['stack_count'] = 3
        p['_stack_mode'] = True
        st.session_state['pending_placement'] = {
            'kind': 'shelf_stack',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': p,
        }

def update_shelf_prop(shelf_index, key):
    cabinet = get_selected_cabinet()
    if key == 'shelf_type': widget_key = f"shelf_t_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'height': widget_key = f"shelf_h_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'thickness': widget_key = f"shelf_e_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'mobile_machining_type': widget_key = f"shelf_m_type_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'custom_holes_above': widget_key = f"shelf_c_above_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'custom_holes_below': widget_key = f"shelf_c_below_{st.session_state.selected_cabinet_index}_{shelf_index}"
    elif key == 'zone_id': widget_key = f"shelf_zone_{st.session_state.selected_cabinet_index}_{shelf_index}"
    else: widget_key = f"shelf_{key[0]}_{st.session_state.selected_cabinet_index}_{shelf_index}"
    if cabinet and widget_key in st.session_state:
        if 'shelves' in cabinet and shelf_index < len(cabinet['shelves']): 
            cabinet['shelves'][shelf_index][key] = st.session_state[widget_key]

def delete_shelf_callback(shelf_index):
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'shelves' in cabinet and shelf_index < len(cabinet['shelves']):
            cabinet['shelves'].pop(shelf_index)
            st.rerun()

def update_selected_cabinet_material(key):
    cabinet = get_selected_cabinet()
    widget_key = f"{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state: cabinet[key] = st.session_state[widget_key]
def update_selected_cabinet_door_material(key):
    cabinet = get_selected_cabinet()
    widget_key = f"door_{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state: cabinet['door_props']['material'] = st.session_state[widget_key]

def update_hinge_count(cabinet_index):
    """Met à jour le nombre de charnières personnalisées."""
    if cabinet_index < len(st.session_state['scene_cabinets']):
        cabinet = st.session_state['scene_cabinets'][cabinet_index]
        if 'door_props' in cabinet:
            widget_key = f"num_hinges_{cabinet_index}"
            if widget_key in st.session_state:
                num_hinges = int(st.session_state[widget_key])
                custom_positions = cabinet['door_props'].get('custom_hinge_positions', [])
                door_height = cabinet['dims']['H_raw']
                
                # Ajuster la liste selon le nouveau nombre
                if len(custom_positions) < num_hinges:
                    # Ajouter des positions par défaut
                    for i in range(len(custom_positions), num_hinges):
                        pos = (i + 1) * door_height / (num_hinges + 1)
                        custom_positions.append(pos)
                else:
                    # Retirer les positions en trop
                    custom_positions = custom_positions[:num_hinges]
                
                cabinet['door_props']['custom_hinge_positions'] = custom_positions

def update_hinge_position(cabinet_index, hinge_index):
    """Met à jour la position d'une charnière personnalisée."""
    if cabinet_index < len(st.session_state['scene_cabinets']):
        cabinet = st.session_state['scene_cabinets'][cabinet_index]
        if 'door_props' in cabinet:
            widget_key = f"hinge_pos_{cabinet_index}_{hinge_index}"
            if widget_key in st.session_state:
                if 'custom_hinge_positions' not in cabinet['door_props']:
                    cabinet['door_props']['custom_hinge_positions'] = []
                custom_positions = cabinet['door_props']['custom_hinge_positions']
                
                # S'assurer que la liste est assez longue
                while len(custom_positions) <= hinge_index:
                    custom_positions.append(0.0)
                
                custom_positions[hinge_index] = float(st.session_state[widget_key])
                cabinet['door_props']['custom_hinge_positions'] = custom_positions
def update_selected_cabinet_drawer_material(key):
    cabinet = get_selected_cabinet()
    widget_key = f"drawer_{key}_{st.session_state.selected_cabinet_index}"
    if cabinet and widget_key in st.session_state: cabinet['drawer_props']['material'] = st.session_state[widget_key]
def update_shelf_material(shelf_index, key):
    widget_key = f"shelf_m_{st.session_state.selected_cabinet_index}_{shelf_index}"
    cabinet = get_selected_cabinet()
    if cabinet and widget_key in st.session_state:
        if 'shelves' in cabinet and shelf_index < len(cabinet['shelves']): cabinet['shelves'][shelf_index]['material'] = st.session_state[widget_key]

def add_cabinet(origin_type='central'):
    if origin_type == 'central':
        if st.session_state['scene_cabinets']: return
        new_cabinet = {
            'dims': get_default_dims_19(), 'debit_data': get_default_debit_data(), 'name': "Caisson 0 (Central)",
            'parent_index': None, 'attachment_dir': None, 'door_props': get_default_door_props_19(),
            'drawer_props': get_default_drawer_props_19(), 'drawers': [], 'shelves': [], 'material_body': 'Matière Corps',
            'vertical_dividers': [],  # Nouveaux montants verticaux secondaires
            'vertical_shelves': []  # Étagères verticales
        }
        st.session_state['scene_cabinets'].append(new_cabinet)
        st.session_state['selected_cabinet_index'] = 0
        st.session_state['base_cabinet_index'] = 0
    else: 
        base_index = st.session_state.get('base_cabinet_index', 0)
        if base_index is None or base_index >= len(st.session_state['scene_cabinets']):
            st.error("Aucun caisson de base sélectionné.")
            return
        base_caisson = st.session_state['scene_cabinets'][base_index]
        new_cabinet = {
            'dims': copy.deepcopy(base_caisson['dims']), 'debit_data': get_default_debit_data(),
            'parent_index': base_index, 'attachment_dir': origin_type, 'door_props': get_default_door_props_19(),
            'drawer_props': get_default_drawer_props_19(), 'drawers': [], 'shelves': [], 'material_body': 'Matière Corps',
            'vertical_dividers': [],  # Nouveaux montants verticaux secondaires
            'vertical_shelves': []  # Étagères verticales
        }
        if origin_type == 'right': new_name = f"D de {base_index}"
        elif origin_type == 'left': new_name = f"G de {base_index}"
        else: new_name = f"H de {base_index}"
        new_cabinet['name'] = f"Caisson {len(st.session_state['scene_cabinets'])} ({new_name})"
        st.session_state['scene_cabinets'].append(new_cabinet)
        new_index = len(st.session_state['scene_cabinets']) - 1
        st.session_state['selected_cabinet_index'] = new_index
        st.session_state['base_cabinet_index'] = st.session_state['selected_cabinet_index']

def clear_scene():
    st.session_state['scene_cabinets'] = []
    st.session_state['selected_cabinet_index'] = None
    st.session_state['base_cabinet_index'] = 0

def delete_selected_cabinet():
    idx = st.session_state.get('selected_cabinet_index')
    if idx is None or idx >= len(st.session_state['scene_cabinets']): return
    indices_to_remove = set()
    queue = [idx]
    while queue:
        curr = queue.pop()
        if curr not in indices_to_remove:
            indices_to_remove.add(curr)
            for i, c in enumerate(st.session_state['scene_cabinets']):
                if c['parent_index'] == curr: queue.append(i)
    new_scene = []
    map_old_new = {}
    counter = 0
    for i, c in enumerate(st.session_state['scene_cabinets']):
        if i not in indices_to_remove:
            map_old_new[i] = counter
            new_scene.append(c)
            counter += 1
    for c in new_scene:
        if c['parent_index'] is not None: c['parent_index'] = map_old_new.get(c['parent_index'], None) 
    st.session_state['scene_cabinets'] = new_scene
    st.session_state['selected_cabinet_index'] = 0 if new_scene else None
    st.session_state['base_cabinet_index'] = 0

# Callbacks pour les montants verticaux secondaires
def add_vertical_divider_callback():
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'vertical_dividers' not in cabinet:
            cabinet['vertical_dividers'] = []
        # Pose en 2 temps: on crée un "pending" au lieu d'ajouter directement
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        new_divider = get_default_vertical_divider_props()
        # S'assurer que la position est valide (entre les montants)
        dims = cabinet['dims']
        t_lr = dims['t_lr_raw']
        L_raw = dims['L_raw']
        # Position par défaut au milieu si c'est le premier montant
        if len(cabinet['vertical_dividers']) == 0:
            new_divider['position_x'] = (L_raw - 2*t_lr) / 2.0 + t_lr
            new_divider['zone_id'] = None  # Premier montant : pas de zone assignée, placement libre dans Zone 0
        else:
            # Positionner après le dernier montant
            last_div = max(cabinet['vertical_dividers'], key=lambda d: d['position_x'])
            new_divider['position_x'] = min(last_div['position_x'] + 200.0, L_raw - t_lr - 50.0)
            new_divider['zone_id'] = None  # Sera assigné par l'utilisateur via le selectbox
        st.session_state['pending_placement'] = {
            'kind': 'vertical_divider',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': new_divider
        }

def add_vertical_divider_double_callback():
    """Ajoute un double montant secondaire (2 montants côte à côte, sans jeu) en mode preview."""
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'vertical_dividers' not in cabinet:
            cabinet['vertical_dividers'] = []
        # Pose en 2 temps : on crée un pending spécifique
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        new_divider = get_default_vertical_divider_props()
        dims = cabinet['dims']
        t_lr = dims['t_lr_raw']
        L_raw = dims['L_raw']
        # Centre par défaut au milieu du caisson
        new_divider['position_x'] = (L_raw - 2 * t_lr) / 2.0 + t_lr
        new_divider['zone_id'] = None
        new_divider['double'] = True  # marqueur pour l'UI
        st.session_state['pending_placement'] = {
            'kind': 'vertical_divider_double',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': new_divider
        }

def update_vertical_divider_prop(divider_index, key):
    cabinet = get_selected_cabinet()
    if key == 'zone_id':
        widget_key = f"divider_zone_{st.session_state.selected_cabinet_index}_{divider_index}"
    else:
        widget_key = f"divider_{key}_{st.session_state.selected_cabinet_index}_{divider_index}"
    if cabinet and widget_key in st.session_state:
        if 'vertical_dividers' in cabinet and divider_index < len(cabinet['vertical_dividers']):
            cabinet['vertical_dividers'][divider_index][key] = st.session_state[widget_key]

def delete_vertical_divider_callback(divider_index):
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'vertical_dividers' in cabinet and divider_index < len(cabinet['vertical_dividers']):
            cabinet['vertical_dividers'].pop(divider_index)
            st.rerun()

def update_vertical_divider_material(divider_index):
    cabinet = get_selected_cabinet()
    widget_key = f"divider_material_{st.session_state.selected_cabinet_index}_{divider_index}"
    if cabinet and widget_key in st.session_state:
        if 'vertical_dividers' in cabinet and divider_index < len(cabinet['vertical_dividers']):
            cabinet['vertical_dividers'][divider_index]['material'] = st.session_state[widget_key]

# Callbacks pour les étagères verticales
def add_vertical_shelf_callback():
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'vertical_shelves' not in cabinet:
            cabinet['vertical_shelves'] = []
        # Pose en 2 temps: on crée un "pending" au lieu d'ajouter directement
        if st.session_state.get('pending_placement') is not None:
            st.warning("Une pose est déjà en cours. Validez ou annulez avant d'ajouter un nouvel élément.")
            return
        new_shelf = get_default_vertical_shelf_props()
        # S'assurer que la position est valide
        dims = cabinet['dims']
        t_lr = dims['t_lr_raw']
        L_raw = dims['L_raw']
        H_raw = dims['H_raw']
        # Position par défaut au milieu si c'est la première étagère
        if len(cabinet['vertical_shelves']) == 0:
            new_shelf['position_x'] = (L_raw - 2*t_lr) / 2.0 + t_lr
            new_shelf['bottom_y'] = 100.0
            new_shelf['top_y'] = min(400.0, H_raw - 100.0)
        else:
            # Positionner après la dernière étagère
            last_shelf = max(cabinet['vertical_shelves'], key=lambda s: s['position_x'])
            new_shelf['position_x'] = min(last_shelf['position_x'] + 200.0, L_raw - t_lr - 50.0)
            new_shelf['bottom_y'] = 100.0
            new_shelf['top_y'] = min(400.0, H_raw - 100.0)
        st.session_state['pending_placement'] = {
            'kind': 'vertical_shelf',
            'cabinet_index': st.session_state.get('selected_cabinet_index'),
            'props': new_shelf
        }

def update_vertical_shelf_prop(shelf_index, key):
    cabinet = get_selected_cabinet()
    if key == 'zone_id':
        widget_key = f"vertical_shelf_zone_{st.session_state.selected_cabinet_index}_{shelf_index}"
    else:
        widget_key = f"vertical_shelf_{key}_{st.session_state.selected_cabinet_index}_{shelf_index}"
    if cabinet and widget_key in st.session_state:
        if 'vertical_shelves' in cabinet and shelf_index < len(cabinet['vertical_shelves']):
            cabinet['vertical_shelves'][shelf_index][key] = st.session_state[widget_key]

def delete_vertical_shelf_callback(shelf_index):
    cabinet = get_selected_cabinet()
    if cabinet:
        if 'vertical_shelves' in cabinet and shelf_index < len(cabinet['vertical_shelves']):
            cabinet['vertical_shelves'].pop(shelf_index)
            st.rerun()

def update_vertical_shelf_material(shelf_index):
    cabinet = get_selected_cabinet()
    widget_key = f"vertical_shelf_material_{st.session_state.selected_cabinet_index}_{shelf_index}"
    if cabinet and widget_key in st.session_state:
        if 'vertical_shelves' in cabinet and shelf_index < len(cabinet['vertical_shelves']):
            cabinet['vertical_shelves'][shelf_index]['material'] = st.session_state[widget_key]