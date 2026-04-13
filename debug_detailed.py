#!/usr/bin/env python3
"""
DEBUG SCRIPT: Trace EXACTLY which plans are added and drawn
"""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

# Monkey-patch to trace plans.append calls
original_append = list.append
plans_trace = []

def traced_append(self, item):
    """Track all plans.append calls"""
    if hasattr(item, '__getitem__') and len(item) > 0:
        try:
            title = item[0]
            plans_trace.append(('added', title))
        except:
            pass
    return original_append(self, item)

list.append = traced_append

import streamlit as st
if 'project_name' not in st.session_state:
    st.session_state.project_name = "Test Project"
if 'unit_select' not in st.session_state:
    st.session_state.unit_select = "mm"
if 'foot_height' not in st.session_state:
    st.session_state.foot_height = 100

# Now import and test
from export_manager import generate_stacked_html_plans
import ezdxf
import tempfile
from pathlib import Path

# Restore normal append
list.append = original_append

def test_comprehensive_cabinet():
    """Full cabinet with all features"""
    return {
        "dims": {
            "L_raw": 900,
            "W_raw": 550,
            "H_raw": 1000,
            "t_lr_raw": 19,
            "t_fb_raw": 3,
            "t_tb_raw": 19,
        },
        "base_elements": {
            "has_bottom_traverse": True,
            "has_top_traverse": True,
            "has_left_upright": True,
            "has_right_upright": True,
            "has_back_panel": True,
        },
        "shelves": [
            {"shelf_type": "fixe", "thickness": 19, "height": 300, "zone_id": 0},
            {"shelf_type": "mobile", "thickness": 19, "height": 600, "zone_id": 0},
        ],
        "drawers": [
            {
                "drawer_tech_type": "K",
                "drawer_bottom_offset": 0,
                "drawer_system": "TANDEMBOX",
                "drawer_gap": 2.0,
                "zone_id": 0,
            }
        ],
        "door_props": {
            "has_door": True,
            "door_type": "single",
            "door_gap": 10,
            "door_opening": "left",
            "door_thickness": 18,
            "door_model": "standard",
        },
        "vertical_dividers": [
            {"position_x": 450, "thickness": 19}
        ],
    }

print("=" * 90)
print("DETAILED DIAGNOSTIC: Plans Added vs. DXF Drawn")
print("=" * 90)

cabinet = test_comprehensive_cabinet()

print("\n1. EXPORTING DXF...")
try:
    dxf_bytes, success = generate_stacked_html_plans(
        cabinets_to_process=[cabinet],
        indices_to_process=[0],
        output_format='dxf'
    )
    
    if not success:
        error_msg = dxf_bytes.decode('utf-8', errors='ignore')
        print(f"\n❌ DXF GENERATION FAILED:")
        print(error_msg)
        sys.exit(1)
    
    print(f"✓ DXF generated ({len(dxf_bytes)} bytes)")
    
    # Analyze DXF
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
        temp_path = Path(f.name)
        f.write(dxf_bytes)
    
    try:
        doc = ezdxf.readfile(str(temp_path))
        msp = doc.modelspace()
        
        dxf_titles = []
        for entity in msp.query('TEXT'):
            try:
                text = entity.dxf.text
                if "FEUILLE D'USINAGE" in text:
                    title = text.replace("FEUILLE D'USINAGE : ", "").strip()
                    dxf_titles.append(title)
            except:
                pass
        
        print(f"\n2. DXF CONTAINS {len(dxf_titles)} LAYOUTS:")
        for i, title in enumerate(dxf_titles, 1):
            print(f"   {i:2d}. {title}")
        
    finally:
        temp_path.unlink(missing_ok=True)
    
    # Now trace what SHOULD have been added
    print(f"\n3. EXPECTED PLANS (based on cabinet data):")
    
    expected_items = []
    cab_idx = 0
    
    # Base elements
    print("   a) BASE ELEMENTS:")
    for name in ["Traverse Bas (Tb)", "Traverse Haut (Th)", "Montant Gauche (Mg)", 
                 "Montant Droit (Md)", "Panneau Arrière (F)"]:
        print(f"      - {name}")
        expected_items.append(name)
    
    # Shelves
    if 'shelves' in cabinet and cabinet['shelves']:
        print("   b) SHELVES:")
        for i, shelf in enumerate(cabinet['shelves']):
            s_type = shelf.get('shelf_type', 'mobile').capitalize()
            print(f"      - Étagère {s_type} (C{cab_idx})")
            expected_items.append(f"Étagère {s_type} (C{cab_idx})")
    
    # Drawers
    if 'drawers' in cabinet and cabinet['drawers']:
        print("   c) DRAWERS:")
        print(f"      - Façade Tiroir Groupe X (C{cab_idx}) [Type K]")
        print(f"      - Tiroir-Dos Groupe X (C{cab_idx}) [Type K]")
        print(f"      - Tiroir-Fond Groupe X (C{cab_idx}) [Type K]")
        expected_items.extend([
            f"Façade Tiroir Groupe 1 (C{cab_idx})",
            f"Tiroir-Dos Groupe 1 (C{cab_idx})",
            f"Tiroir-Fond Groupe 1 (C{cab_idx})",
        ])
    
    # Door
    if cabinet.get('door_props', {}).get('has_door'):
        print("   d) DOOR:")
        print(f"      - Porte (C{cab_idx})")
        expected_items.append(f"Porte (C{cab_idx})")
    
    # Dividers
    if 'vertical_dividers' in cabinet and cabinet['vertical_dividers']:
        print("   e) VERTICAL DIVIDERS:")
        for div_idx in range(len(cabinet['vertical_dividers'])):
            print(f"      - Montant Secondaire {div_idx+1} (C{cab_idx}) - 1/2")
            print(f"      - Montant Secondaire {div_idx+1} (C{cab_idx}) - 2/2")
        expected_items.extend([
            f"Montant Secondaire 1 (C{cab_idx}) - 1/2",
            f"Montant Secondaire 1 (C{cab_idx}) - 2/2",
        ])
    
    print(f"\n4. COMPARISON:")
    print(f"   Expected items: {len(expected_items)}")
    print(f"   DXF layouts:    {len(dxf_titles)}")
    
    # Match them up
    print(f"\n5. DETAILED MATCHING:")
    matched = 0
    for title in expected_items:
        # Try to find partial match
        found = False
        for dxf_title in dxf_titles:
            if title.replace("Groupe X", "Groupe 1") in dxf_title or \
               (title.replace("Groupe X", "Groupe 1").split("(C")[0] in dxf_title):
                print(f"   ✓ {title}")
                matched += 1
                found = True
                break
        if not found:
            print(f"   ✗ {title} ← MISSING FROM DXF!")
    
    print(f"\nMatched: {matched}/{len(expected_items)}")
    
    if matched == len(expected_items):
        print("\n✅ ALL ITEMS PRESENT IN DXF")
    else:
        print(f"\n❌ {len(expected_items) - matched} ITEMS MISSING FROM DXF")

except Exception as e:
    import traceback
    print(f"\n❌ Exception:")
    print(traceback.format_exc())
    sys.exit(1)
