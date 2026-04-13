"""Démo minimale Streamlit: export DXF robuste en mode CNC ou Editable."""

import streamlit as st

from dxf_export import export_project_to_dxf


st.set_page_config(page_title="DXF Robust Demo", layout="centered")
st.title("Export DXF robuste (AutoCAD)")

mode = st.selectbox(
    "Mode d'export",
    options=["cnc", "editable"],
    format_func=lambda m: "Atelier/CNC (recommandé)" if m == "cnc" else "CAD éditable",
)

sheet_count = st.slider("Nombre de feuilles", min_value=1, max_value=10, value=3)

sheet_data = []
for idx in range(sheet_count):
    sheet_data.append(
        {
            "title": f"Feuille_{idx+1}",
            "width": 400.0 + idx * 10.0,
            "height": 300.0,
            "legend": "Text only (pas MTEXT), noms ASCII, DXF R2010",
        }
    )

try:
    fake_cabinets = [{"dims": {"L_raw": s["width"], "W_raw": s["height"]}, "name": s["title"]} for s in sheet_data]
    result = export_project_to_dxf(
        {"cabinets_data": fake_cabinets, "project_name": "DXF_DEMO"},
        mode=mode,
        force_primitives_dims=(mode != "editable"),
        debug=False,
    )
    if not result.ok:
        raise RuntimeError(result.report)
    dxf_bytes = result.dxf_bytes
    st.success("DXF valide généré avec audit ezdxf.")
    st.download_button(
        label="Télécharger DXF",
        data=dxf_bytes,
        file_name=f"demo_{mode}.dxf",
        mime="application/dxf",
        use_container_width=True,
    )
except Exception as exc:
    st.error(f"Export refusé: {exc}")
