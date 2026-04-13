#!/usr/bin/env python3
"""
DIAGNOSTIC SCRIPT: Trace all elements being added to the plans list
This will show EXACTLY what gets exported in HTML vs DXF
"""

import sys
import re

# Read export_manager.py and find all plans.append calls
with open('/Users/lenapatarin/Documents/ANNEE1/code/export_manager.py', 'r') as f:
    content = f.read()

# Find all plans.append() calls with their context
pattern = r'plans\.append\(\(([^)]+)\)\)'
matches = re.finditer(pattern, content, re.MULTILINE | re.DOTALL)

print("=" * 90)
print("ALL ELEMENTS ADDED TO plans LIST IN export_manager.py")
print("=" * 90)

elements = []
for i, match in enumerate(matches, 1):
    # Get the matched content
    matched_tuple = match.group(1)
    
    # Extract first parameter (usually the title)
    # Split by comma but be careful with nested parentheses
    lines = matched_tuple.strip().split('\n')
    first_line = lines[0].strip()
    
    # Get line number
    line_num = content[:match.start()].count('\n') + 1
    
    elements.append({
        'num': i,
        'line': line_num,
        'title': first_line,
        'full': matched_tuple[:100] + ('...' if len(matched_tuple) > 100 else '')
    })
    
    print(f"\n{i}. Line {line_num}:")
    print(f"   Title: {first_line}")
    if len(matched_tuple) > 100:
        print(f"   Content: {matched_tuple[:100]}...")

print("\n" + "=" * 90)
print(f"TOTAL ELEMENTS IN plans.append() CALLS: {len(elements)}")
print("=" * 90)

print("\nELEMENTS BY CATEGORY:")
print("-" * 90)

categories = {}
for elem in elements:
    title = elem['title']
    # Categorize
    if 'Étagère' in title or 'Shelf' in title:
        cat = 'Shelf (Étagère)'
    elif 'Traverse' in title:
        cat = 'Traverse (Traverse Haut/Bas)'
    elif 'Montant' in title and 'Secondaire' in title:
        cat = 'Divider (Montant Secondaire)'
    elif 'Montant' in title or 'Upright' in title:
        cat = 'Upright (Montant Gauche/Droit)'
    elif 'Panneau' in title or 'Panel' in title:
        cat = 'Back Panel (Panneau Arrière)'
    elif 'Façade' in title or 'Face' in title:
        cat = 'Drawer Face (Façade Tiroir)'
    elif 'Tiroir-Dos' in title or 'Drawer-Back' in title:
        cat = 'Drawer Back (Tiroir-Dos)'
    elif 'Tiroir-Fond' in title or 'Drawer-Bottom' in title:
        cat = 'Drawer Bottom (Tiroir-Fond)'
    elif 'Porte' in title or 'Door' in title:
        cat = 'Door (Porte)'
    else:
        cat = 'Other'
    
    if cat not in categories:
        categories[cat] = []
    categories[cat].append(elem)

for cat in sorted(categories.keys()):
    print(f"\n{cat}: {len(categories[cat])} instances")
    for elem in categories[cat][:3]:  # Show first 3 of each category
        print(f"  - Line {elem['line']}: {elem['title']}")
    if len(categories[cat]) > 3:
        print(f"  ... and {len(categories[cat]) - 3} more")

print("\n" + "=" * 90)
print("CONCLUSION:")
print("=" * 90)
print("""
Each plans.append() call represents ONE element that will become:
- ONE page/layout in HTML
- ONE layout/plan in DXF

If DXF is missing elements, it means:
1) These plans.append() calls are not being reached
2) OR they are being skipped in the 'for item in plans:' loop
3) OR the conditions that add them are False for your cabinet data
""")

