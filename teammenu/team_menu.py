"""Core (bpy-free) read/patch for the MUA team-menu lineup.

Two data sources:
  • mlm_team_back.igb — igTransform nodes pad01_model..pad24_model (back
    lineup), actor01_model..actor04_model (active team), cam_position*/
    cam_spin/cam_origin (cameras). Each carries a Matrix44f (row-major,
    translation in the LAST row) = where that slot is in 3D.
  • herostat.xmlb — per-character `menulocation = N` puts that character on
    pad N (0 = not shown).

Position editing is an in-place float patch of the Matrix44f (no structural
change → no risky v8 rewrite). menulocation editing round-trips herostat.xmlb
via the addon's pure-Python XMLB read/write.
"""

import struct
from pathlib import Path


# ── Which transform nodes are editable team-menu slots ──────────────────

def classify_node(name):
    """Return a slot category for a transform name, or None if not a slot.

    Categories: 'pad' (back lineup, has a 1-based index), 'actor' (active
    team front slot), 'camera'. Returns (category, index_or_None).
    """
    n = name.lower()
    if n.startswith('pad') and n.endswith('_model'):
        # pad01_model -> ('pad', 1)
        digits = ''.join(c for c in n[3:] if c.isdigit())
        return ('pad', int(digits)) if digits else (None, None)
    if n.startswith('actor') and n.endswith('_model'):
        digits = ''.join(c for c in n[5:] if c.isdigit())
        return ('actor', int(digits)) if digits else (None, None)
    if n.startswith('cam_position'):
        digits = ''.join(c for c in n[12:] if c.isdigit())
        return ('camera', int(digits)) if digits else ('camera', None)
    if n in ('cam_spin', 'cam_origin'):
        return ('camera', None)
    return (None, None)


def _node_name(obj):
    for slot, val, fi in obj._raw_fields:
        if fi.short_name == b'String' and isinstance(val, (str, bytes)):
            return val.decode('utf-8', 'replace') if isinstance(val, bytes) else val
    return ''


def read_team_transforms(igb_path):
    """Read editable team-menu transforms from an IGB.

    Returns a dict: name -> {
        'category': 'pad'|'actor'|'camera', 'index': int|None,
        'matrix': [16 floats] (row-major), 'offset': int (file byte offset of
        the Matrix44f data), 'obj_index': int, 'slot': int }
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject

    reader = IGBReader(str(igb_path))
    reader.track_field_offsets = True
    reader.read()

    out = {}
    for o in reader.objects:
        if not isinstance(o, IGBObject) or not o.is_type(b'igTransform'):
            continue
        name = _node_name(o)
        cat, idx = classify_node(name)
        if cat is None:
            continue
        mat = None
        mslot = None
        for slot, val, fi in o._raw_fields:
            if fi.short_name == b'Matrix44f':
                mat = list(val)
                mslot = slot
                break
        if mat is None:
            continue
        off = reader.field_offsets.get((o.index, mslot))
        if off is None:
            continue
        out[name] = {
            'category': cat, 'index': idx, 'matrix': mat,
            'offset': off, 'obj_index': o.index, 'slot': mslot,
        }
    return out


def patch_team_transforms(igb_path, out_path, updates):
    """Patch transform matrices in place.

    Args:
        igb_path: source IGB.
        out_path: destination (may equal igb_path).
        updates: dict name -> 16-float row-major matrix.

    Returns:
        (patched_count, skipped_names)
    """
    transforms = read_team_transforms(igb_path)
    data = bytearray(Path(igb_path).read_bytes())
    patched = 0
    skipped = []
    for name, mat in updates.items():
        info = transforms.get(name)
        if info is None or len(mat) != 16:
            skipped.append(name)
            continue
        struct.pack_into('<16f', data, info['offset'], *[float(x) for x in mat])
        patched += 1
    Path(out_path).write_bytes(bytes(data))
    return patched, skipped


# ── herostat menulocation ───────────────────────────────────────────────

def read_menulocations(herostat_path):
    """Read per-character menulocation from herostat.xmlb.

    Returns a list of dicts (in file order):
        {'name', 'charactername', 'skin', 'menulocation' (int)}
    Only <stats> elements that actually carry a menulocation are returned.
    """
    from ..mapmaker.xmlb import read_xmlb
    root = read_xmlb(Path(herostat_path))
    chars = []
    for el in root.iter('stats'):
        if 'menulocation' not in el.attrib:
            continue
        try:
            ml = int(el.get('menulocation'))
        except (TypeError, ValueError):
            ml = 0
        chars.append({
            'name': el.get('name') or '',
            'charactername': el.get('charactername') or '',
            'skin': el.get('skin') or '',
            'menulocation': ml,
        })
    return chars


def write_menulocations(herostat_path, out_path, updates):
    """Write menulocation changes back to herostat.xmlb (byte-identical to
    xmlb-compile elsewhere).

    Args:
        updates: dict character `name` -> new menulocation (int).

    Returns:
        number of characters updated.
    """
    from ..mapmaker.xmlb import read_xmlb, write_xmlb
    root = read_xmlb(Path(herostat_path))
    n = 0
    for el in root.iter('stats'):
        nm = el.get('name')
        if nm in updates:
            el.set('menulocation', str(int(updates[nm])))
            n += 1
    write_xmlb(root, Path(out_path))
    return n


def default_herostat_for(igb_path):
    """Guess the herostat.xmlb path from a team-menu IGB path.

    mlm_team_back.igb lives at <game>/ui/menus/; herostat at <game>/data/.
    """
    p = Path(igb_path)
    # ui/menus/mlm_team_back.igb -> up 2 (ui/menus -> game root) ... actually
    # parents: [menus, ui, <game>], so game root = parents[2]
    for up in (p.parent.parent.parent, p.parent.parent, p.parent):
        cand = up / 'data' / 'herostat.xmlb'
        if cand.exists():
            return str(cand)
    return ''


def default_backdrop_for(igb_path):
    """Guess the backdrop-geometry IGB for a team-menu layout file.

    mlm_team_back.igb (layout, no geometry) lives at <game>/ui/menus/; the TRUE
    MUA team-menu stage is <game>/ui/models/m_team_stage.igb (occupies the SAME
    coordinate space — the lineup stands on it). m_act_team_1.igb (the active-
    team helicarrier deck) is a fallback if the stage isn't present.
    """
    p = Path(igb_path)
    bases = [
        p.parent.parent / 'models',          # ui/menus -> ui/models
        p.parent,
        p.parent.parent.parent / 'ui' / 'models',
    ]
    for stem in ('m_team_stage.igb', 'm_act_team_1.igb'):
        for base in bases:
            cand = base / stem
            if cand.exists():
                return str(cand)
    return ''
