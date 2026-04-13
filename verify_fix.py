#!/usr/bin/env python3
"""Verify that hole parameter order fixes have been applied to export_manager.py"""

import re
import sys

def check_parameter_order():
    """Check that tuples in export_manager use correct parameter order."""
    print("=" * 70)
    print("VERIFICATION: Hole Parameter Order Fix in export_manager.py")
    print("=" * 70)
    
    with open('/Users/lenapatarin/Documents/ANNEE1/code/export_manager.py', 'r') as f:
        content = f.read()
    
    all_correct = True
    
    # Check 1: Traverse parameters
    # Should be: traverse_face_holes_left, tholes, []
    if 'traverse_face_holes_left, tholes, []' in content:
        print("\n✓ Traverse Bas/Haut: uses correct order")
        print("  Position [5] = fh (face holes)")
        print("  Position [6] = t_long_h (top/bottom edge holes) ✓")
        print("  Position [7] = t_cote_h (side edge holes = empty []) ✓")
    else:
        print("\n✗ Traverse: parameter order NOT fixed properly")
        all_correct = False
    
    # Check 2: Montant Gauche/Droit
    # Should be: holes_mg, [], tranche_holes_mg
    if 'holes_mg, [], tranche_holes_mg' in content:
        print("\n✓ Montant Gauche/Droit: uses correct order")
        print("  Position [5] = fh (face holes)")
        print("  Position [6] = t_long_h (top/bottom edge = empty []) ✓")
        print("  Position [7] = t_cote_h (side edge holes) ✓")
    else:
        print("\n✗ Montant Gauche/Droit: parameter order NOT fixed properly")
        all_correct = False
    
    # Check 3: Montant Secondaire
    # Should be: divider_element_holes_left[div_idx], [], div_tranche_holes
    if 'divider_element_holes_left[div_idx], [], div_tranche_holes' in content:
        print("\n✓ Montant Secondaire: uses correct order")
        print("  Position [5] = fh (face holes)")
        print("  Position [6] = t_long_h (top/bottom edge = empty []) ✓")
        print("  Position [7] = t_cote_h (side edge holes) ✓")
    else:
        print("\n✗ Montant Secondaire: parameter order NOT fixed properly")
        all_correct = False
    
    # Verify parsing code expects this order
    parsing_pattern = r'title, Lp, Wp, Tp, ch, fh, t_long_h, t_cote_h'
    if re.search(parsing_pattern, content):
        print("\n✓ Parsing code: expects correct order (fh, t_long_h, t_cote_h)")
    else:
        print("\n✗ Parsing code: order mismatch detected")
        all_correct = False
    
    print("\n" + "=" * 70)
    if all_correct:
        print("RESULT: All parameter ordering fixes SUCCESSFULLY APPLIED ✓")
        print("=" * 70)
        print("\nSummary of fixes:")
        print("  1. Traverses: tholes now in t_long_h position (was in t_cote_h)")
        print("  2. Montants: tranche_holes now in t_cote_h position (was in t_long_h)")
        print("  3. Montants Secondaires: tranche_holes now in t_cote_h position")
        print("\nExpected result: All holes now positioned correctly during drawing")
        return 0
    else:
        print("RESULT: Some parameter ordering fixes may not have been applied ✗")
        print("=" * 70)
        return 1

if __name__ == "__main__":
    sys.exit(check_parameter_order())
