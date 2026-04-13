import plotly.graph_objects as go
import numpy as np

def cuboid_mesh_for(width, depth, height, origin, color='grey', opacity=1.0, name="cuboid", showlegend=True, rotation_angle=0, rotation_axis='z', rotation_pivot=None):
    """
    Crée un mesh 3D pour un cuboïde (boîte rectangulaire).
    
    Args:
        width: Largeur (X)
        depth: Profondeur (Y)
        height: Hauteur (Z)
        origin: Tuple (x, y, z) de l'origine (coin inférieur avant gauche)
        color: Couleur (string ou rgba)
        opacity: Opacité (0.0 à 1.0)
        name: Nom de la trace
        showlegend: Afficher dans la légende
        rotation_angle: Angle de rotation en degrés
        rotation_axis: Axe de rotation ('x', 'y', 'z')
        rotation_pivot: Point de pivot pour la rotation (x, y, z)
    
    Returns:
        go.Mesh3d trace
    """
    x0, y0, z0 = origin
    
    # 8 sommets du cuboïde (dans l'ordre pour faciliter l'indexation)
    # Sommet 0: (x0, y0, z0) - coin inférieur avant gauche
    # Sommet 1: (x0+width, y0, z0) - coin inférieur avant droit
    # Sommet 2: (x0+width, y0+depth, z0) - coin inférieur arrière droit
    # Sommet 3: (x0, y0+depth, z0) - coin inférieur arrière gauche
    # Sommet 4: (x0, y0, z0+height) - coin supérieur avant gauche
    # Sommet 5: (x0+width, y0, z0+height) - coin supérieur avant droit
    # Sommet 6: (x0+width, y0+depth, z0+height) - coin supérieur arrière droit
    # Sommet 7: (x0, y0+depth, z0+height) - coin supérieur arrière gauche
    vertices_x = [x0, x0+width, x0+width, x0, x0, x0+width, x0+width, x0]
    vertices_y = [y0, y0, y0+depth, y0+depth, y0, y0, y0+depth, y0+depth]
    vertices_z = [z0, z0, z0, z0, z0+height, z0+height, z0+height, z0+height]
    
    # Indices des triangles pour les 6 faces (2 triangles par face, sens antihoraire vu de l'extérieur)
    # Face avant (y=y0): 0,1,5 et 0,5,4
    # Face arrière (y=y0+depth): 3,7,6 et 3,6,2
    # Face gauche (x=x0): 0,4,7 et 0,7,3
    # Face droite (x=x0+width): 1,2,6 et 1,6,5
    # Face bas (z=z0): 0,3,2 et 0,2,1
    # Face haut (z=z0+height): 4,5,6 et 4,6,7
    i_tris = [0, 0, 3, 3, 0, 0, 1, 1, 4, 4]
    j_tris = [1, 5, 7, 6, 4, 7, 2, 6, 5, 6]
    k_tris = [5, 4, 6, 2, 7, 3, 6, 5, 6, 7]
    
    # Appliquer la rotation si nécessaire
    if rotation_angle != 0 and rotation_pivot is not None:
        pivot_x, pivot_y, pivot_z = rotation_pivot
        angle_rad = np.radians(rotation_angle)
        
        # Translation vers l'origine du pivot
        vertices_x = [x - pivot_x for x in vertices_x]
        vertices_y = [y - pivot_y for y in vertices_y]
        vertices_z = [z - pivot_z for z in vertices_z]
        
        # Rotation
        if rotation_axis == 'z':
            cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
            new_x = [x * cos_a - y * sin_a for x, y in zip(vertices_x, vertices_y)]
            new_y = [x * sin_a + y * cos_a for x, y in zip(vertices_x, vertices_y)]
            vertices_x, vertices_y = new_x, new_y
        elif rotation_axis == 'x':
            cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
            new_y = [y * cos_a - z * sin_a for y, z in zip(vertices_y, vertices_z)]
            new_z = [y * sin_a + z * cos_a for y, z in zip(vertices_y, vertices_z)]
            vertices_y, vertices_z = new_y, new_z
        elif rotation_axis == 'y':
            cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
            new_x = [x * cos_a + z * sin_a for x, z in zip(vertices_x, vertices_z)]
            new_z = [-x * sin_a + z * cos_a for x, z in zip(vertices_x, vertices_z)]
            vertices_x, vertices_z = new_x, new_z
        
        # Translation de retour
        vertices_x = [x + pivot_x for x in vertices_x]
        vertices_y = [y + pivot_y for y in vertices_y]
        vertices_z = [z + pivot_z for z in vertices_z]
    
    return go.Mesh3d(
        x=vertices_x,
        y=vertices_y,
        z=vertices_z,
        i=i_tris,
        j=j_tris,
        k=k_tris,
        color=color,
        opacity=opacity,
        flatshading=True,
        showscale=False,
        name=name,
        showlegend=showlegend
    )

def cylinder_mesh_for(origin, height, radius, n_points=20, color='grey', name="cyl", showlegend=False):
    """Crée un mesh 3D pour un cylindre."""
    x0, y0, z0 = origin
    angles = np.linspace(0, 2*np.pi, n_points, endpoint=False)
    
    # Cercle inférieur
    x_bottom = [x0 + radius * np.cos(a) for a in angles]
    y_bottom = [y0 + radius * np.sin(a) for a in angles]
    z_bottom = [z0] * n_points
    
    # Cercle supérieur
    x_top = [x0 + radius * np.cos(a) for a in angles]
    y_top = [y0 + radius * np.sin(a) for a in angles]
    z_top = [z0 + height] * n_points
    
    # Centre inférieur et supérieur
    x_center_bottom, y_center_bottom, z_center_bottom = x0, y0, z0
    x_center_top, y_center_top, z_center_top = x0, y0, z0 + height
    
    # Combiner tous les points
    all_x = [x_center_bottom] + x_bottom + [x_center_top] + x_top
    all_y = [y_center_bottom] + y_bottom + [y_center_top] + y_top
    all_z = [z_center_bottom] + z_bottom + [z_center_top] + z_top
    
    # Indices des triangles
    i_tris, j_tris, k_tris = [], [], []
    
    # Face inférieure
    for i in range(n_points):
        i_tris.append(0)
        j_tris.append(1 + i)
        k_tris.append(1 + ((i + 1) % n_points))
    
    # Face supérieure
    center_top_idx = 1 + n_points
    for i in range(n_points):
        i_tris.append(center_top_idx)
        j_tris.append(center_top_idx + 1 + ((i + 1) % n_points))
        k_tris.append(center_top_idx + 1 + i)
    
    # Surface latérale
    for i in range(n_points):
        idx_bottom = 1 + i
        idx_top = center_top_idx + 1 + i
        next_bottom = 1 + ((i + 1) % n_points)
        next_top = center_top_idx + 1 + ((i + 1) % n_points)
        
        # Triangle 1
        i_tris.append(idx_bottom)
        j_tris.append(next_bottom)
        k_tris.append(idx_top)
        
        # Triangle 2
        i_tris.append(next_bottom)
        j_tris.append(next_top)
        k_tris.append(idx_top)

    return go.Mesh3d(
        x=all_x,
        y=all_y,
        z=all_z,
        i=i_tris,
        j=j_tris,
        k=k_tris,
        color=color,
        opacity=1.0,
        flatshading=True,
        showscale=False,
        name=name,
        showlegend=showlegend
    )

def add_zone_annotations_to_figure(fig, zones, cabinet_origin, dims, unit_factor):
    """Ajoute les annotations de zones (labels) à la figure 3D."""
    if not zones:
        return
    
    o_x, o_y, o_z = cabinet_origin
    W = dims['W_raw'] * unit_factor
    t_tb = dims['t_tb_raw'] * unit_factor
    y_plane = o_y + (W / 2.0) - 0.008
    
    for z in zones:
        annot_x = o_x + ((z['x_min'] + z['x_max']) / 2.0) * unit_factor
        annot_z = o_z + t_tb + ((z['y_min'] + z['y_max']) / 2.0) * unit_factor
        annot_y = y_plane
        
        fig.add_trace(go.Scatter3d(
            x=[annot_x],
            y=[annot_y],
            z=[annot_z],
            mode='text',
            text=[z['label']],
            textfont=dict(size=14, color='black'),
            showlegend=False,
            hoverinfo='skip'
        ))

def _create_hatch_lines_2d(x0, y0, x1, y1, spacing=0.04):
    """Crée des lignes de hachure diagonales dans un rectangle 2D."""
    lines = []
    width = x1 - x0
    height = y1 - y0
    diagonal = np.sqrt(width**2 + height**2)
    n_lines = int(diagonal / spacing)
    
    for i in range(n_lines):
        t = i / max(n_lines - 1, 1)
        # Ligne diagonale de bas-gauche à haut-droite
        if t <= width / diagonal:
            x_start = x0 + t * diagonal
            y_start = y0
            x_end = x0
            y_end = y0 + t * diagonal
        else:
            x_start = x0 + width
            y_start = y0 + (t * diagonal - width)
            x_end = x0 + (t * diagonal - height)
            y_end = y0 + height
        
        if x_start >= x0 and x_start <= x1 and y_start >= y0 and y_start <= y1:
            if x_end >= x0 and x_end <= x1 and y_end >= y0 and y_end <= y1:
                lines.append(((x_start, y_start), (x_end, y_end)))
    
    return lines

def add_hatched_zones_3d(fig, zones_2d, cabinet_origin, dims, unit_factor, zone_ids_to_hatch=None, color="rgba(80,80,80,0.65)", line_width=2, spacing_m=0.04, y_plane_offset=-0.01):
    """Ajoute des zones hachurées en 3D."""
    if not zones_2d:
        return
    
    o_x, o_y, o_z = cabinet_origin
    W = dims['W_raw'] * unit_factor
    t_tb_mm = float(dims['t_tb_raw'])
    t_tb = t_tb_mm * unit_factor
    y_plane = o_y + (W / 2.0) + y_plane_offset

    def _zone_y_mm_to_inner(y_mm: float) -> float:
        # Les zones sont stockees en coordonnees caisson (incluant l'epaisseur traverse basse).
        # Le rendu 3D travaille en coordonnees interieures pour Z.
        return float(y_mm) - t_tb_mm
    
    for z in zones_2d:
        if zone_ids_to_hatch is not None and z['id'] not in zone_ids_to_hatch:
            continue
        
        x0 = o_x + (z['x_min'] * unit_factor)
        x1 = o_x + (z['x_max'] * unit_factor)
        z0 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_min']) * unit_factor)
        z1 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_max']) * unit_factor)
        
        hatch_lines = _create_hatch_lines_2d(x0, z0, x1, z1, spacing_m)
        
        for (start, end) in hatch_lines:
            fig.add_trace(go.Scatter3d(
                x=[start[0], end[0]],
                y=[y_plane, y_plane],
                z=[start[1], end[1]],
                mode='lines',
                line=dict(color=color, width=line_width),
                hoverinfo='skip',
                showlegend=False
            ))

def add_zone_outlines_3d(fig, zones_2d, cabinet_origin, dims, unit_factor, zone_ids_to_show=None, fill_color="rgba(0,100,200,0.25)", line_color="rgba(0,100,200,0.9)", line_width=3, y_plane_offset=-0.01):
    """Ajoute les contours et remplissages des zones en 3D."""
    if not zones_2d:
        return
    
    o_x, o_y, o_z = cabinet_origin
    W = dims['W_raw'] * unit_factor
    t_tb_mm = float(dims['t_tb_raw'])
    t_tb = t_tb_mm * unit_factor
    y_plane = o_y + (W / 2.0) + y_plane_offset

    def _zone_y_mm_to_inner(y_mm: float) -> float:
        return float(y_mm) - t_tb_mm
    
    for z in zones_2d:
        if zone_ids_to_show is not None and z['id'] not in zone_ids_to_show:
            continue
        
        x0 = o_x + (z['x_min'] * unit_factor)
        x1 = o_x + (z['x_max'] * unit_factor)
        z0 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_min']) * unit_factor)
        z1 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_max']) * unit_factor)
        
        # Remplissage
        fig.add_trace(go.Mesh3d(
            x=[x0, x1, x1, x0],
            y=[y_plane, y_plane, y_plane, y_plane],
            z=[z0, z0, z1, z1],
            i=[0, 0],
            j=[1, 2],
            k=[2, 3],
            color=fill_color,
            opacity=0.25,
            flatshading=True,
            showscale=False,
            hoverinfo='skip',
            showlegend=False
        ))
        
        # Contours
        fig.add_trace(go.Scatter3d(
            x=[x0, x1, x1, x0, x0],
            y=[y_plane, y_plane, y_plane, y_plane, y_plane],
            z=[z0, z0, z1, z1, z0],
            mode='lines',
            line=dict(color=line_color, width=line_width),
            hoverinfo='skip',
            showlegend=False
        ))

def add_zone_debug_boxes_3d(fig, zones_2d, cabinet_origin, dims, unit_factor, wireframe=True, opacity=0.3, y_plane_offset=-0.005):
    """Ajoute des boîtes de debug wireframe pour visualiser les bounding boxes exactes des zones."""
    if not zones_2d:
        return
    
    o_x, o_y, o_z = cabinet_origin
    W = dims['W_raw'] * unit_factor
    t_tb_mm = float(dims['t_tb_raw'])
    t_tb = t_tb_mm * unit_factor
    y_plane = o_y + (W / 2.0) + y_plane_offset

    def _zone_y_mm_to_inner(y_mm: float) -> float:
        return float(y_mm) - t_tb_mm
    
    debug_colors = [
        "rgba(255, 0, 0, {})",
        "rgba(0, 255, 0, {})",
        "rgba(0, 0, 255, {})",
        "rgba(255, 255, 0, {})",
        "rgba(255, 0, 255, {})",
        "rgba(0, 255, 255, {})",
    ]
    
    for idx, z in enumerate(zones_2d):
        x0 = o_x + (z['x_min'] * unit_factor)
        x1 = o_x + (z['x_max'] * unit_factor)
        z0 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_min']) * unit_factor)
        z1 = o_z + t_tb + (_zone_y_mm_to_inner(z['y_max']) * unit_factor)
        
        color_template = debug_colors[idx % len(debug_colors)]
        color = color_template.format(opacity)
        
        if wireframe:
            vertices_x = [x0, x1, x1, x0, x0, x1, x1, x0]
            vertices_y = [y_plane, y_plane, y_plane, y_plane, y_plane, y_plane, y_plane, y_plane]
            vertices_z = [z0, z0, z1, z1, z0, z0, z1, z1]
            
            edges = [
                [0, 1], [1, 2], [2, 3], [3, 0],
                [4, 5], [5, 6], [6, 7], [7, 4],
                [0, 4], [1, 5], [2, 6], [3, 7]
            ]
            
            for edge in edges:
                fig.add_trace(go.Scatter3d(
                    x=[vertices_x[edge[0]], vertices_x[edge[1]]],
                    y=[vertices_y[edge[0]], vertices_y[edge[1]]],
                    z=[vertices_z[edge[0]], vertices_z[edge[1]]],
                    mode='lines',
                    line=dict(color=color, width=3),
                    hoverinfo='skip',
                    showlegend=False,
                    name=f"Debug Zone {z['id']}"
                ))

def check_element_placement_validity(element, all_zones_2d, cabinet, element_type='shelf'):
    """
    Vérifie si un élément est correctement placé dans une zone valide.
    IMPORTANT : Vérifie aussi les collisions avec les autres éléments (étagères horizontales).
    
    Args:
        element: Dictionnaire avec les propriétés de l'élément (height, zone_id, position_x, etc.)
        all_zones_2d: Liste de toutes les zones calculées
        cabinet: Dictionnaire du caisson
        element_type: Type d'élément ('shelf', 'vertical_shelf', 'divider')
    
    Returns:
        tuple: (is_valid, reason)
        - is_valid: True si l'élément est dans une zone valide, False sinon
        - reason: Raison de l'invalidité si applicable
    """
    dims = cabinet['dims']
    t_lr = dims['t_lr_raw']
    L_raw = dims['L_raw']
    t_tb = dims['t_tb_raw']
    
    if element_type == 'shelf':
        zone_id = element.get('zone_id', None)
        height = element.get('height', 0.0)
        thickness = element.get('thickness', 19.0)
        
        if zone_id is None:
            # Étagère sur tout le caisson - toujours valide
            return True, None
        
        # Vérifier si la zone existe
        if zone_id >= len(all_zones_2d):
            return False, "Zone invalide"
        
        zone = all_zones_2d[zone_id]
        
        # Vérifier si l'étagère est dans la plage Y de la zone
        shelf_y_bottom = height
        shelf_y_top = height + thickness
        
        if shelf_y_bottom < zone['y_min'] or shelf_y_top > zone['y_max']:
            return False, "Hors limites Y de la zone"
        
        # Vérifier si l'étagère chevauche un montant
        shelf_x_min = zone['x_min']
        shelf_x_max = zone['x_max']
        
        # Vérifier les montants principaux
        if shelf_x_min < t_lr or shelf_x_max > (L_raw - t_lr):
            return False, "Chevauche un montant principal"
        
        # Vérifier les montants secondaires
        for div in cabinet.get('vertical_dividers', []):
            if div.get('_preview', False):
                continue
            div_x = div['position_x']
            div_th = div.get('thickness', 19.0)
            div_x_min = div_x - div_th / 2.0
            div_x_max = div_x + div_th / 2.0
            
            # Si l'étagère chevauche le montant
            if shelf_x_min < div_x_max and shelf_x_max > div_x_min:
                return False, "Chevauche un montant secondaire"
        
        return True, None
    
    elif element_type == 'vertical_shelf':
        zone_id = element.get('zone_id', None)
        position_x = element.get('position_x', 0.0)
        bottom_y = element.get('bottom_y', 0.0)
        top_y = element.get('top_y', 100.0)
        thickness = element.get('thickness', 19.0)
        
        # Vérifier les collisions avec les étagères horizontales
        # Une étagère verticale ne doit pas traverser une planche horizontale
        vs_x_min = position_x - thickness / 2.0
        vs_x_max = position_x + thickness / 2.0
        
        # Vérifier toutes les étagères horizontales pour détecter les collisions
        for shelf in cabinet.get('shelves', []):
            if shelf.get('_preview', False):
                continue
            
            shelf_height = shelf.get('height', 0.0)
            shelf_thickness = shelf.get('thickness', 19.0)
            shelf_y_bottom = shelf_height
            shelf_y_top = shelf_height + shelf_thickness
            
            shelf_zone_id = shelf.get('zone_id', None)
            
            # Vérifier si l'étagère horizontale est dans la même zone X que l'étagère verticale
            shelf_in_same_x_zone = False
            if zone_id is not None and shelf_zone_id == zone_id:
                shelf_in_same_x_zone = True
            elif shelf_zone_id is None:
                # Étagère horizontale sur tout le caisson - vérifier si elle chevauche la position X de l'étagère verticale
                if vs_x_min >= t_lr and vs_x_max <= (L_raw - t_lr):
                    shelf_in_same_x_zone = True
            
            if shelf_in_same_x_zone:
                # Vérifier si l'étagère verticale traverse la planche horizontale
                # Collision si : l'étagère verticale commence avant le haut de la planche ET se termine après le bas de la planche
                if bottom_y < shelf_y_top and top_y > shelf_y_bottom:
                    return False, f"Traverse l'étagère horizontale à {shelf_height:.0f}mm (épaisseur {shelf_thickness:.0f}mm)"
        
        if zone_id is None:
            # Étagère verticale sans zone - vérifier si elle est dans les limites du caisson
            if vs_x_min < t_lr or vs_x_max > (L_raw - t_lr):
                return False, "Hors limites X du caisson"
            
            return True, None
        
        # Vérifier si la zone existe
        if zone_id >= len(all_zones_2d):
            return False, "Zone invalide"
        
        zone = all_zones_2d[zone_id]
        
        # Vérifier si l'étagère verticale est dans la plage X de la zone
        if vs_x_min < zone['x_min'] or vs_x_max > zone['x_max']:
            return False, "Hors limites X de la zone"
        
        # Vérifier si l'étagère verticale est dans la plage Y de la zone
        if bottom_y < zone['y_min'] or top_y > zone['y_max']:
            return False, "Hors limites Y de la zone"
        
        return True, None
    
    return True, None
