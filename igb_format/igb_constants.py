"""Constants for the IGB binary format."""

# Magic cookie value indicating little-endian byte order
IGB_MAGIC_COOKIE = 0xFADA

# Header field indices (12 uint32 fields, 48 bytes total)
H_ENTRY_BUFFER_SIZE = 0
H_ENTRY_COUNT = 1
H_META_OBJ_BUFFER_SIZE = 2
H_META_OBJ_COUNT = 3
H_OBJ_BUFFER_SIZE = 4
H_OBJ_COUNT = 5
H_MREF_BUFFER_SIZE = 6
H_MREF_COUNT = 7
H_MF_BUFFER_SIZE = 8
H_MF_COUNT = 9
H_MAGIC_COOKIE = 10
H_VER_FLAGS = 11

# Header size in bytes
HEADER_SIZE = 0x30  # 48 bytes = 12 * 4

# Version flags (upper bits of verFlags field)
FLAG_HAS_INFO = 0x80000000           # Bit 31
FLAG_HAS_EXTERNAL = 0x40000000      # Bit 30
FLAG_SHARED_ENTRIES = 0x20000000    # Bit 29
FLAG_HAS_MEMORY_POOL_NAMES = 0x10000000  # Bit 28

# Version mask (lower 16 bits)
VERSION_MASK = 0xFFFF

# Entry types
ENTRY_TYPE_OBJECT = "igObjectDirEntry"
ENTRY_TYPE_MEMORY = "igMemoryDirEntry"

# Known meta-field short names and their struct format codes
# These map the "short name" (after stripping ig prefix and MetaField suffix) to struct format
FIELD_TYPE_FORMATS = {
    b"Bool": "B",           # 1 byte boolean
    b"Char": "b",           # 1 byte signed
    b"UnsignedChar": "B",   # 1 byte unsigned
    b"Short": "h",          # 2 byte signed
    b"UnsignedShort": "H",  # 2 byte unsigned
    b"Int": "i",            # 4 byte signed
    b"UnsignedInt": "I",    # 4 byte unsigned
    b"Long": "q",           # 8 byte signed
    b"UnsignedLong": "Q",   # 8 byte unsigned
    b"Float": "f",          # 4 byte float
    b"Double": "d",         # 8 byte double
    b"Vec2f": "ff",         # 2 floats
    b"Vec3f": "fff",        # 3 floats
    b"Vec4f": "ffff",       # 4 floats
    b"Quaternionf": "ffff", # 4 floats
    b"Matrix44f": "f" * 16, # 16 floats (4x4 matrix)
    b"Enum": "i",           # 4 byte enum (stored as int)
    b"Handle": "I",         # 4 byte handle
    b"BitField": "I",       # 4 byte bitfield
}

# These are variable-size field types that need special handling
VARIABLE_SIZE_FIELDS = {
    b"String",
    b"CharArray",
    b"UnsignedCharArray",
    b"FloatArray",
    b"IntArray",
    b"UnsignedIntArray",
    b"ShortArray",
    b"UnsignedShortArray",
}

# Reference types
REFERENCE_FIELDS = {
    b"ObjectRef",
    b"MemoryRef",
}

# Known Alchemy class names for scene graph objects
CLASS_SCENE_INFO = b"igSceneInfo"
CLASS_NODE = b"igNode"
CLASS_GROUP = b"igGroup"
CLASS_TRANSFORM = b"igTransform"
CLASS_ATTR_SET = b"igAttrSet"
CLASS_GEOMETRY = b"igGeometry"
CLASS_GEOMETRY_ATTR = b"igGeometryAttr"
CLASS_GEOMETRY_ATTR2 = b"igGeometryAttr2"
CLASS_MATERIAL_ATTR = b"igMaterialAttr"
CLASS_TEXTURE_BIND_ATTR = b"igTextureBindAttr"
CLASS_TEXTURE_ATTR = b"igTextureAttr"
CLASS_IMAGE = b"igImage"
CLASS_SKELETON = b"igSkeleton"
CLASS_SKELETON_BONE_INFO = b"igSkeletonBoneInfo"
CLASS_ACTOR = b"igActor"
CLASS_SKIN = b"igSkin"
CLASS_APPEARANCE = b"igAppearance"
CLASS_ANIMATION = b"igAnimation"
CLASS_ANIMATION_DATABASE = b"igAnimationDatabase"
CLASS_VERTEX_ARRAY2 = b"igVertexArray2"
CLASS_INDEX_ARRAY = b"igIndexArray"
CLASS_LIGHT_SET = b"igLightSet"
CLASS_LIGHT_ATTR = b"igLightAttr"
CLASS_CAMERA = b"igCamera"

# Node flags (from igNode._flags)
NODE_FLAG_HAS_LIGHT = 0x01
NODE_FLAG_HAS_TRANSFORM = 0x02
NODE_FLAG_USE_RENDER_LIST = 0x04
NODE_FLAG_IS_COLLIDABLE = 0x10
NODE_FLAG_IS_INVISIBLE = 0x20
NODE_FLAG_IS_DYNAMIC = 0x40
NODE_FLAG_IS_DYNAMIC_GEOMETRY = 0x80

# Primitive types for geometry
PRIM_TRIANGLES = 0x04        # IG_GFX_DRAW_TRIANGLES
PRIM_TRIANGLE_STRIP = 0x05   # IG_GFX_DRAW_TRIANGLE_STRIP
PRIM_TRIANGLE_FAN = 0x06     # IG_GFX_DRAW_TRIANGLE_FAN
