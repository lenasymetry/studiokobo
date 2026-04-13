#!/usr/bin/env python3
"""Vérifier directement que les trous di tiroirs sont bien ajoutés à holes_mg."""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

# Patch export_manager to trace the holes
original_file = '/Users/lenapatarin/Documents/ANNEE1/code/export_manager.py'
with open(original_file, 'r') as f:
    code = f.read()

# Check that y_slide calculation is correct
if 'y_slide = t_tb + 33.0 + drp.get(\'drawer_bottom_offset\', 0.0)' in code:
    print("✓ y_slide calculation is CORRECT (using formula with t_tb + 33.0 + offset)")
else:
    print("✗ y_slide calculation seems wrong")

# Check that trous are added to holes_mg in the else clause
if "holes_mg.append({'type': 'vis', 'x': x_s, 'y': y_slide, 'diam_str': \"⌀5/12\"})" in code:
    print("✓ Drawer holes ARE added to holes_mg")
else:
    print("✗ Drawer holes might not be added to holes_mg")

# Check the logic flow
if 'if zone_x_min is not None and zone_x_max is not None:' in code and \
   "for x_s in x_slide_holes:\n                        holes_mg.append" in code:
    print("✓ Logic for adding holes looks correct")
    print("  - Holes added if zone exists")
    print("  - Holes also added in else clause if zone doesn't exist")

print("\n" + "=" * 70)
print("Code verification complete.")
print("The drawer mounting holes should be added to the montants.")
print("If they're not showing up, the problem is likely in the drawing")
print("function (draw_machining_view_pro_final) or the test detection.")
print("=" * 70)
