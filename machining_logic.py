import math

def calculate_origins_recursively(scene_cabinets, unit_factor):
    """
    Calcule les origines absolues de chaque caisson dans la scène.
    
    Prend en compte les relations parent-enfant et les directions d'attachement :
    - 'left' : à gauche du caisson parent (X négatif)
    - 'right' : à droite du caisson parent (X positif)
    - 'up' : au-dessus du caisson parent (Z positif)
    """
    abs_origins = []
    
    # Trouver le caisson central (sans parent)
    central_index = None
    for i, cab in enumerate(scene_cabinets):
        if cab.get('parent_index') is None:
            central_index = i
            break
    
    if central_index is None:
        # Pas de caisson central, placer tous les caissons en ligne
        current_x = 0.0
        for cab in scene_cabinets:
            abs_origins.append((current_x, 0.0, 0.0))
            current_x += cab['dims']['L_raw'] * unit_factor
        return abs_origins
    
    # Calculer les origines récursivement en partant du caisson central
    # Utiliser un dictionnaire pour mémoriser les positions calculées
    position_cache = {}
    
    def calculate_position(cab_index):
        # Si déjà calculé, retourner la valeur en cache
        if cab_index in position_cache:
            return position_cache[cab_index]
        
        cab = scene_cabinets[cab_index]
        
        if cab.get('parent_index') is None:
            # Caisson central : origine à (0, 0, 0)
            pos = (0.0, 0.0, 0.0)
            position_cache[cab_index] = pos
            return pos
        
        # Caisson attaché : calculer la position relative au parent
        parent_index = cab['parent_index']
        
        # Vérifier que le parent existe
        if parent_index < 0 or parent_index >= len(scene_cabinets):
            # Parent invalide : placer à (0, 0, 0)
            pos = (0.0, 0.0, 0.0)
            position_cache[cab_index] = pos
            return pos
        
        # Calculer récursivement la position du parent
        parent_pos = calculate_position(parent_index)
        parent_cab = scene_cabinets[parent_index]
        attachment_dir = cab.get('attachment_dir', 'right')
        
        parent_L = parent_cab['dims']['L_raw'] * unit_factor
        parent_H = parent_cab['dims']['H_raw'] * unit_factor
        cab_L = cab['dims']['L_raw'] * unit_factor
        
        if attachment_dir == 'left':
            # À gauche : X négatif
            pos = (parent_pos[0] - cab_L, parent_pos[1], parent_pos[2])
        elif attachment_dir == 'right':
            # À droite : X positif
            pos = (parent_pos[0] + parent_L, parent_pos[1], parent_pos[2])
        elif attachment_dir == 'up':
            # Au-dessus : Z positif
            pos = (parent_pos[0], parent_pos[1], parent_pos[2] + parent_H)
        else:
            # Par défaut : à droite
            pos = (parent_pos[0] + parent_L, parent_pos[1], parent_pos[2])
        
        # Mettre en cache et retourner
        position_cache[cab_index] = pos
        return pos
    
    # Calculer les positions pour tous les caissons
    for i in range(len(scene_cabinets)):
        pos = calculate_position(i)
        abs_origins.append(pos)
    
    return abs_origins

def calculate_hole_positions(W_raw):
    """
    Calcule les positions des trous de vis et tourillons sur une profondeur W_raw.
    
    Nouvelle logique d'alternance vis/tourillons :
    - Toujours commencer par 1 tourillon et finir sur 1 tourillon
    - Les 2 tourillons les plus proches des bords sont à 25mm du bord
    - Ensuite, selon la longueur totale :
      * 0 à 300mm : 2 tourillons au total (= 1 vis entre eux)
      * 300 à 500mm : 3 tourillons au total (2 vis)
      * 500 à 700mm : 4 tourillons au total (3 vis)
      * 700mm ou plus : 5 tourillons au total (4 vis)
    """
    ys_vis = []
    ys_dowel = []
    
    # Les tourillons aux bords sont toujours à 25mm
    edge_dowel = 25.0
    
    # Déterminer le nombre total de tourillons selon la longueur
    if W_raw <= 300.0:
        num_dowels = 2  # 1 à chaque bord, pas de tourillons au milieu
    elif W_raw <= 500.0:
        num_dowels = 3  # 1 à chaque bord + 1 au milieu
    elif W_raw <= 700.0:
        num_dowels = 4  # 1 à chaque bord + 2 au milieu
    else:
        num_dowels = 5  # 1 à chaque bord + 3 au milieu
    
    # Nombre de vis = nombre de tourillons - 1 (car alternance)
    num_vis = num_dowels - 1
    
    # Espace disponible pour placer les tourillons/vis au milieu
    available_length = W_raw - (2 * edge_dowel)
    
    # Placer le premier tourillon au bord gauche
    ys_dowel.append(edge_dowel)
    
    # Si on a des tourillons au milieu (num_dowels > 2)
    if num_dowels > 2:
        # Nombre de tourillons au milieu
        middle_dowels = num_dowels - 2
        
        # On divise l'espace disponible en (middle_dowels + 1) intervalles égaux
        # Les tourillons du milieu sont placés aux limites de ces intervalles
        interval = available_length / (middle_dowels + 1)
        
        # Placer les tourillons au milieu
        for i in range(1, middle_dowels + 1):
            dowel_pos = edge_dowel + (i * interval)
            ys_dowel.append(dowel_pos)
        
        # Placer les vis entre les tourillons (au milieu de chaque intervalle)
        for i in range(middle_dowels + 1):
            vis_pos = edge_dowel + ((i + 0.5) * interval)
            ys_vis.append(vis_pos)
    else:
        # Cas spécial : seulement 2 tourillons (un à chaque bord)
        # Une seule vis au milieu de l'espace disponible
        vis_pos = edge_dowel + (available_length / 2.0)
        ys_vis.append(vis_pos)
    
    # Placer le dernier tourillon au bord droit
    ys_dowel.append(W_raw - edge_dowel)
    
    # Trier les listes pour l'ordre croissant
    ys_dowel.sort()
    ys_vis.sort()
    
    return ys_vis, ys_dowel

def is_drawer_slide_hole(hole):
    """Détecte les trous de coulisses de tiroir à partir du diamètre."""
    diam_str = str(hole.get('diam_str', ''))
    return (
        ("5/12" in diam_str)
        or ("5/11.5" in diam_str)
        or ((("⌀3" in diam_str) or ("/3" in diam_str)) and ("/10" not in diam_str))
    )

def merge_drawer_panel_holes(face_holes, tranche_longue_holes, tranche_cote_holes=None):
    """Fusionne les trous utiles aux cotes Y des faces/dos de tiroir."""
    merged = []
    for holes in (face_holes, tranche_longue_holes, tranche_cote_holes):
        if holes:
            merged.extend(holes)
    return merged

def get_hinge_y_positions(door_height, custom_positions=None):
    """Calcule les positions Y des charnières pour une porte de hauteur donnée.
    
    Args:
        door_height: Hauteur de la porte en mm
        custom_positions: Liste optionnelle de positions Y personnalisées en mm. Si fournie, cette liste est retournée telle quelle.
    
    Returns:
        Liste des positions Y des charnières en mm
    """
    # Si des positions personnalisées sont fournies, les utiliser
    if custom_positions is not None and len(custom_positions) > 0:
        return sorted([float(pos) for pos in custom_positions])
    
    # Sinon, utiliser le calcul par défaut
    # 3 charnières standard : une en haut, une au milieu, une en bas
    # Espacement : 100mm du haut et du bas, puis répartition équitable
    positions = []
    if door_height > 200:
        positions.append(100.0)
        positions.append(door_height / 2.0)
        positions.append(door_height - 100.0)
    elif door_height > 100:
        positions.append(50.0)
        positions.append(door_height - 50.0)
    else:
        positions.append(door_height / 2.0)
    return positions

def get_mobile_shelf_holes(h_side, t_tb, shelf, W_mont):
    """Calcule les trous pour les taquets d'une étagère mobile."""
    holes = []
    y_pos = t_tb + shelf.get('height', 0.0)
    # Trous de taquets : mêmes coordonnées X que les trous
    # d'assemblage montant / traverse (vis + tourillons), pour
    # respecter une trame unique sur la profondeur du montant.
    ys_vis, ys_dowel = calculate_hole_positions(W_mont)
    x_positions = sorted(set(ys_vis + ys_dowel))
    for x in x_positions:
        holes.append({'type': 'etagere_taquet', 'x': x, 'y': y_pos, 'diam_str': "⌀5"})
    return holes

def calculate_back_panel_holes(W_back, H_back, cabinet):
    """
    Calcule les trous pour le panneau arrière.
    
    Règle :
    - Les 2 trous les plus proches du haut et du bas doivent être à 50 mm des bords
    - L'espacement entre les trous est d'environ 100 mm, ajusté pour respecter les 50 mm aux bords
    - Pour chaque montant secondaire, les trous doivent être au centre du montant
    """
    holes = []
    # Trous de tourillons pour les montants secondaires (au centre de chaque panneau)
    if 'vertical_dividers' in cabinet and cabinet['vertical_dividers']:
        for div in cabinet['vertical_dividers']:
            div_x = div['position_x']
            div_thickness = div.get('thickness', 19.0)
            
            # Position au centre du montant
            # IMPORTANT: div_x est déjà en coordonnées relatives au bord intérieur du montant gauche
            # Le fond commence également au bord intérieur, donc pas besoin de soustraire t_lr
            x_center = div_x + div_thickness / 2.0
            x_rel = x_center  # Pas de soustraction de t_lr !
            
            # Premier trou à 50 mm du bord bas, dernier trou à 50 mm du bord haut
            y_start = 50.0
            y_end = H_back - 50.0
            
            # Si la hauteur disponible est trop petite, mettre un seul trou au milieu
            if y_end <= y_start:
                y_center = H_back / 2.0
                holes.append({'type': 'tourillon', 'x': x_rel, 'y': y_center, 'diam_str': "⌀8/22"})
            else:
                # Distance disponible pour répartir les trous
                available_space = y_end - y_start
                
                # Calculer le nombre d'intervalles pour avoir environ 100 mm d'espacement
                # On utilise floor pour rester proche de 100 mm (plutôt que de dépasser)
                import math
                num_intervals = max(1, math.floor(available_space / 100.0))
                
                # Calculer l'espacement exact pour remplir l'espace disponible
                spacing = available_space / num_intervals
                
                # Générer les trous avec l'espacement calculé
                # Premier trou exactement à 50 mm
                holes.append({'type': 'tourillon', 'x': x_rel, 'y': y_start, 'diam_str': "⌀8/22"})
                
                # Trous intermédiaires
                for i in range(1, num_intervals):
                    y = y_start + i * spacing
                    holes.append({'type': 'tourillon', 'x': x_rel, 'y': y, 'diam_str': "⌀8/22"})
                
                # Dernier trou exactement à H_back - 50 mm
                holes.append({'type': 'tourillon', 'x': x_rel, 'y': y_end, 'diam_str': "⌀8/22"})
                # On utilise floor pour rester proche de 100 mm (plutôt que de dépasser)
                import math
                num_intervals = max(1, math.floor(available_space / 100.0))
                
                # Calculer l'espacement exact pour remplir l'espace disponible
                spacing = available_space / num_intervals
                
                # Générer les trous avec l'espacement calculé
                # Premier trou exactement à 50 mm
                holes.append({'type': 'tourillon', 'x': x_rel, 'y': y_start, 'diam_str': "⌀8/22"})
                
                # Trous intermédiaires
                for k in range(1, num_intervals):
                    y = y_start + k * spacing
                    holes.append({'type': 'tourillon', 'x': x_rel, 'y': y, 'diam_str': "⌀8/22"})
                
                # Dernier trou exactement à H_back - 50 mm
                holes.append({'type': 'tourillon', 'x': x_rel, 'y': y_end, 'diam_str': "⌀8/22"})

    # Trous de vis sur tout le pourtour du panneau arrière
    # - 8 mm du bord
    # - Espacement :
    #   * ~300 mm si la longueur du bord >= 300 mm
    #   * ~100 mm si la longueur du bord < 300 mm
    import math
    edge_offset = 8.0
    screw_diam = "⌀5/12"

    # Bords haut et bas (trous répartis en X)
    if W_back > 2 * edge_offset:
        x_start = edge_offset
        x_end = W_back - edge_offset
        available = x_end - x_start
        target_spacing = 100.0 if W_back < 300.0 else 300.0
        num_intervals = max(1, math.floor(available / target_spacing))
        spacing = available / num_intervals

        # Bas (y = 8)
        y_bottom = edge_offset
        holes.append({'type': 'vis', 'x': x_start, 'y': y_bottom, 'diam_str': screw_diam})
        for i in range(1, num_intervals):
            x = x_start + i * spacing
            holes.append({'type': 'vis', 'x': x, 'y': y_bottom, 'diam_str': screw_diam})
        holes.append({'type': 'vis', 'x': x_end, 'y': y_bottom, 'diam_str': screw_diam})

        # Haut (y = H_back - 8)
        y_top = H_back - edge_offset
        holes.append({'type': 'vis', 'x': x_start, 'y': y_top, 'diam_str': screw_diam})
        for i in range(1, num_intervals):
            x = x_start + i * spacing
            holes.append({'type': 'vis', 'x': x, 'y': y_top, 'diam_str': screw_diam})
        holes.append({'type': 'vis', 'x': x_end, 'y': y_top, 'diam_str': screw_diam})

    # Bords gauche et droit (trous répartis en Y)
    if H_back > 2 * edge_offset:
        y_start = edge_offset
        y_end = H_back - edge_offset
        available_y = y_end - y_start
        target_spacing_y = 100.0 if H_back < 300.0 else 300.0
        num_intervals_y = max(1, math.floor(available_y / target_spacing_y))
        spacing_y = available_y / num_intervals_y

        x_left = edge_offset
        x_right = W_back - edge_offset

        # Gauche (x = 8)
        holes.append({'type': 'vis', 'x': x_left, 'y': y_start, 'diam_str': screw_diam})
        for i in range(1, num_intervals_y):
            y = y_start + i * spacing_y
            holes.append({'type': 'vis', 'x': x_left, 'y': y, 'diam_str': screw_diam})
        holes.append({'type': 'vis', 'x': x_left, 'y': y_end, 'diam_str': screw_diam})

        # Droit (x = W_back - 8)
        holes.append({'type': 'vis', 'x': x_right, 'y': y_start, 'diam_str': screw_diam})
        for i in range(1, num_intervals_y):
            y = y_start + i * spacing_y
            holes.append({'type': 'vis', 'x': x_right, 'y': y, 'diam_str': screw_diam})
        holes.append({'type': 'vis', 'x': x_right, 'y': y_end, 'diam_str': screw_diam})

    return holes

def detect_collisions(cabinet):
    """Détecte les collisions entre éléments."""
    # Stub pour l'instant
    return []

def calculate_zones_from_dividers(cabinet):
    """Calcule les zones X (colonnes) créées par les montants secondaires."""
    zones = []
    dims = cabinet['dims']
    L_raw = dims['L_raw']
    t_lr = dims['t_lr_raw']
    
    if 'vertical_dividers' not in cabinet or not cabinet['vertical_dividers']:
        # Pas de montants secondaires : une seule zone
        zones.append({'id': 0, 'x_min': t_lr, 'x_max': L_raw - t_lr})
        return zones
    
    dividers = sorted(cabinet['vertical_dividers'], key=lambda d: d['position_x'])
    x_min = t_lr
    
    for idx, div in enumerate(dividers):
        div_x = div['position_x']
        div_th = div.get('thickness', 19.0)
        div_left = div_x - div_th / 2.0
        zones.append({'id': idx, 'x_min': x_min, 'x_max': div_left})
        x_min = div_x + div_th / 2.0
    
    zones.append({'id': len(dividers), 'x_min': x_min, 'x_max': L_raw - t_lr})
    return zones

def calculate_vertical_zones_in_x_zone(x_zone, cabinet):
    """Calcule les zones verticales (Y) dans une zone X donnée."""
    zones = []
    dims = cabinet['dims']
    H_raw = dims['H_raw']
    t_tb = dims['t_tb_raw']
    tol_mm = 1.0

    y_min_global = t_tb
    y_max_global = H_raw - t_tb
    blocking_elements = []
    
    # Collecter tous les éléments horizontaux qui bloquent
    if 'shelves' in cabinet:
        L_raw = dims['L_raw']
        t_lr = dims['t_lr_raw']
        for s in cabinet['shelves']:
            if s.get('_preview', False):
                continue

            # Si la largeur stockee n'existe pas, on considere l'etagere sur toute la largeur interieure.
            s_x_start = s.get('stored_shelf_x_start_mm')
            s_width = s.get('stored_shelf_width_mm')
            if s_x_start is None or s_width is None or s_width <= 0:
                s_x_start = t_lr
                s_x_end = L_raw - t_lr
            else:
                s_x_end = s_x_start + s_width

            if s_x_start < x_zone['x_max'] and s_x_end > x_zone['x_min']:
                y0 = t_tb + float(s.get('height', 0.0))
                th = float(s.get('thickness', 19.0))
                y1 = y0 + max(0.0, th)
                # Clamp aux limites interieures du caisson
                y0 = max(y_min_global, min(y0, y_max_global))
                y1 = max(y_min_global, min(y1, y_max_global))
                if y1 - y0 > 0.0:
                    blocking_elements.append({'y0': y0, 'y1': y1})
    
    # IMPORTANT : Les tiroirs NE sont PAS des éléments bloquants pour le calcul des zones
    # Les tiroirs sont simplement placés dans des zones existantes créées par les étagères et traverses
    # Ils ne doivent pas créer de nouvelles zones verticales
    
    # Fusionner les intervalles bloquants proches (<= 1 mm) pour eviter les micro-zones parasites.
    blocking_elements.sort(key=lambda e: e['y0'])
    merged = []
    for elem in blocking_elements:
        if not merged:
            merged.append({'y0': elem['y0'], 'y1': elem['y1']})
            continue
        last = merged[-1]
        if elem['y0'] <= last['y1'] + tol_mm:
            last['y1'] = max(last['y1'], elem['y1'])
        else:
            merged.append({'y0': elem['y0'], 'y1': elem['y1']})

    # Construire les zones vides entre intervalles bloquants.
    cursor = y_min_global
    for blk in merged:
        if blk['y0'] > cursor + tol_mm:
            zones.append({'y_min': cursor, 'y_max': blk['y0']})
        cursor = max(cursor, blk['y1'])

    if cursor < y_max_global - tol_mm:
        zones.append({'y_min': cursor, 'y_max': y_max_global})
    
    return zones

def calculate_all_zones_2d(cabinet, include_all_elements=True):
    """Calcule toutes les zones 2D (X et Y) dans le caisson."""
    zones = []
    x_zones = calculate_zones_from_dividers(cabinet)
    
    if not x_zones:
        x_zones = [{'id': 0, 'x_min': cabinet['dims']['t_lr_raw'], 'x_max': cabinet['dims']['L_raw'] - cabinet['dims']['t_lr_raw']}]
    
    zone_id = 0
    for x_zone in x_zones:
        y_zones = calculate_vertical_zones_in_x_zone(x_zone, cabinet)
        for y_zone in y_zones:
            zones.append({
                'id': zone_id,
                'label': f"Zone {zone_id}",
                'x_min': x_zone['x_min'],
                'x_max': x_zone['x_max'],
                'y_min': y_zone['y_min'],
                'y_max': y_zone['y_max']
            })
            zone_id += 1
    
    return zones

def get_vertical_divider_tranche_holes(W_mont, div_th):
    """
    Calcule les trous sur les tranches (haut et bas) d'un montant secondaire.
    
    Règle : Même système que les montants principaux (vis + tourillons)
    - Tourillons : tous les 100 mm avec 25 mm de marge aux bords
    - Vis : tous les 100 mm également, décalées de 50 mm par rapport aux tourillons
    
    IMPORTANT : Pour les tranches longues (haut et bas), la fonction de dessin utilise
    h['x'] comme position le long de la longueur du panneau (qui est W_mont pour un montant).
    """
    holes = []
    # Utiliser le même système que pour les montants principaux
    ys_vis, ys_dowel = calculate_hole_positions(W_mont)
    
    # Trous sur les TRANCHES (haut et bas) : vis ⌀3/10
    # Le système de dessin appliquera ces trous aux deux tranches
    # IMPORTANT : utiliser 'x' pour la position le long de la longueur (W_mont)
    for y_pos in ys_vis:
        holes.append({'type': 'vis', 'x': y_pos, 'y': 0, 'diam_str': "⌀3/10"})
    for y_pos in ys_dowel:
        holes.append({'type': 'tourillon', 'x': y_pos, 'y': 0, 'diam_str': "⌀8/22"})
    
    return holes


def get_vertical_divider_holes(W_raw, h_side, t_tb, div_x, t_lr):
    """
    Retourne lesrous pour les faces d'un montant secondaire (vertical divider).
    Ces trous servent à assembler le diviseur avec les traverses haute et basse.
    
    Args:
        W_raw: Profondeur brute du caisson
        h_side: Hauteur du montant
        t_tb: Épaisseur des traverses
        div_x: Position X du diviseur
        t_lr: Épaisseur des montants latéraux
        
    Returns:
        Liste de dictionnaires de trous
    """
    holes = []
    ys_vis, ys_dowel = calculate_hole_positions(W_raw)
    
    # Trous en haut et en bas pour l'assemblage avec les traverses
    for x in ys_vis:
        holes.append({'type':'vis','x':x,'y':t_tb/2,'diam_str':"⌀3"})
        holes.append({'type':'vis','x':x,'y':h_side-t_tb/2,'diam_str':"⌀3"})
    for x in ys_dowel:
        holes.append({'type':'tourillon','x':x,'y':t_tb/2,'diam_str':"⌀8/10"})
        holes.append({'type':'tourillon','x':x,'y':h_side-t_tb/2,'diam_str':"⌀8/10"})
    
    return holes

def get_traverse_holes_for_divider(L_trav, div_x, t_lr, t_tb, W_raw):
    """
    Calcule les trous sur les tranches des traverses pour un montant secondaire.
    
    Règle demandée :
    - Les trous de tourillons sur la tranche de la traverse doivent être
      alignés en profondeur avec ceux présents sur les bords des montants
      principaux gauche/droite (même pas de 100 mm avec 25 mm de marge aux bords).
    - Uniquement des tourillons (pas de vis).
    """
    holes = []
    # Position du montant secondaire projetée sur la longueur de la traverse
    x_rel = div_x - t_lr
    # Reprendre exactement le même pas que pour les montants principaux
    _, ys_dowel = calculate_hole_positions(W_raw)
    for y in ys_dowel:
        holes.append({'type': 'tourillon', 'x': x_rel, 'y': y, 'diam_str': "⌀8/22"})
    return holes

def get_traverse_face_holes_for_divider(L_trav, div_x, t_lr, t_tb, W_raw, t_fb):
    """
    Calcule les trous sur les faces des traverses pour un montant secondaire.
    
    Règle :
    - Utilise la même logique que les trous de tourillons sur les tranches des traverses
    - MAIS adapté à la profondeur réelle du montant secondaire (W - t_fb)
    - Les montants secondaires ne vont pas jusqu'au fond (profondeur réduite)
    - Uniquement des tourillons (pas de vis)
    """
    holes = []
    # Position relative du montant secondaire
    x_rel = div_x - t_lr
    
    # Calculer la profondeur réelle du montant secondaire
    # Les montants secondaires ont une profondeur de W - t_fb (ne vont pas jusqu'au fond)
    divider_depth = W_raw - t_fb
    
    # Utiliser la logique standard mais avec la profondeur réduite
    _, ys_dowel = calculate_hole_positions(divider_depth)
    
    for y in ys_dowel:
        holes.append({'type': 'tourillon', 'x': x_rel, 'y': y, 'diam_str': "⌀8/10"})
    
    return holes

def get_mounting_holes_for_zone_element(element, zone, cabinet):
    """Calcule les trous de montage pour un élément dans une zone."""
    # Stub pour l'instant
    return []

def get_vertical_shelf_tranche_holes(W_mont, vs_th):
    """Calcule les trous sur les tranches (haut et bas) d'une étagère verticale."""
    holes = []
    # Trous de tourillons : aux 2 extrémités à 25mm des DEUX bords
    # Premier tourillon à 25mm du bord bas
    holes.append({'type': 'tourillon', 'x': vs_th/2, 'y': 25.0, 'diam_str': "⌀8/22"})
    # Dernier tourillon à 25mm du bord haut
    holes.append({'type': 'tourillon', 'x': vs_th/2, 'y': W_mont - 25.0, 'diam_str': "⌀8/22"})
    
    # Trous intermédiaires si nécessaire (espacement de 100mm entre les tourillons)
    # Calculer le nombre de trous intermédiaires
    available_space = W_mont - 50.0  # Espace entre les deux tourillons aux extrémités
    if available_space > 100.0:
        num_intermediate = int((available_space - 25.0) / 100.0)  # Nombre de trous intermédiaires
        for i in range(1, num_intermediate + 1):
            y_pos = 25.0 + (i * 100.0)
            if y_pos < W_mont - 25.0:  # S'assurer qu'on ne dépasse pas le dernier tourillon
                holes.append({'type': 'tourillon', 'x': vs_th/2, 'y': y_pos, 'diam_str': "⌀8/22"})
    
    return holes
