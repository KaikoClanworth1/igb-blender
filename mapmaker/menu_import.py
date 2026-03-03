"""Menu import pipeline — ENGB + IGB → Blender scene.

Reads an ENGB menu file, resolves the layout IGB, imports background
geometry, and creates Empty objects for each menu item positioned at
their igTransform location.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy
from mathutils import Matrix

from .menu_igb_loader import (
    extract_layout_transforms,
    resolve_igb_path,
)


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

COLL_ROOT = "[MenuEditor]"
COLL_BG = "[MenuEditor] Background"
COLL_ITEMS = "[MenuEditor] Items"


def _get_or_create_collection(name, parent=None):
    """Get or create a collection, linking it to the parent."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    coll = bpy.data.collections.new(name)
    if parent is None:
        parent = bpy.context.scene.collection
    parent.children.link(coll)
    return coll


def _clear_collection(name):
    """Remove all objects from a collection if it exists."""
    if name not in bpy.data.collections:
        return
    coll = bpy.data.collections[name]
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def clear_menu_scene():
    """Remove all menu editor collections and objects."""
    for coll_name in (COLL_ITEMS, COLL_BG, COLL_ROOT):
        _clear_collection(coll_name)
        if coll_name in bpy.data.collections:
            coll = bpy.data.collections[coll_name]
            bpy.data.collections.remove(coll)


# ---------------------------------------------------------------------------
# XML ↔ JSON serialization (for lossless ENGB storage)
# ---------------------------------------------------------------------------

def element_to_dict(elem):
    """Convert ET.Element tree to a JSON-serializable dict."""
    d = {
        '_tag': elem.tag,
        '_attrib': dict(elem.attrib),
        '_children': [element_to_dict(child) for child in elem],
    }
    if elem.text and elem.text.strip():
        d['_text'] = elem.text
    if elem.tail and elem.tail.strip():
        d['_tail'] = elem.tail
    return d


def dict_to_element(d):
    """Convert a dict back to ET.Element tree."""
    elem = ET.Element(d['_tag'], d.get('_attrib', {}))
    if '_text' in d:
        elem.text = d['_text']
    if '_tail' in d:
        elem.tail = d['_tail']
    for child_d in d.get('_children', []):
        elem.append(dict_to_element(child_d))
    return elem


def element_to_json(elem):
    """Serialize ET.Element tree to JSON string."""
    return json.dumps(element_to_dict(elem), ensure_ascii=False)


def json_to_element(json_str):
    """Deserialize JSON string to ET.Element tree."""
    return dict_to_element(json.loads(json_str))


# ---------------------------------------------------------------------------
# Transform conversion
# ---------------------------------------------------------------------------

def igb_transform_to_blender(mat_16):
    """Convert row-major Alchemy 4x4 to Blender Matrix.

    Alchemy: row-major, translation in last row [12,13,14]
    Blender: column-major (mathutils.Matrix), translation in last column
    """
    return Matrix((
        (mat_16[0], mat_16[4], mat_16[8],  mat_16[12]),
        (mat_16[1], mat_16[5], mat_16[9],  mat_16[13]),
        (mat_16[2], mat_16[6], mat_16[10], mat_16[14]),
        (mat_16[3], mat_16[7], mat_16[11], mat_16[15]),
    ))


def blender_to_igb_transform(matrix):
    """Convert Blender Matrix to row-major Alchemy 4x4 tuple."""
    m = matrix.transposed()
    return tuple(m[i][j] for i in range(4) for j in range(4))


# ---------------------------------------------------------------------------
# Empty display configuration
# ---------------------------------------------------------------------------

def _configure_empty(empty, item_elem):
    """Set visual properties on an Empty based on item attributes."""
    empty.empty_display_type = 'PLAIN_AXES'
    empty.empty_display_size = 8.0

    item_type = item_elem.attrib.get('type', '')
    neverfocus = item_elem.attrib.get('neverfocus', '').lower() == 'true'
    startactive = item_elem.attrib.get('startactive', '').lower() == 'true'
    debug = item_elem.attrib.get('debug', '').lower() == 'true'
    has_nav = any(item_elem.attrib.get(d, '').strip()
                  for d in ('up', 'down', 'left', 'right'))
    has_cmd = bool(item_elem.attrib.get('usecmd', ''))

    if startactive:
        empty.color = (0.2, 0.9, 0.2, 1.0)     # green = start active
        empty.empty_display_type = 'ARROWS'
        empty.empty_display_size = 12.0
    elif debug:
        empty.color = (0.5, 0.5, 0.5, 0.5)     # dim gray = debug
    elif has_cmd or has_nav:
        empty.color = (0.9, 0.75, 0.25, 1.0)    # gold = selectable
        empty.empty_display_type = 'ARROWS'
    elif item_type == 'MENU_ITEM_MODEL':
        empty.color = (0.25, 0.56, 0.88, 1.0)   # blue = model
        empty.empty_display_type = 'CUBE'
    elif neverfocus:
        empty.color = (0.6, 0.6, 0.6, 0.7)     # gray = never focus
    else:
        empty.color = (0.5, 0.7, 0.9, 0.8)     # light blue = text/other


# ---------------------------------------------------------------------------
# Populate PropertyGroup from XML element
# ---------------------------------------------------------------------------

# Standard attrs that map directly to PropertyGroup fields
_STANDARD_ATTRS = {
    'name', 'type', 'model', 'text', 'style', 'usecmd',
    'up', 'down', 'left', 'right',
    'neverfocus', 'startactive', 'animate', 'debug',
    'animtext_scene', 'mode',
}

# Pre-built sets for enum validation (avoids crash on unknown values)
_VALID_ITEM_TYPES = None
_VALID_ONFOCUS_TYPES = None
_VALID_PRECACHE_TYPES = None


def _ensure_enum_sets():
    """Lazily build enum validation sets from menu_properties."""
    global _VALID_ITEM_TYPES, _VALID_ONFOCUS_TYPES, _VALID_PRECACHE_TYPES
    if _VALID_ITEM_TYPES is not None:
        return
    from . import menu_properties
    _VALID_ITEM_TYPES = {t[0] for t in menu_properties.MENU_ITEM_TYPES}
    _VALID_ONFOCUS_TYPES = {t[0] for t in menu_properties.ONFOCUS_TYPES}
    _VALID_PRECACHE_TYPES = {t[0] for t in menu_properties.PRECACHE_TYPES}


def _populate_item_from_element(pg_item, elem):
    """Fill a ME_Item PropertyGroup from an ET.Element."""
    _ensure_enum_sets()

    pg_item.item_name = elem.attrib.get('name', '')

    # Validate enum value before setting (Blender raises TypeError on unknown enums)
    raw_type = elem.attrib.get('type', '')
    pg_item.item_type = raw_type if raw_type in _VALID_ITEM_TYPES else 'NONE'

    pg_item.model_ref = elem.attrib.get('model', '')
    pg_item.text = elem.attrib.get('text', '')
    pg_item.style = elem.attrib.get('style', '')
    pg_item.usecmd = elem.attrib.get('usecmd', '')

    pg_item.nav_up = elem.attrib.get('up', '')
    pg_item.nav_down = elem.attrib.get('down', '')
    pg_item.nav_left = elem.attrib.get('left', '')
    pg_item.nav_right = elem.attrib.get('right', '')

    pg_item.neverfocus = elem.attrib.get('neverfocus', '').lower() == 'true'
    pg_item.startactive = elem.attrib.get('startactive', '').lower() == 'true'
    pg_item.animate = elem.attrib.get('animate', '').lower() == 'true'
    pg_item.debug_only = elem.attrib.get('debug', '').lower() == 'true'

    pg_item.animtext_scene = elem.attrib.get('animtext_scene', '')
    pg_item.mode = elem.attrib.get('mode', '')

    # Extra attrs — clear first, then populate
    pg_item.extra_attrs.clear()

    # Preserve unknown type as extra attr so it round-trips
    if raw_type and raw_type not in _VALID_ITEM_TYPES:
        ea = pg_item.extra_attrs.add()
        ea.key = 'type'
        ea.value = raw_type

    # Other non-standard attrs
    for k, v in elem.attrib.items():
        if k not in _STANDARD_ATTRS and not k.startswith('_editor'):
            ea = pg_item.extra_attrs.add()
            ea.key = k
            ea.value = v

    # OnFocus entries
    pg_item.onfocus_entries.clear()
    for of_elem in elem.iter('onfocus'):
        entry = pg_item.onfocus_entries.add()
        raw_ft = of_elem.attrib.get('type', 'focus')
        entry.focus_type = raw_ft if raw_ft in _VALID_ONFOCUS_TYPES else 'focus'
        entry.target_item = of_elem.attrib.get('item', '')
        entry.model_ref = of_elem.attrib.get('model', '')
        entry.loop_value = of_elem.attrib.get('loop', '')


# ---------------------------------------------------------------------------
# IGB geometry import (lightweight, reuses existing importer)
# ---------------------------------------------------------------------------

def _import_igb_to_collection(igb_path, collection, context, basename_prefix=""):
    """Import IGB geometry with materials/textures into a specific collection.

    Uses the same pipeline as the main IGB importer (GeometryCollector →
    extract → build) but places results into the given collection.

    Returns list of created objects.
    """
    from ..igb_format.igb_reader import IGBReader
    from ..scene_graph.sg_classes import SceneGraph
    from ..scene_graph.sg_geometry import extract_geometry
    from ..scene_graph.sg_materials import (
        extract_material, extract_texture_bind,
        extract_blend_state, extract_blend_function,
        extract_alpha_state, extract_alpha_function,
        extract_color_attr, extract_cull_face,
    )
    from ..importer.mesh_builder import build_mesh, _tuple_to_matrix
    from ..importer.material_builder import build_material
    from ..game_profiles import detect_profile, get_profile

    igb_path = str(igb_path)
    try:
        reader = IGBReader(igb_path)
        reader.read()
    except Exception:
        return []

    profile = detect_profile(reader)
    if profile is None:
        profile = get_profile("xml2_pc")

    sg = SceneGraph(reader)
    if not sg.build():
        return []

    # Collector that tracks material/texture state (same as GeometryCollector)
    class _Collector:
        def __init__(self):
            self.instances = []
            self._mat = None
            self._tex = None
            self._blend_state = None
            self._blend_func = None
            self._alpha_state = None
            self._alpha_func = None
            self._color = None
            self._cull_face = None

        def visit_material_attr(self, attr, parent):
            self._mat = attr

        def visit_texture_bind_attr(self, attr, parent):
            self._tex = attr

        def visit_blend_state_attr(self, attr, parent):
            self._blend_state = attr

        def visit_blend_function_attr(self, attr, parent):
            self._blend_func = attr

        def visit_alpha_state_attr(self, attr, parent):
            self._alpha_state = attr

        def visit_alpha_function_attr(self, attr, parent):
            self._alpha_func = attr

        def visit_color_attr(self, attr, parent):
            self._color = attr

        def visit_cull_face_attr(self, attr, parent):
            self._cull_face = attr

        def visit_geometry_attr(self, attr, transform, parent):
            self.instances.append((attr.index, transform, {
                'material_obj': self._mat,
                'texbind_obj': self._tex,
                'blend_state_obj': self._blend_state,
                'blend_func_obj': self._blend_func,
                'alpha_state_obj': self._alpha_state,
                'alpha_func_obj': self._alpha_func,
                'color_obj': self._color,
                'cull_face_obj': self._cull_face,
            }))

    collector = _Collector()
    sg.walk(collector)

    if not collector.instances:
        return []

    created = []
    stem = Path(igb_path).stem
    prefix = f"{basename_prefix}{stem}" if basename_prefix else stem

    for i, (attr_index, transform, state) in enumerate(collector.instances):
        attr_obj = reader.objects[attr_index]
        try:
            geom = extract_geometry(reader, attr_obj, profile)
        except Exception:
            continue
        if geom is None or geom.num_verts == 0:
            continue

        obj_name = f"{prefix}_{i:03d}"
        # build_mesh returns a bpy.types.Object (creates mesh + object)
        obj = build_mesh(geom, obj_name, transform=None, profile=profile)
        if obj is None:
            continue

        # Build material + texture
        mat_obj = state.get('material_obj')
        tex_obj = state.get('texbind_obj')
        if mat_obj is not None:
            try:
                parsed_mat = extract_material(reader, mat_obj, profile)
                parsed_tex = None
                if tex_obj is not None:
                    parsed_tex = extract_texture_bind(reader, tex_obj, profile)

                extra_state = {}
                if state.get('blend_state_obj'):
                    extra_state['blend_state'] = extract_blend_state(
                        reader, state['blend_state_obj'], profile)
                if state.get('blend_func_obj'):
                    extra_state['blend_func'] = extract_blend_function(
                        reader, state['blend_func_obj'], profile)
                if state.get('alpha_state_obj'):
                    extra_state['alpha_state'] = extract_alpha_state(
                        reader, state['alpha_state_obj'], profile)
                if state.get('alpha_func_obj'):
                    extra_state['alpha_func'] = extract_alpha_function(
                        reader, state['alpha_func_obj'], profile)
                if state.get('color_obj'):
                    extra_state['color'] = extract_color_attr(
                        reader, state['color_obj'], profile)
                if state.get('cull_face_obj'):
                    extra_state['cull_face'] = extract_cull_face(
                        reader, state['cull_face_obj'], profile)

                bl_mat = build_material(
                    parsed_mat, parsed_tex,
                    extra_state=extra_state or None,
                    name=obj_name,
                    profile=profile,
                )
                if bl_mat is not None:
                    obj.data.materials.append(bl_mat)
            except Exception:
                pass  # geometry still usable without material

        # Move object from wherever build_mesh linked it to our collection
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        collection.objects.link(obj)

        if transform is not None:
            obj.matrix_world = _tuple_to_matrix(transform)

        created.append(obj)

    return created


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

def import_menu(context, engb_path, game_dir):
    """Import a menu ENGB + its layout IGB into the Blender scene.

    Args:
        context: Blender context
        engb_path: path to the .engb file
        game_dir: root game data directory

    Returns:
        (success: bool, message: str)
    """
    from .xmlb import read_xmlb

    scene = context.scene

    # 1. Parse ENGB
    try:
        root = read_xmlb(Path(engb_path))
    except Exception as e:
        return False, f"Failed to parse ENGB: {e}"

    # 2. Clear any previous menu editor state
    clear_menu_scene()
    scene.menu_items.clear()
    scene.menu_animtexts.clear()
    scene.menu_precaches.clear()

    # 3. Store settings
    settings = scene.menu_settings
    settings.engb_path = engb_path
    settings.game_dir = game_dir
    settings.igb_ref = root.attrib.get('igb', '')
    settings.engb_json = element_to_json(root)

    # Try to set menu_type enum — only if the value is valid
    _ensure_enum_sets()
    mt = root.attrib.get('type', '')
    from . import menu_properties
    valid_menu_types = {t[0] for t in menu_properties.MENU_TYPES}
    settings.menu_type = mt if mt in valid_menu_types else 'NONE'

    # 4. Create collections
    root_coll = _get_or_create_collection(COLL_ROOT)
    bg_coll = _get_or_create_collection(COLL_BG, root_coll)
    items_coll = _get_or_create_collection(COLL_ITEMS, root_coll)

    # 5. Resolve layout IGB and extract named transforms
    named_transforms = {}
    igb_path_resolved = None
    if settings.igb_ref and game_dir:
        igb_path_resolved = resolve_igb_path(settings.igb_ref, game_dir)
        if igb_path_resolved is not None:
            named_transforms = extract_layout_transforms(str(igb_path_resolved))

    # 6. Import background geometry from layout IGB
    if igb_path_resolved is not None and settings.show_background:
        bg_objs = _import_igb_to_collection(igb_path_resolved, bg_coll, context)
        # Make background objects non-selectable
        for obj in bg_objs:
            obj.hide_select = True

    # 7. Detect template transforms (duplicated positions) for smart placement
    def _pos_key(mat):
        return (round(mat[12], 1), round(mat[13], 1), round(mat[14], 1))

    pos_counts = {}
    for name, mat in named_transforms.items():
        pk = _pos_key(mat)
        pos_counts[pk] = pos_counts.get(pk, 0) + 1

    # 8. Create item Empties and populate PropertyGroups
    fallback_z = 0.0
    item_count = 0

    for item_elem in root.iter('item'):
        item_name = item_elem.attrib.get('name', '')
        if not item_name:
            continue

        # Create Empty
        empty = bpy.data.objects.new(item_name, None)
        items_coll.objects.link(empty)
        empty["menu_item_name"] = item_name

        # Determine position
        has_transform = False
        placement_mat = None

        # Direct name match
        if item_name in named_transforms:
            mat = named_transforms[item_name]
            pk = _pos_key(mat)
            # Use directly if position isn't overly-shared (template positions)
            if pos_counts.get(pk, 0) <= 2:
                placement_mat = mat

        # Try label_ prefix match
        if placement_mat is None:
            label_name = f"label_{item_name}"
            if label_name in named_transforms:
                placement_mat = named_transforms[label_name]

        # Fall back to direct name even if shared
        if placement_mat is None and item_name in named_transforms:
            placement_mat = named_transforms[item_name]

        if placement_mat is not None:
            empty.matrix_world = igb_transform_to_blender(placement_mat)
            has_transform = True
        else:
            # No transform found — stack vertically
            empty.location = (0.0, 0.0, fallback_z)
            fallback_z -= 30.0

        # Configure visual style
        _configure_empty(empty, item_elem)

        # Import item model as child (optional)
        model_ref = item_elem.attrib.get('model', '')
        if model_ref and settings.show_models and game_dir:
            model_igb = resolve_igb_path(model_ref, game_dir)
            if model_igb is not None:
                model_objs = _import_igb_to_collection(
                    model_igb, items_coll, context,
                    basename_prefix=f"{item_name}_",
                )
                for mobj in model_objs:
                    mobj.parent = empty
                    mobj.hide_select = True

        # Populate PropertyGroup
        pg_item = scene.menu_items.add()
        _populate_item_from_element(pg_item, item_elem)
        pg_item.object_name = empty.name
        pg_item.has_transform = has_transform
        empty["menu_item_index"] = len(scene.menu_items) - 1

        item_count += 1

    # 9. Parse animtext entries
    for at_elem in root.iter('animtext'):
        pg_at = scene.menu_animtexts.add()
        pg_at.anim_name = at_elem.attrib.get('name', '')
        for mark in at_elem.iter('mark'):
            pg_mark = pg_at.marks.add()
            try:
                pg_mark.alpha = float(mark.attrib.get('alpha', '0'))
            except ValueError:
                pg_mark.alpha = 0.0
            try:
                pg_mark.time = float(mark.attrib.get('time', '0'))
            except ValueError:
                pg_mark.time = 0.0

    # 10. Parse precache entries
    for pc_elem in root.iter('precache'):
        pg_pc = scene.menu_precaches.add()
        pg_pc.filename = pc_elem.attrib.get('filename', '')
        pt = pc_elem.attrib.get('type', 'model')
        pg_pc.precache_type = pt if pt in _VALID_PRECACHE_TYPES else 'model'

    settings.is_loaded = True

    return True, f"Loaded {item_count} items from {Path(engb_path).name}"


