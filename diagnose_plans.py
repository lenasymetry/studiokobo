#!/usr/bin/env python3
"""Diagnostic script to check what plans are generated for DXF"""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

# Create a minimal test cabinet data
test_cabinet = {
    "width": 500,
    "height": 700,
    "depth": 550,
    "left_thickness": 19,
    "right_thickness": 19,
    "top_thickness": 19,
    "bottom_thickness": 19,
    "front_thickness": 19,
    "back_thickness": 3,
    "foot_height": 100,
    
    # Add drawers
    "drawers": [
        {
            "drawer_tech_type": "K",
            "drawer_bottom_offset": 0,
            "zone_id": 0,
        }
    ],
    
    # Add door
    "door_props": {
        "has_door": True,
        "door_type": "single",
        "door_gap": 10,
        "door_opening": "left",
        "door_thickness": 18,
        "door_model": "standard",
    },
    
    # Add vertical dividers
    "vertical_dividers": [
        {
            "position_x": 250,
            "thickness": 19,
        }
    ],
    
    # No shelves for simplicity
    "shelves": [],
}

# Simulate cabinet inside a cabinet list
cabinets = [test_cabinet]

# Import and trace the plan creation
try:
    from export_manager import generate_stacked_html_plans
    
    print("=" * 70)
    print("DIAGNOSTIC: Checking which plans are created")
    print("=" * 70)
    
    # Call with dxf format to see what gets generated
    dxf_bytes, success = generate_stacked_html_plans(
        cabinets_to_process=cabinets,
        indices_to_process=[0],
        output_format='dxf'
    )
    
    if success:
        print(f"\n✓ DXF generated successfully ({len(dxf_bytes)} bytes)")
        print("\nNow analyzing DXF structure...")
        
        import ezdxf
        import tempfile
        from pathlib import Path
        
        # Write DXF to temp file
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            temp_path = Path(f.name)
            f.write(dxf_bytes)
        
        # Analyze the DXF
        try:
            doc = ezdxf.readfile(str(temp_path))
            msp = doc.modelspace()
            
            # Count entities
            circles = list(msp.query('CIRCLE'))
            texts = list(msp.query('TEXT'))
            lines = list(msp.query('LINE'))
            lwpolylines = list(msp.query('LWPOLYLINE'))
            
            print(f"  Circles (holes):  {len(circles)}")
            print(f"  Text labels:      {len(texts)}")
            print(f"  Lines:            {len(lines)}")
            print(f"  LWPolylines:      {len(lwpolylines)}")
            
            # Extract unique text labels to see which plans were created
            texts_content = set()
            for text_entity in texts:
                try:
                    content = text_entity.dxf.text
                    if content and len(content) < 100:
                        texts_content.add(content)
                except:
                    pass
            
            print(f"\n  Plan titles found in DXF:")
            for text in sorted(texts_content):
                if "C0" in text or "Groupe" in text or any(x in text for x in ["Montant", "Traverse", "Porte", "Façade", "Dos", "Fond"]):
                    print(f"    - {text}")
        
        finally:
            temp_path.unlink(missing_ok=True)
    else:
        print(f"\n✗ DXF generation failed: {dxf_bytes}")

except Exception as e:
    import traceback
    print(f"✗ Error: {e}")
    traceback.print_exc()
