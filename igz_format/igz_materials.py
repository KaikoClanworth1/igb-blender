"""IGZ material and texture extraction for MUA2 PC.

MUA2 uses external texture files (separate .igz files in materials/ folders).
The scene file contains igTextureBindAttr2 -> igTextureAttr2 references, and
igSceneTexturesInfo -> igStringRefList with paths to external texture IGZ files.

External texture IGZ files contain:
  - igImage2 object: width, height, mip count, name
  - Raw pixel data in the last chunk (DXT1 or DXT5 compressed)

Material/texture hierarchy in the scene graph:
  igAttrSet -> igAttrList -> [igMuaMaterialAttr, igTextureBindAttr2, igColorAttr, ...]
  igTextureBindAttr2 -> igTextureAttr2 (via ROFS at +0x18)
  igSceneTexturesInfo -> igStringRefList (parallel array with igTextureAttr2List)

Multi-texturing: scene graph nodes accumulate multiple igTextureBindAttr2 attrs.
Texture roles are determined by filename suffix:
  _d = diffuse/albedo, _n = normal map, _s = specular, _e = emissive, _m = metallic

Format hashes (from EXNM in texture IGZ files):
  0x883C45B2 = DXT1 (BC1)
  0x05ECD805 = DXT5 (BC3)
  0xB718471E = DXT5 (BC3) alternate
"""

import struct
import os
import re

from ..scene_graph.sg_materials import (
    ParsedMaterial, ParsedTexture, ParsedImage,
    PFMT_RGB_DXT1, PFMT_RGBA_DXT5, PFMT_RGBA_8888_32,
    WRAP_REPEAT,
)

# Format hash -> pixel format
_FORMAT_HASHES = {
    0x883C45B2: PFMT_RGB_DXT1,
    0x05ECD805: PFMT_RGBA_DXT5,
    0xB718471E: PFMT_RGBA_DXT5,
}

# --- Texture role classification ---

# Texture role constants
TEX_ROLE_DIFFUSE = 'diffuse'
TEX_ROLE_NORMAL = 'normal'
TEX_ROLE_SPECULAR = 'specular'
TEX_ROLE_EMISSIVE = 'emissive'
TEX_ROLE_METALLIC = 'metallic'
TEX_ROLE_CUBEMAP = 'cubemap'
TEX_ROLE_UNKNOWN = 'unknown'

# Suffix pattern: match _d, _n, _s, _e, _m before .igz extension
_SUFFIX_RE = re.compile(r'_([dnsem])\.igz$', re.IGNORECASE)
_CUBEMAP_RE = re.compile(r'_cubemap\.igz$', re.IGNORECASE)

_SUFFIX_TO_ROLE = {
    'd': TEX_ROLE_DIFFUSE,
    'n': TEX_ROLE_NORMAL,
    's': TEX_ROLE_SPECULAR,
    'e': TEX_ROLE_EMISSIVE,
    'm': TEX_ROLE_METALLIC,
}


def classify_texture_role(tex_path):
    """Classify a texture's role from its filename suffix.

    MUA2 texture naming convention:
        somename_d.igz -> diffuse
        somename_n.igz -> normal map
        somename_s.igz -> specular
        somename_e.igz -> emissive
        somename_m.igz -> metallic/mask

    Args:
        tex_path: texture file path (relative or absolute)

    Returns:
        One of TEX_ROLE_* constants
    """
    if tex_path is None:
        return TEX_ROLE_UNKNOWN

    basename = os.path.basename(tex_path).lower()

    if _CUBEMAP_RE.search(basename):
        return TEX_ROLE_CUBEMAP

    m = _SUFFIX_RE.search(basename)
    if m:
        return _SUFFIX_TO_ROLE.get(m.group(1).lower(), TEX_ROLE_UNKNOWN)

    # No recognized suffix — assume diffuse (most common)
    return TEX_ROLE_DIFFUSE


def extract_igz_material(reader, mat_obj):
    """Extract material properties from an igMuaMaterialAttr or igMaterialAttr.

    igMuaMaterialAttr (48B):
        +0x20..+0x2C: diffuse RGBA floats (often all 1.0)

    igMaterialAttr (112B):
        Uses same layout as IGB igMaterialAttr (legacy, very rare in MUA2).

    Returns:
        ParsedMaterial
    """
    mat = ParsedMaterial()
    mat.source_obj = mat_obj

    if mat_obj.type_name == 'igMuaMaterialAttr':
        # Read diffuse color at +0x20 (4 floats)
        if mat_obj.type_size >= 0x30:
            try:
                r = mat_obj.read_f32(0x20)
                g = mat_obj.read_f32(0x24)
                b = mat_obj.read_f32(0x28)
                a = mat_obj.read_f32(0x2C)
                mat.diffuse = (r, g, b, a)
            except (struct.error, IndexError):
                pass
    elif mat_obj.type_name == 'igMaterialAttr':
        # Legacy material - rare in MUA2
        if mat_obj.type_size >= 0x60:
            try:
                mat.shininess = mat_obj.read_f32(0x10)
                mat.diffuse = mat_obj.read_vec4f(0x18)
                mat.ambient = mat_obj.read_vec4f(0x28)
                mat.specular = mat_obj.read_vec4f(0x38)
                mat.emission = mat_obj.read_vec4f(0x48)
            except (struct.error, IndexError):
                pass

    return mat


def extract_igz_color(reader, color_obj):
    """Extract color from an igColorAttr (48B).

    igColorAttr fields (v6 32-bit offsets):
        +0x18: RGBA floats (4 * f32)

    Returns:
        tuple of (r, g, b, a) floats
    """
    try:
        return color_obj.read_vec4f(0x18)
    except (struct.error, IndexError):
        return None


def build_texture_path_map(reader, game_data_dir=None):
    """Build a mapping from igTextureAttr2 global offset -> external texture file path.

    The geometry IGZ file contains:
    1. igSceneTexturesInfo with ROFS at +0x28 -> igStringRefList
    2. igTextureAttr2List with parallel ROFS pointers to igTextureAttr2 objects
    3. igStringRefList contains RSTT string indices -> texture file paths in TSTR

    The igTextureAttr2List and igStringRefList have matching indices:
        igTextureAttr2List[i] corresponds to igStringRefList[i]

    Args:
        reader: IGZReader for the geometry file
        game_data_dir: path to MUA2 data/ folder (for resolving texture paths)

    Returns:
        dict mapping igTextureAttr2 global_offset -> absolute texture file path
    """
    tex_attr_to_path = {}

    # Find igSceneTexturesInfo
    scene_tex_infos = reader.get_objects_by_type('igSceneTexturesInfo')
    if not scene_tex_infos:
        return tex_attr_to_path

    # Find igStringRefList (contains texture file paths)
    string_ref_lists = reader.get_objects_by_type('igStringRefList')
    if not string_ref_lists:
        return tex_attr_to_path

    # Find igTextureAttr2List (parallel to string ref list)
    tex_attr_lists = reader.get_objects_by_type('igTextureAttr2List')

    # Extract paths from igStringRefList
    # igDataList: +0x08=count(u32), +0x10=size(u24)|capacity, +0x14=data_ptr(ROFS->memref)
    srl = string_ref_lists[0]
    srl_count = srl.read_u32(0x10)

    # Get the data pointer (resolved ROFS at +0x20 for igDataList)
    srl_data_ref = srl.get_raw_ref(0x20)
    if not isinstance(srl_data_ref, int):
        # Try other known offsets
        srl_data_ref = srl.get_raw_ref(0x18)
        if not isinstance(srl_data_ref, int):
            return tex_attr_to_path

    # Read RSTT string indices from the data
    tex_paths = []
    string_refs = reader._string_refs_by_obj.get(srl.global_offset, {})
    # The data area contains RSTT-marked uint32 string indices
    # Read them from the resolved data pointer
    for i in range(srl_count):
        offset = srl_data_ref + i * 8  # 8 bytes per entry (4-byte index + 4-byte padding)
        if offset + 4 > len(reader.data):
            break
        str_idx = struct.unpack_from('<I', reader.data, offset)[0]
        if str_idx < len(reader.fixups.tstr):
            tex_paths.append(reader.fixups.tstr[str_idx])
        else:
            tex_paths.append(None)

    # Extract igTextureAttr2 objects from igTextureAttr2List (parallel array)
    tex_attrs = []
    if tex_attr_lists:
        tal = tex_attr_lists[0]
        tal_count = tal.read_u32(0x10)
        tal_data_ref = tal.get_raw_ref(0x20)
        if not isinstance(tal_data_ref, int):
            tal_data_ref = tal.get_raw_ref(0x18)

        if isinstance(tal_data_ref, int):
            for i in range(tal_count):
                offset = tal_data_ref + i * 8
                if offset + 4 > len(reader.data):
                    break
                encoded = struct.unpack_from('<I', reader.data, offset)[0]
                target_global = reader._get_global_offset(encoded)
                target_obj = reader.objects.get(target_global)
                tex_attrs.append(target_obj)

    # Build the mapping: tex_attr global_offset -> file path
    for i, path in enumerate(tex_paths):
        if path is None:
            continue
        # Filter out non-texture paths (e.g. object names like "igSceneInfo0")
        if not path.lower().endswith('.igz'):
            continue
        if i < len(tex_attrs) and tex_attrs[i] is not None:
            tex_attr_to_path[tex_attrs[i].global_offset] = path

    return tex_attr_to_path


def resolve_texture_bind(reader, texbind_obj, tex_attr_to_path):
    """Given an igTextureBindAttr2, find the linked texture path and sampler slot.

    igTextureBindAttr2 (40B):
        +0x10: sampler_slot (u16) — actual hardware sampler register (0-5)
        +0x18: ROFS -> igTextureAttr2
        +0x20: unit_id (u32) — always 0xFFFFFFFF in MUA2 (unused)

    Returns:
        (texture_file_path, sampler_slot) or (None, 0)
    """
    # Get linked igTextureAttr2
    tex_attr = texbind_obj.get_ref(0x18)
    if tex_attr is None:
        return None, 0

    # Read actual sampler slot from +0x10 (u16)
    sampler_slot = 0
    try:
        sampler_slot = texbind_obj.read_u16(0x10)
        if sampler_slot == 0xFFFF:
            sampler_slot = 0
    except (struct.error, IndexError):
        pass

    # Look up path
    path = tex_attr_to_path.get(tex_attr.global_offset)
    return path, sampler_slot


def load_external_texture(tex_path, game_data_dir=None, filepath=None):
    """Load an external texture IGZ file and extract the image data.

    External texture IGZ files have:
      - Chunk 0: fixups
      - Chunk 1: objects (igImage2 etc.)
      - Last chunk: raw pixel data (DXT compressed with mipmaps)
      - EXNM format hashes identifying DXT1 vs DXT5

    Args:
        tex_path: relative texture path (e.g. "materials\\actors\\foo_d.igz")
        game_data_dir: MUA2 data/ directory
        filepath: path to the geometry IGZ file (for relative resolution)

    Returns:
        ParsedImage or None
    """
    from .igz_reader import IGZReader

    # Resolve the actual file path
    abs_path = _find_texture_file(tex_path, game_data_dir, filepath)
    if abs_path is None:
        return None

    # Parse the texture IGZ file
    try:
        tex_reader = IGZReader(abs_path)
        tex_reader.read()
    except Exception:
        return None

    # Find igImage2 object
    images = tex_reader.get_objects_by_type('igImage2')
    if not images:
        return None

    img_obj = images[0]
    parsed_image = _extract_image2(tex_reader, img_obj)
    if parsed_image is not None:
        # Use absolute file path as cache key to avoid collisions between
        # different texture files whose igImage2 objects share the same offset
        parsed_image.cache_key = abs_path
    return parsed_image


def _extract_image2(reader, img_obj):
    """Extract image data from an igImage2 object.

    igImage2 (80B):
        +0x10: RSTT -> original path string
        +0x18: width (u16)
        +0x1A: height (u16)
        +0x1C: depth (u16, always 1)
        +0x1E: mipmap level count (u16)

    Pixel data is in the last chunk (raw data section).
    Format is determined from EXNM hashes or size-based detection.

    Returns:
        ParsedImage or None
    """
    data = reader.data

    # Read dimensions
    try:
        width = img_obj.read_u16(0x18)
        height = img_obj.read_u16(0x1A)
        mip_count = img_obj.read_u16(0x1E)
    except (struct.error, IndexError):
        return None

    if width == 0 or height == 0:
        return None

    # Get image name from RSTT at +0x10
    name = ""
    string_refs = reader._string_refs_by_obj.get(img_obj.global_offset, {})
    if 0x10 in string_refs:
        name = string_refs[0x10]

    # Find the raw pixel data section (last chunk, typically)
    pixel_data = _get_pixel_data_section(reader)
    if pixel_data is None:
        return None

    # Detect format from EXNM hashes
    pixel_format = _detect_pixel_format(reader, width, height, mip_count, len(pixel_data))

    # Calculate top mip size
    if pixel_format in (PFMT_RGB_DXT1,):
        block_size = 8  # DXT1: 8 bytes per 4x4 block
    else:
        block_size = 16  # DXT5: 16 bytes per 4x4 block

    top_mip_size = max(1, width // 4) * max(1, height // 4) * block_size

    # Extract only the top mipmap level
    top_mip_data = pixel_data[:top_mip_size]
    if len(top_mip_data) < top_mip_size:
        return None

    # Build ParsedImage
    img = ParsedImage()
    img.width = width
    img.height = height
    img.pixel_format = pixel_format
    img.compressed = True
    img.image_size = top_mip_size
    img.pixel_data = top_mip_data
    img.name = name
    img.source_obj = img_obj

    return img


def _get_pixel_data_section(reader):
    """Find and return the raw pixel data from the texture IGZ file.

    In texture IGZ files, pixel data is in the last chunk that has no
    RVTB objects (typically the last chunk overall, often named "Default"
    with index 0 or "Image" with index 8).

    Returns:
        bytes of raw pixel data or None
    """
    if len(reader.chunks) < 2:
        return None

    # Identify chunks that contain RVTB objects
    rvtb_chunks = {0}  # chunk 0 is always fixups
    for encoded_off in reader.fixups.rvtb:
        global_off = reader._get_global_offset(encoded_off)
        for ci, c in enumerate(reader.chunks):
            if c.offset <= global_off < c.offset + c.size:
                rvtb_chunks.add(ci)
                break

    # Find the data chunk - largest chunk without objects
    best_chunk = None
    best_size = 0
    for ci, c in enumerate(reader.chunks):
        if ci not in rvtb_chunks and c.size > best_size:
            best_chunk = c
            best_size = c.size

    if best_chunk is None or best_chunk.size < 16:
        return None

    # Data starts after alignment padding
    data_start = best_chunk.offset + best_chunk.alignment
    data_end = best_chunk.offset + best_chunk.size
    return reader.data[data_start:data_end]


def _detect_pixel_format(reader, width, height, mip_count, data_size):
    """Detect DXT format from EXNM hashes or data size comparison.

    EXNM contains format hash pairs. Known hashes:
        0x883C45B2 = DXT1
        0x05ECD805 = DXT5
        0xB718471E = DXT5

    Fallback: compare data_size against expected DXT1/DXT5 sizes with mipmaps.
    """
    # Try EXNM hash detection
    for h1, h2 in reader.fixups.exnm:
        fmt_hash = h1 if h1 != 0 else h2
        if fmt_hash in _FORMAT_HASHES:
            return _FORMAT_HASHES[fmt_hash]

    # Fallback: size-based detection
    mips = max(1, mip_count)
    dxt1_total = 0
    dxt5_total = 0
    for level in range(mips):
        mw = max(1, width >> level)
        mh = max(1, height >> level)
        blocks_w = max(1, mw // 4)
        blocks_h = max(1, mh // 4)
        dxt1_total += blocks_w * blocks_h * 8
        dxt5_total += blocks_w * blocks_h * 16

    # Check which matches better
    dxt1_diff = abs(data_size - dxt1_total)
    dxt5_diff = abs(data_size - dxt5_total)

    if dxt1_diff <= dxt5_diff and dxt1_diff < data_size * 0.1:
        return PFMT_RGB_DXT1
    elif dxt5_diff < data_size * 0.1:
        return PFMT_RGBA_DXT5

    # Default to DXT5 (most common in MUA2)
    return PFMT_RGBA_DXT5


def _find_texture_file(tex_path, game_data_dir=None, geometry_filepath=None):
    """Resolve a relative texture path to an absolute file path.

    MUA2 texture paths are relative to the data/ folder, e.g.:
        "materials\\actors\\a-bomb_anti_1_d.igz"

    We try:
    1. game_data_dir + tex_path (if provided)
    2. geometry file's grandparent dir + tex_path (Noesis convention)
    3. Search upward from geometry file for a directory containing "materials"

    Returns:
        absolute path string or None
    """
    # Normalize path separators
    tex_rel = tex_path.replace('\\', os.sep).replace('/', os.sep)

    # Strategy 1: explicit game data directory
    if game_data_dir:
        candidate = os.path.join(game_data_dir, tex_rel)
        if os.path.isfile(candidate):
            return candidate

    if geometry_filepath is None:
        return None

    geom_dir = os.path.dirname(geometry_filepath)

    # Strategy 2: grandparent (Noesis convention - parent of parent)
    grandparent = os.path.dirname(geom_dir)
    if grandparent:
        candidate = os.path.join(grandparent, tex_rel)
        if os.path.isfile(candidate):
            return candidate

    # Strategy 3: search upward for materials/ directory
    search_dir = geom_dir
    for _ in range(6):  # max 6 levels up
        candidate = os.path.join(search_dir, tex_rel)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent

    return None


# ===================================================================
# IGZ-specific state attribute extraction (v6 binary layout)
# ===================================================================

def extract_igz_blend_state(blend_state_obj):
    """Extract blend state from an IGZ igBlendStateAttr (32 bytes).

    +0x18: enabled (u32) — 1=blend ON, 0=OFF

    Returns:
        dict with 'enabled' key
    """
    try:
        enabled = blend_state_obj.read_u32(0x18)
        return {'enabled': bool(enabled)}
    except (struct.error, IndexError):
        return {'enabled': False}


def extract_igz_blend_function(blend_func_obj):
    """Extract blend function from an IGZ igBlendFunctionAttr (64 bytes).

    +0x18: src_blend (u32) — e.g. 4 = SRC_ALPHA
    +0x1C: dst_blend (u32) — e.g. 5 = ONE_MINUS_SRC_ALPHA

    Returns:
        dict with 'src', 'dst' keys
    """
    try:
        src = blend_func_obj.read_u32(0x18)
        dst = blend_func_obj.read_u32(0x1C)
        return {'src': src, 'dst': dst}
    except (struct.error, IndexError):
        return {'src': 4, 'dst': 5}


def extract_igz_alpha_state(alpha_state_obj):
    """Extract alpha test state from an IGZ igAlphaStateAttr (32 bytes).

    +0x18: enabled (u32) — 1=alpha test ON, 0=OFF

    Returns:
        dict with 'enabled' key
    """
    try:
        enabled = alpha_state_obj.read_u32(0x18)
        return {'enabled': bool(enabled)}
    except (struct.error, IndexError):
        return {'enabled': False}


def resolve_all_texture_binds(reader, texbind_list, tex_attr_to_path):
    """Resolve ALL texture binds for a geometry node into a role-classified dict.

    Iterates through the accumulated texbind_list from the scene graph walker,
    resolves each to a texture file path, classifies by filename suffix, and
    returns a dict mapping role -> (path, texbind_obj).

    If multiple textures map to the same role, the last one wins (scene graph
    child overrides parent, matching the inheritance order).

    Args:
        reader: IGZReader instance
        texbind_list: list of igTextureBindAttr2 IGZObject instances
        tex_attr_to_path: dict from build_texture_path_map()

    Returns:
        dict mapping TEX_ROLE_* -> (texture_file_path, texbind_obj)
    """
    role_map = {}
    if not texbind_list:
        return role_map

    for texbind_obj in texbind_list:
        path, sampler_slot = resolve_texture_bind(reader, texbind_obj,
                                                   tex_attr_to_path)
        if path is None:
            continue
        role = classify_texture_role(path)
        role_map[role] = (path, texbind_obj)

    return role_map
