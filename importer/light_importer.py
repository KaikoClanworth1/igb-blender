"""Import igLightAttr lights from an IGB scene into Blender lights.

Lets the user light their Blender scene the way the game does, so a skin (and
its normal map) can be previewed under real game lighting instead of Blender's
default. Actor IGBs carry NO lights — the lights live in map / menu scene IGBs
(e.g. maps/menu/main_back.igb has the menu rig; gameplay maps have dozens).

igLightAttr field layout (decoded from native + SDKS/Alchemy 5.0 igLightAttr.h),
slots on the parsed object:
  [4]  Enum  _lightType   (0 = directional, 1 = point, 2 = spot)
  [6]  Vec3f _position
  [7]  Vec4f _ambient
  [8]  Vec4f _diffuse     (the light colour)
  [9]  Vec4f _specular
  [10] Vec3f _direction
  [11] Float _falloff
  [12] Float _cutoff      (spot cone, degrees)
  [13] Vec3f _attenuation (constant, linear, quadratic)
IGB is Z-up, same as Blender, so positions/directions map directly.
"""

import math


# IG_GFX_LIGHT_TYPE
LIGHT_DIRECTIONAL = 0
LIGHT_POINT = 1
LIGHT_SPOT = 2


def parse_lights(reader):
    """Return a list of light dicts parsed from an already-read IGBReader."""
    from ..igb_format.igb_objects import IGBObject
    out = []
    for o in reader.objects:
        if not isinstance(o, IGBObject):
            continue
        tname = (o.type_name.decode() if isinstance(o.type_name, bytes)
                 else str(o.type_name))
        if tname != 'igLightAttr':
            continue
        f = {s: v for s, v, fi in o._raw_fields}
        diffuse = f.get(8) or (1.0, 1.0, 1.0, 1.0)
        out.append({
            'type': int(f.get(4, LIGHT_POINT)),
            'position': tuple(f.get(6) or (0.0, 0.0, 0.0)),
            'direction': tuple(f.get(10) or (0.0, 0.0, -1.0)),
            'color': tuple(diffuse)[:3],
            'ambient': tuple(f.get(7) or (0.0, 0.0, 0.0, 1.0))[:3],
            'cutoff': float(f.get(12, -0.5)),
            'attenuation': tuple(f.get(13) or (0.0, 0.0, 0.0)),
            'index': o.index,
        })
    return out


def _direction_to_euler(direction):
    """Blender rotation so a light's -Z axis points along `direction`."""
    import mathutils
    d = mathutils.Vector(direction)
    if d.length < 1e-6:
        d = mathutils.Vector((0.0, 0.0, -1.0))
    # Blender lights emit along -Z; align -Z with the light direction.
    quat = (-d).to_track_quat('Z', 'Y')
    return quat.to_euler()


def _get_or_clear_collection(collection_name):
    """Return a dedicated lights collection, clearing it if it already exists so
    a re-run replaces the previous rig instead of stacking duplicates."""
    import bpy
    coll = bpy.data.collections.get(collection_name)
    if coll is not None:
        for ob in list(coll.objects):
            data = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if isinstance(data, bpy.types.Light) and data.users == 0:
                bpy.data.lights.remove(data)
    else:
        coll = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def _set_world_ambient_color(rgb):
    """Set the scene World background to a dim ambient so shadows aren't black."""
    import bpy
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("IGB World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg is not None:
        bg.inputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        bg.inputs[1].default_value = 1.0


# Synthesized "Character Preview" 3-point rig (Blender Z-up; character ~1.8u
# tall at the origin, facing +Y, camera on -Y). SUN lamps: energy is
# distance-independent, only direction matters. These are HAND-TUNED art values
# meant to SHOW OFF normal/spec/gloss relief — NOT the game's exact char-select
# numbers. The real MUA char-select rig (ui/models/mlm_team_back.igb) stores its
# lights node-parented with the igLightAttr position field zeroed, so it can't
# be reconstructed into a usable key direction from the file alone; and the SDK's
# only preview light is a flat dead-on headlight (worst case for relief).
#   name,  aim_from,            aim_at,           color (RGB),         energy
_PREVIEW_RIG = [
    ("key",  (-2.2, -2.6, 3.2), (0.0, 0.0, 1.0), (1.00, 0.97, 0.92), 4.0),
    ("fill", (2.6, -2.0, 1.6),  (0.0, 0.0, 1.0), (0.80, 0.86, 1.00), 1.6),
    ("rim",  (1.5, 3.0, 3.5),   (0.0, 0.0, 1.2), (1.00, 1.00, 1.00), 3.5),
]


def build_character_preview_rig(collection_name="IGB Game Lights",
                                set_world_ambient=True, energy_scale=1.0):
    """Create a synthesized 3-point (key/fill/rim) SUN rig for previewing a skin.

    Purpose-built to reveal normal/specular/gloss-map relief on a posed hero,
    which the game's own front-end lighting does poorly. Clears/replaces the
    shared "IGB Game Lights" collection. Returns the number of lamps created.
    """
    import bpy
    import mathutils

    coll = _get_or_clear_collection(collection_name)
    for name, frm, aim, color, energy in _PREVIEW_RIG:
        ld = bpy.data.lights.new(f"igb_preview_{name}", 'SUN')
        ld.energy = energy * energy_scale
        ld.color = color
        # Soft sun disc — softer fill, crisper key/rim.
        ld.angle = math.radians(28.0 if name == 'fill' else 12.0)
        ld.use_shadow = (name != 'fill')
        ob = bpy.data.objects.new(ld.name, ld)
        d = mathutils.Vector(aim) - mathutils.Vector(frm)
        if d.length < 1e-6:
            d = mathutils.Vector((0.0, 0.0, -1.0))
        # A SUN emits along its local -Z; aim -Z down the key/fill/rim vector.
        ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        ob.location = frm  # cosmetic (SUN ignores position); shows the rig
        coll.objects.link(ob)
    if set_world_ambient:
        _set_world_ambient_color((0.04, 0.045, 0.06))
    return len(_PREVIEW_RIG)


def import_lights_to_blender(filepath, point_energy=400.0, sun_strength=3.0,
                             collection_name="IGB Game Lights"):
    """Read `filepath` (an IGB) and create Blender lights from its igLightAttr.

    Returns (created_count, total_count, ambient_rgb_or_None). Reuses/clears a
    dedicated collection so re-importing replaces the previous rig.

    NOTE: lights node-parented under an igTransform (e.g. the MUA char-select
    rig ui/models/mlm_team_back.igb) store position (0,0,0) on the igLightAttr
    itself — those will spawn at the origin. The XML2 main-menu backdrop
    (maps/menu/main_back.igb) stores real positions and imports faithfully.
    """
    import bpy
    from ..igb_format.igb_reader import IGBReader

    reader = IGBReader(filepath)
    reader.read()
    lights = parse_lights(reader)

    coll = _get_or_clear_collection(collection_name)

    created = 0
    amb = None
    for i, L in enumerate(lights):
        lt = L['type']
        if lt == LIGHT_DIRECTIONAL:
            ld = bpy.data.lights.new(f"igb_sun_{i}", 'SUN')
            ld.energy = sun_strength
        elif lt == LIGHT_SPOT:
            ld = bpy.data.lights.new(f"igb_spot_{i}", 'SPOT')
            ld.energy = point_energy
            cut = L['cutoff']
            if cut > 0:
                ld.spot_size = math.radians(min(179.0, cut * 2.0))
        else:  # point
            ld = bpy.data.lights.new(f"igb_point_{i}", 'POINT')
            ld.energy = point_energy
        ld.color = L['color']
        ld.use_shadow = False  # menu/preview lights generally don't cast shadow
        ob = bpy.data.objects.new(ld.name, ld)
        ob.location = L['position']
        if lt in (LIGHT_DIRECTIONAL, LIGHT_SPOT):
            ob.rotation_euler = _direction_to_euler(L['direction'])
        coll.objects.link(ob)
        created += 1
        if amb is None:
            amb = L['ambient']

    return created, len(lights), amb
