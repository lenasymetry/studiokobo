#!/usr/bin/env python3
"""
COMPARISON SCRIPT: Compare number of pages between HTML and DXF exports
"""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

import streamlit as st
if 'project_name' not in st.session_state:
    st.session_state.project_name = "Test Project"
if 'unit_select' not in st.session_state:
    st.session_state.unit_select = "mm"
if 'foot_height' not in st.session_state:
    st.session_state.foot_height = 100

from export_manager import generate_stacked_html_plans
import ezdxf
import re
import tempfile
from pathlib import Path

def count_html_pages(html_bytes):
    """Count page containers in HTML"""
    html_str = html_bytes.decode('utf-8', errors='ignore')
    count = html_str.count('<div class="page-container">')
    return count

def count_dxf_layouts(dxf_bytes):
    """Count layouts in DXF"""
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
        temp_path = Path(f.name)
        f.write(dxf_bytes)
    
    try:
        doc = ezdxf.readfile(str(temp_path))
        msp = doc.modelspace()
        count = 0
        titles = []
        
        for entity in msp.query('TEXT'):
            try:
                text = entity.dxf.text
                if "FEUILLE D'USINAGE" in text:
                    count += 1
                    title = text.replace("FEUILLE D'USINAGE : ", "").strip()
                    titles.append(title)
            except:
                pass
        
        return count, titles
    finally:
        temp_path.unlink(missing_ok=True)

def test_cabinet():
    """Test cabinet with all features"""
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
            {"shelf_type": "fixe", "thickness": 19, "height": 300},
            {"shelf_type": "mobile", "thickness": 19, "height": 600},
        ],
        "drawers": [
            {"drawer_tech_type": "K", "drawer_bottom_offset": 0, "drawer_system": "TANDEMBOX", "zone_id": 0}
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
print("HTML vs DXF EXPORT COMPARISON")
print("=" * 90)

cabinet = test_cabinet()

# Export HTML
print("\n1. Generating HTML...")
try:
    html_bytes, success = generate_stacked_html_plans(
        cabinets_to_process=[cabinet],
        indices_to_process=[0],
        output_format='html'
    )
    if not success:
        print(f"   ❌ HTML generation failed: {html_bytes.decode('utf-8', errors='ignore')[:200]}")
        sys.exit(1)
    html_pages = count_html_pages(html_bytes)
    print(f"   ✓ HTML has {html_pages} pages")
except Exception as e:
    print(f"   ❌ Exception: {e}")
    sys.exit(1)

# Export DXF
print("\n2. Generating DXF...")
try:
    dxf_bytes, success = generate_stacked_html_plans(
        cabinets_to_process=[cabinet],
        indices_to_process=[0],
        output_format='dxf'
    )
    if not success:
        print(f"   ❌ DXF generation failed: {dxf_bytes.decode('utf-8', errors='ignore')[:200]}")
        sys.exit(1)
    dxf_layouts, dxf_titles = count_dxf_layouts(dxf_bytes)
    print(f"   ✓ DXF has {dxf_layouts} layouts")
except Exception as e:
    print(f"   ❌ Exception: {e}")
    sys.exit(1)

# Compare
print("\n" + "=" * 90)
print("COMPARISON RESULTS:")
print("=" * 90)
print(f"\nHTML Pages:   {html_pages}")
print(f"DXF Layouts:  {dxf_layouts}")

if html_pages == dxf_layouts:
    print(f"\n✅ SUCCESS: HTML and DXF have the SAME number of pages/layouts!")
    print("\nDXF Layouts (in order):")
    for i, title in enumerate(dxf_titles, 1):
        print(f"  {i:2d}. {title}")
else:
    print(f"\n❌ MISMATCH: {abs(html_pages - dxf_layouts)} element(s) difference!")
    print(f"   HTML has {html_pages} pages")
    print(f"   DXF has {dxf_layouts} layouts")
    print(f"\n   DXF Layouts found:")
    for i, title in enumerate(dxf_titles, 1):
        print(f"     {i:2d}. {title}")

print("\n" + "=" * 90)
