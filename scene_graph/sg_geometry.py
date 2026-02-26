"""Geometry extraction from IGB geometry attributes.

Supports two geometry architectures via GameProfile configuration:

v1 geometry (igGeometryAttr / igGeometryAttr1_5 + igVertexArray1_1):
    Used by XML1, XML2, and earlier Alchemy games.
    Vertex data stored as separate stream blocks via igExternalIndexedEntry.
    Default slot mapping (XML2 PC): positions=0, normals=1, colors=2, texcoords=11.
    Slot mappings are configurable via GameProfile for other games.

v2 geometry (igGeometryAttr2 + igVertexArray2):
    Used by MUA1, MUA2, and later Alchemy 4.0+ games. Three sub-paths:

    PS2 VIF path (igPsx2VertexStream):
        Used by MUA2 PS2. Vertex data in VIF packets using IEEE 754 bias trick.

    Component data path (igVertexData with igVec3fList/igVec2fList):
        Used by MUA PC. Per-component float data in separate igDataList objects.
        igVertexArray2.ObjectList -> igVertexData items with componentType enum:
            POSITION=1, COLOR=2, NORMAL=3, TEXCOORD=4, WEIGHT=5, INDEX=6,
            BINORMAL=7, TANGENT=8

    Fixed-point component path (igVertexDataList on igVertexStream):
        Used by XML2/MUA PSP. Per-component data as igShortList/igCharList/
        igUnsignedShortList with fraction bits and scale/offset for decoding:
            value = (raw_int / 2^frac_bits) * scale + offset

    Platform-native blob (componentType STREAM_XENON=15):
        Used by MUA Xbox 360. Pre-built GPU vertex buffer, currently unsupported.
"""

import struct
import math

from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock


# External indexed entry slot assignments
EXT_SLOT_POSITIONS = 0
EXT_SLOT_NORMALS = 1
EXT_SLOT_COLORS = 2
EXT_SLOT_TEXCOORDS = 11

# Vertex format bitmasks (from igVertexArray.h)
VF_HAS_POSITION = 0x00000001
VF_HAS_NORMAL = 0x00000002
VF_HAS_COLOR = 0x00000004
VF_BLEND_WEIGHT_MASK = 0x000000F0
VF_BLEND_WEIGHT_SHIFT = 4
VF_BLEND_INDEX_MASK = 0x00000F00
VF_BLEND_INDEX_SHIFT = 8
VF_TEXCOORD_MASK = 0x000F0000
VF_TEXCOORD_SHIFT = 16

# Primitive types
IG_GFX_DRAW_TRIANGLE_LIST = 3
IG_GFX_DRAW_TRIANGLE_STRIP = 4

# igVertexData component types (IG_VERTEX_COMPONENT_TYPE enum)
VCOMP_NONE = 0
VCOMP_POSITION = 1
VCOMP_COLOR = 2
VCOMP_NORMAL = 3
VCOMP_TEXCOORD = 4
VCOMP_WEIGHT = 5
VCOMP_INDEX = 6
VCOMP_BINORMAL = 7
VCOMP_TANGENT = 8
VCOMP_STREAM_PS2 = 11
VCOMP_STREAM_GC = 12
VCOMP_STREAM_PSP = 14
VCOMP_STREAM_XENON = 15
VCOMP_STREAM_PS3 = 16
VCOMP_STREAM_WII = 17


class ParsedGeometry:
    """Container for extracted geometry data from a single igGeometryAttr1_5."""

    __slots__ = (
        'positions', 'normals', 'uvs', 'colors',
        'indices', 'vertex_format', 'prim_type',
        'strip_lengths', 'blend_weights', 'blend_indices',
        'source_obj',
    )

    def __init__(self):
        self.positions = []     # list of (x, y, z)
        self.normals = []       # list of (nx, ny, nz)
        self.uvs = []           # list of (u, v)
        self.colors = []        # list of (r, g, b, a) as 0.0-1.0
        self.indices = []       # list of int (triangle list indices after strip conversion)
        self.vertex_format = 0
        self.prim_type = IG_GFX_DRAW_TRIANGLE_STRIP
        self.strip_lengths = []  # strip lengths for triangle strip conversion
        self.blend_weights = []  # list of tuples (per-vertex weights)
        self.blend_indices = []  # list of tuples (per-vertex bone indices)
        self.source_obj = None   # reference to source igGeometryAttr object

    @property
    def num_verts(self):
        return len(self.positions)

    @property
    def has_normals(self):
        return bool(self.normals)

    @property
    def has_uvs(self):
        return bool(self.uvs)

    @property
    def has_colors(self):
        return bool(self.colors)

    def triangulate(self):
        """Convert triangle strips to triangle list.

        Uses strip_lengths to split the index buffer into individual strips,
        then converts each strip to triangles.

        For non-indexed geometry (no index buffer but vertices in strip order),
        generates sequential indices [0, 1, 2, ...] automatically.

        Returns a new list of triangle indices (groups of 3).
        """
        # Use provided indices, or generate sequential indices for non-indexed geometry
        indices = self.indices
        if not indices and self.num_verts > 0:
            indices = list(range(self.num_verts))

        if self.prim_type == IG_GFX_DRAW_TRIANGLE_LIST:
            return list(indices)

        if self.prim_type != IG_GFX_DRAW_TRIANGLE_STRIP:
            return list(indices)

        if not self.strip_lengths:
            # Single strip - use all indices
            return _strip_to_triangles(indices)

        # Multiple strips - process each one
        triangles = []
        offset = 0
        for strip_len in self.strip_lengths:
            strip_indices = indices[offset:offset + strip_len]
            triangles.extend(_strip_to_triangles(strip_indices))
            offset += strip_len

        return triangles


def extract_geometry(reader, geom_attr, profile=None):
    """Extract geometry data from an igGeometryAttr object.

    Supports both v1 (igGeometryAttr1_5) and v2 (igGeometryAttr2) geometry
    based on the GameProfile configuration. If no profile is provided,
    defaults to XML2 PC behavior (v1 with standard slot mapping).

    Args:
        reader: IGBReader instance
        geom_attr: IGBObject of type igGeometryAttr1_5, igGeometryAttr2, etc.
        profile: GameProfile instance (optional, defaults to XML2 PC)

    Returns:
        ParsedGeometry or None if extraction fails
    """
    if profile is None:
        from ..game_profiles import get_profile
        profile = get_profile("xml2_pc")

    # Check if this is a v2 geometry attr (igGeometryAttr2)
    gc = profile.geometry
    if geom_attr.is_type(b"igGeometryAttr2"):
        if gc.geometry_attr_version in ("v2", "auto"):
            return _extract_v2_geometry(reader, geom_attr, profile)
        # v2 attr but profile says v1 only — skip
        return None

    endian = reader.header.endian
    geom = ParsedGeometry()
    geom.source_obj = geom_attr

    # Extract fields from geometry attr
    va_ref = None
    ia_ref = None
    prim_lengths_ref = None

    for slot, val, fi in geom_attr._raw_fields:
        name = fi.short_name
        if name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject):
                if ref.is_type(b"igVertexArray"):
                    va_ref = ref
                elif ref.is_type(b"igIndexArray"):
                    ia_ref = ref
                elif ref.is_type(b"igPrimLengthArray"):
                    prim_lengths_ref = ref
        elif name == b"Enum":
            geom.prim_type = val
        elif name == b"UnsignedInt" and slot == 7:
            pass  # _numPrims

    if va_ref is None:
        return None

    # Extract vertex array data
    _extract_vertex_data(reader, va_ref, geom, endian, profile)

    # Extract index array data
    if ia_ref is not None:
        _extract_index_data(reader, ia_ref, geom, endian)

    # Extract strip lengths
    if prim_lengths_ref is not None:
        _extract_strip_lengths(reader, prim_lengths_ref, geom, endian)

    return geom


def _extract_vertex_data(reader, va_obj, geom, endian, profile=None):
    """Extract vertex positions, normals, UVs, colors from igVertexArray1_1."""
    num_verts = None
    vdata_ref_val = None
    vertex_format = None
    blend_weights_ref = None
    blend_indices_ref = None

    for slot, val, fi in va_obj._raw_fields:
        name = fi.short_name
        if name == b"UnsignedInt" and num_verts is None:
            num_verts = val
        elif name == b"Struct":
            vertex_format = val
        elif name == b"MemoryRef":
            if slot == 2:
                vdata_ref_val = val
            elif slot == 7:
                blend_weights_ref = val
            elif slot == 8:
                blend_indices_ref = val

    if num_verts is None or num_verts == 0:
        return

    geom.vertex_format = vertex_format or 0

    if vdata_ref_val is None or vdata_ref_val == -1:
        return

    vdata_block = reader.resolve_ref(vdata_ref_val)
    if not isinstance(vdata_block, IGBMemoryBlock):
        return

    # Resolve the external indexed entry
    ext_slots = _resolve_ext_indexed(reader, vdata_block, endian)
    if ext_slots is None:
        # Direct data - try interleaved interpretation
        _extract_interleaved_vdata(vdata_block, num_verts, vertex_format, geom, endian)
        return

    # Get slot assignments from profile (or use defaults)
    if profile is not None:
        gc = profile.geometry
        slot_pos = gc.ext_slot_positions
        slot_norm = gc.ext_slot_normals
        slot_color = gc.ext_slot_colors
        slot_uv = gc.ext_slot_texcoords
        color_order = gc.color_byte_order
    else:
        slot_pos = EXT_SLOT_POSITIONS
        slot_norm = EXT_SLOT_NORMALS
        slot_color = EXT_SLOT_COLORS
        slot_uv = EXT_SLOT_TEXCOORDS
        color_order = "abgr"

    # Extract positions
    pos_block = ext_slots.get(slot_pos)
    if pos_block is not None and pos_block.data:
        for i in range(num_verts):
            offset = i * 12
            if offset + 12 <= len(pos_block.data):
                x, y, z = struct.unpack_from(endian + "fff", pos_block.data, offset)
                geom.positions.append((x, y, z))

    # Extract normals
    norm_block = ext_slots.get(slot_norm)
    if norm_block is not None and norm_block.data:
        for i in range(num_verts):
            offset = i * 12
            if offset + 12 <= len(norm_block.data):
                nx, ny, nz = struct.unpack_from(endian + "fff", norm_block.data, offset)
                geom.normals.append((nx, ny, nz))

    # Extract colors
    color_block = ext_slots.get(slot_color)
    if color_block is not None and color_block.data:
        for i in range(num_verts):
            offset = i * 4
            if offset + 4 <= len(color_block.data):
                raw = struct.unpack_from(endian + "I", color_block.data, offset)[0]
                r, g, b, a = _decode_color_uint32(raw, color_order)
                geom.colors.append((r, g, b, a))

    # Extract texture coordinates
    uv_block = ext_slots.get(slot_uv)
    if uv_block is not None and uv_block.data:
        for i in range(num_verts):
            offset = i * 8
            if offset + 8 <= len(uv_block.data):
                u, v = struct.unpack_from(endian + "ff", uv_block.data, offset)
                geom.uvs.append((u, v))

    # Extract blend weights (separate MemoryRef, slot 7 of VA)
    if blend_weights_ref is not None and blend_weights_ref != -1:
        bw_block = reader.resolve_ref(blend_weights_ref)
        if isinstance(bw_block, IGBMemoryBlock) and bw_block.data:
            blend_count = (vertex_format & VF_BLEND_WEIGHT_MASK) >> VF_BLEND_WEIGHT_SHIFT
            if blend_count > 0:
                bytes_per_vert = blend_count * 4  # float per weight
                for i in range(num_verts):
                    offset = i * bytes_per_vert
                    if offset + bytes_per_vert <= len(bw_block.data):
                        weights = struct.unpack_from(
                            endian + "f" * blend_count, bw_block.data, offset
                        )
                        geom.blend_weights.append(weights)

    # Extract blend indices (separate MemoryRef, slot 8 of VA)
    if blend_indices_ref is not None and blend_indices_ref != -1:
        bi_block = reader.resolve_ref(blend_indices_ref)
        if isinstance(bi_block, IGBMemoryBlock) and bi_block.data:
            blend_count = (vertex_format & VF_BLEND_INDEX_MASK) >> VF_BLEND_INDEX_SHIFT
            if blend_count > 0:
                for i in range(num_verts):
                    offset = i * blend_count
                    if offset + blend_count <= len(bi_block.data):
                        indices = struct.unpack_from(
                            "B" * blend_count, bi_block.data, offset
                        )
                        geom.blend_indices.append(indices)


def _resolve_ext_indexed(reader, mem_block, endian):
    """Resolve an igExternalIndexedEntry to a dict of slot -> IGBMemoryBlock.

    The 80-byte entry has 20 uint32 slots. Non-0xFFFFFFFF values are indices
    to memory blocks. Returns {slot_number: IGBMemoryBlock}.

    Note: Some platforms (e.g. Wii v8) assign a different mem_type_name
    (b'igImage') to ext indexed entries. We validate structurally: the block
    must be exactly 80 bytes and contain at least one valid slot referencing
    a memory block, with all other entries being either valid references or
    0xFFFFFFFF (empty).
    """
    if mem_block.mem_size != 80:
        return None
    if mem_block.data is None or len(mem_block.data) < 80:
        return None

    slots = {}
    invalid_count = 0
    for i in range(20):
        idx = struct.unpack_from(endian + "I", mem_block.data, i * 4)[0]
        if idx == 0xFFFFFFFF:
            continue  # empty slot
        if idx < len(reader.objects):
            ref = reader.objects[idx]
            if isinstance(ref, IGBMemoryBlock):
                slots[i] = ref
            else:
                invalid_count += 1
        else:
            invalid_count += 1

    # Valid ext indexed entries have mostly 0xFFFFFFFF slots with a few
    # valid MemoryBlock references and no invalid references
    if not slots or invalid_count > 0:
        return None

    return slots


def _extract_interleaved_vdata(vdata_block, num_verts, vertex_format, geom, endian):
    """Fallback: try reading vdata as an interleaved buffer.

    Used when _vdata is a direct memory block (not external indexed).
    """
    if vdata_block.data is None or num_verts == 0:
        return

    total_size = len(vdata_block.data)
    stride = total_size // num_verts
    if stride == 0:
        return

    # Calculate component offsets based on vertex format
    offset = 0
    pos_off = None
    norm_off = None
    color_off = None
    uv_off = None

    if vertex_format & VF_HAS_POSITION:
        pos_off = offset
        offset += 12

    if vertex_format & VF_HAS_NORMAL:
        norm_off = offset
        offset += 12

    if vertex_format & VF_HAS_COLOR:
        color_off = offset
        offset += 4

    # Blend weights
    bw_count = (vertex_format & VF_BLEND_WEIGHT_MASK) >> VF_BLEND_WEIGHT_SHIFT
    offset += bw_count * 4

    # Blend indices
    bi_count = (vertex_format & VF_BLEND_INDEX_MASK) >> VF_BLEND_INDEX_SHIFT
    offset += bi_count

    # Texture coordinates
    tc_count = (vertex_format & VF_TEXCOORD_MASK) >> VF_TEXCOORD_SHIFT
    if tc_count > 0:
        uv_off = offset
        offset += 8  # Vec2f per UV set

    # Read vertices
    buf = vdata_block.data
    for i in range(num_verts):
        base = i * stride
        if base + stride > total_size:
            break

        if pos_off is not None and base + pos_off + 12 <= total_size:
            x, y, z = struct.unpack_from(endian + "fff", buf, base + pos_off)
            geom.positions.append((x, y, z))

        if norm_off is not None and base + norm_off + 12 <= total_size:
            nx, ny, nz = struct.unpack_from(endian + "fff", buf, base + norm_off)
            geom.normals.append((nx, ny, nz))

        if color_off is not None and base + color_off + 4 <= total_size:
            raw = struct.unpack_from(endian + "I", buf, base + color_off)[0]
            r, g, b, a = _decode_color_uint32(raw, "abgr")
            geom.colors.append((r, g, b, a))

        if uv_off is not None and base + uv_off + 8 <= total_size:
            u, v = struct.unpack_from(endian + "ff", buf, base + uv_off)
            geom.uvs.append((u, v))


def _extract_index_data(reader, ia_obj, geom, endian):
    """Extract index data from igIndexArray."""
    num_indices = None
    data_ref = None

    for slot, val, fi in ia_obj._raw_fields:
        name = fi.short_name
        if name == b"UnsignedInt" and num_indices is None:
            num_indices = val
        elif name == b"MemoryRef" and val != -1:
            data_ref = val

    if num_indices is None or num_indices == 0 or data_ref is None:
        return

    mem_block = reader.resolve_ref(data_ref)
    if not isinstance(mem_block, IGBMemoryBlock):
        return

    # Index data may be in an igExternalInfoEntry (92+ bytes with indices)
    # or direct data. Try direct first.
    if mem_block.data is None:
        return

    # Check if this is an igExternalInfoEntry that contains index references
    if mem_block.mem_type_name == b'igExternalInfoEntry':
        _extract_indices_from_info_entry(reader, mem_block, num_indices, geom, endian)
        return

    # Direct index data (uint16)
    for i in range(min(num_indices, len(mem_block.data) // 2)):
        idx = struct.unpack_from(endian + "H", mem_block.data, i * 2)[0]
        geom.indices.append(idx)


def _extract_indices_from_info_entry(reader, mem_block, num_indices, geom, endian):
    """Extract indices from an igExternalInfoEntry.

    The igExternalInfoEntry stores index data directly as uint16 values.
    The block size = num_indices * 2 bytes.
    """
    if mem_block.data is None:
        return

    data = mem_block.data
    size = len(data)

    # The info entry contains raw uint16 index data directly
    count = min(num_indices, size // 2)
    for i in range(count):
        idx = struct.unpack_from(endian + "H", data, i * 2)[0]
        geom.indices.append(idx)


def _extract_strip_lengths(reader, pla_obj, geom, endian):
    """Extract triangle strip lengths from igPrimLengthArray1_1."""
    num_strips = None
    data_ref = None
    elem_bits = 32  # default 32-bit strip lengths

    for slot, val, fi in pla_obj._raw_fields:
        name = fi.short_name
        if name == b"UnsignedInt":
            if num_strips is None:
                num_strips = val
            elif slot == 4:
                elem_bits = val

        elif name == b"MemoryRef" and val != -1:
            data_ref = val

    if num_strips is None or num_strips == 0 or data_ref is None:
        return

    mem_block = reader.resolve_ref(data_ref)
    if not isinstance(mem_block, IGBMemoryBlock) or mem_block.data is None:
        return

    if elem_bits == 32:
        for i in range(num_strips):
            offset = i * 4
            if offset + 4 <= len(mem_block.data):
                length = struct.unpack_from(endian + "I", mem_block.data, offset)[0]
                geom.strip_lengths.append(length)
    elif elem_bits == 16:
        for i in range(num_strips):
            offset = i * 2
            if offset + 2 <= len(mem_block.data):
                length = struct.unpack_from(endian + "H", mem_block.data, offset)[0]
                geom.strip_lengths.append(length)


def _strip_to_triangles(indices):
    """Convert a triangle strip to a triangle list.

    Triangle strip: vertices v0, v1, v2, v3, v4, ...
    Produces triangles:
      (v0, v1, v2), (v2, v1, v3), (v2, v3, v4), ...
    Alternating winding order every other triangle.
    """
    if len(indices) < 3:
        return []

    triangles = []
    for i in range(len(indices) - 2):
        i0 = indices[i]
        i1 = indices[i + 1]
        i2 = indices[i + 2]

        # Skip degenerate triangles
        if i0 == i1 or i1 == i2 or i0 == i2:
            continue

        # Alternate winding
        if i % 2 == 0:
            triangles.extend([i0, i1, i2])
        else:
            triangles.extend([i0, i2, i1])

    return triangles


def _decode_color_uint32(raw, byte_order="abgr"):
    """Decode a uint32 color value to (r, g, b, a) floats in 0.0-1.0.

    Args:
        raw: uint32 color value
        byte_order: "abgr" (DirectX/PC) or "rgba" (OpenGL)

    Returns:
        tuple of (r, g, b, a) as 0.0-1.0 floats
    """
    if byte_order == "abgr":
        a = ((raw >> 24) & 0xFF) / 255.0
        b = ((raw >> 16) & 0xFF) / 255.0
        g = ((raw >> 8) & 0xFF) / 255.0
        r = (raw & 0xFF) / 255.0
    elif byte_order == "rgba":
        r = ((raw >> 24) & 0xFF) / 255.0
        g = ((raw >> 16) & 0xFF) / 255.0
        b = ((raw >> 8) & 0xFF) / 255.0
        a = (raw & 0xFF) / 255.0
    elif byte_order == "bgra":
        b = ((raw >> 24) & 0xFF) / 255.0
        g = ((raw >> 16) & 0xFF) / 255.0
        r = ((raw >> 8) & 0xFF) / 255.0
        a = (raw & 0xFF) / 255.0
    else:
        # Default to ABGR
        a = ((raw >> 24) & 0xFF) / 255.0
        b = ((raw >> 16) & 0xFF) / 255.0
        g = ((raw >> 8) & 0xFF) / 255.0
        r = (raw & 0xFF) / 255.0
    return (r, g, b, a)


def _pos_approx_eq(a, b, eps=0.001):
    """Check if two position tuples are approximately equal."""
    return (abs(a[0] - b[0]) < eps and
            abs(a[1] - b[1]) < eps and
            abs(a[2] - b[2]) < eps)


def _extract_v2_geometry(reader, geom_attr, profile):
    """Extract geometry from igGeometryAttr2 (Alchemy 4.0+).

    Dispatches to the appropriate extraction path based on the stream type
    and vertex data layout found in the file:

    1. PS2 VIF path — igPsx2VertexStream with VIF packets (MUA2 PS2)
    2. Component data path — igVertexData with igVec3fList/igVec2fList (MUA PC)
    3. Fixed-point path — igVertexDataList on stream with igShortList etc. (PSP)
    4. Platform-native blob — STREAM_XENON etc. (Xbox 360, unsupported)

    Args:
        reader: IGBReader instance
        geom_attr: IGBObject of type igGeometryAttr2
        profile: GameProfile instance

    Returns:
        ParsedGeometry or None
    """
    endian = reader.header.endian
    geom = ParsedGeometry()
    geom.source_obj = geom_attr

    # Extract fields from igGeometryAttr2
    va_ref = None           # igVertexArray2
    ia_ref = None           # igIndexArray
    prim_lengths_ref = None  # igPrimLengthArray1_1

    for slot, val, fi in geom_attr._raw_fields:
        name = fi.short_name
        if name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject):
                # igVertexArray2 does NOT inherit from igVertexArray
                # (it inherits from igNamedObject), so check both
                if ref.is_type(b"igVertexArray") or ref.is_type(b"igVertexArray2"):
                    va_ref = ref
                elif ref.is_type(b"igIndexArray"):
                    ia_ref = ref
                elif ref.is_type(b"igPrimLengthArray"):
                    prim_lengths_ref = ref
        elif slot == 7:
            # _primType
            if isinstance(val, int):
                geom.prim_type = val
        elif slot == 8:
            # _numVerts (actually number of strips in v2 PS2)
            pass

    if va_ref is None:
        return None

    # Follow igVertexArray2 -> stream and object list
    stream_obj = None
    va_obj_list = None
    for slot, val, fi in va_ref._raw_fields:
        name = fi.short_name
        if name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject):
                if ref.is_type(b"igVertexStream"):
                    stream_obj = ref
                elif ref.is_type(b"igObjectList"):
                    va_obj_list = ref

    if stream_obj is None:
        return None

    # Determine which extraction path to use:
    # 1. Check for PS2 VIF data (igPsx2VertexStream with MemoryRef at slot 17)
    vif_data = None
    for slot, val, fi in stream_obj._raw_fields:
        name = fi.short_name
        if name == b"MemoryRef" and val != -1 and slot == 17:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBMemoryBlock) and ref.data:
                vif_data = ref.data

    if vif_data is not None:
        # PS2 VIF path (existing)
        return _extract_v2_vif(reader, geom, prim_lengths_ref, vif_data, endian)

    # 2. Check for igVertexDataList on the stream (PSP fixed-point path)
    stream_vdata_list = None
    for slot, val, fi in stream_obj._raw_fields:
        name = fi.short_name
        if name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject) and ref.is_type(b"igVertexDataList"):
                stream_vdata_list = ref

    if stream_vdata_list is not None:
        items = reader.resolve_object_list(stream_vdata_list)
        if items:
            result = _extract_v2_fixed_point(reader, geom, items, endian, profile)
            if result is not None:
                # Extract strip lengths and index array
                if prim_lengths_ref is not None:
                    _extract_strip_lengths(reader, prim_lengths_ref, geom, endian)
                if ia_ref is not None:
                    _extract_index_data(reader, ia_ref, geom, endian)
                # If no index array, build sequential indices (PSP strips)
                if not geom.indices and geom.positions:
                    geom.indices = list(range(len(geom.positions)))
                if not geom.positions:
                    return None
                return geom

    # 3. Check VA2 ObjectList for igVertexData items with standard component types
    if va_obj_list is not None:
        items = reader.resolve_object_list(va_obj_list)
        # Filter to actual igVertexData items (skip igVolumeList etc.)
        vdata_items = [
            item for item in items
            if isinstance(item, IGBObject) and item.is_type(b"igVertexData")
        ]

        if vdata_items:
            # Check if any item has a platform-native stream component type
            has_native = False
            has_component = False
            for vd in vdata_items:
                comp_type = _get_vdata_component_type(vd)
                if comp_type in (VCOMP_STREAM_XENON, VCOMP_STREAM_PS3,
                                 VCOMP_STREAM_WII, VCOMP_STREAM_GC,
                                 VCOMP_STREAM_PSP, VCOMP_STREAM_PS2):
                    has_native = True
                elif comp_type in (VCOMP_POSITION, VCOMP_NORMAL, VCOMP_TEXCOORD,
                                   VCOMP_COLOR, VCOMP_BINORMAL, VCOMP_TANGENT,
                                   VCOMP_WEIGHT, VCOMP_INDEX):
                    has_component = True

            if has_component:
                # Standard component data path (MUA PC, etc.)
                result = _extract_v2_component_data(
                    reader, geom, vdata_items, endian, profile
                )
                if result is not None:
                    if prim_lengths_ref is not None:
                        _extract_strip_lengths(reader, prim_lengths_ref, geom, endian)
                    if ia_ref is not None:
                        _extract_index_data(reader, ia_ref, geom, endian)
                    if not geom.positions:
                        return None
                    return geom

            if has_native and not has_component:
                # Platform-native blob only — not yet supported
                return None

    return None


def _get_vdata_component_type(vdata_obj):
    """Get the componentType enum from an igVertexData object (slot 4)."""
    for slot, val, fi in vdata_obj._raw_fields:
        if slot == 4 and fi.short_name == b"Enum":
            return val
    return VCOMP_NONE


def _extract_v2_vif(reader, geom, prim_lengths_ref, vif_data, endian):
    """Extract v2 geometry from PS2 VIF packets (MUA2 PS2).

    VIF data contains multiple batches separated by MSCAL commands.
    Each batch has positions (V3-16 unsigned), normals (V3-8 unsigned),
    and UVs (V2-8 unsigned) using the IEEE 754 bias trick.
    """
    batches = _decode_vif_vertex_data(vif_data)
    if not batches:
        return None

    # Extract strip lengths FIRST — we need them to compute overlap count
    if prim_lengths_ref is not None:
        _extract_strip_lengths(reader, prim_lengths_ref, geom, endian)

    # Concatenate batches with overlap removal.
    #
    # Triangle strips can span multiple VIF batches (PS2 hardware limit ~52
    # verts per batch). When a strip continues across a batch boundary, the
    # last 2 vertices of batch N are duplicated as the first 2 of batch N+1
    # to maintain strip continuity. We must remove these overlaps.
    #
    # The expected total vertex count = sum(strip_lengths). Any difference
    # between total VIF verts and this number tells us exactly how many
    # overlap vertices to remove.
    total_vif_verts = sum(b['num_verts'] for b in batches)
    expected_verts = sum(geom.strip_lengths) if geom.strip_lengths else total_vif_verts
    expected_overlaps = total_vif_verts - expected_verts
    overlaps_remaining = max(0, expected_overlaps)

    for bi, batch in enumerate(batches):
        skip = 0
        if (bi > 0 and overlaps_remaining >= 2 and
                len(batch['positions']) >= 2 and len(geom.positions) >= 2):
            # Check if the first 2 positions of this batch match the last 2
            # of the accumulated data (strip continuation overlap)
            if (_pos_approx_eq(geom.positions[-2], batch['positions'][0]) and
                    _pos_approx_eq(geom.positions[-1], batch['positions'][1])):
                skip = 2
                overlaps_remaining -= 2

        geom.positions.extend(batch['positions'][skip:])
        if batch['normals']:
            geom.normals.extend(batch['normals'][skip:])
        if batch['uvs']:
            geom.uvs.extend(batch['uvs'][skip:])

    # Build sequential indices (0, 1, 2, 3, ... N-1) since VIF vertices
    # are already in strip order — the strip_lengths define how to cut them
    num_total = len(geom.positions)
    geom.indices = list(range(num_total))

    # If we got strip lengths, set prim type to strip
    if geom.strip_lengths:
        geom.prim_type = IG_GFX_DRAW_TRIANGLE_STRIP

    if not geom.positions:
        return None

    return geom


def _extract_v2_component_data(reader, geom, vdata_items, endian, profile):
    """Extract v2 geometry from igVertexData items with float component lists.

    Used by MUA PC and similar platforms. Each igVertexData has:
      - slot 3: ObjectRef -> igDataList (igVec3fList, igVec2fList, etc.)
      - slot 4: Enum -> componentType (POSITION=1, NORMAL=3, TEXCOORD=4, etc.)
      - slot 6: UnsignedInt -> componentSize (num logical elements per vertex,
        typically 1 for Vec3f meaning "1 Vec3f per vertex")

    The element byte size is determined by the data list type:
      igVec3fList -> 12 bytes, igVec2fList -> 8 bytes, igVec4fList -> 16 bytes,
      igFloatList -> 4 bytes, igUnsignedIntList -> 4 bytes, etc.

    Args:
        reader: IGBReader instance
        geom: ParsedGeometry to populate
        vdata_items: list of igVertexData IGBObject instances
        endian: endian string ('<' or '>')
        profile: GameProfile instance

    Returns:
        geom on success, None on failure
    """
    gc = profile.geometry if profile else None
    color_order = gc.color_byte_order if gc else "abgr"

    for vd in vdata_items:
        comp_type = VCOMP_NONE
        comp_size = 0
        data_list_obj = None

        for slot, val, fi in vd._raw_fields:
            if slot == 3 and fi.short_name == b"ObjectRef" and val != -1:
                ref = reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    data_list_obj = ref
            elif slot == 4 and fi.short_name == b"Enum":
                comp_type = val
            elif slot == 6 and fi.short_name == b"UnsignedInt":
                comp_size = val

        if data_list_obj is None or comp_size == 0:
            continue

        # Resolve the data list's MemoryRef to get raw bytes
        count = None
        mem_ref = None
        for slot, val, fi in data_list_obj._raw_fields:
            if fi.short_name == b"Int" and count is None:
                count = val
            elif fi.short_name == b"MemoryRef":
                mem_ref = val

        if count is None or count == 0 or mem_ref is None or mem_ref == -1:
            continue

        mem_block = reader.resolve_ref(mem_ref)
        if not isinstance(mem_block, IGBMemoryBlock) or not mem_block.data:
            continue

        data = mem_block.data
        # Determine element byte size from data list type and compute stride
        elem_bytes = _datalist_elem_size(data_list_obj)
        stride = elem_bytes * comp_size
        num_verts = count // comp_size if comp_size else count

        # For compound data list types (igVec2fList, igVec3fList, igVec4fList),
        # one element already IS one complete vertex attribute. comp_size is
        # irrelevant — stride is just elem_bytes and count is the vertex count.
        # comp_size only matters for SCALAR lists (igFloatList, igUnsignedCharList)
        # where multiple scalars combine into one vertex attribute.
        is_compound = (
            data_list_obj.is_type(b"igVec2fList") or
            data_list_obj.is_type(b"igVec3fList") or
            data_list_obj.is_type(b"igVec4fList")
        )
        if is_compound:
            stride = elem_bytes
            num_verts = count

        if comp_type == VCOMP_POSITION:
            if data_list_obj.is_type(b"igVec3fList") or elem_bytes == 12:
                for i in range(num_verts):
                    off = i * stride
                    if off + 12 <= len(data):
                        x, y, z = struct.unpack_from(endian + "fff", data, off)
                        geom.positions.append((x, y, z))

        elif comp_type == VCOMP_NORMAL:
            if data_list_obj.is_type(b"igVec3fList") or elem_bytes == 12:
                for i in range(num_verts):
                    off = i * stride
                    if off + 12 <= len(data):
                        nx, ny, nz = struct.unpack_from(endian + "fff", data, off)
                        geom.normals.append((nx, ny, nz))

        elif comp_type == VCOMP_TEXCOORD:
            if data_list_obj.is_type(b"igVec2fList") or elem_bytes == 8:
                for i in range(num_verts):
                    off = i * stride
                    if off + 8 <= len(data):
                        u, v = struct.unpack_from(endian + "ff", data, off)
                        geom.uvs.append((u, v))

        elif comp_type == VCOMP_COLOR:
            if data_list_obj.is_type(b"igUnsignedIntList"):
                for i in range(num_verts):
                    off = i * stride
                    if off + 4 <= len(data):
                        raw = struct.unpack_from(endian + "I", data, off)[0]
                        r, g, b, a = _decode_color_uint32(raw, color_order)
                        geom.colors.append((r, g, b, a))
            elif data_list_obj.is_type(b"igVec4fList"):
                for i in range(num_verts):
                    off = i * stride
                    if off + 16 <= len(data):
                        r, g, b, a = struct.unpack_from(endian + "ffff", data, off)
                        geom.colors.append((r, g, b, a))

        elif comp_type == VCOMP_WEIGHT:
            if data_list_obj.is_type(b"igFloatList"):
                for i in range(num_verts):
                    off = i * comp_size * 4
                    if off + comp_size * 4 <= len(data):
                        weights = struct.unpack_from(
                            endian + "f" * comp_size, data, off
                        )
                        geom.blend_weights.append(weights)
            elif data_list_obj.is_type(b"igVec2fList"):
                for i in range(num_verts):
                    off = i * 8
                    if off + 8 <= len(data):
                        w = struct.unpack_from(endian + "ff", data, off)
                        geom.blend_weights.append(w)
            elif data_list_obj.is_type(b"igVec3fList"):
                for i in range(num_verts):
                    off = i * 12
                    if off + 12 <= len(data):
                        w = struct.unpack_from(endian + "fff", data, off)
                        geom.blend_weights.append(w)

        elif comp_type == VCOMP_INDEX:
            if data_list_obj.is_type(b"igUnsignedCharList"):
                for i in range(num_verts):
                    off = i * comp_size
                    if off + comp_size <= len(data):
                        indices = struct.unpack_from(
                            "B" * comp_size, data, off
                        )
                        geom.blend_indices.append(indices)

        # BINORMAL and TANGENT (comp_type 7, 8) are skipped for import

    if not geom.positions:
        return None
    return geom


def _datalist_elem_size(data_list_obj):
    """Get the byte size of a single element in a data list by type name.

    Returns the byte size for known list types, or 4 as a default.
    """
    if data_list_obj.is_type(b"igVec4fList"):
        return 16
    elif data_list_obj.is_type(b"igVec3fList"):
        return 12
    elif data_list_obj.is_type(b"igVec2fList"):
        return 8
    elif data_list_obj.is_type(b"igFloatList"):
        return 4
    elif data_list_obj.is_type(b"igUnsignedIntList"):
        return 4
    elif data_list_obj.is_type(b"igIntList"):
        return 4
    elif data_list_obj.is_type(b"igShortList"):
        return 2
    elif data_list_obj.is_type(b"igUnsignedShortList"):
        return 2
    elif data_list_obj.is_type(b"igCharList"):
        return 1
    elif data_list_obj.is_type(b"igUnsignedCharList"):
        return 1
    elif data_list_obj.is_type(b"igVec4ucList"):
        return 4
    elif data_list_obj.is_type(b"igVec3ucList"):
        return 3
    return 4


def _extract_v2_fixed_point(reader, geom, vdata_items, endian, profile):
    """Extract v2 geometry from fixed-point igVertexData (PSP).

    PSP stores vertex components as fixed-point integers in igShortList,
    igCharList, or igUnsignedShortList. Each igVertexData has:
      - slot 3: ObjectRef -> igDataList (igShortList, igCharList, etc.)
      - slot 4: Enum -> componentType
      - slot 6: UnsignedInt -> componentSize (num components per vertex)
      - slot 8: UnsignedInt -> componentFraction (number of fraction bits)
      - slot 9: Vec4f -> componentFractionScale
      - slot 10: Vec4f -> componentFractionOffset

    Decoding formula:
        value = (raw_int / 2^frac_bits) * scale[i] + offset[i]

    Args:
        reader: IGBReader instance
        geom: ParsedGeometry to populate
        vdata_items: list of igVertexData IGBObject instances
        endian: endian string
        profile: GameProfile instance

    Returns:
        geom on success, None on failure
    """
    for vd in vdata_items:
        if not isinstance(vd, IGBObject):
            continue

        comp_type = VCOMP_NONE
        comp_size = 0
        frac_bits = 0
        frac_scale = None
        frac_offset = None
        data_list_obj = None

        for slot, val, fi in vd._raw_fields:
            if slot == 3 and fi.short_name == b"ObjectRef" and val != -1:
                ref = reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    data_list_obj = ref
            elif slot == 4 and fi.short_name == b"Enum":
                comp_type = val
            elif slot == 6 and fi.short_name == b"UnsignedInt":
                comp_size = val
            elif slot == 8 and fi.short_name == b"UnsignedInt":
                frac_bits = val
            elif slot == 9 and fi.short_name == b"Vec4f":
                frac_scale = val
            elif slot == 10 and fi.short_name == b"Vec4f":
                frac_offset = val

        if data_list_obj is None or comp_size == 0:
            continue

        # Skip platform-native stream types
        if comp_type in (VCOMP_STREAM_PSP, VCOMP_STREAM_XENON,
                         VCOMP_STREAM_PS2, VCOMP_STREAM_PS3):
            continue

        # Resolve the data list's MemoryRef
        count = None
        mem_ref = None
        for slot, val, fi in data_list_obj._raw_fields:
            if fi.short_name == b"Int" and count is None:
                count = val
            elif fi.short_name == b"MemoryRef":
                mem_ref = val

        if count is None or count == 0 or mem_ref is None or mem_ref == -1:
            continue

        mem_block = reader.resolve_ref(mem_ref)
        if not isinstance(mem_block, IGBMemoryBlock) or not mem_block.data:
            continue

        data = mem_block.data
        num_verts = count // comp_size if comp_size else count
        divisor = float(1 << frac_bits) if frac_bits > 0 else 1.0

        # Default scale/offset
        scale = frac_scale if frac_scale else tuple(1.0 for _ in range(comp_size))
        offset = frac_offset if frac_offset else tuple(0.0 for _ in range(comp_size))

        if comp_type == VCOMP_POSITION and comp_size >= 3:
            if data_list_obj.is_type(b"igShortList"):
                # int16 positions with fraction
                for i in range(num_verts):
                    off = i * comp_size * 2
                    if off + comp_size * 2 <= len(data):
                        raw = struct.unpack_from(endian + "h" * comp_size, data, off)
                        x = (raw[0] / divisor) * scale[0] + offset[0]
                        y = (raw[1] / divisor) * scale[1] + offset[1]
                        z = (raw[2] / divisor) * scale[2] + offset[2]
                        geom.positions.append((x, y, z))
            elif data_list_obj.is_type(b"igVec3fList"):
                # Float positions (fallback)
                for i in range(num_verts):
                    off = i * 12
                    if off + 12 <= len(data):
                        x, y, z = struct.unpack_from(endian + "fff", data, off)
                        geom.positions.append((x, y, z))

        elif comp_type == VCOMP_NORMAL and comp_size >= 3:
            if data_list_obj.is_type(b"igCharList"):
                # int8 normals with fraction
                for i in range(num_verts):
                    off = i * comp_size
                    if off + comp_size <= len(data):
                        raw = struct.unpack_from("b" * comp_size, data, off)
                        nx = (raw[0] / divisor) * scale[0] + offset[0]
                        ny = (raw[1] / divisor) * scale[1] + offset[1]
                        nz = (raw[2] / divisor) * scale[2] + offset[2]
                        # Normalize
                        length = math.sqrt(nx*nx + ny*ny + nz*nz)
                        if length > 0.001:
                            nx /= length
                            ny /= length
                            nz /= length
                        geom.normals.append((nx, ny, nz))
            elif data_list_obj.is_type(b"igVec3fList"):
                for i in range(num_verts):
                    off = i * 12
                    if off + 12 <= len(data):
                        nx, ny, nz = struct.unpack_from(endian + "fff", data, off)
                        geom.normals.append((nx, ny, nz))

        elif comp_type == VCOMP_TEXCOORD and comp_size >= 2:
            if data_list_obj.is_type(b"igUnsignedShortList"):
                # uint16 UVs with fraction
                for i in range(num_verts):
                    off = i * comp_size * 2
                    if off + comp_size * 2 <= len(data):
                        raw = struct.unpack_from(endian + "H" * comp_size, data, off)
                        u = (raw[0] / divisor) * scale[0] + offset[0]
                        v = (raw[1] / divisor) * scale[1] + offset[1]
                        geom.uvs.append((u, v))
            elif data_list_obj.is_type(b"igShortList"):
                # int16 UVs with fraction
                for i in range(num_verts):
                    off = i * comp_size * 2
                    if off + comp_size * 2 <= len(data):
                        raw = struct.unpack_from(endian + "h" * comp_size, data, off)
                        u = (raw[0] / divisor) * scale[0] + offset[0]
                        v = (raw[1] / divisor) * scale[1] + offset[1]
                        geom.uvs.append((u, v))
            elif data_list_obj.is_type(b"igVec2fList"):
                for i in range(num_verts):
                    off = i * 8
                    if off + 8 <= len(data):
                        u, v = struct.unpack_from(endian + "ff", data, off)
                        geom.uvs.append((u, v))

        # Other component types (COLOR, WEIGHT, INDEX) — not typically
        # present in PSP fixed-point data but handle float fallback
        elif comp_type == VCOMP_COLOR:
            if data_list_obj.is_type(b"igUnsignedIntList"):
                for i in range(num_verts):
                    off = i * 4
                    if off + 4 <= len(data):
                        raw = struct.unpack_from(endian + "I", data, off)[0]
                        r, g, b, a = _decode_color_uint32(raw, "abgr")
                        geom.colors.append((r, g, b, a))

    if not geom.positions:
        return None
    return geom


# ---------------------------------------------------------------------------
# PS2 VIF (Vector Interface) packet decoding
# ---------------------------------------------------------------------------

# VIF opcodes
_VIF_NOP = 0x00
_VIF_STCYCL = 0x01
_VIF_STMOD = 0x05
_VIF_MARK = 0x07
_VIF_FLUSHE = 0x10
_VIF_FLUSH = 0x11
_VIF_FLUSHA = 0x13
_VIF_MSCAL = 0x14
_VIF_MSCALF = 0x15
_VIF_MSCNT = 0x17
_VIF_STMASK = 0x20
_VIF_STROW = 0x30
_VIF_STCOL = 0x31

# UNPACK format codes
_UNPACK_V2_8 = 0x06
_UNPACK_V3_8 = 0x0A
_UNPACK_V4_8 = 0x0E
_UNPACK_V3_16 = 0x09
_UNPACK_V4_32 = 0x0C

# Bytes per element for UNPACK formats
_UNPACK_ELEM_SIZE = {
    0x00: 4, 0x01: 2, 0x02: 1,
    0x04: 8, 0x05: 4, 0x06: 2,
    0x08: 12, 0x09: 6, 0x0A: 3,
    0x0C: 16, 0x0D: 8, 0x0E: 4,
    0x0F: 2,
}


def _int_bits_to_float(i):
    """Reinterpret a 32-bit unsigned int as IEEE 754 float."""
    return struct.unpack('<f', struct.pack('<I', i & 0xFFFFFFFF))[0]


def _parse_vif_commands(data):
    """Parse raw VIF packet data into a list of command dicts.

    Each command has at minimum: 'op' (string) and 'pos' (byte offset).
    UNPACK commands also have: 'vnvl', 'num', 'addr', 'usn', 'flg', 'data'.
    """
    pos = 0
    cmds = []

    while pos < len(data) - 3:
        word = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        cmd_byte = (word >> 24) & 0x7F
        imm = word & 0xFFFF

        if cmd_byte == _VIF_NOP:
            cmds.append({'op': 'NOP', 'pos': pos - 4})

        elif cmd_byte == _VIF_STCYCL:
            cmds.append({'op': 'STCYCL', 'cl': imm & 0xFF,
                         'wl': (imm >> 8) & 0xFF, 'pos': pos - 4})

        elif cmd_byte == _VIF_STMOD:
            cmds.append({'op': 'STMOD', 'mode': imm & 0x03, 'pos': pos - 4})

        elif cmd_byte == _VIF_MSCAL:
            cmds.append({'op': 'MSCAL', 'addr': imm, 'pos': pos - 4})

        elif cmd_byte == _VIF_MSCNT:
            cmds.append({'op': 'MSCNT', 'pos': pos - 4})

        elif cmd_byte == _VIF_STMASK:
            if pos + 4 <= len(data):
                mask = struct.unpack_from('<I', data, pos)[0]
                pos += 4
                cmds.append({'op': 'STMASK', 'mask': mask, 'pos': pos - 8})

        elif cmd_byte == _VIF_STROW:
            if pos + 16 <= len(data):
                row = struct.unpack_from('<4I', data, pos)
                pos += 16
                cmds.append({'op': 'STROW', 'row': row, 'pos': pos - 20})

        elif cmd_byte in (_VIF_FLUSH, _VIF_FLUSHE, _VIF_FLUSHA):
            cmds.append({'op': 'FLUSH', 'pos': pos - 4})

        elif cmd_byte == _VIF_MARK:
            cmds.append({'op': 'MARK', 'pos': pos - 4})

        elif cmd_byte >= 0x60:
            # UNPACK command
            vnvl = cmd_byte & 0x0F
            num = (word >> 16) & 0xFF
            addr = imm & 0x3FF
            usn = (word >> 14) & 1
            flg = (word >> 15) & 1

            elem_size = _UNPACK_ELEM_SIZE.get(vnvl, 4)
            data_size = num * elem_size
            data_size_aligned = (data_size + 3) & ~3

            if pos + data_size_aligned <= len(data):
                raw = data[pos:pos + data_size]
                cmds.append({
                    'op': 'UNPACK', 'vnvl': vnvl, 'num': num,
                    'addr': addr, 'usn': usn, 'flg': flg,
                    'data': raw, 'pos': pos - 4,
                })
                pos += data_size_aligned
            else:
                break
        else:
            cmds.append({'op': 'UNKNOWN', 'pos': pos - 4})

    return cmds


def _decode_vif_vertex_data(vif_data):
    """Decode PS2 VIF packets into vertex batches.

    The VIF data contains multiple batches separated by MSCAL commands.
    Each batch has the structure:

        1. V4-32 x1 at VU[29]: Center biases (pos_bias, nrm_bias, uv_bias, 0)
        2. V3-32 x1 at VU[2..17]: Per-vertex bone config (skipped)
        3. STMASK + STROW + STMOD=offset
        4. V3-16 unsigned x N: Positions
        5. V3-8 unsigned x N: Normals
        6. V2-8 unsigned x N: UVs
        7. V4-8 x1 at VU[0]: Batch control flags
        8. MSCAL: Execute VU1 program

    The IEEE 754 bias trick:
        result = float(raw_uint + STROW_int32) - center_bias
    converts packed integers to floating point efficiently on PS2 hardware.

    Returns:
        List of batch dicts with 'positions', 'normals', 'uvs', 'num_verts'.
    """
    cmds = _parse_vif_commands(vif_data)

    # Split into batches (MSCAL/MSCNT delimited)
    batches_cmds = []
    current = []
    for cmd in cmds:
        current.append(cmd)
        if cmd['op'] in ('MSCAL', 'MSCNT'):
            batches_cmds.append(current)
            current = []
    if current and any(c['op'] == 'UNPACK' for c in current):
        batches_cmds.append(current)

    results = []

    # Center biases from first batch's V4-32 at VU[29]
    pos_bias = None
    nrm_bias = None
    uv_bias = None

    for batch_cmds in batches_cmds:
        positions = []
        normals = []
        uvs = []
        strow = (0, 0, 0, 0)
        stmod = 0  # 0=normal, 1=offset

        for cmd in batch_cmds:
            if cmd['op'] == 'STROW':
                strow = cmd['row']
            elif cmd['op'] == 'STMOD':
                stmod = cmd['mode']
            elif cmd['op'] == 'UNPACK':
                vnvl = cmd['vnvl']
                num = cmd['num']
                addr = cmd['addr']
                usn = cmd['usn']
                raw = cmd['data']

                # V4-32 x1 at VU[29]: center biases
                if vnvl == _UNPACK_V4_32 and num == 1 and addr == 29:
                    x, y, z, w = struct.unpack_from('<ffff', raw)
                    pos_bias = x
                    nrm_bias = y
                    uv_bias = z

                # V3-16 unsigned with offset mode: positions
                elif vnvl == _UNPACK_V3_16 and usn and stmod == 1 and num > 1:
                    if pos_bias is not None:
                        sw = strow[0]  # Same STROW for all 3 components
                        for j in range(num):
                            off = j * 6
                            if off + 6 > len(raw):
                                break
                            rx, ry, rz = struct.unpack_from('<HHH', raw, off)
                            fx = _int_bits_to_float(rx + sw) - pos_bias
                            fy = _int_bits_to_float(ry + sw) - pos_bias
                            fz = _int_bits_to_float(rz + sw) - pos_bias
                            positions.append((fx, fy, fz))

                # V3-8 unsigned: normals
                elif vnvl == _UNPACK_V3_8 and usn and num > 1:
                    if nrm_bias is not None:
                        sw = strow[0]
                        for j in range(num):
                            off = j * 3
                            if off + 3 > len(raw):
                                break
                            rx, ry, rz = struct.unpack_from('<BBB', raw, off)
                            fx = _int_bits_to_float(rx + sw) - nrm_bias
                            fy = _int_bits_to_float(ry + sw) - nrm_bias
                            fz = _int_bits_to_float(rz + sw) - nrm_bias
                            # Normalize to unit length
                            length = math.sqrt(fx*fx + fy*fy + fz*fz)
                            if length > 0.001:
                                fx /= length
                                fy /= length
                                fz /= length
                            normals.append((fx, fy, fz))

                # V2-8 unsigned: UVs
                elif vnvl == _UNPACK_V2_8 and usn and num > 1:
                    if uv_bias is not None:
                        sw = strow[0]
                        for j in range(num):
                            off = j * 2
                            if off + 2 > len(raw):
                                break
                            ru, rv = struct.unpack_from('<BB', raw, off)
                            fu = _int_bits_to_float(ru + sw) - uv_bias
                            fv = _int_bits_to_float(rv + sw) - uv_bias
                            uvs.append((fu, fv))

        if positions:
            results.append({
                'positions': positions,
                'normals': normals,
                'uvs': uvs,
                'num_verts': len(positions),
            })

    return results
