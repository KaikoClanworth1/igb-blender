"""Build a brand-new PS2 CLUT texture IGB from scratch.

Creates a minimal IGB file with PSMT8 palette-based textures that work
in both XML2 and MUA on all platforms. Builds a proper scene graph
matching real game texture-only IGBs (igSceneInfo -> igAttrSet -> igTextureAttr).
"""

import struct

from ..igb_format.igb_writer import (
    IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
    EntryDef, ObjectDef, MemoryBlockDef, ObjectFieldDef,
)
from ..exporter.skin_builder import META_FIELDS, ALIGNMENT_BUFFER, MEMORY_POOL_NAMES


# ============================================================================
# Field type indices (into META_FIELDS)
# ============================================================================

_ObjRef = 0
_Int = 1
_Bool = 2
_String = 4
_Enum = 6
_MemRef = 7
_UInt = 9
_Long = 12
_Short = 17
_Vec3f = 36


# ============================================================================
# Texture-only meta-object registry — 27 types
# Matches real game texture-only IGB structure (e.g. blank.igb) + igClut
# ============================================================================

TEXTURE_META_OBJECTS = [
    # [0] igObject
    ("igObject", 1, 0, -1, 2, []),
    # [1] igNamedObject
    ("igNamedObject", 1, 0, 0, 3, [(_String, 2, 4)]),
    # [2] igDirEntry
    ("igDirEntry", 1, 0, 1, 7, [(_String, 2, 4)]),
    # [3] igObjectDirEntry
    ("igObjectDirEntry", 1, 0, 2, 13,
     [(_String, 2, 4), (_Int, 11, 4), (_Int, 12, 4)]),
    # [4] igMemoryDirEntry
    ("igMemoryDirEntry", 1, 0, 2, 14,
     [(_String, 2, 4), (_Int, 7, 4), (_Int, 10, 4),
      (_Bool, 11, 1), (_Int, 12, 4), (_Int, 13, 4)]),
    # [5] igExternalDirEntry
    ("igExternalDirEntry", 1, 0, 2, 11,
     [(_String, 2, 4), (_String, 7, 4), (_String, 8, 4), (_Int, 9, 4)]),
    # [6] igExternalImageEntry
    ("igExternalImageEntry", 1, 0, 5, 11,
     [(_String, 2, 4), (_String, 7, 4), (_String, 8, 4), (_Int, 9, 4)]),
    # [7] igExternalIndexedEntry
    ("igExternalIndexedEntry", 1, 0, 2, 13,
     [(_String, 2, 4), (_Int, 7, 4), (_Int, 8, 4), (_Int, 10, 4), (_Int, 12, 4)]),
    # [8] igExternalInfoEntry
    ("igExternalInfoEntry", 1, 0, 2, 10,
     [(_String, 2, 4), (_String, 7, 4), (_Int, 8, 4), (_String, 9, 4)]),
    # [9] igDataList
    ("igDataList", 1, 0, 0, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [10] igObjectList
    ("igObjectList", 1, 0, 9, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [11] igInfo
    ("igInfo", 1, 0, 1, 5, [(_String, 2, 4), (_Bool, 4, 1)]),
    # [12] igImage
    ("igImage", 1, 0, 0, 23,
     [(_UInt, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4),
      (_UInt, 6, 4), (_UInt, 7, 4), (_UInt, 8, 4), (_UInt, 9, 4), (_UInt, 10, 4),
      (_Enum, 11, 4), (_Int, 12, 4), (_MemRef, 13, 4), (_MemRef, 14, 4),
      (_Bool, 15, 1), (_UInt, 16, 4), (_ObjRef, 17, 4), (_UInt, 18, 4),
      (_Int, 19, 4), (_Bool, 20, 1), (_UInt, 21, 4), (_String, 22, 4)]),
    # [13] igNode
    ("igNode", 1, 0, 1, 7,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4)]),
    # [14] igGroup
    ("igGroup", 1, 0, 13, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4)]),
    # [15] igAttrSet
    ("igAttrSet", 1, 0, 14, 10,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [16] igNodeList
    ("igNodeList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [17] igAttrList
    ("igAttrList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [18] igTextureList
    ("igTextureList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [19] igGraphPathList
    ("igGraphPathList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [20] igImageMipMapList
    ("igImageMipMapList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [21] igSceneInfo
    ("igSceneInfo", 1, 0, 11, 12,
     [(_String, 2, 4), (_Bool, 4, 1), (_ObjRef, 5, 4), (_ObjRef, 6, 4),
      (_ObjRef, 7, 4), (_Long, 8, 8), (_Long, 9, 8), (_Vec3f, 10, 12),
      (_ObjRef, 11, 4)]),
    # [22] igInfoList
    ("igInfoList", 1, 0, 10, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [23] igAttr
    ("igAttr", 1, 0, 0, 4, [(_Short, 2, 2)]),
    # [24] igVisualAttribute
    ("igVisualAttribute", 1, 0, 23, 4, [(_Short, 2, 2)]),
    # [25] igTextureAttr
    ("igTextureAttr", 1, 0, 24, 19,
     [(_Short, 2, 2), (_UInt, 4, 4), (_Enum, 5, 4), (_Enum, 6, 4),
      (_Enum, 7, 4), (_Enum, 8, 4), (_Enum, 10, 4), (_Enum, 11, 4),
      (_ObjRef, 12, 4), (_Bool, 13, 1), (_ObjRef, 14, 4), (_Int, 15, 4),
      (_ObjRef, 16, 4)]),
    # [26] igClut (PS2 CLUT palette)
    ("igClut", 1, 0, 0, 9, [
        (_Enum, 2, 4), (_UInt, 3, 4), (_Int, 4, 4),
        (_MemRef, 5, 4), (_Int, 6, 4),
    ]),
]

# Meta-object indices
TMO_OBJECT = 0
TMO_OBJECT_DIR_ENTRY = 3
TMO_MEMORY_DIR_ENTRY = 4
TMO_EXTERNAL_INFO_ENTRY = 8
TMO_IMAGE = 12
TMO_ATTR_SET = 15
TMO_NODE_LIST = 16
TMO_ATTR_LIST = 17
TMO_TEXTURE_LIST = 18
TMO_GRAPH_PATH_LIST = 19
TMO_MIPMAP_LIST = 20
TMO_SCENE_INFO = 21
TMO_INFO_LIST = 22
TMO_TEXTURE_ATTR = 25
TMO_CLUT = 26


def build_texture_igb(textures, output_path):
    """Build a new PS2 CLUT texture IGB from scratch.

    Args:
        textures: list of (palette_bytes, index_bytes, width, height, name)
            palette_bytes: 1024 bytes (256 RGBA palette entries)
            index_bytes: width*height bytes (palette indices)
            width: image width
            height: image height
            name: texture name string
        output_path: path to write the IGB file
    """
    builder = _TextureIGBBuilder()
    for palette, indices, w, h, name in textures:
        builder.add_texture(palette, indices, w, h, name)
    builder.write(output_path)


class _TextureIGBBuilder:
    """Lightweight IGB builder for texture-only files."""

    def __init__(self):
        self._obj_list = []
        self._ref_infos = []
        self._tex_attr_indices = []
        self._tex_image_indices = []

    def _add_obj(self, meta_obj_idx, fields):
        """Add an object, return its global index."""
        idx = len(self._obj_list)
        self._obj_list.append(('obj', meta_obj_idx, fields))
        self._ref_infos.append({
            'is_object': True,
            'type_index': meta_obj_idx,
            'type_name': TEXTURE_META_OBJECTS[meta_obj_idx][0].encode(),
            'mem_pool_handle': -1,
        })
        return idx

    def _add_mem(self, type_idx, data, align_type=-1, pool=-1):
        """Add a memory block, return its global index."""
        idx = len(self._obj_list)
        self._obj_list.append(('mem', type_idx, data))
        self._ref_infos.append({
            'is_object': False,
            'type_index': type_idx,
            'type_name': TEXTURE_META_OBJECTS[type_idx][0].encode(),
            'mem_size': len(data),
            'ref_counted': 1,
            'align_type_idx': align_type,
            'mem_pool_handle': pool,
        })
        return idx

    def add_texture(self, palette_data, index_data, width, height, name):
        """Add a CLUT texture to the IGB.

        Args:
            palette_data: 1024 bytes (256 RGBA palette entries)
            index_data: width*height bytes (palette indices)
            width: image width
            height: image height
            name: texture name string
        """
        # igClut object + palette memory block
        palette_mb = self._add_mem(TMO_EXTERNAL_INFO_ENTRY, palette_data,
                                   align_type=1)
        clut_idx = self._add_obj(TMO_CLUT, [
            (2, 7, 'Enum', 4),              # _fmt = RGBA_8888_32
            (3, 256, 'UnsignedInt', 4),      # _numEntries
            (4, 4, 'Int', 4),                # _stride
            (5, palette_mb, 'MemoryRef', 4), # _pData
            (6, 1024, 'Int', 4),             # _clutSize
        ])

        # igImage object + pixel index memory block
        pixel_mb = self._add_mem(TMO_EXTERNAL_INFO_ENTRY, index_data,
                                 align_type=1)
        img_size = len(index_data)

        base_img_idx = self._add_obj(TMO_IMAGE, [
            (2, width, 'UnsignedInt', 4),        # _px (width)
            (3, height, 'UnsignedInt', 4),        # _py (height)
            (4, 1, 'UnsignedInt', 4),             # components=1 (indexed)
            (5, 1, 'UnsignedInt', 4),             # orderPreservation
            (6, 100, 'UnsignedInt', 4),           # _order = DEFAULT
            (7, 0, 'UnsignedInt', 4),             # bitsRed=0
            (8, 0, 'UnsignedInt', 4),             # bitsGreen=0
            (9, 0, 'UnsignedInt', 4),             # bitsBlue=0
            (10, 0, 'UnsignedInt', 4),            # bitsAlpha=0
            (11, 65536, 'Enum', 4),               # pfmt=PSMT8
            (12, img_size, 'Int', 4),             # imageSize
            (13, pixel_mb, 'MemoryRef', 4),       # pixel data (indices)
            (14, -1, 'MemoryRef', 4),             # unused
            (15, 1, 'Bool', 1),                   # localImage=true
            (16, 0, 'UnsignedInt', 4),
            (17, clut_idx, 'ObjectRef', 4),       # -> igClut
            (18, 8, 'UnsignedInt', 4),            # bitsPerPixel=8
            (19, width, 'Int', 4),                # bytesPerRow
            (20, 0, 'Bool', 1),                   # compressed=false
            (21, 0, 'UnsignedInt', 4),
            (22, name, 'String', 4),              # texture name
        ])

        # igImageMipMapList (empty — no mipmaps for CLUT)
        mipmap_list_idx = self._add_obj(TMO_MIPMAP_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # igTextureAttr
        tex_attr_idx = self._add_obj(TMO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),                   # _priority
            (4, 0, 'UnsignedInt', 4),
            (5, 0, 'Enum', 4),                    # magFilter=NEAREST
            (6, 0, 'Enum', 4),                    # minFilter=NEAREST
            (7, 1, 'Enum', 4),                    # wrapS=REPEAT
            (8, 1, 'Enum', 4),                    # wrapT=REPEAT
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx, 'ObjectRef', 4),   # base igImage
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, 1, 'Int', 4),                    # imageCount=1
            (16, mipmap_list_idx, 'ObjectRef', 4), # mipmap list
        ])

        self._tex_attr_indices.append(tex_attr_idx)
        self._tex_image_indices.append(base_img_idx)

    def write(self, output_path):
        """Finalize and write the IGB file."""
        writer = self._init_writer()

        # --- igAttrList (contains all texture attrs) ---
        n_attrs = len(self._tex_attr_indices)
        attr_refs_data = struct.pack(
            "<" + "i" * n_attrs, *self._tex_attr_indices)
        attr_refs_mb = self._add_mem(TMO_OBJECT, attr_refs_data)
        attr_list_idx = self._add_obj(TMO_ATTR_LIST, [
            (2, n_attrs, 'Int', 4),
            (3, n_attrs, 'Int', 4),
            (4, attr_refs_mb, 'MemoryRef', 4),
        ])

        # --- igNodeList (empty children for AttrSet) ---
        children_list_idx = self._add_obj(TMO_NODE_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # --- igAttrSet (root scene graph node) ---
        attrset_idx = self._add_obj(TMO_ATTR_SET, [
            (2, 'Scene Graph', 'String', 4),   # name
            (3, -1, 'ObjectRef', 4),           # _propertyList = none
            (5, 0, 'Int', 4),                  # container flags
            (7, children_list_idx, 'ObjectRef', 4),  # children
            (8, attr_list_idx, 'ObjectRef', 4),      # attrs
            (9, 0, 'Bool', 1),
        ])

        # --- igTextureList (all image refs) ---
        n_tex = len(self._tex_image_indices)
        tex_refs_data = struct.pack(
            "<" + "i" * n_tex, *self._tex_image_indices)
        tex_refs_mb = self._add_mem(TMO_OBJECT, tex_refs_data)
        texture_list_idx = self._add_obj(TMO_TEXTURE_LIST, [
            (2, n_tex, 'Int', 4),
            (3, n_tex, 'Int', 4),
            (4, tex_refs_mb, 'MemoryRef', 4),
        ])

        # --- igGraphPathList (empty) ---
        graph_path_idx = self._add_obj(TMO_GRAPH_PATH_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # --- igNodeList for SceneInfo (empty) ---
        scene_node_list_idx = self._add_obj(TMO_NODE_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # --- igSceneInfo ---
        scene_info_idx = self._add_obj(TMO_SCENE_INFO, [
            (2, 'Scene Graph', 'String', 4),
            (4, 1, 'Bool', 1),
            (5, attrset_idx, 'ObjectRef', 4),      # _sceneGraph
            (6, texture_list_idx, 'ObjectRef', 4),  # _textureList
            (7, graph_path_idx, 'ObjectRef', 4),    # _graphPathList
            (8, 0, 'Long', 8),
            (9, 0, 'Long', 8),
            (10, (0.0, 0.0, 1.0), 'Vec3f', 12),   # up vector (Z-up)
            (11, scene_node_list_idx, 'ObjectRef', 4),
        ])

        # --- igInfoList (root — contains igSceneInfo) ---
        info_refs = struct.pack("<i", scene_info_idx)
        info_refs_mb = self._add_mem(TMO_OBJECT, info_refs)
        info_list_idx = self._add_obj(TMO_INFO_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, info_refs_mb, 'MemoryRef', 4),
        ])

        self._finalize_writer(writer, info_list_idx)
        writer.write(output_path)

    def _init_writer(self):
        """Create and configure a new IGBWriter."""
        writer = IGBWriter()
        writer.version = 6
        writer.endian = "<"
        writer.has_info = True
        writer.has_external = True
        writer.shared_entries = True
        writer.has_memory_pool_names = True

        writer.meta_fields = [
            MetaFieldDef(n, maj, min_) for n, maj, min_ in META_FIELDS]

        writer.meta_objects = []
        for name, major, minor, parent_idx, slot_count, fields \
                in TEXTURE_META_OBJECTS:
            field_defs = [MetaObjectFieldDef(ti, slot, size)
                          for ti, slot, size in fields]
            writer.meta_objects.append(MetaObjectDef(
                name, major, minor, field_defs, parent_idx, slot_count))

        writer.alignment_data = ALIGNMENT_BUFFER
        writer.external_dirs = [b"system"]
        writer.external_dirs_unk = 1
        writer.memory_pool_names = list(MEMORY_POOL_NAMES)

        return writer

    def _finalize_writer(self, writer, info_list_idx):
        """Convert internal state into writer structures."""
        entries = []
        index_map = []

        for i, (kind, type_idx, _data) in enumerate(self._obj_list):
            if kind == 'obj':
                entries.append(EntryDef(
                    TMO_OBJECT_DIR_ENTRY, [0, type_idx, -1]))
            else:
                ri = self._ref_infos[i]
                entries.append(EntryDef(TMO_MEMORY_DIR_ENTRY, [
                    0, ri['mem_size'], ri['type_index'],
                    ri['ref_counted'], ri['align_type_idx'],
                    ri['mem_pool_handle'],
                ]))
            index_map.append(len(entries) - 1)

        writer.entries = entries
        writer.index_map = index_map
        writer.info_list_index = info_list_idx
        writer.ref_info = self._ref_infos

        writer.objects = []
        for _i, (kind, type_idx, data) in enumerate(self._obj_list):
            if kind == 'obj':
                raw_fields = []
                for slot, val, sname, size in data:
                    fd = ObjectFieldDef(
                        slot,
                        sname.encode() if isinstance(sname, str) else sname,
                        size,
                    )
                    raw_fields.append((slot, val, fd))
                writer.objects.append(ObjectDef(type_idx, raw_fields))
            else:
                writer.objects.append(MemoryBlockDef(data))
