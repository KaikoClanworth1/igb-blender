"""MUA2 entity placement parser for IGZ map import.

MUA2 maps use companion .mua XML files to define entity instances placed
in the world. Each map has:
  - {mapname}_entities.mua: physical objects (breakables, props, water)
  - {mapname}.mua: AI spawners, triggers, scripts, cameras

Entity chain for physical objects:
  CEntityInstance (_pos, _orient) → CEntityDef (mEntity ref) → CPhysicalEntity (_modelName)

Model path resolution:
  _modelName = "FreeWakanda/WakRock01" → models/freewakanda/wakrock01.igz

Orientation is Euler angles in radians (XYZ order).
"""

import os
import math
import xml.etree.ElementTree as ET


class EntityInstance:
    """A placed entity in the game world."""

    __slots__ = ('refname', 'pos', 'orient', 'entity_def_ref', 'model_name',
                 'entity_type')

    def __init__(self):
        self.refname = ""
        self.pos = (0.0, 0.0, 0.0)
        self.orient = (0.0, 0.0, 0.0)
        self.entity_def_ref = ""
        self.model_name = ""       # e.g. "FreeWakanda/WakRock01"
        self.entity_type = ""      # e.g. "CPhysicalEntity"


def parse_entities_mua(filepath):
    """Parse a MUA XML entity file and return placed instances with model info.

    Resolves the entity chain:
      CEntityInstance → CEntityDef → CPhysicalEntity._modelName

    Args:
        filepath: path to the .mua XML file

    Returns:
        list of EntityInstance objects that have valid model names
    """
    if not os.path.isfile(filepath):
        return []

    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        print(f"[IGZ Entities] Failed to parse {filepath}: {e}")
        return []

    root = tree.getroot()

    # Build lookup tables from XML objects
    # refname -> dict of var name -> var value/ref
    objects = {}
    for obj_elem in root.findall('object'):
        refname = obj_elem.get('refname', '')
        obj_type = obj_elem.get('type', '')
        if not refname:
            continue

        obj_data = {'_type': obj_type}
        for var_elem in obj_elem.findall('var'):
            var_name = var_elem.get('name', '')
            if not var_name:
                continue
            if 'value' in var_elem.attrib:
                obj_data[var_name] = var_elem.get('value')
            elif 'ref' in var_elem.attrib:
                obj_data[var_name] = ('ref', var_elem.get('ref'))
        objects[refname] = obj_data

    # Resolve entity chains
    instances = []

    for refname, data in objects.items():
        if data['_type'] != 'CEntityInstance':
            continue

        inst = EntityInstance()
        inst.refname = refname

        # Parse position
        pos_str = data.get('_pos', '')
        if pos_str:
            try:
                parts = pos_str.split(',')
                inst.pos = (float(parts[0]), float(parts[1]), float(parts[2]))
            except (ValueError, IndexError):
                pass

        # Parse orientation (Euler radians)
        orient_str = data.get('_orient', '')
        if orient_str:
            try:
                parts = orient_str.split(',')
                inst.orient = (float(parts[0]), float(parts[1]), float(parts[2]))
            except (ValueError, IndexError):
                pass

        # Resolve entity def chain: CEntityInstance → CEntityDef → Entity._modelName
        entity_def_ref = data.get('_entityDef')
        if not entity_def_ref or not isinstance(entity_def_ref, tuple):
            continue
        _, def_refname = entity_def_ref

        # Strip file prefix (e.g. "fwb_map1_entities.FreeWakanda_WakRock01_def")
        def_refname = _strip_file_prefix(def_refname)

        entity_def = objects.get(def_refname)
        if entity_def is None:
            continue

        # CEntityDef has mEntity ref pointing to the actual entity
        m_entity_ref = entity_def.get('mEntity')
        if not m_entity_ref or not isinstance(m_entity_ref, tuple):
            continue
        _, entity_refname = m_entity_ref
        entity_refname = _strip_file_prefix(entity_refname)

        entity = objects.get(entity_refname)
        if entity is None:
            continue

        model_name = entity.get('_modelName', '')
        if not model_name:
            continue

        inst.model_name = model_name
        inst.entity_type = entity.get('_type', '')
        instances.append(inst)

    return instances


def resolve_model_path(model_name, data_dir):
    """Resolve a _modelName to an absolute .igz file path.

    _modelName format: "FreeWakanda/WakRock01"
    File path: {data_dir}/models/freewakanda/wakrock01.igz

    The game stores model names with mixed case but filesystem paths
    are lowercased.

    Args:
        model_name: _modelName value (e.g. "FreeWakanda/WakRock01")
        data_dir: path to the MUA2 data directory (containing models/)

    Returns:
        absolute path to the .igz file, or None if not found
    """
    if not model_name or not data_dir:
        return None

    # Normalize: replace backslashes, lowercase
    normalized = model_name.replace('\\', '/').lower()
    rel_path = os.path.join('models', *normalized.split('/'))
    igz_path = os.path.join(data_dir, rel_path + '.igz')

    if os.path.isfile(igz_path):
        return igz_path

    # Try case-insensitive search in the directory
    parent_dir = os.path.dirname(igz_path)
    if os.path.isdir(parent_dir):
        target_name = os.path.basename(igz_path)
        for fn in os.listdir(parent_dir):
            if fn.lower() == target_name:
                return os.path.join(parent_dir, fn)

    return None


def find_entity_files(map_igz_path):
    """Find companion entity MUA files next to a map IGZ file.

    Given a map file like "maps/hubs/fbb_hub/fbb_hub.igz", looks for:
      - fbb_hub_entities.mua (physical objects with models)
      - fbb_hub.mua (AI, triggers, scripts — also has some models)

    Args:
        map_igz_path: absolute path to the map .igz file

    Returns:
        list of absolute paths to .mua files that exist
    """
    map_dir = os.path.dirname(map_igz_path)
    map_basename = os.path.splitext(os.path.basename(map_igz_path))[0]

    mua_files = []

    # Primary: _entities.mua (most physical objects/props)
    entities_path = os.path.join(map_dir, f"{map_basename}_entities.mua")
    if os.path.isfile(entities_path):
        mua_files.append(entities_path)

    # Secondary: main .mua (AI spawners, some physical objects)
    main_mua = os.path.join(map_dir, f"{map_basename}.mua")
    if os.path.isfile(main_mua):
        mua_files.append(main_mua)

    return mua_files


def collect_entity_models(map_igz_path, data_dir):
    """Collect all model placements from companion entity files.

    Parses all companion .mua files and resolves model paths.

    Args:
        map_igz_path: absolute path to the map .igz file
        data_dir: path to the MUA2 data directory (containing models/)

    Returns:
        dict mapping model_igz_path -> list of (pos, orient, refname) tuples
        Each unique model path maps to all its instances.
    """
    mua_files = find_entity_files(map_igz_path)
    if not mua_files:
        return {}

    # Collect all instances from all entity files
    all_instances = []
    for mua_path in mua_files:
        instances = parse_entities_mua(mua_path)
        all_instances.extend(instances)

    if not all_instances:
        return {}

    # Group by resolved model path
    model_placements = {}  # igz_path -> [(pos, orient, refname), ...]
    unresolved = set()

    for inst in all_instances:
        igz_path = resolve_model_path(inst.model_name, data_dir)
        if igz_path is None:
            unresolved.add(inst.model_name)
            continue

        if igz_path not in model_placements:
            model_placements[igz_path] = []
        model_placements[igz_path].append((inst.pos, inst.orient, inst.refname))

    if unresolved:
        print(f"[IGZ Entities] {len(unresolved)} model names could not be resolved:")
        for name in sorted(unresolved)[:10]:
            print(f"  - {name}")
        if len(unresolved) > 10:
            print(f"  ... and {len(unresolved) - 10} more")

    return model_placements


def _strip_file_prefix(ref_str):
    """Strip the file prefix from an entity reference.

    References look like "fwb_map1_entities.FreeWakanda_WakRock01_def"
    We need just "FreeWakanda_WakRock01_def" for local lookup.
    """
    if '.' in ref_str:
        return ref_str.split('.', 1)[1]
    return ref_str
