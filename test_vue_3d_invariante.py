#!/usr/bin/env python
"""
RÈGLE — La vue 3D du PDF « Vues 3D cotées » est INTOUCHABLE.
============================================================

    La vue isométrique du meuble rendue dans le PDF coté doit rester
    STRICTEMENT identique, au pixel près. Les cotations se déplacent pour
    s'accrocher au meuble ; ce n'est JAMAIS au meuble de bouger pour arranger
    les cotations.

Concrètement, il est interdit de modifier, dans `views_3d_export.py` :

  - `_view_vector`, `_camera_basis`, `_backface_set`  (l'angle de vue)
  - `_fit_affine`, `_make_projector`                  (l'échelle et le cadrage)
  - `_box_faces`, `_add_box`, `_rasterize_scene`      (la géométrie et le rendu)
  - l'appel `ax_img.imshow(...)` de `_render_cabinet` — en particulier son
    paramètre `aspect` : le laisser par défaut ('equal') est ce qui rend l'axe
    carré, donc ce qui rend le repère de données ISOTROPE. C'est dans CE repère
    que la vue est dessinée, et c'est pour ça que les cotations doivent y être
    tracées elles aussi (`ax.transData`) plutôt que sur `fig.transFigure`.
  - les angles `azim=315, elev=22` passés par `_cabinet_page`

Ce test verrouille la règle par une empreinte SHA-256 de la vue rendue : toute
modification, même d'un pixel, le fait échouer. Si un changement de la vue est
un jour VOULU, il faut le décider explicitement et régénérer les empreintes
(voir --regenerate) — jamais les ajuster pour faire passer un test au vert.

Usage :
    python test_vue_3d_invariante.py
    python test_vue_3d_invariante.py --regenerate   # uniquement si voulu
"""
import sys
import hashlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import views_3d_export as V

# Empreintes de référence de la vue 3D (meuble seul, sans cotations).
EMPREINTES = {
    "C1": "384074223bf63e0aa53c849d10ecbba44b00d997f8a3c746841b8376d8f02532",
    "C2": "6a6c5bf080a5b0681ef557cf121eddd654fbd9b8b67aa8242200c032441b738b",
    "C3": "420ac85dc686cf690baaf36614375dad66210cb9b1b09e55cd2eac8f2ff021fb",
}

RECT = (0.30, 0.12, 0.68, 0.82)
AZIM, ELEV = 315, 22

CABS = {
    "C1": dict(  # haute, 2 tablettes, portes doubles
        dims={"L_raw": 800., "W_raw": 400., "H_raw": 1800.,
              "t_lr_raw": 19., "t_fb_raw": 19., "t_tb_raw": 19.},
        shelves=[{"height": 400., "thickness": 19.}, {"height": 900., "thickness": 19.}],
        vertical_dividers=[], drawers=[],
        door_props={"has_door": True, "door_type": "double", "door_gap": 2.,
                    "door_thickness": 19., "door_model": "standard"},
        base_elements={"has_back_panel": True}),
    "C2": dict(  # large et basse, séparateur + tiroir
        dims={"L_raw": 1600., "W_raw": 600., "H_raw": 700.,
              "t_lr_raw": 19., "t_fb_raw": 19., "t_tb_raw": 19.},
        shelves=[{"height": 300., "thickness": 19.}],
        vertical_dividers=[{"position_x": 800., "thickness": 19.}],
        drawers=[{"drawer_gap": 2., "drawer_face_H_raw": 150.,
                  "drawer_face_thickness": 19., "drawer_bottom_offset": 20.}],
        door_props={}, base_elements={"has_back_panel": True}),
    "C3": dict(  # colonne sans tablette, porte sol, sans fond
        dims={"L_raw": 500., "W_raw": 550., "H_raw": 2000.,
              "t_lr_raw": 19., "t_fb_raw": 19., "t_tb_raw": 19.},
        shelves=[], vertical_dividers=[], drawers=[],
        door_props={"has_door": True, "door_type": "single", "door_gap": 2.,
                    "door_thickness": 19., "door_model": "floor_length"},
        base_elements={"has_back_panel": False}),
}


def _rendre(cab, avec_cotations=False):
    """Rend une page comme `_cabinet_page`, et renvoie (image RGB, raster, fig)."""
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    raster = V._render_cabinet(ax, fig, RECT, cab, origin=(0, 0, 0),
                               show_all_dims=True, azim=AZIM, elev=ELEV)
    fig.canvas.draw()
    if avec_cotations:
        V._draw_dims_overlay(fig, cab, origin=(0, 0, 0), raster=raster)
        fig.canvas.draw()
    img = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    return img, raster, fig


def test_vue_3d_inchangee():
    """RÈGLE 1 — la vue isométrique du meuble ne bouge pas d'un pixel."""
    echecs = []
    for nom, cab in CABS.items():
        img, _, fig = _rendre(cab, avec_cotations=False)
        plt.close(fig)
        h = hashlib.sha256(img.tobytes()).hexdigest()
        attendu = EMPREINTES[nom]
        etat = "OK" if h == attendu else "MODIFIEE"
        print(f"  {nom} : vue 3D {etat}")
        if h != attendu:
            echecs.append(f"{nom} : empreinte {h[:16]}… au lieu de {attendu[:16]}…")
    if echecs:
        raise AssertionError(
            "La vue 3D a été modifiée — c'est interdit :\n  " + "\n  ".join(echecs))


def test_cotations_ancrees_sur_le_dessin():
    """RÈGLE 2 — chaque cotation part d'un point réellement dessiné du meuble.

    C'est la contrepartie de la règle 1 : si la vue ne doit pas bouger, ce sont
    les cotations qui doivent la rejoindre. On vérifie que chaque sommet du
    registre, projeté puis converti dans le repère de la PAGE, tombe sur des
    pixels réellement encrés (tolérance 2 pt = 0,7 mm)."""
    TOL_PT = 2.0
    pire_global = 0.0
    for nom, cab in CABS.items():
        img, raster, fig = _rendre(cab, avec_cotations=False)
        ax = raster["ax"]
        inv = fig.transFigure.inverted()
        h, w, _ = img.shape
        encre = np.any(img < 245, axis=-1)
        iy, ix = np.nonzero(encre)
        px_pt = ix / w * 11.69 * 72
        py_pt = (1 - iy / h) * 8.27 * 72

        pire = 0.0
        for v3d in raster["vertices"].values():
            dx, dy = raster["project"](*v3d)
            fx, fy = inv.transform(ax.transData.transform((dx, dy)))
            d = float(np.min(np.hypot(px_pt - fx * 11.69 * 72,
                                      py_pt - fy * 8.27 * 72)))
            pire = max(pire, d)
        plt.close(fig)
        print(f"  {nom} : ecart max ancrage/dessin = {pire:.2f} pt")
        pire_global = max(pire_global, pire)
        assert pire <= TOL_PT, (
            f"{nom} : une cotation s'ancre à {pire:.2f} pt du dessin — "
            f"dessin et cotations n'utilisent plus le même repère.")
    return pire_global


def test_pdf_complet_se_genere():
    """RÈGLE 3 — le PDF complet se génère sans lever (les `_assert_anchored`
    internes de `_draw_dims_overlay` sont autant de contrôles d'ancrage)."""
    cabs = [dict(c, name=n) for n, c in CABS.items()]
    data = V.generate_3d_views_pdf(cabs, project_name="TEST", client="", date_str="")
    assert data[:4] == b"%PDF", "sortie non-PDF"
    print(f"  PDF genere : {len(data)} octets")


def _regenerer():
    print("Regeneration des empreintes (changement de vue VOULU) :")
    for nom, cab in CABS.items():
        img, _, fig = _rendre(cab, avec_cotations=False)
        plt.close(fig)
        print(f'    "{nom}": "{hashlib.sha256(img.tobytes()).hexdigest()}",')


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        _regenerer()
        sys.exit(0)
    print("REGLE 1 - la vue 3D est intouchable")
    test_vue_3d_inchangee()
    print("REGLE 2 - les cotations s'ancrent sur le dessin")
    test_cotations_ancrees_sur_le_dessin()
    print("REGLE 3 - le PDF complet se genere")
    test_pdf_complet_se_genere()
    print("\nTOUTES LES REGLES RESPECTEES")
