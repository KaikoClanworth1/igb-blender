"""Menu IGB loader — extracts geometry, textures and transforms from menu IGBs.

Provides two levels of extraction:
1. Texture-only: extracts DXT3 textures to PNG for thumbnails
2. Full scene: extracts geometry instances with world transforms + textures
   for rendering a visual 16:9 preview of the menu as it appears in-game

Uses the existing IGB parser + scene graph walker + DXT3 decompressor.
"""

import struct
from pathlib import Path

from .hud_extract import write_png


def get_menu_cache_dir():
    """Get the menu texture cache directory path (alongside this module)."""
    return Path(__file__).parent / "menu_cache"


def extract_igb_textures(igb_path, cache_dir=None):
    """Extract all textures from an IGB file to PNG.

    Args:
        igb_path: Path to an IGB file
        cache_dir: Directory to write PNGs to (default: menu_cache/)

    Returns:
        list of (name, png_path, width, height) tuples, or empty list on failure
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject
    from ..scene_graph.sg_materials import extract_image
    from ..utils.image_convert import convert_image_to_rgba

    igb_path = Path(igb_path)
    if cache_dir is None:
        cache_dir = get_menu_cache_dir()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    stem = igb_path.stem
    results = []

    try:
        reader = IGBReader(str(igb_path))
        reader.read()
    except Exception:
        return results

    # Walk all igImage objects in the file
    tex_index = 0
    for obj in reader.objects:
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igImage"):
            continue

        out_path = cache_dir / f"{stem}_{tex_index}.png"

        # Skip if already cached
        if out_path.exists():
            # Still need width/height — parse image header only
            parsed_img = extract_image(reader, obj)
            if parsed_img is not None and parsed_img.width > 0 and parsed_img.height > 0:
                # Get original name from the image if available
                img_name = getattr(parsed_img, 'name', '') or f"{stem}_tex{tex_index}"
                results.append((img_name, str(out_path), parsed_img.width, parsed_img.height))
                tex_index += 1
            continue

        parsed_img = extract_image(reader, obj)
        if parsed_img is None or parsed_img.pixel_data is None:
            continue
        if parsed_img.width == 0 or parsed_img.height == 0:
            continue

        rgba = convert_image_to_rgba(parsed_img)
        if rgba is None:
            continue

        write_png(rgba, parsed_img.width, parsed_img.height, str(out_path))

        img_name = getattr(parsed_img, 'name', '') or f"{stem}_tex{tex_index}"
        results.append((img_name, str(out_path), parsed_img.width, parsed_img.height))
        tex_index += 1

    return results


def resolve_igb_path(ref_path, game_dir):
    """Resolve a menu IGB reference to an absolute file path.

    Handles two reference styles:
    - igb="x2m_main"  → {game_dir}/ui/menus/x2m_main.igb
    - model="ui/models/m_pda_option_light_on" → {game_dir}/ui/models/m_pda_option_light_on.igb

    Args:
        ref_path: IGB reference string from the menu XML
        game_dir: Root game data directory

    Returns:
        Path if file exists, None otherwise
    """
    game_dir = Path(game_dir)

    # If the ref already contains a directory separator, treat as relative path
    if '/' in ref_path or '\\' in ref_path:
        candidate = game_dir / (ref_path.replace('\\', '/') + '.igb')
        if candidate.exists():
            return candidate
        return None

    # Bare name (igb= attribute) — look in ui/menus/
    candidate = game_dir / 'ui' / 'menus' / f'{ref_path}.igb'
    if candidate.exists():
        return candidate

    return None


def load_menu_textures(root_element, game_dir, cache_dir=None):
    """Load all textures referenced by a menu XML tree.

    Extracts textures from:
    1. The background IGB (MENU igb= attribute)
    2. All model IGBs referenced by item[@model] elements

    Args:
        root_element: ET.Element tree (parsed from ENGB via read_xmlb)
        game_dir: Root game data directory
        cache_dir: Cache directory for PNGs (default: menu_cache/)

    Returns:
        dict mapping igb_stem → list of (name, png_path, width, height)
    """
    if cache_dir is None:
        cache_dir = get_menu_cache_dir()
    cache_dir = Path(cache_dir)

    results = {}

    # 1. Background IGB from MENU root
    igb_ref = root_element.attrib.get('igb', '')
    if igb_ref:
        igb_path = resolve_igb_path(igb_ref, game_dir)
        if igb_path is not None:
            textures = extract_igb_textures(igb_path, cache_dir)
            if textures:
                results[igb_path.stem] = textures

    # 2. Model IGBs from item elements
    seen_models = set()
    for item in root_element.iter('item'):
        model_ref = item.attrib.get('model', '')
        if not model_ref or model_ref in seen_models:
            continue
        seen_models.add(model_ref)

        igb_path = resolve_igb_path(model_ref, game_dir)
        if igb_path is not None:
            textures = extract_igb_textures(igb_path, cache_dir)
            if textures:
                results[igb_path.stem] = textures

    # 3. Model IGBs from onfocus elements
    for onfocus in root_element.iter('onfocus'):
        model_ref = onfocus.attrib.get('model', '')
        if not model_ref or model_ref in seen_models:
            continue
        seen_models.add(model_ref)

        igb_path = resolve_igb_path(model_ref, game_dir)
        if igb_path is not None:
            textures = extract_igb_textures(igb_path, cache_dir)
            if textures:
                results[igb_path.stem] = textures

    # 4. External items files
    for items_elem in root_element.iter('items'):
        items_igb = items_elem.attrib.get('igb', '')
        if items_igb and items_igb not in seen_models:
            seen_models.add(items_igb)
            igb_path = resolve_igb_path(items_igb, game_dir)
            if igb_path is not None:
                textures = extract_igb_textures(igb_path, cache_dir)
                if textures:
                    results[igb_path.stem] = textures

    return results


# ---------------------------------------------------------------------------
# Layout IGB extraction — named transforms for positioning model geometry
# ---------------------------------------------------------------------------

def extract_layout_transforms(igb_path):
    """Extract named transforms from a layout IGB file.

    Menu layout IGBs (like x2m_main.igb) contain igTransform nodes with
    names matching ENGB item names (e.g. 'option04', 'logo', 'track01')
    and 4x4 matrices defining where each element is positioned.

    The scene graph walker accumulates transforms down the hierarchy,
    giving us world-space positions for each named node.

    Args:
        igb_path: Path to layout IGB file

    Returns:
        dict mapping node_name -> accumulated 4x4 matrix (tuple of 16 floats),
        or empty dict on failure.
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject
    from ..scene_graph.sg_classes import SceneGraph

    igb_path = Path(igb_path)
    try:
        reader = IGBReader(str(igb_path))
        reader.read()
    except Exception:
        return {}

    sg = SceneGraph(reader)
    sg.build()

    # Collector that captures named transforms
    class _TransformCollector:
        def __init__(self):
            self.named_transforms = {}

        def visit_transform(self, obj, accumulated_transform):
            # igTransform name is in slot 2 (String field from igNamedObject)
            name = ""
            for slot, val, fi in obj._raw_fields:
                if fi.short_name == b"String":
                    name = val
                    break
            if name and accumulated_transform is not None:
                self.named_transforms[name] = accumulated_transform

        # Required methods for walker (no-ops since we only want transforms)
        def visit_geometry_attr(self, attr, transform, parent):
            pass
        def visit_material_attr(self, attr, parent):
            pass
        def visit_texture_bind_attr(self, attr, parent):
            pass

    collector = _TransformCollector()
    sg.walk(collector)

    return collector.named_transforms


def _multiply_matrices(a, b):
    """Multiply two 4x4 row-major matrices. a and b are 16-float tuples."""
    result = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row * 4 + k] * b[k * 4 + col]
            result[row * 4 + col] = s
    return tuple(result)


# ---------------------------------------------------------------------------
# Full scene extraction — geometry + transforms + textures for 2D rendering
# ---------------------------------------------------------------------------

class MenuSceneInstance:
    """A single geometry instance from the menu IGB scene graph."""
    __slots__ = ('positions', 'uvs', 'colors', 'indices',
                 'transform', 'texture_png', 'tex_width', 'tex_height',
                 'diffuse')

    def __init__(self):
        self.positions = []   # list of (x, y, z) in local space
        self.uvs = []         # list of (u, v)
        self.colors = []      # list of (r, g, b, a) 0.0-1.0
        self.indices = []     # triangle list indices
        self.transform = None # 4x4 row-major tuple(16 floats) or None
        self.texture_png = None  # path to cached PNG
        self.tex_width = 0
        self.tex_height = 0
        self.diffuse = (1.0, 1.0, 1.0, 1.0)


def _transform_point(p, mat):
    """Transform a 3D point by a 4x4 row-major matrix.

    For row-major Alchemy matrices, point * matrix gives world coords.
    p is (x,y,z), mat is tuple of 16 floats.
    """
    if mat is None:
        return p
    x, y, z = p
    wx = x * mat[0] + y * mat[4] + z * mat[8]  + mat[12]
    wy = x * mat[1] + y * mat[5] + z * mat[9]  + mat[13]
    wz = x * mat[2] + y * mat[6] + z * mat[10] + mat[14]
    return (wx, wy, wz)


def extract_menu_scene(igb_path, cache_dir=None):
    """Extract full scene geometry from a menu IGB for 2D rendering.

    Returns a list of MenuSceneInstance objects, each containing
    world-space triangulated geometry with texture references.

    Args:
        igb_path: Path to menu IGB file
        cache_dir: Directory for cached texture PNGs

    Returns:
        list of MenuSceneInstance, or empty list on failure
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject
    from ..scene_graph.sg_classes import SceneGraph
    from ..scene_graph.sg_geometry import extract_geometry
    from ..scene_graph.sg_materials import (
        extract_material, extract_texture_bind, extract_image
    )
    from ..utils.image_convert import convert_image_to_rgba
    from ..game_profiles import detect_profile

    igb_path = Path(igb_path)
    if cache_dir is None:
        cache_dir = get_menu_cache_dir()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        reader = IGBReader(str(igb_path))
        reader.read()
    except Exception:
        return []

    profile = detect_profile(reader)

    # Build scene graph
    sg = SceneGraph(reader)
    sg.build()

    # Lightweight geometry collector (avoids bpy dependency)
    class _Collector:
        def __init__(self):
            self.instances = []
            self._mat = None
            self._texbind = None

        def visit_material_attr(self, attr, parent):
            self._mat = attr

        def visit_texture_bind_attr(self, attr, parent):
            self._texbind = attr

        def visit_geometry_attr(self, attr, transform, parent):
            self.instances.append((
                attr, transform,
                self._mat, self._texbind,
            ))

    collector = _Collector()
    sg.walk(collector)

    # Cache for texture PNGs (image obj index → png path)
    tex_cache = {}
    stem = igb_path.stem

    results = []
    for attr, transform, mat_obj, texbind_obj in collector.instances:
        inst = MenuSceneInstance()
        inst.transform = transform

        # Extract geometry
        try:
            geom = extract_geometry(reader, attr, profile)
        except Exception:
            continue
        if geom is None or not geom.positions:
            continue

        # Triangulate strips — triangulate() returns new indices, does NOT modify in place
        tri_indices = geom.triangulate()

        inst.positions = geom.positions
        inst.uvs = geom.uvs if geom.uvs else []
        inst.colors = geom.colors if geom.colors else []
        inst.indices = tri_indices

        # Extract material color
        if mat_obj is not None:
            try:
                parsed_mat = extract_material(reader, mat_obj)
                if parsed_mat and parsed_mat.diffuse:
                    inst.diffuse = parsed_mat.diffuse
            except Exception:
                pass

        # Extract texture
        if texbind_obj is not None:
            try:
                parsed_tex = extract_texture_bind(reader, texbind_obj)
                if parsed_tex and parsed_tex.image_obj is not None:
                    img_idx = parsed_tex.image_obj.index
                    if img_idx in tex_cache:
                        inst.texture_png = tex_cache[img_idx]
                    else:
                        parsed_img = extract_image(reader, parsed_tex.image_obj)
                        if (parsed_img and parsed_img.pixel_data
                                and parsed_img.width > 0 and parsed_img.height > 0):
                            rgba = convert_image_to_rgba(parsed_img)
                            if rgba is not None:
                                png_path = cache_dir / f"{stem}_t{img_idx}.png"
                                if not png_path.exists():
                                    write_png(rgba, parsed_img.width,
                                              parsed_img.height, str(png_path))
                                inst.texture_png = str(png_path)
                                inst.tex_width = parsed_img.width
                                inst.tex_height = parsed_img.height
                                tex_cache[img_idx] = str(png_path)
                    if inst.texture_png and img_idx in tex_cache:
                        # Get dimensions if we haven't already
                        if inst.tex_width == 0:
                            parsed_img = extract_image(
                                reader, parsed_tex.image_obj)
                            if parsed_img:
                                inst.tex_width = parsed_img.width
                                inst.tex_height = parsed_img.height
            except Exception:
                pass

        results.append(inst)

    return results
