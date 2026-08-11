#!/usr/bin/env python3
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


def _r(v: float, nd: int = 1) -> float:
    return round(float(v), nd)


def _pt(v) -> Point:
    return (float(v.x), float(v.y))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dim_points(dim) -> Optional[Tuple[Point, Point, Point]]:
    try:
        return _pt(dim.dxf.defpoint), _pt(dim.dxf.defpoint2), _pt(dim.dxf.defpoint3)
    except Exception:
        return None


def _dim_orientation(p1: Point, p2: Point) -> Optional[str]:
    if abs(p1[1] - p2[1]) <= 1e-3:
        return "H"
    if abs(p1[0] - p2[0]) <= 1e-3:
        return "V"
    return None


def _panel_boxes(msp) -> List[PanelBox]:
    boxes: List[PanelBox] = []
    for pl in msp.query("LWPOLYLINE"):
        try:
            pts = [(float(p[0]), float(p[1])) for p in pl.get_points("xy")]
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
        b = PanelBox(min(xs), min(ys), max(xs), max(ys))
        if b.width >= 350.0 and b.height >= 220.0:
            boxes.append(b)

    # Infer additional panel boxes from large horizontal+vertical dimensions.
    h_dims = []
    v_dims = []
    for d in msp.query("DIMENSION"):
        pts = _dim_points(d)
        if not pts:
            continue
        _, p1, p2 = pts
        ori = _dim_orientation(p1, p2)
        if ori == "H":
            x1, x2 = sorted((p1[0], p2[0]))
            if (x2 - x1) >= 350.0:
                h_dims.append((x1, x2, p1[1]))
        elif ori == "V":
            y1, y2 = sorted((p1[1], p2[1]))
            if (y2 - y1) >= 220.0:
                v_dims.append((p1[0], y1, y2))

    tol = 35.0
    for x1, x2, hy in h_dims:
        for vx, y1, y2 in v_dims:
            x_match = abs(vx - x1) <= tol or abs(vx - x2) <= tol
            y_match = abs(hy - y1) <= tol or abs(hy - y2) <= tol
            if x_match and y_match:
                boxes.append(PanelBox(x1, y1, x2, y2))

    # merge unique
    out: List[PanelBox] = []
    seen = set()
    for b in sorted(boxes, key=lambda z: (z.min_x, z.min_y, z.max_x, z.max_y)):
        k = (_r(b.min_x), _r(b.min_y), _r(b.max_x), _r(b.max_y))
        if k in seen:
            continue
        seen.add(k)
        out.append(b)
    return out


def _panel_idx_for_point(point: Point, panels: Sequence[PanelBox], margin: float = 220.0) -> int:
    candidates: List[Tuple[int, float]] = []
    for i, b in enumerate(panels):
        if not b.contains(point, margin=margin):
            continue
        cx = (b.min_x + b.max_x) * 0.5
        cy = (b.min_y + b.max_y) * 0.5
        d2 = (point[0] - cx) ** 2 + (point[1] - cy) ** 2
        candidates.append((i, d2))
    if not candidates:
        return -1
    candidates.sort(key=lambda t: t[1])
    return candidates[0][0]


def _collect_holes(msp, panels: Sequence[PanelBox]) -> List[Hole]:
    holes: List[Hole] = []
    for c in msp.query("CIRCLE"):
        try:
            ctr = _pt(c.dxf.center)
            r = float(c.dxf.radius)
        except Exception:
            continue
        idx = _panel_idx_for_point(ctr, panels, margin=240.0)
        if idx < 0:
            continue
        holes.append(Hole(center=ctr, radius=r, panel_idx=idx))
    return holes


def _family(h: Hole) -> str:
    if h.radius >= 16.0:
        return "CUP"
    if h.radius <= 2.3:
        return "VIS"
    if h.radius >= 3.8:
        return "TOURILLON"
    return "OTHER"


def _add_dim(msp, base: Point, p1: Point, p2: Point, angle: float, layer: str = "DIM") -> None:
    dim = msp.add_linear_dim(
        base=base,
        p1=p1,
        p2=p2,
        angle=angle,
        dimstyle="COTATIONS_PRO",
        dxfattribs={"layer": layer, "color": 1},
    )
    try:
        dim.dimtad = 1
        dim.dimgap = 2.0
        dim.dimtix = 0
    except Exception:
        pass
    dim.render()


def _classify_panel(panel: PanelBox, holes: Sequence[Hole]) -> str:
    fams = [_family(h) for h in holes]
    if any(f == "CUP" for f in fams):
        return "DOOR"
    tour_out = [h for h in holes if _family(h) == "TOURILLON" and (h.center[0] < panel.min_x - 5 or h.center[0] > panel.max_x + 5)]
    if panel.width >= panel.height and len(tour_out) >= 4:
        return "TRAVERSE_OR_FIXED_SHELF"
    vis = [h for h in holes if _family(h) == "VIS"]
    tour = [h for h in holes if _family(h) == "TOURILLON"]
    if vis and tour:
        return "MONTANT_WITH_FIXED_SHELVES"
    return "OTHER"


def _remove_duplicate_25_right(msp, panel: PanelBox, panel_idx: int) -> int:
    cand = []
    for d in msp.query("DIMENSION"):
        pts = _dim_points(d)
        if not pts:
            continue
        base, p1, p2 = pts
        if _panel_idx_for_point(((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5), [panel], margin=260.0) != 0:
            continue
        if _dim_orientation(p1, p2) != "V":
            continue
        span = abs(p2[1] - p1[1])
        if not (24.0 <= span <= 26.0):
            continue
        x = p1[0]
        if x <= panel.max_x + 8:
            continue
        cand.append((d, x, min(p1[1], p2[1]), max(p1[1], p2[1])))

    # Keep one representative per close X column.
    removed = 0
    by_x: Dict[float, List[Tuple]] = {}
    for item in cand:
        key = _r(item[1], 0)
        by_x.setdefault(key, []).append(item)
    for _, items in by_x.items():
        items.sort(key=lambda t: (t[2], t[3]))
        for extra in items[1:]:
            try:
                msp.delete_entity(extra[0])
                removed += 1
            except Exception:
                pass
    return removed


def _move_thickness_outside(msp, panel: PanelBox) -> int:
    moved = 0
    for d in msp.query("DIMENSION"):
        pts = _dim_points(d)
        if not pts:
            continue
        base, p1, p2 = pts
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if not panel.contains(mid, margin=260.0):
            continue
        ori = _dim_orientation(p1, p2)
        if ori is None:
            continue
        span = abs(p2[0] - p1[0]) if ori == "H" else abs(p2[1] - p1[1])
        if not (18.0 <= span <= 30.5):
            continue
        if ori == "H" and panel.min_y <= base[1] <= panel.max_y:
            try:
                d.dxf.defpoint = (base[0], panel.min_y - 55.0, 0.0)
                moved += 1
            except Exception:
                pass
        elif ori == "V" and panel.min_x <= base[0] <= panel.max_x:
            try:
                d.dxf.defpoint = (panel.max_x + 55.0, base[1], 0.0)
                moved += 1
            except Exception:
                pass
    return moved


def _remove_small_border_ticks(msp, panel: PanelBox) -> int:
    removed = 0
    to_delete = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        try:
            c = int(getattr(e.dxf, "color", 256))
            if c not in {1, 7, 256}:
                continue
            s = _pt(e.dxf.start)
            t = _pt(e.dxf.end)
            ln = _distance(s, t)
            if ln > 20.0:
                continue
            mx = (s[0] + t[0]) * 0.5
            my = (s[1] + t[1]) * 0.5
            # near contour
            near_border = (
                abs(mx - panel.min_x) <= 3.0 or abs(mx - panel.max_x) <= 3.0 or
                abs(my - panel.min_y) <= 3.0 or abs(my - panel.max_y) <= 3.0
            )
            if not near_border:
                continue
            # mostly perpendicular to border direction
            dx = abs(t[0] - s[0])
            dy = abs(t[1] - s[1])
            if not (dx <= 1e-3 or dy <= 1e-3):
                continue
            to_delete.append(e)
        except Exception:
            continue
    for e in to_delete:
        try:
            msp.delete_entity(e)
            removed += 1
        except Exception:
            pass
    return removed


def _remove_existing_hole_spacing_dims(msp, panel: PanelBox, holes: Sequence[Hole]) -> int:
    removed = 0
    to_delete = []

    def nearest(point: Point, tol: float = 24.0) -> Optional[Hole]:
        best = None
        best_d = tol
        for h in holes:
            d = _distance(point, h.center)
            if d <= best_d:
                best = h
                best_d = d
        return best

    for d in msp.query("DIMENSION"):
        pts = _dim_points(d)
        if not pts:
            continue
        _, p1, p2 = pts
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if not panel.contains(mid, margin=260.0):
            continue
        h1 = nearest(p1)
        h2 = nearest(p2)
        if h1 is None or h2 is None:
            continue
        if _family(h1) in {"VIS", "TOURILLON"} and _family(h2) in {"VIS", "TOURILLON"}:
            to_delete.append(d)

    for d in to_delete:
        try:
            msp.delete_entity(d)
            removed += 1
        except Exception:
            pass
    return removed


def _add_vertical_tranche_tourillon_dims(msp, panel: PanelBox, holes: Sequence[Hole]) -> int:
    created = 0
    sides = {
        "L": sorted({_r(h.center[1]) for h in holes if _family(h) == "TOURILLON" and h.center[0] < panel.min_x - 5}),
        "R": sorted({_r(h.center[1]) for h in holes if _family(h) == "TOURILLON" and h.center[0] > panel.max_x + 5}),
    }
    for side, ys in sides.items():
        if len(ys) < 2:
            continue
        base_x = panel.min_x - 85.0 if side == "L" else panel.max_x + 85.0
        x_ref = panel.min_x - 20.0 if side == "L" else panel.max_x + 20.0
        for i in range(len(ys) - 1):
            y1, y2 = ys[i], ys[i + 1]
            _add_dim(msp, base=(base_x, y1), p1=(x_ref, y1), p2=(x_ref, y2), angle=90)
            created += 1
    return created


def _add_one_row_x_dims_vis_tourillon(msp, panel: PanelBox, holes: Sequence[Hole]) -> int:
    created = 0
    fam_rows: Dict[str, Dict[float, List[Hole]]] = {"VIS": {}, "TOURILLON": {}}
    for h in holes:
        f = _family(h)
        if f not in fam_rows:
            continue
        y = _r(h.center[1])
        fam_rows[f].setdefault(y, []).append(h)

    for fam in ("VIS", "TOURILLON"):
        best = None
        best_n = 0
        for y, hs in fam_rows[fam].items():
            xs = sorted({_r(h.center[0]) for h in hs if panel.min_x - 5 <= h.center[0] <= panel.max_x + 5})
            n = len(xs)
            if n > best_n:
                best_n = n
                best = (y, xs)
        if best is None or best_n < 3:
            continue
        y, xs = best
        base_y = panel.min_y - 78.0 if fam == "VIS" else panel.max_y + 78.0
        for i in range(len(xs) - 1):
            _add_dim(msp, base=(xs[i], base_y), p1=(xs[i], y), p2=(xs[i + 1], y), angle=0)
            created += 1
    return created


def _clean_montant_dims_and_rebuild_rows(msp, panel: PanelBox, holes: Sequence[Hole]) -> Tuple[int, int, int]:
    """For montants with fixed shelves:
    - remove duplicated vertical pitch chains,
    - remove cluttered horizontal row dimensions,
    - rebuild one VIS row and one TOURILLON row in X.
    """
    removed_vertical_dup = 0
    removed_horizontal_mid = 0
    added_rows = 0

    # 1) Vertical duplicated chains: keep one representative per (span,y1,y2).
    groups: Dict[Tuple[float, float, float], List[Tuple[float, object]]] = {}
    for dim in list(msp.query("DIMENSION")):
        pts = _dim_points(dim)
        if not pts:
            continue
        _, p1, p2 = pts
        if _dim_orientation(p1, p2) != "V":
            continue
        x = p1[0]
        y1, y2 = sorted((p1[1], p2[1]))
        span = y2 - y1
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if not panel.contains(mid, margin=220.0):
            continue
        if not (80.0 <= span <= 260.0):
            continue
        if not (panel.min_x - 20.0 <= x <= panel.max_x + 20.0):
            continue
        key = (_r(span), _r(y1), _r(y2))
        groups.setdefault(key, []).append((x, dim))

    for items in groups.values():
        if len(items) <= 1:
            continue
        items = sorted(items, key=lambda t: t[0])
        for _, dim in items[1:]:
            try:
                msp.delete_entity(dim)
                removed_vertical_dup += 1
            except Exception:
                pass

    # 2) Horizontal clutter removal on rows.
    for dim in list(msp.query("DIMENSION")):
        pts = _dim_points(dim)
        if not pts:
            continue
        _, p1, p2 = pts
        if _dim_orientation(p1, p2) != "H":
            continue
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if not panel.contains(mid, margin=220.0):
            continue
        span = abs(p2[0] - p1[0])
        # Keep tiny thickness and full-width dimensions; remove middle clutter.
        if 40.0 < span < (panel.width - 5.0):
            try:
                msp.delete_entity(dim)
                removed_horizontal_mid += 1
            except Exception:
                pass

    # 3) Rebuild one representative VIS row + one TOURILLON row in X.
    for fam, base_offset in (("VIS", -76.0), ("TOURILLON", -98.0)):
        fam_h = [h for h in holes if _family(h) == fam and panel.min_x - 5.0 <= h.center[0] <= panel.max_x + 5.0]
        by_y: Dict[float, List[Hole]] = {}
        for h in fam_h:
            by_y.setdefault(_r(h.center[1]), []).append(h)

        best_y = None
        best_xs: List[float] = []
        for y, row in by_y.items():
            xs = sorted({_r(h.center[0]) for h in row})
            if len(xs) > len(best_xs):
                best_xs = xs
                best_y = y
        if best_y is None or len(best_xs) < 3:
            continue

        max_seg = 5
        seg = 0
        for i in range(len(best_xs) - 1):
            if seg >= max_seg:
                break
            x1 = best_xs[i]
            x2 = best_xs[i + 1]
            _add_dim(msp, base=(x1, panel.min_y + base_offset), p1=(x1, best_y), p2=(x2, best_y), angle=0)
            added_rows += 1
            seg += 1

    return removed_vertical_dup, removed_horizontal_mid, added_rows


def _remove_upper_hinge_pair_spacing(msp, panel: PanelBox, holes: Sequence[Hole]) -> int:
    # For door panels: remove top duplicated spacing between the two screw holes of hinge lots.
    removed = 0
    to_delete = []

    def nearest(point: Point, tol: float = 22.0) -> Optional[Hole]:
        best = None
        best_d = tol
        for h in holes:
            d = _distance(point, h.center)
            if d <= best_d:
                best = h
                best_d = d
        return best

    y_mid = (panel.min_y + panel.max_y) * 0.5
    for d in msp.query("DIMENSION"):
        pts = _dim_points(d)
        if not pts:
            continue
        _, p1, p2 = pts
        ori = _dim_orientation(p1, p2)
        if ori != "V":
            continue
        mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if not panel.contains(mid, margin=220.0):
            continue
        if mid[1] <= y_mid:
            continue
        span = abs(p2[1] - p1[1])
        if not (40.0 <= span <= 50.0):
            continue
        h1 = nearest(p1)
        h2 = nearest(p2)
        if h1 is None or h2 is None:
            continue
        if _family(h1) == "TOURILLON" and _family(h2) == "TOURILLON":
            to_delete.append(d)

    for d in to_delete:
        try:
            msp.delete_entity(d)
            removed += 1
        except Exception:
            pass
    return removed


def apply_piece_rules(src: Path, dst: Path) -> Dict[str, int]:
    doc = ezdxf.readfile(str(src))
    msp = doc.modelspace()
    panels = _panel_boxes(msp)
    all_holes = _collect_holes(msp, panels)

    stats = {
        "panels": len(panels),
        "removed_duplicate_25": 0,
        "moved_thickness": 0,
        "removed_border_ticks": 0,
        "removed_hole_spacing": 0,
        "added_tranche_tourillon_y": 0,
        "added_montant_x_rows": 0,
        "removed_montant_vertical_dup": 0,
        "removed_montant_horizontal_mid": 0,
        "removed_upper_hinge_pair": 0,
    }

    by_panel: Dict[int, List[Hole]] = {}
    for h in all_holes:
        by_panel.setdefault(h.panel_idx, []).append(h)

    for idx, panel in enumerate(panels):
        holes = by_panel.get(idx, [])
        ptype = _classify_panel(panel, holes)

        if ptype == "TRAVERSE_OR_FIXED_SHELF":
            stats["moved_thickness"] += _move_thickness_outside(msp, panel)
            stats["removed_duplicate_25"] += _remove_duplicate_25_right(msp, panel, idx)
            stats["removed_border_ticks"] += _remove_small_border_ticks(msp, panel)
            stats["removed_hole_spacing"] += _remove_existing_hole_spacing_dims(msp, panel, holes)
            stats["added_tranche_tourillon_y"] += _add_vertical_tranche_tourillon_dims(msp, panel, holes)

        elif ptype == "MONTANT_WITH_FIXED_SHELVES":
            stats["removed_hole_spacing"] += _remove_existing_hole_spacing_dims(msp, panel, holes)
            rv, rh, add = _clean_montant_dims_and_rebuild_rows(msp, panel, holes)
            stats["removed_montant_vertical_dup"] += rv
            stats["removed_montant_horizontal_mid"] += rh
            stats["added_montant_x_rows"] += add

        elif ptype == "DOOR":
            stats["removed_upper_hinge_pair"] += _remove_upper_hinge_pair_spacing(msp, panel, holes)

    # Keep AutoCAD-friendly defaults.
    for k, v in (("$DIMASSOC", 2), ("$ORTHOMODE", 0), ("$SNAPMODE", 0), ("$OSMODE", 39)):
        try:
            doc.header[k] = v
        except Exception:
            pass

    doc.saveas(str(dst))
    stats["final_dim_count"] = len(list(msp.query("DIMENSION")))
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply piece-by-piece DXF rules for dimensions cleanup")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true", help="Modify input file in place (creates .bak backup)")
    args = parser.parse_args(argv)

    src = args.input
    if not src.exists() or src.suffix.lower() != ".dxf":
        print(f"[ERROR] Invalid input DXF: {src}")
        return 2

    if args.in_place:
        dst = src
        bak = src.with_suffix(src.suffix + ".bak")
        try:
            bak.write_bytes(src.read_bytes())
        except Exception:
            pass
    else:
        dst = args.output or src.with_name(f"{src.stem}_piece_rules{src.suffix}")
    try:
        stats = apply_piece_rules(src, dst)
    except Exception as exc:
        print(f"[ERROR] Failed: {exc}")
        return 1

    print("[OK] Piece-by-piece rules applied")
    print(f"  input:  {src}")
    print(f"  output: {dst}")
    for k in (
        "panels",
        "removed_duplicate_25",
        "moved_thickness",
        "removed_border_ticks",
        "removed_hole_spacing",
        "added_tranche_tourillon_y",
        "added_montant_x_rows",
        "removed_montant_vertical_dup",
        "removed_montant_horizontal_mid",
        "removed_upper_hinge_pair",
        "final_dim_count",
    ):
        print(f"  {k}: {stats[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
