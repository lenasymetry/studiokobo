#!/usr/bin/env python3
"""Regression checks for door DXF generation around cache-pied behavior."""

import io
import ezdxf
import streamlit as st

from export_manager import generate_stacked_html_plans


def _base_cabinet():
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


def _generate_doc(cab, idx=1):
    payload, ok = generate_stacked_html_plans([cab], [idx], output_format="dxf")
    if not ok:
        msg = payload.decode("utf-8", errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
        raise RuntimeError(f"DXF generation failed: {msg}")
    txt = payload.decode("utf-8", errors="ignore")
    return txt, ezdxf.read(io.StringIO(txt))


def _plan_x_bounds(plan_index, plan_width):
    x0 = (plan_index * 2600.0) + 400.0 + 600.0 - (plan_width / 2.0)
    x1 = x0 + plan_width
    return x0, x1


def _hinge_centers_y(doc, plan_index, plan_width):
    """Return sorted Y centers for hinge cups (diameter 35 => radius 17.5)."""
    x0, x1 = _plan_x_bounds(plan_index, plan_width)
    ys = []
    for entity in doc.modelspace().query("CIRCLE"):
        if abs(float(entity.dxf.radius) - 17.5) > 1e-6:
            continue
        x, y, _ = entity.dxf.center
        if (x0 - 1.0) <= x <= (x1 + 1.0):
            ys.append(round(float(y), 1))
    return sorted(set(ys))


def _hinge_centers_x(doc, plan_index, plan_width):
    """Return sorted X centers (relative to plan origin) for hinge cups."""
    x0, x1 = _plan_x_bounds(plan_index, plan_width)
    xs = []
    for entity in doc.modelspace().query("CIRCLE"):
        if abs(float(entity.dxf.radius) - 17.5) > 1e-6:
            continue
        x, _y, _ = entity.dxf.center
        if (x0 - 1.0) <= x <= (x1 + 1.0):
            xs.append(round(float(x - x0), 1))
    return sorted(set(xs))


def _assert_equal_spacings(ys):
    assert len(ys) >= 2, f"Need at least 2 hinges, got {ys}"
    diffs = [round(ys[i + 1] - ys[i], 1) for i in range(len(ys) - 1)]
    assert len(set(diffs)) == 1, f"Hinge spacings are not uniform: {diffs}"


def check_double_cachepied_variants():
    cab = _base_cabinet()
    txt, doc = _generate_doc(cab, idx=1)

    assert "Porte (C1) - Variante Gauche" in txt, "Missing left variant sheet"
    assert "Porte (C1) - Variante Droite" in txt, "Missing right variant sheet"

    dW = (cab["dims"]["L_raw"] - 2.0 * cab["door_props"]["door_gap"]) / 2.0

    # Plan order: Tb, Th, Mg, Md, F, Door-left, Door-right
    ys_left = _hinge_centers_y(doc, plan_index=5, plan_width=dW)
    ys_right = _hinge_centers_y(doc, plan_index=6, plan_width=dW)
    xs_left = _hinge_centers_x(doc, plan_index=5, plan_width=dW)
    xs_right = _hinge_centers_x(doc, plan_index=6, plan_width=dW)

    # Base default positions for dH=808 are [100, 404, 708]
    # Left variant (edge min offset +80): [180, 444, 708]
    # Right variant (edge max offset +80): [100, 364, 628]
    assert ys_left == [580.0, 844.0, 1108.0], f"Unexpected left variant hinge Y centers: {ys_left}"
    assert ys_right == [500.0, 764.0, 1028.0], f"Unexpected right variant hinge Y centers: {ys_right}"
    assert xs_left == [23.5], f"Unexpected left variant hinge X centers: {xs_left}"
    assert xs_right == [23.5], f"Unexpected right variant hinge X centers: {xs_right}"

    _assert_equal_spacings(ys_left)
    _assert_equal_spacings(ys_right)


def check_other_cases_unchanged():
    # Case 1: single without cache-pied => one door sheet, no variants
    cab1 = _base_cabinet()
    cab1["door_props"]["door_type"] = "single"
    cab1["door_props"]["door_model"] = "standard"
    txt1, _ = _generate_doc(cab1, idx=2)
    assert "Porte (C2) - Variante" not in txt1
    assert "Porte (C2)" in txt1

    # Case 2: single with cache-pied => one door sheet, no variants
    cab2 = _base_cabinet()
    cab2["door_props"]["door_type"] = "single"
    cab2["door_props"]["door_model"] = "floor_length"
    txt2, _ = _generate_doc(cab2, idx=3)
    assert "Porte (C3) - Variante" not in txt2
    assert "Porte (C3)" in txt2

    # Case 3: double without cache-pied => one grouped sheet, quantity=2 (no variants)
    cab3 = _base_cabinet()
    cab3["door_props"]["door_type"] = "double"
    cab3["door_props"]["door_model"] = "standard"
    txt3, _ = _generate_doc(cab3, idx=4)
    assert "Porte (C4) - Variante" not in txt3
    assert "Porte (C4)" in txt3


def main():
    st.session_state.project_name = "VERIFY"
    st.session_state.unit_select = "mm"
    st.session_state.foot_height = 100.0

    check_double_cachepied_variants()
    check_other_cases_unchanged()

    print("OK: door DXF checks passed (double cache-pied variants + non-regressions)")


if __name__ == "__main__":
    main()
