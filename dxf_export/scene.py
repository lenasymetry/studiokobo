"""Scene graph neutre pour rendu Streamlit et DXF.

Inclut un mapping strict 1 layout = 1 element via `SheetSpec`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union
import math


Point = Tuple[float, float]


@dataclass
class HoleFeature:
    x: float
    y: float
    diameter: float
    hole_type: str = "drill"
    depth: Optional[float] = None
    group: Optional[str] = None
    layer: str = "DRILL"


@dataclass
class PolyFeature:
    points: List[Point] = field(default_factory=list)
    layer: str = "CUT"
    kind: str = "poly"


@dataclass
class MarkFeature:
    x: float
    y: float
    text: str = ""
    layer: str = "TEXT"


@dataclass
class PartFeatures:
    holes: List[HoleFeature] = field(default_factory=list)
    slots: List[PolyFeature] = field(default_factory=list)
    grooves: List[PolyFeature] = field(default_factory=list)
    pockets: List[PolyFeature] = field(default_factory=list)
    cutouts: List[PolyFeature] = field(default_factory=list)
    marks: List[MarkFeature] = field(default_factory=list)


@dataclass
class Part:
    part_id: str
    name: str
    length: float
    height: float
    thickness: float
    quantity: int = 1
    material: str = ""
    outline: List[Point] = field(default_factory=list)
    features: PartFeatures = field(default_factory=PartFeatures)
    metadata: Dict = field(default_factory=dict)
    scene: Optional["Scene"] = None


@dataclass
class EntityBase:
    layer: str = "GEOM"
    color: Optional[int] = None
    lineweight: Optional[int] = None
    category: str = "geometry"  # geometry|text|dimension|legend|hatch
    export: bool = True  # Flag: si False, skip dans render_dxf (filtre les rectangles transparents)


@dataclass
class Line(EntityBase):
    start: Point = (0.0, 0.0)
    end: Point = (0.0, 0.0)


@dataclass
class Polyline(EntityBase):
    points: List[Point] = field(default_factory=list)
    closed: bool = False


@dataclass
class Arc(EntityBase):
    center: Point = (0.0, 0.0)
    radius: float = 1.0
    start_angle: float = 0.0
    end_angle: float = 360.0


@dataclass
class Circle(EntityBase):
    center: Point = (0.0, 0.0)
    radius: float = 1.0


@dataclass
class Text(EntityBase):
    text: str = ""
    insert: Point = (0.0, 0.0)
    height: float = 2.5
    align: str = "LEFT"  # LEFT|CENTER
    style: str = "Standard"


@dataclass
class Leader(EntityBase):
    points: List[Point] = field(default_factory=list)
    text: str = ""


@dataclass
class Dimension(EntityBase):
    p1: Point = (0.0, 0.0)
    p2: Point = (0.0, 0.0)
    offset: float = 10.0
    text: Optional[str] = None
    axis: str = "auto"  # auto|x|y
    dimstyle: str = "DIM_STD"
    side: Optional[str] = None  # None|'top'|'bottom'|'left'|'right' — direction du texte de cote
    text_height: Optional[float] = None  # Hauteur texte cote (par défaut 10.0), None=auto


@dataclass
class DiameterDimension(EntityBase):
    """Repère AutoCAD de diamètre, éventuellement enrichi avec profondeur."""
    center: Point = (0.0, 0.0)
    radius: float = 1.0
    angle: float = 45.0        # angle du leader en degrés (0=droite, 45=diag, 135=haut-gauche…)
    text_location: Optional[Point] = None
    dimstyle: str = "POTECH_DIM"
    text_height: Optional[float] = None
    label: Optional[str] = None


@dataclass
class DimensionData:
    p1: Point
    p2: Point
    dim_line_offset: float = 10.0
    side: str = "top"  # top|bottom|left|right
    text_override: Optional[str] = None
    axis: str = "auto"  # auto|x|y
    layer: str = "DIM"
    dimstyle: str = "POTECH_DIM"


@dataclass
class HatchSimple(EntityBase):
    boundary: List[Point] = field(default_factory=list)
    pattern: str = "ANSI31"
    scale: float = 1.0


@dataclass
class BlockDef:
    name: str
    entities: List["SceneEntity"] = field(default_factory=list)


@dataclass
class BlockRef(EntityBase):
    name: str = ""
    insert: Point = (0.0, 0.0)
    xscale: float = 1.0
    yscale: float = 1.0
    rotation: float = 0.0


SceneEntity = Union[Line, Polyline, Arc, Circle, Text, Leader, Dimension, DiameterDimension, HatchSimple, BlockRef]


@dataclass
class Scene:
    name: str
    width: float = 297.0
    height: float = 210.0
    entities: List[SceneEntity] = field(default_factory=list)
    blocks: List[BlockDef] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class PartSpec:
    element_id: str
    scene: Scene
    metadata: Dict = field(default_factory=dict)


@dataclass
class SheetSpec:
    layout_name: str
    element_id: str
    scene: Scene
    metadata: Dict = field(default_factory=dict)


def build_sheets_strict(parts: List[PartSpec]) -> List[SheetSpec]:
    """Build strict mapping SHEET_xx -> unique element_id.

    Lève ValueError si un `element_id` est dupliqué.
    """
    seen: set = set()
    sheets: List[SheetSpec] = []
    for idx, part in enumerate(parts, start=1):
        if not part.element_id:
            raise ValueError("element_id manquant pour une piece")
        if part.element_id in seen:
            raise ValueError(f"element_id duplique: {part.element_id}")
        seen.add(part.element_id)
        layout_name = f"SHEET_{idx:02d}"
        merged_meta = dict(part.scene.meta or {})
        merged_meta.update(part.metadata or {})
        merged_meta["element_id"] = part.element_id
        sheets.append(SheetSpec(layout_name=layout_name, element_id=part.element_id, scene=part.scene, metadata=merged_meta))
    return sheets


def build_sheet_scene(sheet_data) -> Scene:
    """Construit une Scene à partir de données neutres ou d'une figure Plotly.

    Entrées supportées:
    - dict: {title, width, height, entities}
    - dict: {title, figure} où figure est une figure Plotly
    """
    from .render_streamlit import convert_plotly_figure_to_scene

    title = str((sheet_data or {}).get("title") or "SHEET")
    if isinstance(sheet_data, dict) and sheet_data.get("figure") is not None:
        scene = convert_plotly_figure_to_scene(sheet_data["figure"], name=title)
        scene.meta.update({k: v for k, v in sheet_data.items() if k != "figure"})
        return scene

    scene = Scene(name=title, width=float(sheet_data.get("width", 297.0)), height=float(sheet_data.get("height", 210.0)))
    for item in sheet_data.get("entities", []):
        if isinstance(item, (Line, Polyline, Arc, Circle, Text, Leader, Dimension, DiameterDimension, HatchSimple, BlockRef)):
            scene.entities.append(item)
    return scene


def compute_scene_bbox(scene: Scene, offset: Point = (0.0, 0.0), geometry_only: bool = False) -> Optional[Tuple[float, float, float, float]]:
    """Bounding box complète de la scène (mm), utile pour l'auto-fit viewport.
    
    geometry_only=True : inclut seulement les entités géométriques (pas les cotes)
                         pour calculer un centre non biaisé.
    """
    ox, oy = float(offset[0]), float(offset[1])
    min_x = None
    min_y = None
    max_x = None
    max_y = None

    def _add_pt(px, py):
        nonlocal min_x, min_y, max_x, max_y
        x = float(px) + ox
        y = float(py) + oy
        min_x = x if min_x is None else min(min_x, x)
        min_y = y if min_y is None else min(min_y, y)
        max_x = x if max_x is None else max(max_x, x)
        max_y = y if max_y is None else max(max_y, y)

    for ent in scene.entities:
        if isinstance(ent, Line):
            _add_pt(*ent.start)
            _add_pt(*ent.end)
        elif isinstance(ent, Polyline):
            for p in ent.points:
                _add_pt(*p)
        elif isinstance(ent, Arc):
            # borne simple: cercle englobant de l'arc
            cx, cy = ent.center
            r = abs(float(ent.radius))
            _add_pt(cx - r, cy - r)
            _add_pt(cx + r, cy + r)
        elif isinstance(ent, Circle):
            cx, cy = ent.center
            r = abs(float(ent.radius))
            _add_pt(cx - r, cy - r)
            _add_pt(cx + r, cy + r)
        elif isinstance(ent, Text):
            tx, ty = ent.insert
            h = max(1.0, float(ent.height or 2.5))
            w = max(h * 0.6, len(str(ent.text or "")) * h * 0.5)
            if (ent.align or "LEFT").upper() == "CENTER":
                _add_pt(tx - w / 2.0, ty - h / 2.0)
                _add_pt(tx + w / 2.0, ty + h / 2.0)
            else:
                _add_pt(tx, ty - h / 2.0)
                _add_pt(tx + w, ty + h / 2.0)
        elif isinstance(ent, Leader):
            for p in ent.points:
                _add_pt(*p)
        elif not geometry_only and isinstance(ent, Dimension):
            _add_pt(*ent.p1)
            _add_pt(*ent.p2)
            ext = float(ent.offset) + 16.0  # ligne de cote + marge texte
            axis = ent.axis if ent.axis in ("x", "y") else (
                "x" if abs(ent.p2[1] - ent.p1[1]) <= abs(ent.p2[0] - ent.p1[0]) else "y"
            )
            if axis == "x":
                # Cote horizontale : inclure les deux côtés (top ET bottom)
                y_top = max(ent.p1[1], ent.p2[1]) + ext
                y_bot = min(ent.p1[1], ent.p2[1]) - ext
                _add_pt(ent.p1[0], y_top)
                _add_pt(ent.p2[0], y_top)
                _add_pt(ent.p1[0], y_bot)
                _add_pt(ent.p2[0], y_bot)
            else:
                # Cote verticale : inclure les deux côtés (left ET right)
                x_right = max(ent.p1[0], ent.p2[0]) + ext
                x_left = min(ent.p1[0], ent.p2[0]) - ext
                _add_pt(x_right, ent.p1[1])
                _add_pt(x_right, ent.p2[1])
                _add_pt(x_left, ent.p1[1])
                _add_pt(x_left, ent.p2[1])
        elif not geometry_only and isinstance(ent, DiameterDimension):
            cx, cy = ent.center
            r = abs(float(ent.radius))
            _add_pt(cx - r, cy - r)
            _add_pt(cx + r, cy + r)
            if ent.text_location is not None:
                tx, ty = ent.text_location
                ext = max(12.0, (ent.text_height or 10.0) * 2.5)
                _add_pt(tx - ext, ty - ext * 0.5)
                _add_pt(tx + ext, ty + ext * 0.5)
            else:
                ang = math.radians(float(ent.angle))
                ext = r + 24.0
                _add_pt(cx + math.cos(ang) * ext, cy + math.sin(ang) * ext)
        elif not geometry_only and isinstance(ent, HatchSimple):
            for p in ent.boundary:
                _add_pt(*p)
        elif not geometry_only and isinstance(ent, BlockRef):
            _add_pt(*ent.insert)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None

    if math.isclose(min_x, max_x):
        max_x += 1.0
    if math.isclose(min_y, max_y):
        max_y += 1.0
    return (min_x, min_y, max_x, max_y)
