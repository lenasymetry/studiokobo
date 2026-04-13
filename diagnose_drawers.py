#!/usr/bin/env python3
"""Diagnostiquer pourquoi les tiroirs disparaissent de l'export DXF."""

import sys
import json

# Ajouter le chemin
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

def check_drawer_structure():
    """Vérifier la structure des données de tiroir."""
    print("=" * 70)
    print("DIAGNOSTIC: Drawer Data Structure")
    print("=" * 70)
    
    # Chercher où les tiroirs sont créés/stockés
    import inspect
    from export_manager import generate_cabinet_dxf_export
    
    # Lire le code source
    source = inspect.getsource(generate_cabinet_dxf_export)
    
    # Vérifier si la clé 'drawers' existe
    if "'drawers'" in source or '"drawers"' in source:
        print("\n✓ Code references 'drawers' key")
        
        # Chercher les points d'accès à 'drawers'
        lines = source.split('\n')
        drawer_lines = []
        for i, line in enumerate(lines, 1):
            if 'drawers' in line.lower() and not line.strip().startswith('#'):
                drawer_lines.append((i, line.strip()))
        
        print(f"\nFound {len(drawer_lines)} references to 'drawers':")
        for line_no, line in drawer_lines[:15]:
            print(f"  Line {line_no}: {line[:80]}")
    else:
        print("\n✗ Code does NOT reference 'drawers' key")
    
    # Chercher où cab est créé/modifié
    print("\n" + "=" * 70)
    print("CHECKING: How 'cab' data is built")
    print("=" * 70)
    
    if "cab['drawers']" in source:
        print("\n✓ Code accesses cab['drawers']")
    else:
        print("\n✗ Code NEVER accesses cab['drawers']")
        print("\nThis could mean:")
        print("  1. The 'drawers' key is never set in the 'cab' dictionary")
        print("  2. Drawer data is stored under a different key")
        print("  3. Drawer processing is missing from export_manager.py")
    
    # Chercher la structure du dictionnaire 'cab'
    print("\n" + "=" * 70)
    print("CHECKING: Cabinet (cab) dictionary structure")
    print("=" * 70)
    
    if "cab = {" in source or "cab.get(" in source:
        print("✓ Code builds or uses 'cab' dictionary")
        
        # Chercher les clés utilisées
        import re
        key_pattern = r"cab\[(?:['|\"])([^'|\"]+)(?:['|\"])\]|cab\.get\((?:['|\"])([^'|\"]+)(?:['|\"])"
        keys = set()
        for match in re.finditer(key_pattern, source):
            key = match.group(1) or match.group(2)
            if key:
                keys.add(key)
        
        print(f"\nZnown 'cab' keys used in code:")
        for key in sorted(keys)[:20]:
            print(f"  - cab['{key}']")
    
    # Chercher où les données du meuble viennent
    print("\n" + "=" * 70)
    print("CHECKING: Data source for cabinet")
    print("=" * 70)
    
    if "project_data['cabinets']" in source:
        print("✓ Cabinet data comes from: project_data['cabinets']")
    if "cabinets" in source:
        print("✓ Code processes 'cabinets' from project_data")
    
    print("\n" + "=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)
    print("""
The most likely cause is that 'drawers' data is NOT being populated in the 'cab' dictionary
that gets passed to the export function.

Possible solutions:
1. Check if drawer data is supposed to come from project_data but isn't
2. Verify the exact key name (might be 'drawer', 'drawers_list', etc.)
3. Check if drawers are stored at a different level in the data structure
4. Look for where cabinet data is built from project_data
""")

if __name__ == "__main__":
    check_drawer_structure()
