#!/usr/bin/env python3
"""Rebuild a minimal, cleaner set of DXF dimensions from the sheet geometry.

Rules:
- Keep the main width/height dimensions for each panel.
- Remove the existing spacing dimensions and rebuild cleaner aligned chains
  for vis and tourillon hole families from hole-center geometry.
- Push traverse thickness dimensions outside the panel area when possible.
- Never modify construction geometry: no LINE/LWPOLYLINE/CIRCLE edits.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import ezdxf


Point = Tuple[float, float]


@dataclass
class PanelBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def contains(self, p: Point, margin: float = 0.0) -> bool:
        return (
            self.min_x - margin <= p[0] <= self.max_x + margin
            and self.min_y - margin <= p[1] <= self.max_y + margin
        )


@dataclass
class Hole:
    center: Point
    radius: float
    panel_idx: int
    family: str  # VIS / TOURILLON / OTHER


def _merge_unique_boxes(boxes: Iterable[PanelBox]) -> List[PanelBox]:
    out: List[PanelBox] = []
    seen = set()
    for box in sorted(boxes, key=lambda b: (b.min_x, b.min_y, b.max_x, b.max_y)):
        key = (_r(box.min_x), _r(box.min_y), _r(box.max_x), _r(box.max_y))
        if key in seen:
            continue
        seen.add(key)
        out.append(box)
    return out


def _panel_boxes_from_main_dims(msp) -> List[PanelBox]:
    h_dims = []
    v_dims = []
    for dim in msp.query("DIMENSION"):
        pts = _dim_points(dim)
        if not pts:
            continue
        _, p1, p2 = pts
        if abs(p1[1] - p2[1]) <= 1e-3:
            x1, x2 = sorted((p1[0], p2[0]))
            span = x2 - x1
            if span >= 350.0:
                h_dims.append((x1, x2, p1[1]))
        elif abs(p1[0] - p2[0]) <= 1e-3:
            y1, y2 = sorted((p1[1], p2[1]))
            span = y2 - y1
            if span >= 220.0:
                v_dims.append((p1[0], y1, y2))

    inferred: List[PanelBox] = []
    tol = 35.0
    for x1, x2, hy in h_dims:
        for vx, y1, y2 in v_dims:
            x_edge_match = abs(vx - x1) <= tol or abs(vx - x2) <= tol
            y_edge_match = abs(hy - y1) <= tol or abs(hy - y2) <= tol
            if not (x_edge_match and y_edge_match):
                continue
            inferred.append(PanelBox(x1, y1, x2, y2))

    return _merge_unique_boxes(inferred)


def _r(v: float, nd: int = 1) -> float:
    return round(float(v), nd)


def _pt(v) -> Point:
    return (float(v.x), float(v.y))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _panel_boxes(msp) -> List[PanelBox]:
    boxes: List[PanelBox] = []

    # Preferred path: explicit panel layer.
    for pl in msp.query("LWPOLYLINE[layer=='PANNEAU']"):
        try:
            pts = []
            for p in pl.get_points("xy"):
                pts.append((float(p[0]), float(p[1])))
        except Exception:
            continue
        if len(pts) < 4:
            continue
        pts4 = pts[:-1] if pts and pts[0] == pts[-1] else pts
        if len(pts4) != 4:
            continue
        xs = [p[0] for p in pts4]
        ys = [p[1] for p in pts4]
        boxes.append(PanelBox(min(xs), min(ys), max(xs), max(ys)))

    # Also infer panels from main dimensions; converted DXF often lacks clean panel layer.
    inferred = _panel_boxes_from_main_dims(msp)
    if boxes:
        return _merge_unique_boxes([*boxes, *inferred])

    # Fallback: detect large axis-aligned rectangles in generic converted DXF.
    candidates: List[PanelBox] = []
    for pl in msp.query("LWPOLYLINE"):
        try:
            pts = []
            for p in pl.get_points("xy"):
                pts.append((float(p[0]), float(p[1])))
        except Exception:
            continue
        if len(pts) < 4:
            continue
        pts4 = pts[:-1] if pts and pts[0] == pts[-1] else pts
        if len(pts4) != 4:
            continue

        ok = True
        for i in range(4):
            x1, y1 = pts4[i]
            x2, y2 = pts4[(i + 1) % 4]
            if not (abs(x1 - x2) <= 1e-3 or abs(y1 - y2) <= 1e-3):
                ok = False
                break
        if not ok:
            continue

        xs = [p[0] for p in pts4]
        ys = [p[1] for p in pts4]
        box = PanelBox(min(xs), min(ys), max(xs), max(ys))
        if box.width >= 350.0 and box.height >= 220.0:
            candidates.append(box)

    boxes = _merge_unique_boxes(candidates)
    return _merge_unique_boxes([*boxes, *inferred])


def _closest_panel_idx(point: Point, panels: Sequence[PanelBox]) -> int:
    if not panels:
        return -1
    best_idx = 0
    best_d2 = float("inf")
    for i, box in enumerate(panels):
        cx = (box.min_x + box.max_x) * 0.5
        cy = (box.min_y + box.max_y) * 0.5
        d2 = (point[0] - cx) ** 2 + (point[1] - cy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_idx = i
    return best_idx


def _panel_idx_for_point(point: Point, panels: Sequence[PanelBox], margin: float = 220.0) -> int:
    # Strict ownership: only points close to a panel belong to that panel.
    candidates: List[Tuple[int, float]] = []
    for i, box in enumerate(panels):
        if box.contains(point, margin=margin):
            cx = (box.min_x + box.max_x) * 0.5
            cy = (box.min_y + box.max_y) * 0.5
            d2 = (point[0] - cx) ** 2 + (point[1] - cy) ** 2
            candidates.append((i, d2))
    if not candidates:
        return -1
    candidates.sort(key=lambda t: t[1])
    return candidates[0][0]


def _dim_points(dim) -> Optional[Tuple[Point, Point, Point]]:
    try:
        b = _pt(dim.dxf.defpoint)
        p1 = _pt(dim.dxf.defpoint2)
        p2 = _pt(dim.dxf.defpoint3)
        return b, p1, p2
    except Exception:
        return None


def _dim_orientation(p1: Point, p2: Point) -> Optional[str]:
    if abs(p1[1] - p2[1]) <= 1e-3:
        return "H"
    if abs(p1[0] - p2[0]) <= 1e-3:
        return "V"
    return None


def _collect_holes(msp, panels: Sequence[PanelBox]) -> List[Hole]:
    holes: List[Hole] = []
    for c in msp.query("CIRCLE"):
        try:
            center = _pt(c.dxf.center)
            radius = float(c.dxf.radius)
            panel_idx = _panel_idx_for_point(center, panels, margin=220.0)
            family = "OTHER"
            # 3mm-ish circles are vis, larger 4mm+ circles are tourillons.
            if radius <= 2.3:
                family = "VIS"
            elif radius >= 3.8:
                family = "TOURILLON"
            holes.append(Hole(center=center, radius=radius, panel_idx=panel_idx, family=family))
        except Exception:
            continue
    return holes


def _nearest_hole(point: Point, holes: Sequence[Hole], panel_idx: int, tol: float = 6.0) -> Optional[Hole]:
    best: Optional[Hole] = None
    best_d = tol
    for hole in holes:
        if hole.panel_idx != panel_idx:
            continue
        d = _distance(point, hole.center)
        if d <= best_d:
            best = hole
            best_d = d
    return best


def _add_linear_dim(msp, base: Point, p1: Point, p2: Point, angle: float, layer: str, text_override: Optional[str] = None):
    dim = msp.add_linear_dim(
        base=base,
        p1=p1,
        p2=p2,
        angle=angle,
        dimstyle="COTATIONS_PRO",
        dxfattribs={"layer": layer, "color": 1},
    )
    if text_override:
        try:
            dim.text = text_override
        except Exception:
            pass
    try:
        dim.dimtad = 1
        dim.dimgap = 2.0
        dim.dimtix = 0
        dim.dimdli = 3.75
    except Exception:
        pass
    dim.render()
    return dim


def _safe_panel_margin_dim_base(panel: PanelBox, orientation: str, side: str) -> Point:
    # Place dimensions away from the panel body.
    if orientation == "H":
        y = panel.min_y - 60.0 if side == "bottom" else panel.max_y + 60.0
        x = panel.min_x
        return (x, y)
    x = panel.min_x - 60.0 if side == "left" else panel.max_x + 60.0
    y = panel.min_y
    return (x, y)


def _hole_group_key(hole: Hole, axis: str) -> Tuple[int, str, float]:
    # Group by panel and aligned row/column.
    if axis == "H":
        return (hole.panel_idx, hole.family, _r(hole.center[1]))
    return (hole.panel_idx, hole.family, _r(hole.center[0]))


def _build_hole_dimensions(msp, panels: Sequence[PanelBox], holes: Sequence[Hole]) -> int:
    created = 0

    def split_x_clusters(xs: List[float], max_gap: float = 160.0) -> List[List[float]]:
        if not xs:
            return []
        clusters: List[List[float]] = [[xs[0]]]
        for x in xs[1:]:
            if (x - clusters[-1][-1]) > max_gap:
                clusters.append([x])
            else:
                clusters[-1].append(x)
        return clusters

    by_panel: Dict[int, List[Hole]] = {}
    for h in holes:
        if h.panel_idx < 0:
            continue
        by_panel.setdefault(h.panel_idx, []).append(h)

    for panel_idx, items in by_panel.items():
        panel = panels[panel_idx]
        # Group aligned rows per family.
        horizontal_groups: Dict[Tuple[str, float], List[Hole]] = {}

        for h in items:
            horizontal_groups.setdefault(_hole_group_key(h, "H"), []).append(h)

        # Keep only one representative row per family and panel (max aligned centers).
        best_row_by_family: Dict[str, Tuple[float, List[Hole]]] = {}
        panel_center_y = (panel.min_y + panel.max_y) * 0.5
        for (_panel_idx_key, family, ry), group in sorted(horizontal_groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
            if family not in {"VIS", "TOURILLON"}:
                continue
            if len(group) < 2:
                continue
            unique_x_count = len({_r(h.center[0]) for h in group})
            if unique_x_count < 2:
                continue
            xs_for_row = sorted({_r(h.center[0]) for h in group})
            span = xs_for_row[-1] - xs_for_row[0]
            # Guardrail: row chain must fit this panel and not bridge another sheet.
            if span > (panel.width + 260.0):
                continue
            current = best_row_by_family.get(family)
            if current is None:
                best_row_by_family[family] = (ry, group)
            else:
                cur_xs = {_r(h.center[0]) for h in current[1]}
                cur_n = len(cur_xs)
                if unique_x_count > cur_n:
                    best_row_by_family[family] = (ry, group)
                elif unique_x_count == cur_n:
                    # Tie-break: pick row closest to panel center for readability.
                    if abs(ry - panel_center_y) < abs(current[0] - panel_center_y):
                        best_row_by_family[family] = (ry, group)

        # Draw exactly one chain line for VIS and one for TOURILLON when available.
        for family in ("VIS", "TOURILLON"):
            if family not in best_row_by_family:
                continue
            ry, group = best_row_by_family[family]
            xs = sorted({_r(h.center[0]) for h in group})
            # Rule: only dimension holes belonging to the same local line/group.
            # This prevents dimensions between two distinct edge tranches.
            x_clusters = [c for c in split_x_clusters(xs, max_gap=160.0) if len(c) >= 2]
            if not x_clusters:
                continue

            y = ry
            base_y = panel.min_y - 78.0 if family == "VIS" else panel.max_y + 78.0
            lane_shift = 0.0
            for cluster in x_clusters:
                # Small shift per cluster to avoid text overlap when multiple local groups exist.
                for i in range(len(cluster) - 1):
                    x1 = cluster[i]
                    x2 = cluster[i + 1]
                    _add_linear_dim(
                        msp,
                        base=(x1, base_y + lane_shift),
                        p1=(x1, y),
                        p2=(x2, y),
                        angle=0,
                        layer="DIM",
                    )
                    created += 1
                lane_shift += 16.0

    return created


def _keep_or_move_main_panel_dims(msp, panels: Sequence[PanelBox]) -> Tuple[int, int]:
    kept = 0
    moved = 0
    for dim in msp.query("DIMENSION"):
        info = _dim_points(dim)
        if not info:
            continue
        base, p1, p2 = info
        ori = _dim_orientation(p1, p2)
        if ori is None:
            continue

        panel_idx = _closest_panel_idx(((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5), panels)
        if panel_idx < 0:
            continue
        panel = panels[panel_idx]
        span = abs(p2[0] - p1[0]) if ori == "H" else abs(p2[1] - p1[1])

        # Main panel width/height dimensions are preserved.
        is_main_width = ori == "H" and abs(span - panel.width) <= 2.0
        is_main_height = ori == "V" and abs(span - panel.height) <= 2.0

        if is_main_width or is_main_height:
            kept += 1
            continue

        # Traverse thickness-like dimensions should stay outside the panel.
        if ori == "H" and panel.min_y <= base[1] <= panel.max_y:
            try:
                dim.dxf.defpoint = (base[0], panel.min_y - 55.0, 0.0)
                moved += 1
            except Exception:
                pass
        elif ori == "V" and panel.min_x <= base[0] <= panel.max_x:
            try:
                dim.dxf.defpoint = (panel.max_x + 55.0, base[1], 0.0)
                moved += 1
            except Exception:
                pass

    return kept, moved


def _deduplicate_existing_dimensions(msp, panels: Sequence[PanelBox], holes: Sequence[Hole]) -> int:
    # Remove spacing dimensions but keep main and thickness dimensions.
    removed = 0
    to_delete = []
    for dim in msp.query("DIMENSION"):
        info = _dim_points(dim)
        if not info:
            continue
        _, p1, p2 = info
        ori = _dim_orientation(p1, p2)
        if ori is None:
            continue
        # Keep main panel dimensions.
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        panel_idx = _panel_idx_for_point(mid, panels, margin=260.0)
        if panel_idx < 0:
            # Keep unknown context dimensions to avoid accidental data loss.
            continue
        panel = panels[panel_idx]
        span = abs(p2[0] - p1[0]) if ori == "H" else abs(p2[1] - p1[1])
        if (ori == "H" and abs(span - panel.width) <= 2.0) or (ori == "V" and abs(span - panel.height) <= 2.0):
            continue

        # Keep traverse/small-thickness dimensions.
        if span <= 40.0:
            continue

        # Remove only hole-to-hole spacing dimensions for vis/tourillon.
        h1 = _nearest_hole(p1, holes, panel_idx, tol=22.0)
        h2 = _nearest_hole(p2, holes, panel_idx, tol=22.0)
        if h1 is not None and h2 is not None and h1.family in {"VIS", "TOURILLON"} and h2.family in {"VIS", "TOURILLON"}:
            to_delete.append(dim)

        # Also remove legacy intermediate spacing dimensions (typical drilling pitch range),
        # while keeping global and thickness dimensions.
        elif 80.0 <= span <= 400.0:
            to_delete.append(dim)

    for dim in to_delete:
        try:
            msp.delete_entity(dim)
            removed += 1
        except Exception:
            pass
    return removed


def _sanitise_dimstyle(doc) -> None:
    try:
        ds = doc.dimstyles.get("COTATIONS_PRO")
    except Exception:
        return
    try:
        ds.dxf.dimtix = 0
        ds.dxf.dimtad = 1
        ds.dxf.dimgap = 2.0
        ds.dxf.dimdli = 3.75
    except Exception:
        pass


def clean_dxf(input_path: Path, output_path: Path) -> Dict[str, int]:
    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()
    panels = _panel_boxes(msp)
    holes = _collect_holes(msp, panels)

    _sanitise_dimstyle(doc)

    removed_existing = _deduplicate_existing_dimensions(msp, panels, holes)
    created_hole_dims = _build_hole_dimensions(msp, panels, holes)
    kept_main, moved_thickness = _keep_or_move_main_panel_dims(msp, panels)

    # Lightweight AutoCAD friendliness.
    for key, value in (("$DIMASSOC", 2), ("$ORTHOMODE", 0), ("$SNAPMODE", 0), ("$OSMODE", 39)):
        try:
            doc.header[key] = value
        except Exception:
            pass

    doc.saveas(str(output_path))
    return {
        "panels": len(panels),
        "holes": len(holes),
        "removed_existing_dims": removed_existing,
        "created_hole_dims": created_hole_dims,
        "kept_main_dims": kept_main,
        "moved_thickness_dims": moved_thickness,
        "final_dim_count": len(list(msp.query("DIMENSION"))),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild a minimal clean dimension set in a DXF")
    parser.add_argument("input", type=Path, help="Input DXF path")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output DXF path")
    args = parser.parse_args(argv)

    src = args.input
    if not src.exists() or src.suffix.lower() != ".dxf":
        print(f"[ERROR] Invalid DXF input: {src}")
        return 2

    dst = args.output or src.with_name(f"{src.stem}_clean_strict{src.suffix}")
    try:
        stats = clean_dxf(src, dst)
    except Exception as exc:
        print(f"[ERROR] Failed to clean DXF: {exc}")
        return 1

    print("[OK] Strict DXF cleanup complete")
    print(f"  input:                 {src}")
    print(f"  output:                {dst}")
    print(f"  panels detected:       {stats['panels']}")
    print(f"  holes detected:        {stats['holes']}")
    print(f"  existing dims removed: {stats['removed_existing_dims']}")
    print(f"  hole dims created:     {stats['created_hole_dims']}")
    print(f"  main dims preserved:   {stats['kept_main_dims']}")
    print(f"  thickness dims moved:  {stats['moved_thickness_dims']}")
    print(f"  final dim count:       {stats['final_dim_count']}")
    print("  geometry untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
