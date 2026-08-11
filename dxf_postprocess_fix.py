#!/usr/bin/env python3
"""Post-process machining DXF files exported by the Streamlit app.

This script intentionally does not modify export code. It reads an existing DXF,
applies layout/dimension/cleanup fixes, then writes a corrected DXF.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import ezdxf


Point = Tuple[float, float]


@dataclass
class BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def expand(self, amount: float) -> "BBox":
        return BBox(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    def center(self) -> Point:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)


def _vec2(v) -> Point:
    return (float(v[0]), float(v[1]))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _line_len(p1: Point, p2: Point) -> float:
    return _distance(p1, p2)


def _is_hv(p1: Point, p2: Point, tol: float = 1e-3) -> bool:
    return abs(p1[0] - p2[0]) <= tol or abs(p1[1] - p2[1]) <= tol


def _polyline_points_2d(ent) -> List[Point]:
    pts: List[Point] = []
    if ent.dxftype() == "LWPOLYLINE":
        for p in ent.get_points("xy"):
            pts.append((float(p[0]), float(p[1])))
    elif ent.dxftype() == "POLYLINE":
        for v in ent.vertices:
            pts.append((float(v.dxf.location.x), float(v.dxf.location.y)))
    return pts


def _bbox_from_points(points: Sequence[Point]) -> Optional[BBox]:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def _is_rectangle_like(points: Sequence[Point], tol: float = 1e-2) -> bool:
    if len(points) < 4:
        return False
    pts = list(points)
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) != 4:
        return False
    # Axis-aligned rectangle check: each edge is horizontal or vertical.
    for i in range(4):
        p1 = pts[i]
        p2 = pts[(i + 1) % 4]
        if not (abs(p1[0] - p2[0]) <= tol or abs(p1[1] - p2[1]) <= tol):
            return False
    return True


def _collect_panel_boxes(msp) -> List[BBox]:
    boxes: List[BBox] = []
    for ent in msp.query("LWPOLYLINE[layer=='PANNEAU']"):
        pts = _polyline_points_2d(ent)
        if not _is_rectangle_like(pts):
            continue
        b = _bbox_from_points(pts)
        if b:
            boxes.append(b)
    return boxes


def _sanitize_viewports(doc) -> int:
    """Unlock paper-space viewports to avoid locked model/paper navigation."""
    fixed = 0

    # Drawing defaults that help interactive behavior in AutoCAD.
    for key, value in {
        "$ORTHOMODE": 0,
        "$DIMASSOC": 2,
    }.items():
        try:
            doc.header[key] = value
        except Exception:
            pass

    # Unlock all viewport entities in all paper layouts.
    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        try:
            for vp in layout.query("VIEWPORT"):
                if hasattr(vp.dxf, "flags"):
                    # Bit 16384 = locked viewport.
                    vp.dxf.flags = int(vp.dxf.flags) & (~16384)
                    fixed += 1
                if hasattr(vp.dxf, "status") and int(vp.dxf.status) < 1:
                    vp.dxf.status = 1
        except Exception:
            continue

    return fixed


def _remove_outside_short_artifacts(msp, panel_boxes: Sequence[BBox]) -> int:
    """Remove small isolated line artifacts outside sheet extents."""
    if not panel_boxes:
        return 0

    # Keep a wide margin because dimensions/cartouche exist around the panel.
    extended = [b.expand(950.0) for b in panel_boxes]
    removed = 0

    def is_in_any_sheet(p: Point) -> bool:
        return any(b.contains(p[0], p[1]) for b in extended)

    to_delete = []
    for ent in msp:
        et = ent.dxftype()
        layer = str(getattr(ent.dxf, "layer", ""))

        if et == "LINE":
            p1 = _vec2(ent.dxf.start)
            p2 = _vec2(ent.dxf.end)
            length = _line_len(p1, p2)
            # Heuristic for tiny residue traits.
            if length <= 35.0 and _is_hv(p1, p2):
                both_outside = (not is_in_any_sheet(p1)) and (not is_in_any_sheet(p2))
                likely_noise_layer = layer in {"REPERAGE", "LEGENDE"}
                if both_outside or likely_noise_layer:
                    to_delete.append(ent)

        elif et == "LWPOLYLINE":
            pts = _polyline_points_2d(ent)
            if len(pts) == 2:
                p1, p2 = pts
                length = _line_len(p1, p2)
                if length <= 35.0 and _is_hv(p1, p2):
                    both_outside = (not is_in_any_sheet(p1)) and (not is_in_any_sheet(p2))
                    likely_noise_layer = layer in {"REPERAGE", "LEGENDE"}
                    if both_outside or likely_noise_layer:
                        to_delete.append(ent)

    for ent in to_delete:
        try:
            msp.delete_entity(ent)
            removed += 1
        except Exception:
            pass
    return removed


def _normalize_dimstyle(doc) -> int:
    changed = 0
    for ds in doc.dimstyles:
        try:
            ds.dxf.dimtix = 0
            ds.dxf.dimtad = 1
            ds.dxf.dimgap = 2.0
            ds.dxf.dimzin = 8
            changed += 1
        except Exception:
            continue
    return changed


def _closest_panel(panel_boxes: Sequence[BBox], p: Point) -> Optional[BBox]:
    if not panel_boxes:
        return None
    best = None
    best_d = 1e18
    for b in panel_boxes:
        cx, cy = b.center()
        d = math.hypot(cx - p[0], cy - p[1])
        if d < best_d:
            best_d = d
            best = b
    return best


def _move_dim_base_outside(msp, panel_boxes: Sequence[BBox]) -> int:
    """Push dimension lines outside panel area when base point is inside."""
    fixed = 0
    for dim in msp.query("DIMENSION"):
        try:
            layer = str(getattr(dim.dxf, "layer", ""))
            if layer not in {"COTES", "COTES_INTER"}:
                continue

            if not hasattr(dim.dxf, "defpoint") or not hasattr(dim.dxf, "defpoint2") or not hasattr(dim.dxf, "defpoint3"):
                continue

            base = _vec2(dim.dxf.defpoint)
            p1 = _vec2(dim.dxf.defpoint2)
            p2 = _vec2(dim.dxf.defpoint3)

            panel = _closest_panel(panel_boxes, base)
            if panel is None:
                continue

            if not panel.contains(base[0], base[1]):
                continue

            # Determine orientation from extension points.
            dx = abs(p2[0] - p1[0])
            dy = abs(p2[1] - p1[1])

            # Larger offset for thickness-like dims (short p1-p2 distance).
            span = _line_len(p1, p2)
            outside_gap = 65.0 if span <= 80.0 else 45.0

            new_base = list(base)
            if dx >= dy:
                # Horizontal dimension: move base below/above panel.
                dist_to_bottom = abs(base[1] - panel.min_y)
                dist_to_top = abs(panel.max_y - base[1])
                if dist_to_bottom <= dist_to_top:
                    new_base[1] = panel.min_y - outside_gap
                else:
                    new_base[1] = panel.max_y + outside_gap
            else:
                # Vertical dimension: move base left/right of panel.
                dist_to_left = abs(base[0] - panel.min_x)
                dist_to_right = abs(panel.max_x - base[0])
                if dist_to_left <= dist_to_right:
                    new_base[0] = panel.min_x - outside_gap
                else:
                    new_base[0] = panel.max_x + outside_gap

            dim.dxf.defpoint = (new_base[0], new_base[1], 0.0)
            fixed += 1
        except Exception:
            continue
    return fixed


def _stagger_overlapping_dim_texts(msp) -> int:
    """De-overlap dimension texts by offsetting close labels on same baseline."""
    dims = []
    for dim in msp.query("DIMENSION"):
        try:
            layer = str(getattr(dim.dxf, "layer", ""))
            if layer not in {"COTES", "COTES_INTER"}:
                continue
            if not hasattr(dim.dxf, "defpoint2") or not hasattr(dim.dxf, "defpoint3"):
                continue
            p1 = _vec2(dim.dxf.defpoint2)
            p2 = _vec2(dim.dxf.defpoint3)
            base = _vec2(dim.dxf.defpoint) if hasattr(dim.dxf, "defpoint") else ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            horizontal = abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1])
            key = ("H" if horizontal else "V", round(base[1] if horizontal else base[0], 1))
            dims.append((key, dim, p1, p2, horizontal))
        except Exception:
            continue

    groups = {}
    for item in dims:
        groups.setdefault(item[0], []).append(item)

    moved = 0
    for _, items in groups.items():
        if len(items) < 2:
            continue
        # Sort along measurement axis.
        items.sort(key=lambda it: (it[2][0] + it[3][0]) / 2.0 if it[4] else (it[2][1] + it[3][1]) / 2.0)

        last_axis = None
        rank = 0
        for _, dim, p1, p2, horizontal in items:
            axis_mid = (p1[0] + p2[0]) / 2.0 if horizontal else (p1[1] + p2[1]) / 2.0
            if last_axis is None or abs(axis_mid - last_axis) > 26.0:
                rank = 0
            else:
                rank += 1
            last_axis = axis_mid

            if rank == 0:
                continue

            sign = -1 if (rank % 2 == 0) else 1
            delta = 7.0 + 5.0 * (rank // 2)
            text_mid = _vec2(dim.dxf.text_midpoint) if hasattr(dim.dxf, "text_midpoint") else ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            if horizontal:
                dim.dxf.text_midpoint = (text_mid[0], text_mid[1] + sign * delta, 0.0)
            else:
                dim.dxf.text_midpoint = (text_mid[0] + sign * delta, text_mid[1], 0.0)
            moved += 1

    return moved


def _remove_empty_texts_and_proxies(msp) -> int:
    removed = 0
    to_delete = []
    for ent in msp:
        et = ent.dxftype()
        if et in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
            try:
                txt = ent.text if et == "MTEXT" else ent.dxf.text
            except Exception:
                txt = ""
            if not str(txt).strip():
                to_delete.append(ent)
        if et in {"ACAD_PROXY_ENTITY", "PROXY_ENTITY"}:
            to_delete.append(ent)

    for ent in to_delete:
        try:
            msp.delete_entity(ent)
            removed += 1
        except Exception:
            pass
    return removed


def _fix_file(input_path: Path, output_path: Path) -> None:
    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()

    panel_boxes = _collect_panel_boxes(msp)

    stats = {
        "viewport_unlocked": _sanitize_viewports(doc),
        "dimstyles_normalized": _normalize_dimstyle(doc),
        "dims_moved_outside": _move_dim_base_outside(msp, panel_boxes),
        "dim_texts_staggered": _stagger_overlapping_dim_texts(msp),
        "short_artifacts_removed": _remove_outside_short_artifacts(msp, panel_boxes),
        "empty_entities_removed": _remove_empty_texts_and_proxies(msp),
    }

    # Friendly defaults to help object snaps and free navigation.
    try:
        doc.header["$SNAPMODE"] = 0
    except Exception:
        pass
    try:
        doc.header["$OSMODE"] = 39
    except Exception:
        pass

    doc.saveas(str(output_path))

    print("DXF post-process complete")
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")
    for key, val in stats.items():
        print(f"- {key}: {val}")


def _build_output_path(input_path: Path, out: Optional[str]) -> Path:
    if out:
        return Path(out).expanduser().resolve()
    stem = input_path.stem + "-fixed"
    return input_path.with_name(stem + input_path.suffix)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fix layout/dimension/noise issues in machining DXF exports."
    )
    parser.add_argument("input", help="Path to source DXF")
    parser.add_argument("-o", "--output", help="Path to output DXF (default: <input>-fixed.dxf)")
    args = parser.parse_args(argv)

    src = Path(args.input).expanduser().resolve()
    if not src.exists() or src.suffix.lower() != ".dxf":
        print(f"Invalid DXF input: {src}")
        return 2

    out = _build_output_path(src, args.output)
    _fix_file(src, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
