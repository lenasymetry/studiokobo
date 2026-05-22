#!/usr/bin/env python3
"""Regression check for DXF export of double doors with cache-pied.

Expected behavior:
- two distinct sheets are generated (left/right variants)
- outer hinge-side offsets are existing offsets + 80 mm
"""

import io
import streamlit as st
import ezdxf

from export_manager import generate_stacked_html_plans


def build_test_cabinet():
    return {
        "dims": {
            "L_raw": 1000.0,
            "W_raw": 560.0,
            "H_raw": 720.0,
            "t_lr_raw": 19.0,
            "t_fb_raw": 3.0,
            "t_tb_raw": 19.0,
        },
        "shelves": [],
        "drawers": [],
        "vertical_dividers": [],
        "base_elements": {
            "has_back_panel": True,
            "has_left_upright": True,
            "has_right_upright": True,
            "has_bottom_traverse": True,
            "has_top_traverse": True,
        },
        "door_props": {
            "has_door": True,
            "door_type": "double",
            "door_opening": "right",
            "door_thickness": 19.0,
            "door_gap": 2.0,
            "door_model": "floor_length",
            "hinge_mode": "default",
            "custom_hinge_positions": [],
        },
    }


def local_x_positions_for_plan(doc, plan_index, door_width, y_min=399.0, y_max=1210.0):
    x0 = (plan_index * 2600.0) + 400.0 + 600.0 - (door_width / 2.0)
    x1 = x0 + door_width
    vals = []
    for entity in doc.modelspace().query("CIRCLE"):
        x, y, _ = entity.dxf.center
        if (x0 - 1.0) <= x <= (x1 + 1.0) and y_min <= y <= y_max:
            vals.append(round(x - x0, 1))
    return sorted(set(vals))


def main():
    st.session_state.project_name = "VERIFY"
    st.session_state.unit_select = "mm"
    st.session_state.foot_height = 100.0

    payload, ok = generate_stacked_html_plans([build_test_cabinet()], [1], output_format="dxf")
    if not ok:
        msg = payload.decode("utf-8", errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
        raise RuntimeError(f"DXF generation failed: {msg}")

    txt = payload.decode("utf-8", errors="ignore")
    assert "Porte (C1) - Variante Gauche" in txt, "Missing left variant sheet title"
    assert "Porte (C1) - Variante Droite" in txt, "Missing right variant sheet title"

    doc = ezdxf.read(io.StringIO(txt))
    door_width = (1000.0 - (2 * 2.0)) / 2.0

    # Plan order in export: Tb, Th, Mg, Md, F, Door-left, Door-right
    left_local_x = local_x_positions_for_plan(doc, plan_index=5, door_width=door_width)
    right_local_x = local_x_positions_for_plan(doc, plan_index=6, door_width=door_width)

    assert left_local_x == [103.5, 113.0], f"Unexpected left-variant offsets: {left_local_x}"
    assert right_local_x == [385.0, 394.5], f"Unexpected right-variant offsets: {right_local_x}"

    print("OK: double-door cache-pied DXF variants and offsets are correct")


if __name__ == "__main__":
    main()
