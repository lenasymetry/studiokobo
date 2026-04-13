#!/usr/bin/env python3
"""Diagnostiquer le problème du fond du tiroir (traits de construction manquants)."""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

def diagnose_drawer_bottom():
    """Analyser la structure du fond du tiroir."""
    print("=" * 70)
    print("DIAGNOSTIC: Drawer Bottom Structure")
    print("=" * 70)
    
    import streamlit as st
    st.session_state.project_name = "Test"
    st.session_state.unit_select = "mm"
    
    from export_manager import get_all_machining_plans_figures
    
    test_cabinet = {
        'dims': {
            'L_raw': 400.0, 'W_raw': 300.0, 'H_raw': 900.0,
            't_lr_raw': 19.0, 't_fb_raw': 18.0, 't_tb_raw': 18.0
        },
        'drawers': [
            {
                'drawer_system': 'TANDEMBOX',
                'drawer_tech_type': 'K',
                'drawer_face_H_raw': 150.0,
                'drawer_face_thickness': 19.0,
                'inner_thickness': 16.0,
                'drawer_gap': 2.0,
                'zone_id': 0,
                'drawer_bottom_offset': 0.0,
            }
        ],
        'door_props': {'has_door': False},
        'vertical_dividers': [],
        'shelves': []
    }
    
    try:
        print("\n✓ Generating figures...")
        figures = get_all_machining_plans_figures([test_cabinet], [0])
        
        # Find the bottom figure
        bottom_fig = None
        for title, fig in figures:
            if 'Tiroir-Fond' in title or 'Fond' in title and 'Tiroir' in title:
                bottom_fig = (title, fig)
                break
        
        if not bottom_fig:
            print("✗ No drawer bottom figure found")
            return 1
        
        title, fig = bottom_fig
        print(f"\n✓ Found drawer bottom: {title}")
        
        if hasattr(fig, 'data') and hasattr(fig, 'layout'):
            print(f"\n  Figure properties:")
            print(f"    - Traces: {len(fig.data)}")
            
            # Analyze traces
            trace_types = {}
            for trace in fig.data:
                trace_type = trace.type if hasattr(trace, 'type') else 'unknown'
                trace_types[trace_type] = trace_types.get(trace_type, 0) + 1
            
            print(f"    - Trace types: {trace_types}")
            
            # Check for rectangle/box that forms the base
            if hasattr(fig, 'layout') and hasattr(fig.layout, 'shapes'):
                print(f"    - Layout shapes: {len(fig.layout.shapes) if fig.layout.shapes else 0}")
            
            # Check if there are any issues with the figure
            print(f"\n  Analysis:")
            if 'scatter' in trace_types or 'lines' in trace_types:
                print("    ✓ Has geometric traces")
            else:
                print("    ⚠ Missing basic geometric traces")
            
            # The issue might be that the bottom doesn't have a complete outline
            # or that some construction elements are missing
            print(f"\n  Potential issues:")
            print("    - The drawer bottom might need explicit edge outlines")
            print("    - Internal construction lines might be missing")
            print("    - Consider adding a rectangle trace for the base outline")
            
            return 0
        else:
            print("  Figure structure unexpected")
            return 1
            
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(diagnose_drawer_bottom())
