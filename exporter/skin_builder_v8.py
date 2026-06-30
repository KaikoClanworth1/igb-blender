"""From-scratch v8 (MUA / Alchemy 5.0) skin builder.

Builds a native-style version-8 IGB skin without any template, mirroring the
proven 3ds-Max-2012 export structure (separate typed vertex lists, NON-indexed
triangle list). See memory/mua_v8_native_export.md for the full reverse-
engineered format spec. The IGBWriter v8 name-pool support (igb_writer.py) is a
prerequisite (object String fields serialize as 4-byte name-pool indices).

Geometry layout (per igGeometryAttr2):
    igGeometryAttr2 (primType=3 TRI LIST, s5 index=-1 -> non-indexed)
      -> igVertexArray2
           s3 -> igObjectList of igVertexData (one per component)
           s4 -> igVertexStream
      components: POSITION->igVec3fList, NORMAL->igVec3fList, TEXCOORD->igVec2fList,
                  WEIGHT->igFloatList, INDEX(blend)->igUnsignedCharList
                  (+ TANGENT/BINORMAL igVec3fList when normal-mapped)
"""

import struct

from ..igb_format.igb_writer import (
    IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
    EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
)
from .skin_builder_v8_meta import (
    V8_META_FIELDS, V8_META_OBJECTS, V8_ALIGNMENT, V8_MEMORY_POOL_NAMES)


# meta-object name -> index
_MO = {t[0]: i for i, t in enumerate(V8_META_OBJECTS)}

# Memory blocks are tagged with a generic base type (igObject), matching what
# the proven v6/Max builders do — the _memTypeIndex is an allocation hint, not
# a data-interpretation key, and tagging each block with its specific list type
# (igVec3fList, etc.) diverged from every native file and crashed the engine.
MO_OBJECT = _MO['igObject']

# meta-field SHORT name -> meta-field index (short = strip 'ig' + 'MetaField')
_FIELD_IDX = {}
for _i, (_fn, _a, _b) in enumerate(V8_META_FIELDS):
    _short = _fn[2:-9] if _fn.startswith('ig') and _fn.endswith('MetaField') else _fn
    _FIELD_IDX.setdefault(_short, _i)

# Entry / dir-entry meta indices
MO_OBJ_ENTRY = _MO['igObjectDirEntry']
MO_MEM_ENTRY = _MO['igMemoryDirEntry']
MO_INFO_LIST = _MO['igInfoList']

# igVertexData component-type enums (sg_geometry.py:60-74)
CT_POSITION = 1
CT_COLOR = 2
CT_NORMAL = 3
CT_TEXCOORD = 4
CT_WEIGHT = 5
CT_INDEX = 6
CT_BINORMAL = 7
CT_TANGENT = 8

# Memory pool name table is now imported VERBATIM from the native v8 donor
# (skin_builder_v8_meta.V8_MEMORY_POOL_NAMES, 76 entries). The previous
# hand-typed table was shifted — it lacked System2/ASP/PPFX/Effect and had a
# spurious Current/Frame — so every pool handle we wrote (Vertex/Image/Node)
# pointed at the WRONG engine pool. Derive every index by NAME so they stay
# correct regardless of table changes.
_POOL = {n: i for i, n in enumerate(V8_MEMORY_POOL_NAMES)}
POOL_BOOTSTRAP = _POOL['Bootstrap']     # 0
POOL_ACTOR = _POOL['Actor']             # 23
POOL_GEOMETRY = _POOL['Geometry']       # 25
POOL_VERTEX = _POOL['Vertex']           # 26
POOL_IMAGE = _POOL['Image']             # 27
POOL_ATTRIBUTE = _POOL['Attribute']     # 29
POOL_NODE = _POOL['Node']               # 30

# igImage pixel-format enums (verified): 14 = RGBA_DXT1, 16 = RGBA_DXT5.
PFMT_DXT1 = 14
PFMT_DXT5 = 16

_IDENTITY44 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


# --- bind-pose math (copied verbatim from skin_builder_v4 to keep this module
#     self-contained / bpy-free for the standalone round-trip tests) ----------
def _quat_from_matrix3(m):
    """Quaternion (x, y, z, w) from row-major 3x3 rotation rows."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = (t + 1.0) ** 0.5 * 2.0
        return ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s,
                (m[1][0] - m[0][1]) / s, 0.25 * s)
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = (1.0 + m[0][0] - m[1][1] - m[2][2]) ** 0.5 * 2.0
        return (0.25 * s, (m[0][1] + m[1][0]) / s,
                (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    if m[1][1] > m[2][2]:
        s = (1.0 + m[1][1] - m[0][0] - m[2][2]) ** 0.5 * 2.0
        return ((m[0][1] + m[1][0]) / s, 0.25 * s,
                (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    s = (1.0 + m[2][2] - m[0][0] - m[1][1]) ** 0.5 * 2.0
    return ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s,
            0.25 * s, (m[1][0] - m[0][1]) / s)


def _bone_world_rotation(bone):
    inv = bone.get('inv_joint_matrix')
    if not inv:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return [[inv[0], inv[4], inv[8]],
            [inv[1], inv[5], inv[9]],
            [inv[2], inv[6], inv[10]]]


def _mat3_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _mat3_transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _local_bind_transforms(skeleton_data):
    """Per-bone (quat_xyzw, trans) parent-local bind transforms."""
    bones = skeleton_data['bones']
    world = [_bone_world_rotation(b) for b in bones]
    out = []
    for i, b in enumerate(bones):
        p = b.get('parent_idx', b.get('parent_index', -1))
        if p < 0:
            local = world[i]
        else:
            local = _mat3_mul(_mat3_transpose(world[p]), world[i])
        t = b.get('translation', b.get('trans', (0.0, 0.0, 0.0)))
        out.append((_quat_from_matrix3(local), tuple(t)))
    return out


def _world_bind_matrix(bone):
    """Row-major world bind Matrix44f (flat 16 floats) from inv_joint."""
    inv = bone.get('inv_joint_matrix')
    if not inv:
        return _IDENTITY44
    r = _bone_world_rotation(bone)
    ti = (inv[12], inv[13], inv[14])
    t = tuple(-(ti[0] * inv[0 + c] + ti[1] * inv[4 + c] + ti[2] * inv[8 + c])
              for c in range(3))
    return (r[0][0], r[1][0], r[2][0], 0.0,
            r[0][1], r[1][1], r[2][1], 0.0,
            r[0][2], r[1][2], r[2][2], 0.0,
            t[0], t[1], t[2], 1.0)


class SkinBuilderV8:
    """Builds a v8 MUA-native skin IGB from mesh + skeleton data."""

    def __init__(self):
        # each item: ('obj', mo_idx, fields) | ('mem', tag_mo_idx, data, align, pool)
        self._obj_list = []
        # shared igTextureAttr per unique texture (keyed by compressed bytes)
        self._tex_attr_cache = {}

    # ------------------------------------------------------------------ alloc
    def _add_obj(self, mo_idx, fields):
        self._obj_list.append(('obj', mo_idx, fields))
        return len(self._obj_list) - 1

    def _add_mem(self, tag_mo_idx, data, align=-1, pool=-1):
        self._obj_list.append(('mem', tag_mo_idx, data, align, pool))
        return len(self._obj_list) - 1

    # ------------------------------------------------------------ data lists
    def _data_list(self, mo_name, count, data, pool=POOL_VERTEX, align=-1):
        """igDataList-derived list (igVec3fList/igVec2fList/igFloatList/...).

        Native v8 keeps all these typed-list payloads in the Vertex pool (26).
        """
        mb = self._add_mem(MO_OBJECT, data, align=align, pool=pool)
        return self._add_obj(_MO[mo_name], [
            (2, count, 'Int', 4), (3, count, 'Int', 4), (4, mb, 'MemoryRef', 4)])

    def _vec3f_list(self, vecs):
        data = b''.join(struct.pack('<fff', *v) for v in vecs)
        return self._data_list('igVec3fList', len(vecs), data)

    def _vec2f_list(self, vecs):
        data = b''.join(struct.pack('<ff', *v) for v in vecs)
        return self._data_list('igVec2fList', len(vecs), data)

    def _float_list(self, vals):
        data = struct.pack('<' + 'f' * len(vals), *vals)
        return self._data_list('igFloatList', len(vals), data)

    def _uchar_list(self, vals):
        data = struct.pack('<' + 'B' * len(vals), *[int(v) & 0xFF for v in vals])
        # native tags the uchar (blend-index) block with alignment type 3
        return self._data_list('igUnsignedCharList', len(vals), data, align=3)

    def _obj_ref_list(self, mo_name, refs, pool=POOL_NODE):
        """igObjectList-derived list of 4-byte object indices.

        `pool` MUST be a valid pool for non-empty lists — a -1 handle on a
        block that actually holds data crashes the MUA allocator on load.
        Empty lists carry no memory block (mb=-1), so the pool is irrelevant.
        """
        data = struct.pack('<' + 'i' * len(refs), *refs) if refs else b''
        mb = self._add_mem(MO_OBJECT, data, pool=pool) if refs else -1
        return self._add_obj(_MO[mo_name], [
            (2, len(refs), 'Int', 4), (3, len(refs), 'Int', 4),
            (4, mb, 'MemoryRef', 4)])

    # ------------------------------------------------------------- vertex data
    def _vertex_data(self, comp_type, list_idx, s7=0, comp_size=1):
        # slot 6 (comp_size) = scalars-per-vertex for SCALAR lists: the importer
        # reads `count // comp_size` vertices, comp_size floats/uchars each. MUA
        # character skinning is 4-bone, so WEIGHT/INDEX use comp_size=4.
        return self._add_obj(_MO['igVertexData'], [
            (2, '', 'String', 4), (3, list_idx, 'ObjectRef', 4),
            (4, comp_type, 'Enum', 4), (5, 0, 'UnsignedInt', 4),
            (6, comp_size, 'UnsignedInt', 4), (7, s7, 'Int', 4),
            (8, 0, 'UnsignedInt', 4),
            (9, (1.0, 1.0, 1.0, 1.0), 'Vec4f', 16),
            (10, (0.0, 0.0, 0.0, 0.0), 'Vec4f', 16),
            (11, 0, 'Bool', 1), (12, -1, 'Int', 4)])

    def build_geometry(self, positions, normals, uvs, weights, blend_idx,
                       tangents=None, binormals=None, weights_per_vertex=1):
        """Build an igGeometryAttr2 from per-vertex (non-indexed, tri-list) data.

        positions/normals/uvs are per-vertex (len = 3*tris). weights/blend_idx
        are flat lists of length weights_per_vertex * nverts (MUA characters are
        4-bone, so weights_per_vertex=4 -> 4 weights + 4 indices per vertex).
        Returns the igGeometryAttr2 object index.
        """
        nverts = len(positions)
        wpv = max(1, weights_per_vertex)
        comps = []
        # s7 is a per-component format field the engine reads. Native 2103:
        # POSITION=20, NORMAL=8, TEXCOORD/WEIGHT/INDEX=0 (verified). Wrong values
        # make the engine misread the vertex stream at render -> crash.
        comps.append(self._vertex_data(CT_POSITION, self._vec3f_list(positions), s7=20))
        comps.append(self._vertex_data(CT_NORMAL, self._vec3f_list(normals), s7=8))
        comps.append(self._vertex_data(CT_TEXCOORD, self._vec2f_list(uvs)))
        comps.append(self._vertex_data(CT_WEIGHT, self._float_list(weights),
                                       comp_size=wpv))
        comps.append(self._vertex_data(CT_INDEX, self._uchar_list(blend_idx),
                                       comp_size=wpv))
        if tangents is not None and binormals is not None:
            comps.append(self._vertex_data(CT_TANGENT, self._vec3f_list(tangents)))
            comps.append(self._vertex_data(CT_BINORMAL, self._vec3f_list(binormals)))

        objlist = self._obj_ref_list('igObjectList', comps, pool=POOL_VERTEX)
        stream = self._add_obj(_MO['igVertexStream'], [
            (2, '', 'String', 4), (5, -1, 'ObjectRef', 4), (6, 0, 'Enum', 4),
            (7, 3, 'Enum', 4), (8, 0, 'Enum', 4), (9, 0, 'Bool', 1)])
        va = self._add_obj(_MO['igVertexArray2'], [
            (2, '', 'String', 4), (3, objlist, 'ObjectRef', 4),
            (4, stream, 'ObjectRef', 4)])
        ntris = nverts // 3
        ga = self._add_obj(_MO['igGeometryAttr2'], [
            (2, 0, 'Short', 2), (4, va, 'ObjectRef', 4), (5, -1, 'ObjectRef', 4),
            (6, -1, 'ObjectRef', 4), (7, 3, 'Enum', 4),
            (8, ntris, 'UnsignedInt', 4), (9, 0, 'UnsignedInt', 4),
            (10, 0, 'UnsignedInt', 4), (11, 1, 'Bool', 1)])
        return ga

    def wrap_geometry(self, geom_attr_idx, name=''):
        """Wrap an igGeometryAttr2 in an igGeometry node (s8 -> attr list of
        [geometry attr]; s7 -> empty child node list), matching native 0201."""
        attrlist = self._obj_ref_list('igAttrList', [geom_attr_idx],
                                      pool=POOL_ATTRIBUTE)
        empty_nodes = self._obj_ref_list('igNodeList', [])
        return self._add_obj(_MO['igGeometry'], [
            (2, name, 'String', 4), (3, -1, 'ObjectRef', 4), (5, 16, 'Int', 4),
            (7, empty_nodes, 'ObjectRef', 4), (8, attrlist, 'ObjectRef', 4),
            (9, 1, 'Bool', 1)])

    # ------------------------------------------------------------- skeleton
    def _build_int_list(self, values, pool=POOL_ACTOR):
        data = struct.pack('<' + 'i' * len(values), *values) if values else b''
        mb = self._add_mem(MO_OBJECT, data, pool=pool) if values else -1
        return self._add_obj(_MO['igIntList'], [
            (2, len(values), 'Int', 4), (3, len(values), 'Int', 4),
            (4, mb, 'MemoryRef', 4)])

    def _build_skeleton(self, skeleton_data):
        bones = skeleton_data['bones']
        name = skeleton_data.get('name', '')
        joint_count = skeleton_data.get('joint_count', 0)
        bone_infos = []
        for b in bones:
            bone_infos.append(self._add_obj(_MO['igSkeletonBoneInfo'], [
                (2, b['name'], 'String', 4), (3, b.get('parent_idx', -1), 'Int', 4),
                (4, b.get('bm_idx', -1), 'Int', 4), (5, b.get('flags', 0), 'Int', 4)]))
        bil = self._obj_ref_list('igSkeletonBoneInfoList', bone_infos,
                                 pool=POOL_ACTOR)
        tdata = bytearray(len(bones) * 12)
        for i, b in enumerate(bones):
            struct.pack_into('<fff', tdata, i * 12, *b.get('translation', (0, 0, 0)))
        tmb = self._add_mem(MO_OBJECT, bytes(tdata), pool=POOL_ACTOR)
        if joint_count > 0:
            idata = bytearray(joint_count * 64)
            for b in bones:
                bm = b.get('bm_idx', -1)
                ijm = b.get('inv_joint_matrix')
                if ijm is not None and 0 <= bm < joint_count:
                    for j, val in enumerate(ijm[:16]):
                        struct.pack_into('<f', idata, bm * 64 + j * 4, val)
            imb = self._add_mem(MO_OBJECT, bytes(idata), pool=POOL_ACTOR)
        else:
            imb = -1
        return self._add_obj(_MO['igSkeleton'], [
            (2, name, 'String', 4), (3, tmb, 'MemoryRef', 4),
            (4, bil, 'ObjectRef', 4), (5, imb, 'MemoryRef', 4),
            (6, joint_count, 'Int', 4)])

    def _build_anim_db(self, skeleton_idx, skin_idx, name, anim_idx=None,
                       appearance_idx=None, combiner_idx=None):
        # All animation-database child lists live in the Actor pool (native).
        skel_list = self._obj_ref_list('igSkeletonList', [skeleton_idx],
                                       pool=POOL_ACTOR)
        skin_list = self._obj_ref_list(
            'igSkinList',
            [skin_idx] if skin_idx is not None and skin_idx >= 0 else [],
            pool=POOL_ACTOR)
        anim_list = self._obj_ref_list(
            'igAnimationList', [anim_idx] if anim_idx is not None else [],
            pool=POOL_ACTOR)
        appear_list = self._obj_ref_list(
            'igAppearanceList',
            [appearance_idx] if appearance_idx is not None else [],
            pool=POOL_ACTOR)
        comb_list = self._obj_ref_list(
            'igAnimationCombinerList',
            [combiner_idx] if combiner_idx is not None else [],
            pool=POOL_ACTOR)
        return self._add_obj(_MO['igAnimationDatabase'], [
            (2, name, 'String', 4), (4, 1, 'Bool', 1),
            (5, skel_list, 'ObjectRef', 4), (6, anim_list, 'ObjectRef', 4),
            (7, skin_list, 'ObjectRef', 4), (8, appear_list, 'ObjectRef', 4),
            (9, comb_list, 'ObjectRef', 4)])

    # ----------------------------------------------------- actor / anim graph
    def _build_bindpose_animation(self, skeleton_idx, bones, locals_):
        """A constant-pose igAnimation (one track/bone), like native 2103's
        embedded 'defaultAnimation'. Field layouts are the v8 slots dumped from
        2103 (igAnimation/igAnimationBinding/igAnimationTrack)."""
        n = len(bones)
        idmap = struct.pack('<' + 'i' * n, *range(n))
        map_mb = self._add_mem(MO_OBJECT, idmap, pool=POOL_ACTOR)
        binding = self._add_obj(_MO['igAnimationBinding'], [
            (2, skeleton_idx, 'ObjectRef', 4), (3, map_mb, 'MemoryRef', 4),
            (4, n, 'Int', 4), (5, -1, 'ObjectRef', 4), (6, -1, 'ObjectRef', 4)])
        binding_list = self._obj_ref_list('igAnimationBindingList', [binding],
                                          pool=POOL_ACTOR)
        tracks = []
        for bone, (quat, _trans) in zip(bones, locals_):
            tracks.append(self._add_obj(_MO['igAnimationTrack'], [
                (2, bone['name'], 'String', 4), (3, -1, 'ObjectRef', 4),
                (4, quat, 'Vec4f', 16), (5, (0.0, 0.0, 0.0), 'Vec3f', 12)]))
        track_list = self._obj_ref_list('igAnimationTrackList', tracks,
                                        pool=POOL_ACTOR)
        trans_def = self._obj_ref_list('igAnimationTransitionDefinitionList',
                                       [], pool=POOL_ACTOR)
        return self._add_obj(_MO['igAnimation'], [
            (2, 'defaultAnimation', 'String', 4), (3, 0, 'Int', 4),
            (4, binding_list, 'ObjectRef', 4), (5, track_list, 'ObjectRef', 4),
            (6, trans_def, 'ObjectRef', 4), (7, 0, 'Long', 8),
            (8, 0, 'Long', 8), (9, 0, 'Long', 8), (10, -1, 'ObjectRef', 4)])

    def _build_actor_graph(self, skel_idx, skeleton_data, skin_idx, anim_idx,
                           bms_palette, locals_):
        """Build igAnimationState + per-bone combiner infos + igAnimationCombiner
        + igAppearance + igActor. Field layouts + the timing constants are taken
        verbatim from native 2103 (tools/_v8rawvals.py). Returns
        (state, combiner, appearance, actor)."""
        bones = skeleton_data['bones']
        n = len(bones)

        # igAnimationState — 26 slots, constants verbatim from 2103 (the Long
        # fields are nanosecond durations; any positive value is engine-valid).
        state = self._add_obj(_MO['igAnimationState'], [
            (2, anim_idx, 'ObjectRef', 4), (3, 0, 'Enum', 4), (4, 5, 'Enum', 4),
            (5, 0, 'Enum', 4), (6, -1, 'ObjectRef', 4), (7, 0, 'Bool', 1),
            (8, 1.0, 'Float', 4), (9, 99175890944, 'Long', 8), (10, 0, 'Long', 8),
            (11, 1.0, 'Float', 4), (12, 0, 'Long', 8), (13, 9945333300, 'Long', 8),
            (14, 0.0, 'Float', 4), (15, 1.0, 'Float', 4), (16, 133333344, 'Long', 8),
            (17, 0, 'Long', 8), (20, -1, 'ObjectRef', 4), (21, 0, 'Bool', 1),
            (22, 0, 'Bool', 1), (23, 0, 'Bool', 1), (24, 0, 'Long', 8),
            (25, 0, 'Long', 8), (26, 0, 'Long', 8)])
        state_list = self._obj_ref_list('igAnimationStateList', [state],
                                        pool=POOL_ACTOR)

        # one igAnimationCombinerBoneInfo per bone, each in a 1-entry list
        bi_lists = []
        for (quat, trans) in locals_:
            bi = self._add_obj(_MO['igAnimationCombinerBoneInfo'], [
                (2, state, 'ObjectRef', 4), (3, -1, 'ObjectRef', 4),
                (4, quat, 'Vec4f', 16), (5, trans, 'Vec3f', 12),
                (6, 0, 'Int', 4), (7, 0, 'UnsignedChar', 1), (9, -1, 'Int', 4)])
            bi_lists.append(self._obj_ref_list(
                'igAnimationCombinerBoneInfoList', [bi], pool=POOL_ACTOR))
        bill = self._obj_ref_list('igAnimationCombinerBoneInfoListList',
                                  bi_lists, pool=POOL_ACTOR)
        comb_int = self._build_int_list([0] * n)  # per-bone state index = 0

        quat_bytes = b''.join(struct.pack('<4f', *q) for q, _t in locals_)
        world_bytes = b''.join(struct.pack('<16f', *_world_bind_matrix(b))
                               for b in bones)
        n_pal = max(len(bms_palette), 1)
        pal_bytes = struct.pack('<16f', *_IDENTITY44) * n_pal
        quat_mb = self._add_mem(MO_OBJECT, quat_bytes, pool=POOL_ACTOR)
        world_mb1 = self._add_mem(MO_OBJECT, world_bytes, pool=POOL_ACTOR)
        world_mb2 = self._add_mem(MO_OBJECT, world_bytes, pool=POOL_ACTOR)
        pal_mb = self._add_mem(MO_OBJECT, pal_bytes, pool=POOL_ACTOR)

        combiner = self._add_obj(_MO['igAnimationCombiner'], [
            (2, '', 'String', 4), (3, skel_idx, 'ObjectRef', 4),
            (4, bill, 'ObjectRef', 4), (5, comb_int, 'ObjectRef', 4),
            (6, state_list, 'ObjectRef', 4), (7, quat_mb, 'MemoryRef', 4),
            (8, world_mb1, 'MemoryRef', 4),
            (9, 99176036700, 'Long', 8), (10, 1, 'Bool', 1),
            (12, world_mb2, 'MemoryRef', 4), (13, pal_mb, 'MemoryRef', 4),
            (14, -1, 'Long', 8), (15, 0, 'Long', 8), (16, 100000000, 'Long', 8),
            (17, 16700000, 'Long', 8)])

        appearance = self._add_obj(_MO['igAppearance'], [
            (2, '', 'String', 4), (3, skin_idx, 'ObjectRef', 4),
            (4, self._obj_ref_list('igSkinList', [], pool=POOL_ACTOR),
             'ObjectRef', 4),
            (5, self._obj_ref_list('igModelViewMatrixBoneSelectList', [],
                                   pool=POOL_NODE), 'ObjectRef', 4),
            (6, self._obj_ref_list('igStringObjList', [], pool=POOL_NODE),
             'ObjectRef', 4),
            (7, self._obj_ref_list('igNodeList', [], pool=POOL_NODE),
             'ObjectRef', 4)])

        actor_world = self._add_mem(MO_OBJECT, world_bytes, pool=POOL_ACTOR)
        actor_pal = self._add_mem(MO_OBJECT, pal_bytes, pool=POOL_ACTOR)
        modifier_list = self._obj_ref_list('igAnimationModifierList', [],
                                           pool=POOL_NODE)
        actor = self._add_obj(_MO['igActor'], [
            (2, 'igActor01', 'String', 4), (3, -1, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
            (7, self._obj_ref_list('igNodeList', [], pool=POOL_NODE),
             'ObjectRef', 4),
            (8, combiner, 'ObjectRef', 4), (9, actor_world, 'MemoryRef', 4),
            (10, actor_pal, 'MemoryRef', 4), (11, appearance, 'ObjectRef', 4),
            (12, -1, 'ObjectRef', 4), (13, modifier_list, 'ObjectRef', 4),
            (14, _IDENTITY44, 'Matrix44f', 64)])
        return state, combiner, appearance, actor

    def _build_actor_info(self, actor_idx, anim_db_idx, combiner_idx,
                          appearance_idx, name):
        """igActorInfo hub (v8 slots from 2103): s4 Bool, s6 actorList,
        s7 animationDatabase, s8 combinerList, s9 appearanceList."""
        actor_list = self._obj_ref_list('igActorList', [actor_idx],
                                        pool=POOL_ACTOR)
        comb_list = self._obj_ref_list('igAnimationCombinerList',
                                       [combiner_idx], pool=POOL_ACTOR)
        appear_list = self._obj_ref_list('igAppearanceList', [appearance_idx],
                                         pool=POOL_ACTOR)
        return self._add_obj(_MO['igActorInfo'], [
            (2, name, 'String', 4), (4, 1, 'Bool', 1),
            (5, -1, 'ObjectRef', 4), (6, actor_list, 'ObjectRef', 4),
            (7, anim_db_idx, 'ObjectRef', 4), (8, comb_list, 'ObjectRef', 4),
            (9, appear_list, 'ObjectRef', 4)])

    def _build_scene_info(self, actor_idx, bbox, name):
        """Build the igSceneInfo render root (v8 slots from native 2103). The
        igActor hangs under it (igSceneInfo -> igAttrSet -> igNodeList -> actor)
        so the engine has a renderable scene graph to draw at gameplay start."""
        bbmin, bbmax = bbox
        aabox = self._add_obj(_MO['igAABox'], [
            (2, tuple(bbmin), 'Vec3f', 12), (3, tuple(bbmax), 'Vec3f', 12)])
        # scene-root igAttrSet: s3 bound, s7 nodeList=[actor], s8 empty attrList
        scene_nl = self._obj_ref_list('igNodeList', [actor_idx], pool=POOL_NODE)
        scene_al = self._obj_ref_list('igAttrList', [], pool=POOL_ATTRIBUTE)
        scene_root = self._add_obj(_MO['igAttrSet'], [
            (2, 'Scene Graph', 'String', 4), (3, aabox, 'ObjectRef', 4),
            (5, 0, 'Int', 4), (7, scene_nl, 'ObjectRef', 4),
            (8, scene_al, 'ObjectRef', 4), (9, 0, 'Bool', 1)])
        # texture list (the shared igTextureAttrs) + empty graph-path list
        tex_attrs = list(self._tex_attr_cache.values())
        tlist = self._obj_ref_list('igTextureList', tex_attrs, pool=POOL_NODE)
        gpath = self._add_obj(_MO['igGraphPathList'], [
            (2, 0, 'Int', 4), (3, 0, 'Int', 4), (4, -1, 'MemoryRef', 4)])
        empty_nl = self._obj_ref_list('igNodeList', [], pool=POOL_NODE)
        return self._add_obj(_MO['igSceneInfo'], [
            (2, 'Scene Graph', 'String', 4), (4, 1, 'Bool', 1),
            (5, scene_root, 'ObjectRef', 4), (6, tlist, 'ObjectRef', 4),
            (7, gpath, 'ObjectRef', 4), (8, 0, 'Long', 8), (9, 0, 'Long', 8),
            (10, (0.0, 0.0, 1.0), 'Vec3f', 12), (11, empty_nl, 'ObjectRef', 4)])

    def _build_material(self, mat=None):
        mat = mat or {}
        d = mat.get('diffuse', (1.0, 1.0, 1.0, 1.0))
        a = mat.get('ambient', (1.0, 1.0, 1.0, 1.0))
        sp = mat.get('specular', (0.0, 0.0, 0.0, 0.0))
        em = mat.get('emission', (0.0, 0.0, 0.0, 0.0))
        # Round-trip shininess(4)/flags(9)/priority(2) from the material instead
        # of hardcoding 0 — otherwise a re-exported v8 hero loses its specular
        # power and any flags. Slots are identical to v4/v6 (base Short @2 on v8).
        sh = float(mat.get('shininess', 0.0))
        fl = int(mat.get('flags', 0))
        pr = int(mat.get('priority', 0))
        return self._add_obj(_MO['igMaterialAttr'], [
            (2, pr, 'Short', 2), (4, sh, 'Float', 4),
            (5, tuple(d), 'Vec4f', 16), (6, tuple(a), 'Vec4f', 16),
            (7, tuple(sp), 'Vec4f', 16), (8, tuple(em), 'Vec4f', 16),
            (9, fl, 'UnsignedInt', 4)])

    # ------------------------------------------------------------- full skin
    IDENTITY44 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    def build_skin(self, submeshes, skeleton_data, bms_palette=None,
                   export_name='', actor_graph=True):
        """Build a complete v8 MUA-native skin IGB.

        submeshes: list of dicts with 'mesh' (positions/normals/uvs/weights/
        blend_idx lists, non-indexed tri-list) and optional 'material'.
        actor_graph: when True (default) emit the igActor/igAnimationCombiner +
        bind-pose animation that the MUA engine needs to drive the character in
        gameplay (without it, v8 skins crash right before the gameplay handoff).
        Returns an IGBWriter.
        """
        self._obj_list = []
        self._tex_attr_cache = {}
        skel_idx = self._build_skeleton(skeleton_data)
        if bms_palette is None:
            bms_palette = list(range(skeleton_data.get('joint_count', 0)))
        pal = self._build_int_list(bms_palette)

        # global bounding box (for the scene-info / actor igAABox)
        all_pos = [p for sub in submeshes for p in sub['mesh']['positions']]
        if all_pos:
            bbmin = (min(p[0] for p in all_pos), min(p[1] for p in all_pos),
                     min(p[2] for p in all_pos))
            bbmax = (max(p[0] for p in all_pos), max(p[1] for p in all_pos),
                     max(p[2] for p in all_pos))
        else:
            bbmin = bbmax = (0.0, 0.0, 0.0)

        # Exact Max-v8 (proven) per-unit chain, verified field-for-field against
        # 2103 (3dsmax2012).igb:
        #   igBlendMatrixSelect (attrs EMPTY, palette)
        #     -> igSegment -> igAttrSet [color, material, globalColorState]
        #          -> igAttrSet [cullFace, vertexBlendState] -> igGeometry [geomAttr]
        # Render-STATE attrs are SHARED across units. The skin-graph walk showed
        # native's inner attr list = [vbs] for the BODY, but [cullFace, vbs] for
        # the SEGMENT (gun) units — cull face is a segment-only render state.
        shared_vbs = self._add_obj(_MO['igVertexBlendStateAttr'],
                                   [(2, 0, 'Short', 2), (4, 1, 'Bool', 1)])
        shared_cull = self._add_obj(_MO['igCullFaceAttr'],
                                    [(2, 0, 'Short', 2), (4, 1, 'Bool', 1), (5, 0, 'Enum', 4)])
        shared_gcs = self._add_obj(_MO['igGlobalColorStateAttr'],
                                   [(2, 0, 'Short', 2), (4, 1, 'Bool', 1)])
        units = []
        for sub in submeshes:
            m = sub['mesh']
            ga = self.build_geometry(
                m['positions'], m['normals'], m['uvs'],
                m.get('weights', [1.0] * len(m['positions'])),
                m.get('blend_idx', [0] * len(m['positions'])),
                m.get('tangents'), m.get('binormals'),
                weights_per_vertex=m.get('weights_per_vertex', 1))
            geom = self.wrap_geometry(ga, export_name)
            # inner AttrSet: [cullFace, vbs] for segment units, [vbs] for body
            seg_name = sub.get('segment_name', '')
            inner_attrs = ([shared_cull, shared_vbs] if seg_name
                           else [shared_vbs])
            inner = self._build_attrset(geom, inner_attrs, export_name)
            # outer AttrSet: color + material + (texture binds) + global color
            # state, -> inner. Textures sit between material and gcs (native order).
            color = self._add_obj(_MO['igColorAttr'],
                                  [(2, 0, 'Short', 2), (4, (1.0, 1.0, 1.0, 1.0), 'Vec4f', 16)])
            matu = self._build_material(sub.get('material'))
            # Texture units: 0=diffuse, 1=normal, 2=specular, 5=gloss/mask.
            # Each absent map yields [] (no attrs), so a diffuse-only skin is
            # byte-identical to before. Normal/spec/gloss ride the tangent-frame
            # vertex streams emitted by build_geometry when 'tangents' is present.
            tname = sub.get('texture_name') or export_name
            tex_attrs = []
            tex_attrs += self._build_texture_chain(
                sub.get('texture_levels'), tname, unit_id=0,
                pfmt=sub.get('diffuse_pfmt', PFMT_DXT5))
            tex_attrs += self._build_texture_chain(
                sub.get('normal_levels'), tname + '_n', unit_id=1)
            tex_attrs += self._build_texture_chain(
                sub.get('specular_levels'), tname + '_s', unit_id=2)
            tex_attrs += self._build_texture_chain(
                sub.get('gloss_levels'), tname + '_g', unit_id=5)
            outer = self._build_attrset(
                inner, [color, matu] + tex_attrs + [shared_gcs], export_name)
            # Only DETACHABLE parts (named segments — e.g. holsterable guns) get
            # an igSegment. The body goes BMS -> igAttrSet directly, like native
            # 2103. Wrapping the body in a segment diverged from native (found by
            # the parallel skin-graph walk) and is the first structural mismatch.
            seg_name = sub.get('segment_name', '')
            unit_child = (self._build_segment(outer, seg_name) if seg_name
                          else outer)
            units.append(self._build_unit_bms(unit_child, pal))

        root = self._build_group_node(export_name, units)
        skin = self._add_obj(_MO['igSkin'], [
            (2, export_name, 'String', 4), (3, root, 'ObjectRef', 4),
            (4, -1, 'ObjectRef', 4)])

        if actor_graph:
            # Bind-pose animation + actor/combiner graph — the engine builds the
            # animated character from these just before gameplay. Top-level info
            # list mirrors native 2103: [igActorInfo, igAnimationDatabase].
            locals_ = _local_bind_transforms(skeleton_data)
            anim = self._build_bindpose_animation(
                skel_idx, skeleton_data['bones'], locals_)
            _state, combiner, appearance, actor = self._build_actor_graph(
                skel_idx, skeleton_data, skin, anim, bms_palette, locals_)
            adb = self._build_anim_db(
                skel_idx, skin, export_name, anim_idx=anim,
                appearance_idx=appearance, combiner_idx=combiner)
            actor_info = self._build_actor_info(
                actor, adb, combiner, appearance, export_name)
            # igSceneInfo is the RENDER root: native hangs the igActor under it
            # (igSceneInfo -> igAttrSet -> igNodeList -> igActor). Without a
            # renderable scene root the engine has nothing to draw -> crash right
            # when gameplay starts. Native top-level info list:
            # [igActorInfo, igAnimationDatabase, igSceneInfo].
            scene = self._build_scene_info(actor, (bbmin, bbmax), export_name)
            info = self._obj_ref_list('igInfoList', [actor_info, adb, scene],
                                      pool=POOL_NODE)
        else:
            adb = self._build_anim_db(skel_idx, skin, export_name)
            info = self._obj_ref_list('igInfoList', [adb], pool=POOL_NODE)
        return self.finalize(info_list_idx=info)

    def _build_group_node(self, name, child_indices):
        nl = self._obj_ref_list('igNodeList', child_indices)
        return self._add_obj(_MO['igGroup'], [
            (2, name, 'String', 4), (3, -1, 'ObjectRef', 4), (5, 0, 'Int', 4),
            (7, nl, 'ObjectRef', 4)])

    def _build_attrset(self, child_idx, attr_indices, name=''):
        nl = self._obj_ref_list('igNodeList', [child_idx])
        al = self._obj_ref_list('igAttrList', attr_indices, pool=POOL_ATTRIBUTE)
        return self._add_obj(_MO['igAttrSet'], [
            (2, name, 'String', 4), (3, -1, 'ObjectRef', 4), (5, 0, 'Int', 4),
            (7, nl, 'ObjectRef', 4), (8, al, 'ObjectRef', 4), (9, 0, 'Bool', 1)])

    def _build_texture_chain(self, levels, tex_name, unit_id=0, pfmt=PFMT_DXT5):
        """Build igTextureBindAttr -> igTextureAttr -> igImage(+mipmaps) from a
        list of (compressed_bytes, w, h) levels (base first). Matches native
        0201's chain. Returns [igTextureBindAttr, igTextureStateAttr] to append
        to the unit's outer AttrSet attr list.
        """
        if not levels:
            return []
        # Share the (expensive) igImage chain + igTextureAttr across submeshes
        # that use the SAME texture. Native 2103 has 3 texture binds -> 1
        # igTextureAttr -> 11 igImage; we were emitting a full copy per mesh
        # part (3x the texture data -> a 4.5MB file vs native's 1.4MB). Cache
        # keyed by the base compressed bytes, so identical textures collapse.
        cache_key = (unit_id, pfmt, bytes(levels[0][0]))
        ta = self._tex_attr_cache.get(cache_key)
        if ta is None:
            ta = self._build_texture_attr(levels, tex_name, pfmt)
            self._tex_attr_cache[cache_key] = ta
        # bind + state are cheap and per-submesh (native: 3 binds / 3 states)
        tb = self._add_obj(_MO['igTextureBindAttr'],
                           [(2, unit_id, 'Short', 2), (4, ta, 'ObjectRef', 4),
                            (5, unit_id, 'Int', 4)])
        ts = self._add_obj(_MO['igTextureStateAttr'],
                           [(2, unit_id, 'Short', 2), (4, 1, 'Bool', 1),
                            (5, unit_id, 'Int', 4)])
        return [tb, ts]

    def _build_texture_attr(self, levels, tex_name, pfmt=PFMT_DXT5):
        """Build the shared igImage(+mipmaps) chain + igTextureAttr once."""
        # DXT bytes-per-4x4-block: DXT1=8, DXT5=16. Native igImage encodes the
        # per-row stride (s19 = blocks_per_row * bpb) and a mip-extent field
        # (s7..s10 = log2(blocks_per_row), all four equal) — our old 1/raw-width
        # values diverged from every native file.
        bpb = 8 if pfmt == PFMT_DXT1 else 16
        imgs = []
        for data, lw, lh in levels:
            blocks_w = max(1, (lw + 3) // 4)
            stride = blocks_w * bpb
            ext = max(1, blocks_w.bit_length() - 1)   # log2(blocks_w)
            # pixel block: Image pool, alignment type 1 (native igImage s13)
            mb = self._add_mem(MO_OBJECT, data, align=1, pool=POOL_IMAGE)
            imgs.append(self._add_obj(_MO['igImage'], [
                (2, lw, 'UnsignedInt', 4), (3, lh, 'UnsignedInt', 4),
                (4, 4, 'UnsignedInt', 4), (5, 1, 'UnsignedInt', 4),
                (6, 100, 'Enum', 4), (7, ext, 'UnsignedInt', 4),
                (8, ext, 'UnsignedInt', 4), (9, ext, 'UnsignedInt', 4),
                (10, ext, 'UnsignedInt', 4), (11, pfmt, 'Enum', 4),
                (12, len(data), 'Int', 4), (13, mb, 'MemoryRef', 4),
                (14, -1, 'MemoryRef', 4), (15, 1, 'Bool', 1),
                (16, 0, 'UnsignedInt', 4), (17, -1, 'ObjectRef', 4),
                (18, 0, 'UnsignedInt', 4), (19, stride, 'Int', 4),
                (20, 1, 'Bool', 1), (21, 0, 'UnsignedInt', 4),
                (22, tex_name, 'String', 4)]))
        base = imgs[0]
        mml = self._obj_ref_list('igImageMipMapList', imgs[1:],
                                 pool=POOL_ATTRIBUTE)
        # texture-attr filter/wrap enums: native uses 1 / 3 (matches the proven
        # v6 builder); our 5 / 5 were out-of-range guesses.
        return self._add_obj(_MO['igTextureAttr'], [
            (2, 0, 'Short', 2), (4, 0, 'UnsignedInt', 4), (5, 1, 'Enum', 4),
            (6, 3, 'Enum', 4), (7, 1, 'Enum', 4), (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4), (11, 0, 'Enum', 4), (12, base, 'ObjectRef', 4),
            (13, 0, 'Bool', 1), (14, -1, 'ObjectRef', 4), (15, 11, 'Int', 4),
            (16, mml, 'ObjectRef', 4)])

    def _build_segment(self, child_idx, name=''):
        nl = self._obj_ref_list('igNodeList', [child_idx])
        return self._add_obj(_MO['igSegment'], [
            (2, name, 'String', 4), (3, -1, 'ObjectRef', 4), (5, 0, 'Int', 4),
            (7, nl, 'ObjectRef', 4)])

    def _build_unit_bms(self, child_idx, palette_idx):
        # Native BMS carries an EMPTY attr list (the blend-state attr lives in
        # the inner AttrSet, verified against 2103). It holds the palette + the
        # two identity blend matrices.
        nl = self._obj_ref_list('igNodeList', [child_idx])
        al = self._obj_ref_list('igAttrList', [])
        return self._add_obj(_MO['igBlendMatrixSelect'], [
            (2, '', 'String', 4), (3, -1, 'ObjectRef', 4), (5, 0, 'Int', 4),
            (7, nl, 'ObjectRef', 4), (8, al, 'ObjectRef', 4), (9, 0, 'Bool', 1),
            (10, palette_idx, 'ObjectRef', 4),
            (11, self.IDENTITY44, 'Matrix44f', 64),
            (12, self.IDENTITY44, 'Matrix44f', 64)])

    # ------------------------------------------------------------- finalize
    def finalize(self, info_list_idx=None):
        writer = IGBWriter()
        writer.version = 8
        writer.endian = '<'
        writer.has_info = info_list_idx is not None
        writer.has_external = True
        writer.shared_entries = True
        writer.has_memory_pool_names = True

        writer.meta_fields = [MetaFieldDef(n, ma, mi)
                              for n, ma, mi in V8_META_FIELDS]

        name_to_idx = {t[0]: i for i, t in enumerate(V8_META_OBJECTS)}
        writer.meta_objects = []
        for name, major, minor, parent_name, slot_count, fields in V8_META_OBJECTS:
            fdefs = []
            for short, slot, size in fields:
                ti = _FIELD_IDX.get(short, 0)
                fd = MetaObjectFieldDef(ti, slot, size)
                fd.short_name = short.encode('ascii')
                fdefs.append(fd)
            parent_idx = name_to_idx.get(parent_name, -1) if parent_name else -1
            writer.meta_objects.append(MetaObjectDef(
                name, major, minor, fdefs, parent_idx, slot_count))

        writer.alignment_data = V8_ALIGNMENT
        writer.external_dirs = [b'system']
        writer.external_dirs_unk = 1
        writer.memory_pool_names = list(V8_MEMORY_POOL_NAMES)

        # Pre-intern the empty entry name so every dir entry can reference it by
        # a stable pool index (entries serialize field_values as raw ints).
        empty_idx = writer._intern_name(b'')

        entries = []
        index_map = []
        writer.objects = []
        writer.ref_info = []

        for i, item in enumerate(self._obj_list):
            if item[0] == 'obj':
                _kind, mo_idx, fields = item
                # igObjectDirEntry: [emptyName, objTypeIdx(s11), pool(s12)]
                entries.append(EntryDef(MO_OBJ_ENTRY, [empty_idx, mo_idx, -1]))
                raw_fields = [(slot, val, ObjectFieldDef(slot, short, size))
                              for slot, val, short, size in fields]
                writer.objects.append(ObjectDef(mo_idx, raw_fields))
                writer.ref_info.append({
                    'is_object': True, 'type_index': mo_idx,
                    'type_name': V8_META_OBJECTS[mo_idx][0].encode('ascii'),
                    'mem_pool_handle': -1,
                })
            else:
                _kind, tag, data, align, pool = item
                # igMemoryDirEntry: [emptyName, size(s7), memType(s10),
                #                    refCounted=1(s11), align(s12), pool(s13)]
                entries.append(EntryDef(MO_MEM_ENTRY,
                                        [empty_idx, len(data), tag, 1, align, pool]))
                writer.objects.append(MemoryBlockDef(data))
                writer.ref_info.append({
                    'is_object': False, 'type_index': tag,
                    'type_name': V8_META_OBJECTS[tag][0].encode('ascii'),
                    'mem_size': len(data), 'ref_counted': 1,
                    'align_type_idx': align, 'mem_pool_handle': pool,
                })
            index_map.append(i)

        writer.entries = entries
        writer.index_map = index_map
        writer.info_list_index = info_list_idx
        return writer
