# ... existing code ...

def calculate_available_space_between_horizontal_shelves(cabinet, zone_x_min, zone_x_max, position_x, vs_thickness):
    """
    Calcule l'espace disponible entre les planches horizontales pour une étagère verticale.
    
    Args:
        cabinet: Dictionnaire du caisson
        zone_x_min, zone_x_max: Limites X de la zone
        position_x: Position X de l'étagère verticale
        vs_thickness: Épaisseur de l'étagère verticale
    
    Returns:
        tuple: (available_spaces, blocking_shelves)
        - available_spaces: Liste de dict avec 'y_min', 'y_max', 'y_min_face', 'y_max_face' (faces intérieures)
        - blocking_shelves: Liste des étagères horizontales qui bloquent cette position X
    """
    vs_x_min = position_x - vs_thickness / 2.0
    vs_x_max = position_x + vs_thickness / 2.0
    
    # Collecter toutes les étagères horizontales qui chevauchent cette position X
    blocking_shelves = []
    
    for shelf in cabinet.get('shelves', []):
        if shelf.get('_preview', False):
            continue
        
        shelf_zone_id = shelf.get('zone_id', None)
        shelf_height = shelf.get('height', 0.0)
        shelf_thickness = shelf.get('thickness', 19.0)
        
        # Vérifier si l'étagère horizontale est dans la même zone X
        shelf_in_same_x_zone = False
        if shelf_zone_id is None:
            # Étagère sur tout le caisson
            shelf_in_same_x_zone = True
        else:
            # Vérifier si la zone de l'étagère horizontale chevauche la zone X actuelle
            # Pour simplifier, on considère que si l'étagère verticale est dans une zone,
            # on vérifie seulement les étagères horizontales de la même zone
            # (cette logique sera améliorée si nécessaire)
            shelf_in_same_x_zone = True  # Simplification pour l'instant
        
        if shelf_in_same_x_zone:
            # L'étagère horizontale bloque cette position X
            blocking_shelves.append({
                'y_bottom': shelf_height,
                'y_top': shelf_height + shelf_thickness,
                'y_bottom_face': shelf_height + shelf_thickness,  # Face supérieure de la planche
                'y_top_face': shelf_height,  # Face inférieure de la planche
                'thickness': shelf_thickness
            })
    
    # Trier par Y
    blocking_shelves.sort(key=lambda s: s['y_bottom'])
    
    # Calculer les espaces disponibles entre les planches
    available_spaces = []
    dims = cabinet['dims']
    t_tb = dims['t_tb_raw']
    H_raw = dims['H_raw']
    usable_height = H_raw - 2 * t_tb
    
    if not blocking_shelves:
        # Pas d'étagères horizontales : espace de 0 à usable_height
        available_spaces.append({
            'y_min': 0.0,
            'y_max': usable_height,
            'y_min_face': 0.0,  # Face inférieure de la traverse
            'y_max_face': usable_height  # Face supérieure de la traverse
        })
    else:
        # Espace avant la première étagère (traverse inférieure -> première planche)
        first_shelf = blocking_shelves[0]
        if first_shelf['y_bottom'] > 0.1:
            available_spaces.append({
                'y_min': 0.0,
                'y_max': first_shelf['y_bottom'],
                'y_min_face': 0.0,
                'y_max_face': first_shelf['y_top']  # Jusqu'à la face supérieure de la première planche
            })
        
        # Espaces entre les planches
        for i in range(len(blocking_shelves) - 1):
            shelf_below = blocking_shelves[i]
            shelf_above = blocking_shelves[i + 1]
            
            # L'espace disponible est entre la face supérieure de la planche du bas
            # et la face inférieure de la planche du haut
            gap_y_min = shelf_below['y_top']  # Après l'épaisseur de la planche du bas
            gap_y_max = shelf_above['y_bottom']  # Avant le début de la planche du haut
            
            if gap_y_max - gap_y_min > 0.1:
                available_spaces.append({
                    'y_min': gap_y_min,
                    'y_max': gap_y_max,
                    'y_min_face': shelf_below['y_top'],  # Face supérieure de la planche du bas
                    'y_max_face': shelf_above['y_bottom']  # Face inférieure de la planche du haut
                })
        
        # Espace après la dernière étagère (dernière planche -> traverse supérieure)
        last_shelf = blocking_shelves[-1]
        if last_shelf['y_top'] < usable_height - 0.1:
            available_spaces.append({
                'y_min': last_shelf['y_top'],
                'y_max': usable_height,
                'y_min_face': last_shelf['y_top'],  # Face supérieure de la dernière planche
                'y_max_face': usable_height  # Face supérieure de la traverse
            })
    
    return available_spaces, blocking_shelves
