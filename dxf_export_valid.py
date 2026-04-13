"""Export DXF via export_manager (maintain existing pipeline)."""

import datetime
from export_manager import generate_stacked_html_plans


def generate_complete_dxf(cabinets_data, project_name, mode="cnc"):
    """Generate DXF using the existing export_manager pipeline."""
    if not cabinets_data:
        return "Pas de caissons à exporter.", False, None

    try:
        dxf_bytes, success = generate_stacked_html_plans(
            cabinets_to_process=cabinets_data,
            indices_to_process=list(range(len(cabinets_data))),
            output_format='dxf'
        )
        
        if not success:
            return dxf_bytes if isinstance(dxf_bytes, bytes) else dxf_bytes.encode('utf-8'), False, None

        safe_name = str(project_name or "Projet").replace(" ", "_")
        filename = f"Plans_{safe_name}_{datetime.date.today().isoformat()}.dxf"
        return dxf_bytes, True, filename
        
    except Exception as exc:
        error_msg = f"Export DXF failed: {exc}"
        return error_msg.encode("utf-8", errors="ignore"), False, None


__all__ = ["generate_complete_dxf"]

