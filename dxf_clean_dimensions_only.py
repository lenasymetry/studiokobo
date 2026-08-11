#!/usr/bin/env python3
"""DXF dimension cleanup without touching construction geometry.

This tool only removes DIMENSION entities that are redundant/repetitive.
It never edits or deletes LINE/LWPOLYLINE/CIRCLE construction entities.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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

    def contains(self, p: Point) -> bool:
        return self.min_x <= p[0] <= self.max_x and self.min_y <= p[1] <= self.max_y


@dataclass
class DimInfo:
    ent: object
    handle: str
    layer: str
    orientation: str  # H or V
    a1: float
    a2: float
    row: float
    base: float
    span: float
    panel_idx: int
    outside_score: float


def _r(v: float, nd: int = 1) -> float:
    return round(float(v), nd)


def _panel_boxes(msp) -> List[PanelBox]:
    out: List[PanelBox] = []
    for pl in msp.query("LWPOLYLINE[layer=='PANNEAU']"):
        try:
            pts = [(float(x), float(y)) for x, y in pl.get_points("xy")]
        except Exception:
            continue
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append(PanelBox(min(xs), min(ys), max(xs), max(ys)))

    if out:
        return out

    # Fallback for converted DXF files where everything is on a generic layer (e.g. GEOM).
    # We detect large axis-aligned rectangles as panel candidates.
    candidates: List[PanelBox] = []
    for pl in msp.query("LWPOLYLINE"):
        try:
            pts = [(float(x), float(y)) for x, y in pl.get_points("xy")]
        except Exception:
            continue
        if len(pts) < 4:
            continue
        pts4 = pts[:-1] if pts[0] == pts[-1] else pts
        if len(pts4) != 4:
            continue

        hv_ok = True
        for i in range(4):
            x1, y1 = pts4[i]
            x2, y2 = pts4[(i + 1) % 4]
            if not (abs(x1 - x2) <= 1e-3 or abs(y1 - y2) <= 1e-3):
                hv_ok = False
                break
        if not hv_ok:
            continue

        xs = [p[0] for p in pts4]
        ys = [p[1] for p in pts4]
        b = PanelBox(min(xs), min(ys), max(xs), max(ys))
        if b.width >= 400.0 and b.height >= 250.0:
            candidates.append(b)

    # Keep unique large boxes only.
    uniq: List[PanelBox] = []
    seen = set()
    for b in sorted(candidates, key=lambda z: (z.min_x, z.min_y, z.max_x, z.max_y)):
        key = (_r(b.min_x), _r(b.min_y), _r(b.max_x), _r(b.max_y))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)
    return uniq


def _closest_panel_idx(p: Point, panels: Sequence[PanelBox]) -> int:
    if not panels:
        return -1
    best_idx = 0
    best_d2 = 10**30
    for i, b in enumerate(panels):
        cx = (b.min_x + b.max_x) * 0.5
        cy = (b.min_y + b.max_y) * 0.5
        d2 = (p[0] - cx) ** 2 + (p[1] - cy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_idx = i
    return best_idx


def _outside_score(orientation: str, base: float, panel: Optional[PanelBox]) -> float:
    if panel is None:
        return 0.0
    if orientation == "H":
        # Positive when base is outside vertical panel bounds.
        if base < panel.min_y:
            return panel.min_y - base
        if base > panel.max_y:
            return base - panel.max_y
        return -min(base - panel.min_y, panel.max_y - base)
    # V
    if base < panel.min_x:
        return panel.min_x - base
    if base > panel.max_x:
        return base - panel.max_x
    return -min(base - panel.min_x, panel.max_x - base)


def _iter_dims(msp, panels: Sequence[PanelBox]) -> List[DimInfo]:
    infos: List[DimInfo] = []
    for ent in msp.query("DIMENSION"):
        try:
            layer = str(getattr(ent.dxf, "layer", "")).upper()
            if layer not in {"DIM", "COTES", "COTES_INTER"}:
                continue
            p1 = ent.dxf.defpoint2
            p2 = ent.dxf.defpoint3
            bp = ent.dxf.defpoint
            x1, y1 = float(p1.x), float(p1.y)
            x2, y2 = float(p2.x), float(p2.y)
            bx, by = float(bp.x), float(bp.y)
        except Exception:
            continue

        # Keep only axis-aligned linear dims.
        if abs(y1 - y2) <= 1e-3:
            orientation = "H"
            a1, a2 = sorted((_r(x1), _r(x2)))
            row = _r(y1)
            base = _r(by)
            span = _r(abs(x2 - x1))
            anchor = ((x1 + x2) * 0.5, y1)
        elif abs(x1 - x2) <= 1e-3:
            orientation = "V"
            a1, a2 = sorted((_r(y1), _r(y2)))
            row = _r(x1)
            base = _r(bx)
            span = _r(abs(y2 - y1))
            anchor = (x1, (y1 + y2) * 0.5)
        else:
            continue

        panel_idx = _closest_panel_idx(anchor, panels)
        panel = panels[panel_idx] if 0 <= panel_idx < len(panels) else None
        score = _outside_score(orientation, base, panel)
        infos.append(
            DimInfo(
                ent=ent,
                handle=str(getattr(ent.dxf, "handle", "")),
                layer=layer,
                orientation=orientation,
                a1=a1,
                a2=a2,
                row=row,
                base=base,
                span=span,
                panel_idx=panel_idx,
                outside_score=score,
            )
        )
    return infos


def _keep_global_dimensions(dims: List[DimInfo], panels: Sequence[PanelBox]) -> set[str]:
    """Always keep one width and one height global dim per panel when present."""
    keep: set[str] = set()
    by_panel: Dict[int, List[DimInfo]] = {}
    for d in dims:
        by_panel.setdefault(d.panel_idx, []).append(d)

    for p_idx, items in by_panel.items():
        if not (0 <= p_idx < len(panels)):
            continue
        p = panels[p_idx]
        target_w = _r(p.width)
        target_h = _r(p.height)

        h_candidates = [d for d in items if d.orientation == "H" and abs(d.span - target_w) <= 2.0]
        v_candidates = [d for d in items if d.orientation == "V" and abs(d.span - target_h) <= 2.0]

        if h_candidates:
            best = sorted(h_candidates, key=lambda d: (-d.outside_score, d.base))[0]
            keep.add(best.handle)
        if v_candidates:
            best = sorted(v_candidates, key=lambda d: (-d.outside_score, d.base))[0]
            keep.add(best.handle)

    return keep


def cleanup_dimensions(doc, aggressive: bool = True) -> Tuple[int, int, int]:
    """Return (before_count, removed_count, kept_count)."""
    msp = doc.modelspace()
    panels = _panel_boxes(msp)
    dims = _iter_dims(msp, panels)
    before = len(dims)

    if not dims:
        return 0, 0, 0

    keep_handles = _keep_global_dimensions(dims, panels)

    # 1) Keep exactly one representative per strict key including row.
    #    If two are equivalent, keep the one most outside panel (readability).
    strict_best: Dict[Tuple, DimInfo] = {}
    for d in dims:
        key = (d.panel_idx, d.layer, d.orientation, d.a1, d.a2, d.row)
        cur = strict_best.get(key)
        if cur is None or (d.outside_score, -abs(d.base)) > (cur.outside_score, -abs(cur.base)):
            strict_best[key] = d

    # 2) Remove repeated patterns across rows/cols (common shelf-row duplication).
    #    Keep only one per (panel, layer, orientation, endpoints).
    pattern_best: Dict[Tuple, DimInfo] = {}
    for d in strict_best.values():
        pkey = (d.panel_idx, d.layer, d.orientation, d.a1, d.a2)
        cur = pattern_best.get(pkey)
        if cur is None or (d.outside_score, -abs(d.base)) > (cur.outside_score, -abs(cur.base)):
            pattern_best[pkey] = d

    # 3) Optional aggressive pass: if a panel has many tiny-step inter dims,
    #    keep only unique spans per orientation to reduce visual overload.
    final_keep: Dict[str, DimInfo] = {d.handle: d for d in pattern_best.values()}
    if aggressive:
        by_panel_ori: Dict[Tuple[int, str], List[DimInfo]] = {}
        for d in final_keep.values():
            by_panel_ori.setdefault((d.panel_idx, d.orientation), []).append(d)

        reduced_keep: Dict[str, DimInfo] = {}
        for key, items in by_panel_ori.items():
            span_best: Dict[float, DimInfo] = {}
            for d in items:
                cur = span_best.get(d.span)
                if cur is None or (d.outside_score, -abs(d.base)) > (cur.outside_score, -abs(cur.base)):
                    span_best[d.span] = d
            for d in span_best.values():
                reduced_keep[d.handle] = d
        final_keep = reduced_keep

    # Force-keep important globals.
    for h in keep_handles:
        if h not in final_keep:
            d = next((x for x in dims if x.handle == h), None)
            if d is not None:
                final_keep[h] = d

    keep_set = set(final_keep.keys())

    removed = 0
    for d in dims:
        if d.handle in keep_set:
            continue
        try:
            msp.delete_entity(d.ent)
            removed += 1
        except Exception:
            pass

    return before, removed, before - removed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Remove redundant DIMENSION entities from DXF")
    parser.add_argument("input", type=Path, help="Input DXF")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output DXF (default: <input>_clean_dims.dxf)")
    parser.add_argument("--non-aggressive", action="store_true", help="Disable strongest reduction pass")
    args = parser.parse_args(argv)

    src = args.input
    if not src.exists() or src.suffix.lower() != ".dxf":
        print(f"[ERROR] Invalid input DXF: {src}")
        return 2

    dst = args.output or src.with_name(f"{src.stem}_clean_dims{src.suffix}")

    try:
        doc = ezdxf.readfile(str(src))
        before, removed, kept = cleanup_dimensions(doc, aggressive=not args.non_aggressive)
        doc.saveas(str(dst))
    except Exception as exc:
        print(f"[ERROR] Failed: {exc}")
        return 1

    print("[OK] Dimension cleanup completed")
    print(f"  input:    {src}")
    print(f"  output:   {dst}")
    print(f"  before:   {before}")
    print(f"  removed:  {removed}")
    print(f"  kept:     {kept}")
    print("  geometry: untouched (no LINE/LWPOLYLINE/CIRCLE edits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
