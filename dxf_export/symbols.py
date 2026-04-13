"""Symboles DXF: triangles plein/vide (identiques visuellement à l'UI)."""

from __future__ import annotations

import math
import os

from .sanitize import sanitize_layer_name


def _triangle_points(center, size, rotation=0.0):
    cx, cy = float(center[0]), float(center[1])
    s = float(size)
    h = s * math.sqrt(3.0) / 2.0

    # triangle équilatéral pointant vers le haut
    pts = [
        (0.0, 2.0 * h / 3.0),
        (-s / 2.0, -h / 3.0),
        (s / 2.0, -h / 3.0),
    ]

    ang = math.radians(float(rotation))
    cos_a = math.cos(ang)
    sin_a = math.sin(ang)

    out = []
    for x, y in pts:
        xr = x * cos_a - y * sin_a
        yr = x * sin_a + y * cos_a
        out.append((cx + xr, cy + yr))
    return out


def add_filled_triangle(msp_or_layout, center, size, rotation=0, layer="SYMBOLS"):
    """Triangle plein: LWPOLYLINE fermée + HATCH (robuste AutoCAD)."""
    p1, p2, p3 = _triangle_points(center=center, size=size, rotation=rotation)
    pts = [p1, p2, p3]
    # Use close=True for proper polygon
    msp_or_layout.add_lwpolyline(
        pts,
        dxfattribs={"layer": sanitize_layer_name(layer, fallback="SYMBOLS"), "color": 7},
        close=True,
    )
    try:
        hatch = msp_or_layout.add_hatch(color=7, dxfattribs={"layer": sanitize_layer_name(layer, fallback="SYMBOLS"), "color": 7})
        hatch.paths.add_polyline_path(pts, is_closed=True)
    except Exception:
        pass  # If hatch fails, polyline alone is sufficient
    return (p1, p2, p3)


def add_empty_triangle(msp_or_layout, center, size, rotation=0, layer="SYMBOLS"):
    """Triangle vide: LWPOLYLINE fermée sans hatch."""
    pts = _triangle_points(center=center, size=size, rotation=rotation)
    # Use close=True instead of duplicating first point
    msp_or_layout.add_lwpolyline(
        pts,
        dxfattribs={"layer": sanitize_layer_name(layer, fallback="SYMBOLS"), "color": 7},
        close=True,
    )
    return tuple(pts)


def add_required_sheet_triangles(
    layout,
    metadata,
    paper_width_mm=420.0,
    margin_mm=10.0,
    base_y_mm=18.0,
    size_mm=8.0,
    rotation_deg=0.0,
):
    """Ajoute les triangles obligatoires (plein + vide) sur chaque layout.

    Position exacte possible via metadata:
      - triangle_filled_center: (x, y)
      - triangle_empty_center: (x, y)
      - triangle_size
      - triangle_rotation
    """
    md = metadata or {}
    size = float(md.get("triangle_size", size_mm))
    rot = float(md.get("triangle_rotation", rotation_deg))

    default_filled = (float(paper_width_mm) - float(margin_mm) - 18.0, float(base_y_mm))
    default_empty = (float(paper_width_mm) - float(margin_mm) - 34.0, float(base_y_mm))

    filled_center = md.get("triangle_filled_center", default_filled)
    empty_center = md.get("triangle_empty_center", default_empty)

    add_filled_triangle(layout, center=filled_center, size=size, rotation=rot, layer="SYMBOLS")
    add_empty_triangle(layout, center=empty_center, size=size, rotation=rot, layer="SYMBOLS")


def _rotated_ellipse_points(cx, cy, rx, ry, rot_deg=0.0, segments=72):
    pts = []
    ang = math.radians(float(rot_deg))
    c = math.cos(ang)
    s = math.sin(ang)
    for i in range(int(segments)):
        t = (2.0 * math.pi * i) / float(segments)
        ex = float(rx) * math.cos(t)
        ey = float(ry) * math.sin(t)
        xr = ex * c - ey * s
        yr = ex * s + ey * c
        pts.append((float(cx) + xr, float(cy) + yr))
    return pts


def _resolve_logo_path(source_logo_path):
    if not source_logo_path:
        return None
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.abspath(os.path.join(script_dir, ".."))
    workspace_parent = os.path.abspath(os.path.join(project_dir, ".."))

    candidates = [
        source_logo_path,
        os.path.join(os.getcwd(), source_logo_path),
        os.path.join(project_dir, source_logo_path),
        os.path.join(workspace_parent, source_logo_path),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _draw_logo_png_as_vector(layout, target_rect, source_logo_path, layer, color):
    resolved = _resolve_logo_path(source_logo_path)
    if not resolved:
        return False
    try:
        from PIL import Image
    except Exception:
        return False

    try:
        x0, y0, x1, y1 = target_rect
        x0 = float(x0)
        y0 = float(y0)
        x1 = float(x1)
        y1 = float(y1)
        w = max(1.0, x1 - x0)
        h = max(1.0, y1 - y0)

        with Image.open(resolved) as im:
            rgba = im.convert("RGBA")
            px = rgba.load()
            iw, ih = rgba.size

            # Bounding box des pixels utiles (alpha + pixels non clairs)
            min_x, min_y = iw, ih
            max_x, max_y = -1, -1
            for yy in range(ih):
                for xx in range(iw):
                    r, g, b, a = px[xx, yy]
                    lum = 0.299 * r + 0.587 * g + 0.114 * b
                    if a > 12 and lum < 245:
                        if xx < min_x:
                            min_x = xx
                        if yy < min_y:
                            min_y = yy
                        if xx > max_x:
                            max_x = xx
                        if yy > max_y:
                            max_y = yy

            if max_x < min_x or max_y < min_y:
                return False

            cropped = rgba.crop((min_x, min_y, max_x + 1, max_y + 1))

            # Ajuste l'espacement du mot du bas (INGENIERIE) dans le bitmap lui-même.
            # Objectif: conserver le logo identique et n'agir que sur l'écartement des lettres.
            def _spread_bottom_word_pixels(img, spacing_factor=5.0):
                try:
                    from PIL import Image
                except Exception:
                    return img

                w0, h0 = img.size
                if w0 < 10 or h0 < 10:
                    return img

                src = img.copy()
                px0 = src.load()

                def is_dark(xx, yy):
                    r, g, b, a = px0[xx, yy]
                    lum = 0.299 * r + 0.587 * g + 0.114 * b
                    return a > 24 and lum < 210

                # Composantes connexes sur le logo cropé.
                visited = [[False] * w0 for _ in range(h0)]
                components = []
                for yy in range(h0):
                    for xx in range(w0):
                        if visited[yy][xx]:
                            continue
                        visited[yy][xx] = True
                        if not is_dark(xx, yy):
                            continue

                        stack = [(xx, yy)]
                        pixels = []
                        minx = maxx = xx
                        miny = maxy = yy
                        while stack:
                            cx, cy = stack.pop()
                            pixels.append((cx, cy))
                            if cx < minx:
                                minx = cx
                            if cx > maxx:
                                maxx = cx
                            if cy < miny:
                                miny = cy
                            if cy > maxy:
                                maxy = cy

                            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                                if 0 <= nx < w0 and 0 <= ny < h0 and not visited[ny][nx]:
                                    visited[ny][nx] = True
                                    if is_dark(nx, ny):
                                        stack.append((nx, ny))

                        area = len(pixels)
                        bw = maxx - minx + 1
                        bh = maxy - miny + 1
                        components.append({
                            "bbox": (minx, miny, maxx, maxy),
                            "area": area,
                            "bw": bw,
                            "bh": bh,
                            "pixels": pixels,
                        })

                # Filtre des composantes du mot du bas uniquement.
                text_candidates = []
                y_min = int(h0 * 0.66)
                y_max = int(h0 * 0.995)
                max_bw = max(3, int(w0 * 0.14))
                max_bh = max(4, int(h0 * 0.20))
                min_area = 4

                for comp in components:
                    minx, miny, maxx, maxy = comp["bbox"]
                    if comp["area"] < min_area:
                        continue
                    if miny < y_min or maxy > y_max:
                        continue
                    if comp["bw"] > max_bw or comp["bh"] > max_bh:
                        continue
                    text_candidates.append(comp)

                if len(text_candidates) < 8:
                    return img

                text_candidates.sort(key=lambda c: c["bbox"][0])

                # Transforme les composantes en runs horizontaux (glyphes), en fusionnant les overlaps.
                intervals = [(c["bbox"][0], c["bbox"][2]) for c in text_candidates]
                intervals.sort()
                glyph_runs = []
                cur_l, cur_r = intervals[0]
                for l, r in intervals[1:]:
                    if l <= cur_r + 1:
                        cur_r = max(cur_r, r)
                    else:
                        glyph_runs.append((cur_l, cur_r))
                        cur_l, cur_r = l, r
                glyph_runs.append((cur_l, cur_r))

                if len(glyph_runs) < 8:
                    return img

                base_gaps = []
                for i in range(1, len(glyph_runs)):
                    gap = glyph_runs[i][0] - glyph_runs[i - 1][1] - 1
                    if gap >= 0:
                        base_gaps.append(gap)
                if not base_gaps:
                    return img

                base_gaps_sorted = sorted(base_gaps)
                base_gap = base_gaps_sorted[len(base_gaps_sorted) // 2]
                avg_w = sum((r - l + 1) for l, r in glyph_runs) / float(len(glyph_runs))

                # Gap fortement augmenté pour être clairement visible dans AutoCAD.
                target_gap = max(
                    int(round(max(1, base_gap) * float(spacing_factor) * 1.8)),
                    int(round(avg_w * 0.8)),
                )

                widths = [(r - l + 1) for l, r in glyph_runs]
                sum_w = sum(widths)
                max_allowed = int(round(w0 * 0.98))
                if len(glyph_runs) > 1:
                    max_fit_gap = max(1, (max_allowed - sum_w) // (len(glyph_runs) - 1))
                    target_gap = min(target_gap, max_fit_gap)

                strip_w = sum_w + target_gap * max(0, len(glyph_runs) - 1)
                if strip_w <= 0 or strip_w > w0:
                    return img

                min_text_y = min(c["bbox"][1] for c in text_candidates)
                max_text_y = max(c["bbox"][3] for c in text_candidates)
                text_h = max_text_y - min_text_y + 1

                strip = Image.new("RGBA", (strip_w, text_h), (0, 0, 0, 0))
                cursor = 0
                for l, r in glyph_runs:
                    patch = src.crop((l, min_text_y, r + 1, max_text_y + 1))
                    strip.paste(patch, (cursor, 0), patch)
                    cursor += (r - l + 1) + target_gap

                out = src.copy()
                opx = out.load()

                # Efface uniquement les pixels des composantes texte d'origine.
                for comp in text_candidates:
                    for xx, yy in comp["pixels"]:
                        opx[xx, yy] = (0, 0, 0, 0)

                original_l = min(l for l, _ in glyph_runs)
                original_r = max(r for _, r in glyph_runs)
                original_center_x = (original_l + original_r) // 2
                paste_x = max(0, min(w0 - strip_w, original_center_x - strip_w // 2))
                out.paste(strip, (paste_x, min_text_y), strip)
                return out

            cropped = _spread_bottom_word_pixels(cropped, spacing_factor=5.0)
            cw, ch = cropped.size

            # Résolution haute pour un rendu lisse : ~2× plus de pixels qu'avant
            max_w = 320
            max_h = 280
            scale = min(max_w / float(cw), max_h / float(ch), 1.0)
            rw = max(24, int(round(cw * scale)))
            rh = max(24, int(round(ch * scale)))
            try:
                resample = Image.Resampling.LANCZOS
            except Exception:
                resample = Image.LANCZOS
            raster = cropped.resize((rw, rh), resample)
            rpx = raster.load()

            # Fit dans la case en conservant le ratio
            logo_ratio = rw / float(rh)
            box_ratio = w / float(h)
            if logo_ratio >= box_ratio:
                draw_w = w * 0.92
                draw_h = draw_w / logo_ratio
            else:
                draw_h = h * 0.92
                draw_w = draw_h * logo_ratio

            dx0 = x0 + (w - draw_w) * 0.5
            dy0 = y0 + (h - draw_h) * 0.5
            dx1 = dx0 + draw_w
            dy1 = dy0 + draw_h

            px_w = draw_w / float(rw)
            px_h = draw_h / float(rh)

            for yy in range(rh):
                run_start = None
                for xx in range(rw + 1):
                    is_on = False
                    if xx < rw:
                        r, g, b, a = rpx[xx, yy]
                        lum = 0.299 * r + 0.587 * g + 0.114 * b
                        is_on = a > 24 and lum < 210

                    if is_on and run_start is None:
                        run_start = xx
                    elif (not is_on) and run_start is not None:
                        x_start = run_start
                        x_end = xx - 1

                        rx0 = dx0 + x_start * px_w
                        rx1 = dx0 + (x_end + 1) * px_w
                        ry1 = dy1 - yy * px_h
                        ry0 = dy1 - (yy + 1) * px_h

                        layout.add_solid(
                            [(rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)],
                            dxfattribs={"layer": layer, "color": int(color)},
                        )
                        run_start = None

        return True
    except Exception:
        return False


def add_potech_p_mark(layout, target_rect, layer="SYMBOLS", color=7, source_logo_path=None):
    x0, y0, x1, y1 = target_rect
    x0 = float(x0)
    y0 = float(y0)
    x1 = float(x1)
    y1 = float(y1)
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)

    sym_layer = sanitize_layer_name(layer, fallback="SYMBOLS")
    c = int(color)

    # Logo centré sur toute la case (sans texte DXF ajouté)
    png_ok = _draw_logo_png_as_vector(layout, target_rect, source_logo_path, sym_layer, c)

    if not png_ok:
        # Fallback vectoriel : logo comma-style centré dans la case entière
        icon_x0 = x0 + w * 0.06
        icon_x1 = x0 + w * 0.94
        icon_y0 = y0 + h * 0.06
        icon_y1 = y1 - h * 0.04
        iw = max(1.0, icon_x1 - icon_x0)
        ih = max(1.0, icon_y1 - icon_y0)

        loop1_outer = _rotated_ellipse_points(icon_x0 + iw * 0.42, icon_y0 + ih * 0.58, iw * 0.34, ih * 0.34, rot_deg=18.0, segments=72)
        loop1_inner = _rotated_ellipse_points(icon_x0 + iw * 0.42, icon_y0 + ih * 0.58, iw * 0.22, ih * 0.22, rot_deg=18.0, segments=72)

        loop2_outer = _rotated_ellipse_points(icon_x0 + iw * 0.62, icon_y0 + ih * 0.66, iw * 0.31, ih * 0.31, rot_deg=-18.0, segments=72)
        loop2_inner = _rotated_ellipse_points(icon_x0 + iw * 0.62, icon_y0 + ih * 0.66, iw * 0.20, ih * 0.20, rot_deg=-18.0, segments=72)

        try:
            hatch1 = layout.add_hatch(color=c, dxfattribs={"layer": sym_layer, "color": c})
            hatch1.paths.add_polyline_path(loop1_outer, is_closed=True)
            hatch1.paths.add_polyline_path(list(reversed(loop1_inner)), is_closed=True)
        except Exception:
            pass
        try:
            hatch2 = layout.add_hatch(color=c, dxfattribs={"layer": sym_layer, "color": c})
            hatch2.paths.add_polyline_path(loop2_outer, is_closed=True)
            hatch2.paths.add_polyline_path(list(reversed(loop2_inner)), is_closed=True)
        except Exception:
            pass

        try:
            layout.add_lwpolyline(loop1_outer, dxfattribs={"layer": sym_layer, "color": c}, close=True)
            layout.add_lwpolyline(loop2_outer, dxfattribs={"layer": sym_layer, "color": c}, close=True)
        except Exception:
            pass

        tail = [
            (icon_x0 + iw * 0.18, icon_y0 + ih * 0.34),
            (icon_x0 + iw * 0.18, icon_y0 + ih * 0.12),
            (icon_x0 + iw * 0.02, icon_y0 - ih * 0.10),
            (icon_x0 + iw * 0.32, icon_y0 + ih * 0.18),
        ]
        try:
            layout.add_lwpolyline(tail, dxfattribs={"layer": sym_layer, "color": c}, close=True)
            hatch_tail = layout.add_hatch(color=c, dxfattribs={"layer": sym_layer, "color": c})
            hatch_tail.paths.add_polyline_path(tail, is_closed=True)
        except Exception:
            pass