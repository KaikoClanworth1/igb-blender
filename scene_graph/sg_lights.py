"""Light extraction from IGB scene graph.

Handles extraction of:
- igLightAttr: type, position, direction, colors, attenuation, spot params
- igLightSet: container node with list of igLightAttr objects

Field slot mappings (from Alchemy 5.0 SDK headers):

igLightAttr (inherits igVisualAttribute -> igAttr -> igObject):
    slot 2:  Short      - _cachedAttrIndex (from igAttr)
    slot 3:  Short      - _cachedUnitID (from igAttr)
    slot 4:  Enum       - _lightType (0=DIRECTIONAL, 1=POINT, 2=SPOT)
    slot 5:  Int        - _lightId
    slot 6:  Vec3f      - _position (x, y, z)
    slot 7:  Vec4f      - _ambient (R, G, B, A)
    slot 8:  Vec4f      - _diffuse (R, G, B, A)
    slot 9:  Vec4f      - _specular (R, G, B, A)
    slot 10: Vec3f      - _direction (x, y, z)
    slot 11: Float      - _falloff (spot inner cone rate)
    slot 12: Float      - _cutoff (spot outer angle in degrees)
    slot 13: Vec3f      - _attenuation (constant, linear, quadratic)
    slot 14: Float      - _shininess
    slot 15: Vec3f      - _cachedPosition
    slot 16: Vec3f      - _cachedDirection
    slot 17: Bool       - _castShadowState

igLightSet (inherits igNode -> igNamedObject -> igObject):
    slot 2:  String     - _name (from igNamedObject)
    slot 7:  ObjectRef  - _lights (-> igLightList)
"""

from ..igb_format.igb_objects import IGBObject


# Light type constants (IG_GFX_LIGHT_TYPE from igGfx.h)
LIGHT_TYPE_DIRECTIONAL = 0
LIGHT_TYPE_POINT = 1
LIGHT_TYPE_SPOT = 2


class ParsedLight:
    """Container for extracted light properties from igLightAttr."""

    __slots__ = (
        'light_type', 'light_id', 'position', 'ambient', 'diffuse',
        'specular', 'direction', 'falloff', 'cutoff', 'attenuation',
        'shininess', 'cast_shadow', 'node_name', 'source_obj',
    )

    def __init__(self):
        self.light_type = LIGHT_TYPE_DIRECTIONAL
        self.light_id = 0
        self.position = (0.0, 0.0, 0.0)
        self.ambient = (0.0, 0.0, 0.0, 1.0)
        self.diffuse = (1.0, 1.0, 1.0, 1.0)
        self.specular = (0.0, 0.0, 0.0, 1.0)
        self.direction = (0.0, 0.0, -1.0)
        self.falloff = 0.0
        self.cutoff = 45.0
        self.attenuation = (1.0, 0.0, 0.0)  # (constant, linear, quadratic)
        self.shininess = 0.0
        self.cast_shadow = False
        self.node_name = ""   # name from parent igLightSet
        self.source_obj = None

    @property
    def is_ambient(self):
        """Whether this is a scene ambient light (from SceneAmbient node)."""
        return self.node_name == "SceneAmbient"

    @property
    def blender_type(self):
        """Map Alchemy light type to Blender light type string."""
        if self.light_type == LIGHT_TYPE_DIRECTIONAL:
            return 'SUN'
        elif self.light_type == LIGHT_TYPE_SPOT:
            return 'SPOT'
        return 'POINT'

    def __repr__(self):
        type_names = {0: 'DIR', 1: 'POINT', 2: 'SPOT'}
        t = type_names.get(self.light_type, '?')
        return (f"ParsedLight({t}, name={self.node_name!r}, "
                f"pos={self.position}, diffuse={self.diffuse[:3]})")


def extract_light(reader, light_attr_obj, node_name="", profile=None):
    """Extract light properties from an igLightAttr object.

    Reads field slots to populate a ParsedLight data container.

    Args:
        reader: IGBReader instance
        light_attr_obj: IGBObject of type igLightAttr
        node_name: name of the parent igLightSet node
        profile: GameProfile instance (reserved for future game-specific handling)

    Returns:
        ParsedLight or None
    """
    if not isinstance(light_attr_obj, IGBObject):
        return None

    light = ParsedLight()
    light.source_obj = light_attr_obj
    light.node_name = node_name

    for slot, val, fi in light_attr_obj._raw_fields:
        if slot == 4 and fi.short_name == b"Enum":
            light.light_type = val
        elif slot == 5 and fi.short_name == b"Int":
            light.light_id = val
        elif slot == 6 and fi.short_name == b"Vec3f":
            light.position = val
        elif slot == 7 and fi.short_name == b"Vec4f":
            light.ambient = val
        elif slot == 8 and fi.short_name == b"Vec4f":
            light.diffuse = val
        elif slot == 9 and fi.short_name == b"Vec4f":
            light.specular = val
        elif slot == 10 and fi.short_name == b"Vec3f":
            light.direction = val
        elif slot == 11 and fi.short_name == b"Float":
            light.falloff = val
        elif slot == 12 and fi.short_name == b"Float":
            light.cutoff = val
        elif slot == 13 and fi.short_name == b"Vec3f":
            light.attenuation = val
        elif slot == 14 and fi.short_name == b"Float":
            light.shininess = val
        elif slot == 17 and fi.short_name == b"Bool":
            light.cast_shadow = bool(val)

    return light


def extract_lights_from_light_set(reader, light_set_obj, profile=None):
    """Extract all lights from an igLightSet node.

    igLightSet contains a list of igLightAttr objects via the _lights
    field (ObjectRef -> igLightList). The node name (e.g. "SceneAmbient")
    is passed through to each ParsedLight.

    Args:
        reader: IGBReader instance
        light_set_obj: IGBObject of type igLightSet
        profile: GameProfile instance

    Returns:
        list of ParsedLight
    """
    if not isinstance(light_set_obj, IGBObject):
        return []

    # Get node name from slot 2 (String, from igNamedObject)
    node_name = ""
    for slot, val, fi in light_set_obj._raw_fields:
        if slot == 2 and fi.short_name == b"String":
            if isinstance(val, str):
                node_name = val
            elif isinstance(val, bytes):
                node_name = val.decode("utf-8", errors="replace")
            break

    # Get _lights ObjectRef (slot 7 -> igLightList)
    lights_ref = None
    for slot, val, fi in light_set_obj._raw_fields:
        if fi.short_name == b"ObjectRef" and val != -1:
            # The _lights field is the ObjectRef on igLightSet.
            # igLightSet inherits igNode which also has ObjectRef fields
            # (slot 3 = _bound). We need the one that resolves to
            # igLightList, not igBound or igNodeList.
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject) and ref.is_type(b"igLightList"):
                lights_ref = val
                break

    if lights_ref is None:
        return []

    # Resolve igLightList (which is igTObjectList<igLightAttr>)
    light_list_obj = reader.resolve_ref(lights_ref)
    if light_list_obj is None:
        return []

    light_attr_objs = reader.resolve_object_list(light_list_obj)

    result = []
    for light_attr in light_attr_objs:
        if isinstance(light_attr, IGBObject) and light_attr.is_type(b"igLightAttr"):
            parsed = extract_light(reader, light_attr, node_name, profile)
            if parsed is not None:
                result.append(parsed)

    return result
