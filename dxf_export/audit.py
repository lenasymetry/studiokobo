"""Validation DXF: audit ezdxf + contrôles références/table."""

from __future__ import annotations

from .sanitize import sanitize_table_name


def validate_dxf(doc):
    problems = []
    critical = 0

    try:
        auditor = doc.audit()
        errors = list(getattr(auditor, "errors", []) or [])
        if errors:
            critical += len(errors)
            for err in errors[:100]:
                problems.append(f"AUDIT: {err}")
    except Exception as exc:
        critical += 1
        problems.append(f"AUDIT_EXCEPTION: {exc}")

    layer_names = {layer.dxf.name for layer in doc.layers}
    style_names = {style.dxf.name for style in doc.styles}
    dimstyle_names = {ds.dxf.name for ds in doc.dimstyles}

    for name in layer_names:
        if sanitize_table_name(name, fallback="LAYER", max_len=31) != name:
            critical += 1
            problems.append(f"INVALID_LAYER_NAME: {name}")
    for name in style_names:
        if sanitize_table_name(name, fallback="STYLE", max_len=31) != name:
            critical += 1
            problems.append(f"INVALID_STYLE_NAME: {name}")
    for name in dimstyle_names:
        if sanitize_table_name(name, fallback="DIMSTYLE", max_len=31) != name:
            critical += 1
            problems.append(f"INVALID_DIMSTYLE_NAME: {name}")

    for ent in doc.modelspace():
        layer = getattr(ent.dxf, "layer", "")
        if layer and layer not in layer_names:
            critical += 1
            problems.append(f"MISSING_LAYER_REF: {ent.dxftype()} -> {layer}")
        if ent.dxftype() == "TEXT":
            style = getattr(ent.dxf, "style", "")
            if style and style not in style_names:
                critical += 1
                problems.append(f"MISSING_STYLE_REF: TEXT -> {style}")
        if ent.dxftype() == "DIMENSION":
            dimstyle = getattr(ent.dxf, "dimstyle", "")
            if dimstyle and dimstyle not in dimstyle_names:
                critical += 1
                problems.append(f"MISSING_DIMSTYLE_REF: DIMENSION -> {dimstyle}")

    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        for ent in layout:
            layer = getattr(ent.dxf, "layer", "")
            if layer and layer not in layer_names:
                critical += 1
                problems.append(f"MISSING_LAYER_REF_LAYOUT[{layout.name}]: {ent.dxftype()} -> {layer}")

    ok = critical == 0
    report = "Validation DXF OK" if ok else "Validation DXF KO:\n- " + "\n- ".join(problems[:200])
    return ok, report
