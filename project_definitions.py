# Contenu de project_definitions.py
# Contient les constantes et configurations par défaut

def get_default_dims_19():
    return {
        'L_raw': 600.0, 'W_raw': 600.0, 'H_raw': 800.0,
        't_lr_raw': 19.0, 't_fb_raw': 19.0, 't_tb_raw': 19.0  # OBLIGATOIREMENT 19mm PAR DÉFAUT
    }

def get_default_door_props_19():
    return {
        'has_door': False, 'door_type': 'single', 'door_opening': 'right',
        'door_thickness': 19.0, 'door_gap': 2.0, 'door_model': 'standard', 'material': 'Matière Porte',
        'hinge_mode': 'default',  # 'default' ou 'custom'
        'custom_hinge_positions': [],  # Liste des positions Y personnalisées en mm
        'fileur_width': 0,  # 0 = pas de fileur, 50 = 5 cm, 100 = 10 cm
    }

def get_default_drawer_props_19():
    return {
        'has_drawer': False, 'drawer_face_H_raw': 150.0, 'drawer_face_thickness': 19.0,
        'drawer_gap': 2.0, 'drawer_bottom_offset': 0.0, 'drawer_handle_type': 'none',
        'drawer_handle_width': 150.0, 'drawer_handle_height': 40.0, 'drawer_handle_offset_top': 10.0,
        'material': 'Matière Tiroir',
        # Épaisseur intérieure (dos + fond) par défaut
        'inner_thickness': 16.0
    }

def get_default_drawer_props():
    """Retourne les propriétés par défaut pour un tiroir individuel."""
    return {
        'drawer_face_H_raw': 150.0,
        'drawer_face_thickness': 19.0,
        'drawer_gap': 2.0,
        'drawer_bottom_offset': 0.0,  # Position Y du bas du tiroir (en mm depuis la traverse inférieure)
        'drawer_system': 'TANDEMBOX',  # 'TANDEMBOX' ou 'LÉGRABOX'
        'drawer_tech_type': 'K',  # Pour TANDEMBOX: 'K', 'M', 'N', 'D'. Pour LÉGRABOX: 'N', 'M', 'K', 'C'
        'drawer_handle_type': 'none',
        'drawer_handle_width': 150.0,
        'drawer_handle_height': 40.0,
        'drawer_handle_offset_top': 10.0,
        'zone_id': None,
        'material': 'Matière Tiroir',
        # Épaisseur intérieure (dos + fond) par défaut
        'inner_thickness': 16.0
    }

def get_default_vertical_divider_props():
    """Retourne les propriétés par défaut pour un montant vertical secondaire."""
    return {
        'position_x': 300.0,  # Position en mm depuis le montant gauche
        'thickness': 19.0,
        'material': 'Matière Corps'
    }

def get_default_vertical_shelf_props():
    """Retourne les propriétés par défaut pour une étagère verticale."""
    return {
        'position_x': 300.0,  # Position en mm depuis le montant gauche
        'bottom_y': 100.0,  # Position Y du bas (en mm depuis la traverse inférieure)
        'top_y': 400.0,  # Position Y du haut (en mm depuis la traverse inférieure)
        'thickness': 19.0,
        'material': 'Matière Corps'
    }

def get_default_joue_props():
    """Retourne les propriétés par défaut pour une joue manuelle."""
    return {
        'enabled': False,
        'width': 0.0,
        'length': 0.0,
        'thickness': 0.0,
        'material': 'Matière Corps',
    }

def get_legrabox_specs():
    """Retourne les spécifications pour le système LÉGRABOX (N, M, K, C)."""
    return {
        'N': {
            'back_height': 39.0,
            'face_holes': {
                'x_offset': 12.0,  # Distance du bord
                'y_coords': [45.5, 61.5],
                'diam_str': '⌀10/12'
            },
            'back_holes': {
                'x_offset': 9.0,  # Distance du bord
                'y_coords': [19.0],
                'diam_str': '⌀2.5/3'
            },
            'bottom_holes': {
                'x_offset': 48.5,  # Distance du bord
                'y_coords': [24.0, 152.0],
                'diam_str': '⌀2.5/3'
            }
        },
        'M': {
            'back_height': 63.0,
            'face_holes': {
                'x_offset': 12.0,
                'y_coords': [51.0, 83.0],
                'diam_str': '⌀10/12'
            },
            'back_holes': {
                'x_offset': 9.0,
                'y_coords': [19.0, 51.0],
                'diam_str': '⌀2.5/3'
            },
            'bottom_holes': {
                'x_offset': 48.5,
                'y_coords': [24.0, 152.0],
                'diam_str': '⌀2.5/3'
            }
        },
        'K': {
            'back_height': 101.0,
            'face_holes': {
                'x_offset': 12.0,
                'y_coords': [51.0, 83.0],
                'diam_str': '⌀10/12'
            },
            'back_holes': {
                'x_offset': 9.0,
                'y_coords': [19.0, 51.0, 83.0],
                'diam_str': '⌀2.5/3'
            },
            'bottom_holes': {
                'x_offset': 48.5,
                'y_coords': [24.0, 152.0],
                'diam_str': '⌀2.5/3'
            }
        },
        'C': {
            'back_height': 148.0,
            'face_holes': {
                'x_offset': 12.0,
                'y_coords': [51.0, 83.0, 179.0],
                'diam_str': '⌀10/12'
            },
            'back_holes': {
                'x_offset': 9.0,
                'y_coords': [19.0, 51.0, 115.0, 131.0],
                'diam_str': '⌀2.5/3'
            },
            'bottom_holes': {
                'x_offset': 48.5,
                'y_coords': [24.0, 152.0],
                'diam_str': '⌀2.5/3'
            }
        }
    }
