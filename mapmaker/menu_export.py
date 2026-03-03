"""Menu export pipeline — Blender → ENGB + IGB.

Merges edits from PropertyGroups back into the stored ENGB XML tree and
writes the binary ENGB file. Optionally patches igTransform matrices in
the layout IGB for items whose positions were changed.
"""

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy

from .menu_import import (
    json_to_element,
    element_to_json,
    blender_to_igb_transform,
)


# ---------------------------------------------------------------------------
# Sync PropertyGroup edits into XML element tree
# ---------------------------------------------------------------------------

def _sync_item_to_element(pg_item, elem):
    """Write PropertyGroup fields back into an ET.Element."""
    elem.set('name', pg_item.item_name)

    if pg_item.item_type and pg_item.item_type != 'NONE':
        elem.set('type', pg_item.item_type)
    elif 'type' in elem.attrib:
        del elem.attrib['type']

    if pg_item.model_ref:
        elem.set('model', pg_item.model_ref)
    elif 'model' in elem.attrib:
        del elem.attrib['model']

    if pg_item.text:
        elem.set('text', pg_item.text)
    elif 'text' in elem.attrib:
        del elem.attrib['text']

    if pg_item.style:
        elem.set('style', pg_item.style)
    elif 'style' in elem.attrib:
        del elem.attrib['style']

    if pg_item.usecmd:
        elem.set('usecmd', pg_item.usecmd)
    elif 'usecmd' in elem.attrib:
        del elem.attrib['usecmd']

    # Navigation
    for attr, prop in [('up', pg_item.nav_up), ('down', pg_item.nav_down),
                       ('left', pg_item.nav_left), ('right', pg_item.nav_right)]:
        if prop:
            elem.set(attr, prop)
        elif attr in elem.attrib:
            del elem.attrib[attr]

    # Booleans (only write if true)
    for attr, val in [('neverfocus', pg_item.neverfocus),
                      ('startactive', pg_item.startactive),
                      ('animate', pg_item.animate),
                      ('debug', pg_item.debug_only)]:
        if val:
            elem.set(attr, 'true')
        elif attr in elem.attrib:
            del elem.attrib[attr]

    # Other common attrs
    if pg_item.animtext_scene:
        elem.set('animtext_scene', pg_item.animtext_scene)
    elif 'animtext_scene' in elem.attrib:
        del elem.attrib['animtext_scene']

    if pg_item.mode:
        elem.set('mode', pg_item.mode)
    elif 'mode' in elem.attrib:
        del elem.attrib['mode']

    # Extra attrs
    for ea in pg_item.extra_attrs:
        if ea.key:
            elem.set(ea.key, ea.value)

    # Sync onfocus children — rebuild from PropertyGroup
    # First remove existing onfocus children
    for of_child in list(elem):
        if of_child.tag == 'onfocus':
            elem.remove(of_child)

    # Then add from PropertyGroup
    for of_entry in pg_item.onfocus_entries:
        of_elem = ET.SubElement(elem, 'onfocus')
        of_elem.set('type', of_entry.focus_type)
        if of_entry.target_item:
            of_elem.set('item', of_entry.target_item)
        if of_entry.model_ref:
            of_elem.set('model', of_entry.model_ref)
        if of_entry.loop_value:
            of_elem.set('loop', of_entry.loop_value)

    # Store editor position from Blender object
    obj = bpy.data.objects.get(pg_item.object_name)
    if obj is not None:
        elem.set('_editor_x', f'{obj.location.x:.2f}')
        elem.set('_editor_z', f'{obj.location.z:.2f}')


def _sync_animtexts(root, pg_animtexts):
    """Sync animtext entries from PropertyGroups into XML tree."""
    # Remove existing animtext elements
    for at in list(root):
        if at.tag == 'animtext':
            root.remove(at)

    # Re-insert at the top (before items)
    insert_idx = 0
    for pg_at in pg_animtexts:
        at_elem = ET.Element('animtext')
        at_elem.set('name', pg_at.anim_name)
        for pg_mark in pg_at.marks:
            mark = ET.SubElement(at_elem, 'mark')
            mark.set('alpha', str(pg_mark.alpha))
            mark.set('time', str(pg_mark.time))
        root.insert(insert_idx, at_elem)
        insert_idx += 1


def _sync_precaches(root, pg_precaches):
    """Sync precache entries from PropertyGroups into XML tree."""
    # Remove existing precache elements
    for pc in list(root):
        if pc.tag == 'precache':
            root.remove(pc)

    # Find insert position (after animtext and anim, before items)
    insert_idx = 0
    for child in root:
        if child.tag in ('animtext', 'anim'):
            insert_idx += 1
        else:
            break

    for pg_pc in pg_precaches:
        pc_elem = ET.Element('precache')
        pc_elem.set('filename', pg_pc.filename)
        pc_elem.set('type', pg_pc.precache_type)
        root.insert(insert_idx, pc_elem)
        insert_idx += 1


# ---------------------------------------------------------------------------
# ENGB export
# ---------------------------------------------------------------------------

def export_engb(context, output_path):
    """Merge edits from PropertyGroups into stored ENGB tree and write.

    Args:
        context: Blender context
        output_path: path to write the .engb file

    Returns:
        (success: bool, message: str)
    """
    from .xmlb import write_xmlb

    scene = context.scene
    settings = scene.menu_settings

    if not settings.engb_json:
        return False, "No menu loaded"

    # 1. Deserialize stored ENGB tree
    try:
        root = json_to_element(settings.engb_json)
    except Exception as e:
        return False, f"Failed to deserialize ENGB: {e}"

    # 2. Update root attributes (don't write sentinel 'NONE')
    if settings.menu_type and settings.menu_type != 'NONE':
        root.set('type', settings.menu_type)

    # 3. Build map of existing item elements by name
    item_map = {}  # name → ET.Element
    for elem in root.iter('item'):
        name = elem.attrib.get('name', '')
        if name:
            item_map[name] = elem

    # 4. Sync each PropertyGroup item into the XML tree
    pg_names = set()
    for pg_item in scene.menu_items:
        pg_names.add(pg_item.item_name)
        elem = item_map.get(pg_item.item_name)
        if elem is None:
            # New item — append to root
            elem = ET.SubElement(root, 'item')
        _sync_item_to_element(pg_item, elem)

    # 5. Remove deleted items (items in XML but not in PropertyGroups)
    for elem in list(root.iter('item')):
        name = elem.attrib.get('name', '')
        if name and name not in pg_names:
            # Find parent and remove
            _remove_element(root, elem)

    # 6. Sync precache entries (animtexts are preserved from JSON to avoid
    #    float precision loss through Blender's FloatProperty)
    _sync_precaches(root, scene.menu_precaches)

    # 7. Write ENGB binary
    try:
        write_xmlb(root, Path(output_path))
    except Exception as e:
        return False, f"Failed to write ENGB: {e}"

    # 8. Update stored JSON
    settings.engb_json = element_to_json(root)

    return True, f"Saved to {Path(output_path).name}"


def _remove_element(root, target):
    """Remove an element from anywhere in the tree."""
    for parent in root.iter():
        for child in list(parent):
            if child is target:
                parent.remove(child)
                return True
    return False


# ---------------------------------------------------------------------------
# IGB transform patching
# ---------------------------------------------------------------------------

def export_igb_transforms(context, template_igb_path, output_igb_path):
    """Patch igTransform matrices in the IGB with updated positions.

    Reads the template IGB, finds named igTransform objects, locates their
    matrix data in the raw bytes by content matching, patches with updated
    positions from Blender Empties, and writes.

    Args:
        context: Blender context
        template_igb_path: path to the original IGB file (read-only)
        output_igb_path: path to write the patched IGB file

    Returns:
        (success: bool, message: str, patched_count: int)
    """
    from ..igb_format.igb_reader import IGBReader

    scene = context.scene

    # 1. Read template IGB as raw bytes
    template_path = Path(template_igb_path)
    try:
        raw_data = bytearray(template_path.read_bytes())
    except Exception as e:
        return False, f"Failed to read IGB: {e}", 0

    # 2. Parse the IGB to find named transforms and their matrix bytes
    try:
        reader = IGBReader(str(template_path))
        reader.read()
    except Exception as e:
        return False, f"Failed to parse IGB: {e}", 0

    # 3. Find all named igTransform objects and their original matrix data
    transform_map = _find_named_transforms_with_data(reader)

    if not transform_map:
        return True, "No named transforms found in IGB (nothing to patch)", 0

    # 4. For each menu item, find and patch the matrix bytes in raw data
    patched = 0
    for pg_item in scene.menu_items:
        obj = bpy.data.objects.get(pg_item.object_name)
        if obj is None:
            continue

        name = pg_item.item_name
        if name not in transform_map:
            continue

        old_matrix_bytes = transform_map[name]
        if old_matrix_bytes is None:
            continue

        # Find the original matrix bytes in the raw file
        offset = raw_data.find(old_matrix_bytes)
        if offset < 0:
            continue

        # Convert Blender matrix to Alchemy row-major
        alchemy_mat = blender_to_igb_transform(obj.matrix_world)
        new_matrix_bytes = struct.pack('<16f', *alchemy_mat)

        # Patch in place
        raw_data[offset:offset + 64] = new_matrix_bytes
        patched += 1

    # 5. Write patched data
    try:
        Path(output_igb_path).write_bytes(raw_data)
    except Exception as e:
        return False, f"Failed to write IGB: {e}", 0

    return True, f"Patched {patched} transforms", patched


def _find_named_transforms_with_data(reader):
    """Find named igTransform objects and their original matrix bytes.

    Returns:
        dict mapping name (str) → original_matrix_bytes (bytes, 64 bytes)
              or None if the matrix data could not be extracted
    """
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    result = {}

    for obj in reader.objects:
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igTransform"):
            continue

        # Extract name and matrix data from fields
        name = ""
        matrix_data = None

        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"String":
                if isinstance(val, str):
                    name = val
                elif isinstance(val, bytes):
                    name = val.decode('ascii', errors='replace')
            elif fi.short_name == b"MemoryRef":
                # Matrix data is stored in a memory block
                if isinstance(val, int) and val >= 0:
                    ref = reader.resolve_ref(val)
                    if isinstance(ref, IGBMemoryBlock) and ref.data:
                        # First 64 bytes = 16 floats of the 4x4 matrix
                        if len(ref.data) >= 64:
                            matrix_data = bytes(ref.data[:64])

        if name and matrix_data is not None:
            result[name] = matrix_data

    return result


# ---------------------------------------------------------------------------
# Deploy (ENGB + IGB → game directory)
# ---------------------------------------------------------------------------

def deploy_menu(context):
    """Export ENGB and patch IGB, then copy both to the game directory.

    Returns:
        (success: bool, message: str)
    """
    from .menu_igb_loader import resolve_igb_path

    scene = context.scene
    settings = scene.menu_settings

    if not settings.is_loaded:
        return False, "No menu loaded"
    if not settings.game_dir:
        return False, "Game directory not set"

    game_dir = Path(settings.game_dir)
    menus_dir = game_dir / 'ui' / 'menus'
    if not menus_dir.exists():
        return False, f"Menu directory not found: {menus_dir}"

    # Export ENGB
    engb_name = Path(settings.engb_path).name
    engb_out = menus_dir / engb_name
    ok, msg = export_engb(context, str(engb_out))
    if not ok:
        return False, f"ENGB export failed: {msg}"

    # Optionally patch IGB transforms (disabled by default — only needed
    # when item positions were actually moved in Blender)
    igb_msg = ""
    if settings.patch_igb_transforms and settings.igb_ref:
        template_igb = resolve_igb_path(settings.igb_ref, str(game_dir))
        if template_igb is not None:
            igb_stem = Path(settings.igb_ref).name
            igb_out = menus_dir / f"{igb_stem}.igb"
            ok2, msg2, count = export_igb_transforms(
                context, str(template_igb), str(igb_out))
            if ok2:
                igb_msg = f", {msg2}"
            else:
                igb_msg = f" (IGB failed: {msg2})"

    return True, f"Deployed {engb_name}{igb_msg}"
