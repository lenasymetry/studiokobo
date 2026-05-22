"""API haut niveau export DXF multi-layout depuis Scene graph."""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass
from typing import List

import ezdxf

from .audit import validate_dxf
from .centering import center_layout_content
from .layout import fit_viewport_to_bbox, setup_layout_with_viewport_excluding_titleblock
from .render_dxf import DxfRenderConfig, create_empty_doc, doc_to_ascii_bytes, render_scene_to_modelspace
from .scene import Dimension, PartSpec, Scene, Text, build_sheet_scene, build_sheets_strict
from .sanitize import sanitize_table_name, sanitize_text
from .titleblock import add_title_block


@dataclass
class ExportResult:
    ok: bool
    dxf_bytes: bytes
    report: str
    mode_used: str


def _layout_name(index: int) -> str:
    return sanitize_table_name(f"SHEET_{index:02d}", fallback=f"SHEET_{index:02d}", max_len=31)


def _convert_text_entities_to_ascii(scene: Scene):
    for ent in scene.entities:
        if isinstance(ent, Text):
            ent.text = sanitize_text(ent.text, fallback="")


def _ensure_dimension_entities(scene: Scene):
    if any(isinstance(e, Dimension) for e in scene.entities):
        return

    min_x = None
    min_y = None
    max_x = None
    max_y = None

    for ent in scene.entities:
        if hasattr(ent, "start") and hasattr(ent, "end"):
            pts = [ent.start, ent.end]
        elif hasattr(ent, "points"):
            pts = list(getattr(ent, "points", []))
        elif hasattr(ent, "center") and hasattr(ent, "radius"):
            c = ent.center
            r = float(ent.radius)
            pts = [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
        else:
            pts = []

        for x, y in pts:
            min_x = x if min_x is None else min(min_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_x = x if max_x is None else max(max_x, x)
            max_y = y if max_y is None else max(max_y, y)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return

    scene.entities.append(
        Dimension(layer="DIM", category="dimension", p1=(min_x, min_y), p2=(max_x, min_y), offset=12.0, axis="x", side="top", dimstyle="POTECH_DIM", text_height=10.0)
    )
    scene.entities.append(
        Dimension(layer="DIM", category="dimension", p1=(min_x, min_y), p2=(min_x, max_y), offset=16.0, axis="y", side="right", dimstyle="POTECH_DIM", text_height=10.0)
    )


def _build_parts_from_project(project_data) -> List[PartSpec]:
    cabinets_data = project_data.get("cabinets_data", [])
    indices = project_data.get("indices") or list(range(len(cabinets_data)))

    parts: List[PartSpec] = []

    try:
        from export_manager import generate_stacked_html_plans

        figs, figs_ok = generate_stacked_html_plans(
            cabinets_to_process=cabinets_data,
            indices_to_process=indices,
            output_format="figures",
        )
        if not figs_ok:
            figs = []

        for n, (title, fig) in enumerate(figs, start=1):
            element_id = sanitize_table_name(str(title or f"ELEMENT_{n:02d}"), fallback=f"ELEMENT_{n:02d}", max_len=31)
            fig_meta = getattr(getattr(fig, "layout", None), "meta", None) or {}
            qty = 1
            try:
                qty = int(fig_meta.get("quantity", 1) or 1)
            except Exception:
                qty = 1
            sheet = {
                "title": str(title or f"SHEET_{n:02d}"),
                "figure": fig,
                "element_id": element_id,
                "project_name": project_data.get("project_name", "Projet"),
                "client": project_data.get("client", ""),
                "comments": project_data.get("comments", ""),
                "version": project_data.get("version", "V1"),
                "date": datetime.date.today().isoformat(),
            }
            scene = build_sheet_scene(sheet)
            scene.meta.update(sheet)
            _convert_text_entities_to_ascii(scene)
            _ensure_dimension_entities(scene)
            parts.append(PartSpec(element_id=element_id, scene=scene, metadata={"part_name": title, "quantity": qty}))
        if parts:
            return parts
    except Exception:
        pass

    for idx, cab in enumerate(cabinets_data or []):
        dims = cab.get("dims", {}) if isinstance(cab, dict) else {}
        width = float(dims.get("L_raw", dims.get("Lp", 400.0)))
        height = float(dims.get("W_raw", dims.get("Wp", 300.0)))
        thick = float(dims.get("t_lr_raw", dims.get("thickness", 19.0)))
        element_id = sanitize_table_name(cab.get("name", f"ELEMENT_{idx+1:02d}") if isinstance(cab, dict) else f"ELEMENT_{idx+1:02d}", fallback=f"ELEMENT_{idx+1:02d}")
        title = f"{element_id}"

        scene = Scene(
            name=title,
            width=width,
            height=height,
            meta={
                "title": title,
                "project_name": project_data.get("project_name", "Projet"),
                "part_name": cab.get("name", title) if isinstance(cab, dict) else title,
                "reference": element_id,
                "length": width,
                "height": height,
                "thickness": thick,
                "material": cab.get("material_body", "") if isinstance(cab, dict) else "",
                "date": datetime.date.today().isoformat(),
                "version": project_data.get("version", "V1"),
                "client": project_data.get("client", ""),
                "comments": project_data.get("comments", ""),
            },
        )
        from .scene import Polyline

        scene.entities.append(Text(layer="TEXT", category="text", text=title, insert=(0.0, height + 10.0), height=3.0))
        scene.entities.append(Polyline(layer="GEOM", points=[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)], closed=True))
        _ensure_dimension_entities(scene)
        parts.append(PartSpec(element_id=element_id, scene=scene, metadata={"part_name": title, "quantity": 1}))

    if not parts:
        scene = Scene(
            name="ELEMENT_01",
            width=400.0,
            height=300.0,
            meta={
                "title": "ELEMENT_01",
                "project_name": project_data.get("project_name", "Projet"),
                "part_name": "Piece",
                "reference": "ELEMENT_01",
                "length": 400.0,
                "height": 300.0,
                "thickness": 19.0,
                "material": "",
                "date": datetime.date.today().isoformat(),
                "version": project_data.get("version", "V1"),
                "client": project_data.get("client", ""),
                "comments": project_data.get("comments", ""),
            },
        )
        from .scene import Polyline

        scene.entities.append(Polyline(layer="GEOM", points=[(0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)], closed=True))
        _ensure_dimension_entities(scene)
        parts.append(PartSpec(element_id="ELEMENT_01", scene=scene, metadata={"part_name": "Piece", "quantity": 1}))

    return parts


def _merge_sheet_metadata(project_data, scene: Scene, layout_name: str, element_id: str, part_meta: dict):
    md = dict(scene.meta or {})
    md.update(part_meta or {})
    md.setdefault("project_name", project_data.get("project_name", "Projet"))
    md.setdefault("part_name", scene.name)
    md.setdefault("reference", element_id)
    md.setdefault("length", round(scene.width, 2))
    md.setdefault("height", round(scene.height, 2))
    md.setdefault("thickness", project_data.get("default_thickness", ""))
    md.setdefault("material", project_data.get("material", ""))
    md.setdefault("date", datetime.date.today().isoformat())
    md.setdefault("version", project_data.get("version", "V1"))
    md.setdefault("client", project_data.get("client", ""))
    md.setdefault("comments", project_data.get("comments", ""))
    md.setdefault("quantity", part_meta.get("quantity", 1) if isinstance(part_meta, dict) else 1)
    md.setdefault("corps_meuble", project_data.get("corps_meuble", "caisson"))
    md["element_id"] = element_id
    return md


def export_project_to_dxf(project_data, mode="editable", force_primitives_dims=False, debug=False, debug_stage="all") -> ExportResult:
    """Exporte tout le projet en un DXF multi-layout strict (1 layout = 1 element)."""
    parts = _build_parts_from_project(project_data)
    sheets = build_sheets_strict(parts)

    config = DxfRenderConfig(
        mode=("editable" if str(mode).lower().startswith("editable") else "cnc"),
        force_primitives_dims=bool(force_primitives_dims),
        allow_hatch=True,  # Activé pour les triangles pleins
        text_height=float(project_data.get("text_height", 2.5)),
        dimensions_text_height=float(project_data.get("dimensions_text_height", 10.0)),
        arrow_size=float(project_data.get("arrow_size", 3.0)),
        paper_width_mm=float(project_data.get("paper_width_mm", 420.0)),
        paper_height_mm=float(project_data.get("paper_height_mm", 297.0)),
        page_margin_mm=float(project_data.get("page_margin_mm", 10.0)),
        bbox_margin_factor=float(project_data.get("bbox_margin_factor", 1.04)),
        model_gap_mm=float(project_data.get("model_gap_mm", 500.0)),
        dimstyle_name=sanitize_table_name(str(project_data.get("dimstyle_name", "POTECH_DIM")), fallback="POTECH_DIM"),
    )

    titleblock_height = max(48.0, float(project_data.get("titleblock_height_mm", 48.0)))
    logs = []

    def _render_with_mode(current_mode, force_primitives):
        doc = create_empty_doc(config)
        config.mode = current_mode
        config.force_primitives_dims = force_primitives

        msp = doc.modelspace()
        x_cursor = 0.0

        global_min_x = None
        global_min_y = None
        global_max_x = None
        global_max_y = None

        total_sheets = len(sheets)

        for i, sheet in enumerate(sheets, start=1):
            layout_name = _layout_name(i)
            scene = sheet.scene

            bbox, geom_bbox = render_scene_to_modelspace(
                scene,
                msp,
                config=config,
                offset=(x_cursor, 0.0),
                debug_stage=(debug_stage if debug else "all"),
                log=logs,
            )

            layout, viewport, zone_info = setup_layout_with_viewport_excluding_titleblock(
                doc,
                layout_name=layout_name,
                paper_width_mm=config.paper_width_mm,
                paper_height_mm=config.paper_height_mm,
                margin_mm=config.page_margin_mm,
                titleblock_height_mm=titleblock_height,
            )

            vp_info = fit_viewport_to_bbox(
                viewport,
                bbox=bbox,
                margin_factor=config.bbox_margin_factor,
                min_view_height=10.0,
                geometry_bbox=geom_bbox,
            )

            metadata = _merge_sheet_metadata(project_data, scene, layout_name, sheet.element_id, sheet.metadata)
            metadata["view_center"] = vp_info.get("view_center")
            metadata["view_height"] = vp_info.get("view_height")
            metadata["sheet_index"] = i
            metadata["total_sheets"] = total_sheets

            # Calcul automatique de l'echelle: zone papier / hauteur modele
            drawing_zone_h = max(1.0, config.paper_height_mm - 2.0 * config.page_margin_mm - titleblock_height)
            vh = vp_info.get("view_height") or 0
            if vh and float(vh) > 0:
                ratio = float(vh) / float(drawing_zone_h)
                if ratio <= 1.05:
                    scale_str = "1:1"
                else:
                    n = round(ratio)
                    scale_str = f"1:{n}"
            else:
                scale_str = metadata.get("scale", "1:1")
            metadata["scale"] = scale_str

            add_title_block(
                layout,
                paper_w=config.paper_width_mm,
                paper_h=config.paper_height_mm,
                margin=config.page_margin_mm,
                metadata=metadata,
                logo_path=project_data.get("logo_path", "logo.png"),
                text_height=max(3.0, config.text_height),
                titleblock_height=titleblock_height,
            )

            # ---------------------------------------------------------------
            # DECALAGE FIXE : tout le contenu PaperSpace décalé de -10mm en X et -10mm en Y
            # ---------------------------------------------------------------
            from .centering import get_printable_entities, translate_entity
            for _ent in get_printable_entities(layout):
                translate_entity(_ent, -10.0, -10.0)
            logs.append(f"SHIFT[{layout_name}] -10mm X, -10mm Y applied")

            logs.append(
                f"LAYOUT_OK[{layout_name}] element_id={sheet.element_id} bbox={bbox} view_center={vp_info.get('view_center')} draw_zone={zone_info.get('draw_zone')}"
            )

            safe_gap = max(2000.0, float(config.model_gap_mm))
            x_cursor = float(bbox[2]) + safe_gap

            global_min_x = bbox[0] if global_min_x is None else min(global_min_x, bbox[0])
            global_min_y = bbox[1] if global_min_y is None else min(global_min_y, bbox[1])
            global_max_x = bbox[2] if global_max_x is None else max(global_max_x, bbox[2])
            global_max_y = bbox[3] if global_max_y is None else max(global_max_y, bbox[3])

        if global_min_x is not None:
            doc.header["$EXTMIN"] = (float(global_min_x), float(global_min_y), 0.0)
            doc.header["$EXTMAX"] = (float(global_max_x), float(global_max_y), 0.0)
            doc.header["$LIMMIN"] = (0.0, 0.0)
            doc.header["$LIMMAX"] = (float(config.paper_width_mm), float(config.paper_height_mm))

        ok, report = validate_dxf(doc)
        if not ok:
            return None, False, report

        raw = doc_to_ascii_bytes(doc)
        parsed = ezdxf.read(io.StringIO(raw.decode("ascii")))
        ok2, report2 = validate_dxf(parsed)
        if not ok2:
            return None, False, "Post-read audit failed:\n" + report2

        return raw, True, "Validation DXF OK"

    requested_mode = "editable" if str(mode).lower().startswith("editable") else "cnc"

    raw, ok, report = _render_with_mode(requested_mode, force_primitives_dims)
    if ok:
        if logs:
            report = report + "\n" + "\n".join(logs[:300])
        return ExportResult(ok=True, dxf_bytes=raw, report=report, mode_used=requested_mode)

    fallback_mode = "cnc"
    raw2, ok2, report2 = _render_with_mode(fallback_mode, True)
    if ok2:
        report_out = f"Fallback active ({requested_mode} -> cnc primitives).\nInitial cause:\n{report}\n\nFallback result:\n{report2}"
        if logs:
            report_out += "\n" + "\n".join(logs[:300])
        return ExportResult(ok=True, dxf_bytes=raw2, report=report_out, mode_used=fallback_mode)

    report_out = f"Export DXF failed.\nInitial mode:\n{report}\n\nFallback:\n{report2}"
    if logs:
        report_out += "\n" + "\n".join(logs[:300])
    return ExportResult(ok=False, dxf_bytes=b"", report=report_out, mode_used=fallback_mode)


__all__ = [
    "Scene",
    "validate_dxf",
    "DxfRenderConfig",
    "setup_layout_with_viewport_excluding_titleblock",
    "fit_viewport_to_bbox",
    "export_project_to_dxf",
    "ExportResult",
]
