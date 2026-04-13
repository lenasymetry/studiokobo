#!/usr/bin/env python3
"""Script to add detailed logging to trace plan generation"""

import sys
sys.path.insert(0, '/Users/lenapatarin/Documents/ANNEE1/code')

# Patch the export_manager to add logging
original_file = '/Users/lenapatarin/Documents/ANNEE1/code/export_manager.py'

with open(original_file, 'r') as f:
    content = f.read()

# Find the "for item in plans:" section and add logging
if 'for item in plans:' in content:
    print("Found 'for item in plans:' loop")
    
    # Count how many plans are referenced
    import re
    
    # Find all plans.append calls
    append_calls = re.findall(r'plans\.append\(\(([^,]+),', content)
    print(f"\nFound {len(set(append_calls))} unique plan types being added:")
    for title in sorted(set(append_calls)):
        count = sum(1 for t in append_calls if t == title)
        print(f"  - {title}: {count} times")
    
    # Show the structure of the for loop that processes plans
    print("\n" + "="*70)
    print("Plan processing logic for DXF:")
    print("="*70)
    
    # Find the for item in plans: section
    start_idx = content.find('for item in plans:')
    if start_idx > 0:
        # Get next 500 chars
        section = content[start_idx:start_idx+1500]
        print(section[:800])
        print("\n...")
        print(section[-200:])

else:
    print("Could not find 'for item in plans:' in export_manager.py")
