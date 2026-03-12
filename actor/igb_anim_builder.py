"""Build IGB animation files from scratch without a template.

Constructs the complete IGB binary structure — meta-fields, meta-objects,
entries, objects, memory blocks — from skeleton and animation data alone.

The static type-system schema (meta-fields, meta-objects, alignment data,
pool names) is extracted once from a reference file and reused for all
subsequent builds.  Only the object graph is constructed fresh each time.

Usage:
    builder = IGBAnimationBuilder('xml2')
    builder.build(
        skeleton_data={
            'name': '03_wolverine_skel',
            'bones': [
                {'name': '', 'parent': -1, 'flags': 64, 'matrix': [...]},
                {'name': 'Bip01', 'parent': 0, 'flags': 2, 'matrix': [...]},
                ...
            ],
        },
        animations=[{
            'name': 'idle',
            'duration_sec': 0.766,
            'tracks': [
                {
                    'bone_name': '',
                    'track_id': 0,
                    'rest_quat': (0, 0, 0, 1),  # xyzw
                    'rest_trans': (0, 0, 0),
                    'keyframes': [(time_s, (qx,qy,qz,qw), (tx,ty,tz)), ...],
                },
                ...
            ],
            'motion_track': None,  # or TransformSequence data
        }],
        output_path='output.igb',
    )
"""

import struct
import os
import logging

from ..igb_format.igb_writer import (
    IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
    EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
    from_reader,
)

_log = logging.getLogger("igb_anim_builder")


# ---------------------------------------------------------------------------
# Game-specific constants
# ---------------------------------------------------------------------------

GAME_CONFIGS = {
    'xml2': {
        'version': 6,
        'endian': '<',
        'enbaya_signature': 0x10079C10,
        'has_animation_hierarchy': True,   # igSkeleton inherits igAnimationHierarchy
    },
    'mua': {
        'version': 8,
        'endian': '<',
        'enbaya_signature': 0x100A8BAC,
        'has_animation_hierarchy': False,  # igSkeleton inherits igNamedObject directly
    },
}


# ---------------------------------------------------------------------------
# Static schema cache
# ---------------------------------------------------------------------------

_schema_cache = {}  # game -> dict of schema data


def extract_static_schema(reference_igb_path, game='xml2'):
    """Extract the reusable type-system schema from a reference IGB.

    Returns a dict with keys: meta_fields, meta_objects, alignment_data,
    external_dirs, external_dirs_unk, memory_pool_names, has_info,
    has_external, shared_entries, has_memory_pool_names.
    """
    from ..igb_format.igb_reader import IGBReader

    r = IGBReader(reference_igb_path)
    r.read()

    # Use from_reader to get properly structured writer defs
    w = from_reader(r)

    return {
        'meta_fields': w.meta_fields,
        'meta_objects': w.meta_objects,
        'alignment_data': w.alignment_data,
        'external_dirs': w.external_dirs,
        'external_dirs_unk': w.external_dirs_unk,
        'memory_pool_names': w.memory_pool_names,
        'has_info': w.has_info,
        'has_external': w.has_external,
        'shared_entries': w.shared_entries,
        'has_memory_pool_names': w.has_memory_pool_names,
    }


def get_schema(game='xml2', reference_path=None):
    """Get or load the static schema for a game.

    If reference_path is provided, extracts and caches the schema.
    Otherwise returns the cached schema or raises an error.
    """
    if game in _schema_cache:
        return _schema_cache[game]
    if reference_path is None:
        raise RuntimeError(
            f"No cached schema for '{game}'. "
            f"Call extract_static_schema() first or provide reference_path."
        )
    schema = extract_static_schema(reference_path, game)
    _schema_cache[game] = schema
    return schema


# ---------------------------------------------------------------------------
# Meta-object type name → index lookup
# ---------------------------------------------------------------------------

def _build_type_index(meta_objects):
    """Build a name→index mapping for meta-object types."""
    idx = {}
    for i, mo in enumerate(meta_objects):
        name = mo.name if isinstance(mo.name, bytes) else mo.name.encode('ascii')
        idx[name] = i
    return idx


def _build_field_index(meta_fields):
    """Build a short_name→(type_index, MetaFieldDef) mapping."""
    idx = {}
    for i, mf in enumerate(meta_fields):
        idx[mf.short_name] = i
    return idx


# ---------------------------------------------------------------------------
# Object/MemBlock allocation helpers
# ---------------------------------------------------------------------------

class _RefAllocator:
    """Allocates ref indices for objects and memory blocks."""

    def __init__(self):
        self.objects = []      # list of (ObjectDef, is_object=True)
        self.ref_info = []     # list of dict

    def add_object(self, obj_def):
        """Add an ObjectDef and return its ref index."""
        idx = len(self.objects)
        self.objects.append(obj_def)
        self.ref_info.append({'is_object': True})
        return idx

    def add_memblock(self, data, mem_size=None):
        """Add a MemoryBlockDef and return its ref index."""
        if isinstance(data, (bytes, bytearray)):
            mdef = MemoryBlockDef(data=bytes(data))
        else:
            mdef = MemoryBlockDef(data=b'')
        idx = len(self.objects)
        self.objects.append(mdef)
        actual_size = mem_size if mem_size is not None else len(mdef.data)
        self.ref_info.append({'is_object': False, 'mem_size': actual_size})
        return idx


def _make_obj(type_index, fields):
    """Create an ObjectDef with raw_fields from (slot, short_name, value) tuples.

    Args:
        type_index: meta-object type index
        fields: list of (slot, short_name_bytes, size, value)
    """
    raw_fields = []
    for slot, sn, size, val in fields:
        fd = ObjectFieldDef(slot, sn, size)
        raw_fields.append((slot, val, fd))
    return ObjectDef(type_index, raw_fields)


def _make_list_data(ref_indices, endian='<'):
    """Create a memory block containing a list of object ref indices (uint32)."""
    if not ref_indices:
        return b''
    return struct.pack(endian + 'I' * len(ref_indices), *ref_indices)


# ---------------------------------------------------------------------------
# Entry/Index generation
# ---------------------------------------------------------------------------

def _generate_entries_and_index(alloc, meta_objects, meta_fields):
    """Generate the entry table and index map from allocated objects/memblocks.

    Objects sharing the same meta-object type share one igObjectDirEntry.
    Memory blocks are grouped by (mem_size_category, pool_index, alignment).

    Returns:
        entries: list of EntryDef
        index_map: list of int (ref_index → entry_index)
    """
    entries = []
    index_map = [0] * len(alloc.objects)

    # Find igObjectDirEntry and igMemoryDirEntry type indices
    obj_entry_type = None
    mem_entry_type = None
    for i, mo in enumerate(meta_objects):
        name = mo.name if isinstance(mo.name, bytes) else mo.name.encode('ascii')
        if name == b'igObjectDirEntry':
            obj_entry_type = i
        elif name == b'igMemoryDirEntry':
            mem_entry_type = i
    if obj_entry_type is None or mem_entry_type is None:
        raise RuntimeError("Cannot find entry type meta-objects in schema")

    # Group objects by type
    obj_type_to_entry = {}  # meta_obj_type_index -> entry_index
    # Group memblocks by (pool_index, alignment)
    mem_group_to_entry = {}  # (pool_index, alignment) -> entry_index

    for i, ri in enumerate(alloc.ref_info):
        if ri['is_object']:
            obj = alloc.objects[i]
            type_idx = obj.type_index
            if type_idx not in obj_type_to_entry:
                # Create new igObjectDirEntry
                # Fields: slot 2=0(string), slot 11=type_index, slot 12=-1
                entry = EntryDef(obj_entry_type, [0, type_idx, -1])
                eidx = len(entries)
                entries.append(entry)
                obj_type_to_entry[type_idx] = eidx
            index_map[i] = obj_type_to_entry[type_idx]
        else:
            # Memory block — determine pool and alignment from metadata
            mem_size = ri.get('mem_size', 0)
            pool_idx = ri.get('pool_index', 0)
            alignment = ri.get('alignment', -1)

            key = (pool_idx, alignment)
            if key not in mem_group_to_entry:
                # Create new igMemoryDirEntry
                # Fields: slot2=0, slot7=mem_size, slot10=pool_index,
                #         slot11=1(bool), slot12=alignment, slot13=-1
                entry = EntryDef(mem_entry_type, [0, mem_size, pool_idx, 1, alignment, -1])
                eidx = len(entries)
                entries.append(entry)
                mem_group_to_entry[key] = eidx
            index_map[i] = mem_group_to_entry[key]

    return entries, index_map


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class IGBAnimationBuilder:
    """Build IGB animation files from scratch."""

    def __init__(self, game='xml2', reference_path=None):
        """
        Args:
            game: 'xml2' or 'mua'
            reference_path: path to a reference IGB file for schema extraction.
                           Only needed on first use; schema is cached afterward.
        """
        self.game = game
        self.config = GAME_CONFIGS[game]
        self.schema = get_schema(game, reference_path)
        self._type_idx = _build_type_index(self.schema['meta_objects'])
        self._field_idx = _build_field_index(self.schema['meta_fields'])

    def _ti(self, name):
        """Look up meta-object type index by name."""
        if isinstance(name, str):
            name = name.encode('ascii')
        return self._type_idx[name]

    def build(self, skeleton_data, animations, output_path):
        """Build a complete animation IGB file.

        Args:
            skeleton_data: dict with:
                'name': skeleton name string
                'bones': list of dicts, each with:
                    'name': bone name string
                    'parent': parent bone index (-1 for root)
                    'flags': bone flags (64 for root, 2 for normal)
                    'matrix': list of 12 floats (4x3 row-major inv bind)
            animations: list of dicts, each with:
                'name': animation name string
                'duration_sec': float, animation length in seconds
                'tracks': list of dicts, each with:
                    'bone_name': string
                    'track_id': int (index into enbaya stream)
                    'rest_quat': (x,y,z,w) tuple
                    'rest_trans': (x,y,z) tuple
                    'keyframes': list of (time_sec, (qx,qy,qz,qw), (tx,ty,tz))
                'motion_track': None or dict with TransformSequence data
            output_path: file path for output .igb

        Returns:
            path to the written file
        """
        endian = self.config['endian']
        alloc = _RefAllocator()

        # ---- 1. Build skeleton ----
        skel_ref, bone_refs = self._build_skeleton(alloc, skeleton_data, endian)

        # ---- 2. Build animations ----
        anim_refs = []
        for anim_data in animations:
            anim_ref = self._build_animation(
                alloc, anim_data, skel_ref, skeleton_data, endian
            )
            anim_refs.append(anim_ref)

        # ---- 3. Build top-level lists ----
        skel_list_ref = self._build_object_list(
            alloc, b'igSkeletonList', [skel_ref], endian
        )
        anim_list_ref = self._build_object_list(
            alloc, b'igAnimationList', anim_refs, endian
        )
        skin_list_ref = self._build_empty_list(alloc, b'igSkinList')
        appear_list_ref = self._build_empty_list(alloc, b'igAppearanceList')
        combiner_list_ref = self._build_empty_list(alloc, b'igAnimationCombinerList')

        # ---- 4. Build AnimationDatabase ----
        animdb_name = skeleton_data.get('name', 'animation_database')
        # Strip _skel suffix if present
        if animdb_name.endswith('_skel'):
            animdb_name = animdb_name[:-5]

        animdb_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationDatabase'), [
            (2, b'String', 4, animdb_name),
            (4, b'Bool', 1, 1),
            (5, b'ObjectRef', 4, skel_list_ref),
            (6, b'ObjectRef', 4, anim_list_ref),
            (7, b'ObjectRef', 4, skin_list_ref),
            (8, b'ObjectRef', 4, appear_list_ref),
            (9, b'ObjectRef', 4, combiner_list_ref),
        ]))

        # ---- 5. Build InfoList ----
        info_list_data = struct.pack(endian + 'I', animdb_ref)
        info_data_ref = alloc.add_memblock(info_list_data)
        alloc.ref_info[info_data_ref]['pool_index'] = 0
        alloc.ref_info[info_data_ref]['alignment'] = -1

        info_list_ref = alloc.add_object(_make_obj(self._ti(b'igInfoList'), [
            (2, b'Int', 4, 1),       # count
            (3, b'Int', 4, 1),       # capacity
            (4, b'MemoryRef', 4, info_data_ref),
        ]))

        # ---- 6. Generate entries + index ----
        entries, index_map = _generate_entries_and_index(
            alloc, self.schema['meta_objects'], self.schema['meta_fields']
        )

        # Find the info list entry index for the info section
        info_entry_idx = index_map[info_list_ref]

        # ---- 7. Assemble writer ----
        writer = IGBWriter()
        writer.version = self.config['version']
        writer.endian = self.config['endian']
        writer.has_info = self.schema['has_info']
        writer.has_external = self.schema['has_external']
        writer.shared_entries = self.schema['shared_entries']
        writer.has_memory_pool_names = self.schema['has_memory_pool_names']

        writer.meta_fields = self.schema['meta_fields']
        writer.meta_objects = self.schema['meta_objects']
        writer.alignment_data = self.schema['alignment_data']
        writer.external_dirs = self.schema['external_dirs']
        writer.external_dirs_unk = self.schema['external_dirs_unk']
        writer.memory_pool_names = self.schema['memory_pool_names']

        writer.entries = entries
        writer.index_map = index_map
        writer.info_list_index = info_entry_idx
        writer.objects = alloc.objects
        writer.ref_info = alloc.ref_info

        # Write
        writer.write(output_path)
        _log.info("Wrote from-scratch IGB: %s (%d refs, %d animations)",
                  output_path, len(alloc.objects), len(animations))
        return output_path

    # ------------------------------------------------------------------
    # Skeleton construction
    # ------------------------------------------------------------------

    def _build_skeleton(self, alloc, skel_data, endian):
        """Build igSkeleton + igSkeletonBoneInfoList + igSkeletonBoneInfo[].

        Returns:
            (skel_ref, [bone_info_refs])
        """
        bones = skel_data['bones']
        num_bones = len(bones)

        # Build bone info objects
        bone_refs = []
        for bone in bones:
            bone_ref = alloc.add_object(_make_obj(self._ti(b'igSkeletonBoneInfo'), [
                (2, b'String', 4, bone['name']),
                (3, b'Int', 4, bone['parent']),
                (4, b'Int', 4, -1),  # unused
                (5, b'Int', 4, bone.get('flags', 2)),
            ]))
            bone_refs.append(bone_ref)

        # BoneInfoList data (array of ObjectRef indices)
        bone_list_data = _make_list_data(bone_refs, endian)
        bone_list_data_ref = alloc.add_memblock(bone_list_data)
        alloc.ref_info[bone_list_data_ref]['pool_index'] = 36  # Actor pool
        alloc.ref_info[bone_list_data_ref]['alignment'] = 2

        bone_list_ref = alloc.add_object(_make_obj(self._ti(b'igSkeletonBoneInfoList'), [
            (2, b'Int', 4, num_bones),
            (3, b'Int', 4, num_bones),
            (4, b'MemoryRef', 4, bone_list_data_ref),
        ]))

        # Bone matrix data (N * 3 floats = N * 12 bytes: translation per bone)
        matrix_data = bytearray()
        for bone in bones:
            mat = bone.get('matrix', [0.0, 0.0, 0.0])
            # Ensure we have exactly 3 floats
            if len(mat) > 3:
                mat = mat[:3]
            while len(mat) < 3:
                mat.append(0.0)
            matrix_data.extend(struct.pack(endian + '3f', *mat))

        matrices_ref = alloc.add_memblock(bytes(matrix_data))
        alloc.ref_info[matrices_ref]['pool_index'] = 36  # Actor pool
        alloc.ref_info[matrices_ref]['alignment'] = -1

        # Empty inv_bind block (slot 5)
        empty_ref = alloc.add_memblock(b'', mem_size=0)
        alloc.ref_info[empty_ref]['pool_index'] = 40  # Level pool
        alloc.ref_info[empty_ref]['alignment'] = -1

        # Skeleton object
        skel_ref = alloc.add_object(_make_obj(self._ti(b'igSkeleton'), [
            (2, b'String', 4, skel_data['name']),
            (3, b'MemoryRef', 4, matrices_ref),
            (4, b'ObjectRef', 4, bone_list_ref),
            (5, b'MemoryRef', 4, empty_ref),
            (6, b'Int', 4, 0),
        ]))

        return skel_ref, bone_refs

    # ------------------------------------------------------------------
    # Animation construction
    # ------------------------------------------------------------------

    def _build_animation(self, alloc, anim_data, skel_ref, skel_data, endian):
        """Build igAnimation + tracks + bindings + enbaya source.

        Returns:
            anim_ref (ref index of the igAnimation object)
        """
        from .enbaya_encoder import compress_enbaya

        tracks = anim_data['tracks']
        duration_sec = anim_data['duration_sec']
        duration_ns = int(duration_sec * 1_000_000_000)
        num_tracks = len(tracks)

        # ---- Enbaya compression ----
        # Prepare track keyframes in the format enbaya_encoder expects:
        # list of per-track [(time_s, quat_xyzw, trans_xyz), ...]
        track_keyframes = []
        for track in tracks:
            kfs = []
            for time_s, quat, trans in track['keyframes']:
                kfs.append((time_s, quat, trans))
            track_keyframes.append(kfs)

        enbaya_blob = compress_enbaya(
            track_keyframes, duration_sec,
            sample_rate=30,
            signature=self.config['enbaya_signature'],
        )

        enbaya_blob_ref = alloc.add_memblock(enbaya_blob)
        alloc.ref_info[enbaya_blob_ref]['pool_index'] = 3  # NonTracked
        alloc.ref_info[enbaya_blob_ref]['alignment'] = -1

        # igEnbayaAnimationSource
        enbaya_source_ref = alloc.add_object(_make_obj(self._ti(b'igEnbayaAnimationSource'), [
            (2, b'MemoryRef', 4, enbaya_blob_ref),
            (3, b'UnsignedCharArray', 3, b'\x01\x03\x01'),
            (4, b'UnsignedChar', 1, 3),
            (5, b'Enum', 4, 0),
        ]))

        # ---- Transform sources + tracks ----
        track_refs = []
        for track in tracks:
            # igEnbayaTransformSource
            ets_ref = alloc.add_object(_make_obj(self._ti(b'igEnbayaTransformSource'), [
                (2, b'Int', 4, track['track_id']),
                (3, b'ObjectRef', 4, enbaya_source_ref),
            ]))

            # igAnimationTrack
            rq = track['rest_quat']   # (x,y,z,w)
            rt = track['rest_trans']  # (x,y,z)
            at_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationTrack'), [
                (2, b'String', 4, track['bone_name']),
                (3, b'ObjectRef', 4, ets_ref),
                (4, b'Vec4f', 16, rq),
                (5, b'Vec3f', 12, rt),
            ]))
            track_refs.append(at_ref)

        # ---- Motion track (igTransformSequence1_5) ----
        motion = anim_data.get('motion_track')
        if motion is not None:
            motion_ref = self._build_motion_track(alloc, motion, endian)
            # igAnimationTrack for Motion
            motion_track_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationTrack'), [
                (2, b'String', 4, 'Motion'),
                (3, b'ObjectRef', 4, motion_ref),
                (4, b'Vec4f', 16, motion.get('rest_quat', (0.0, 0.0, 0.0, 999.0))),
                (5, b'Vec3f', 12, motion.get('rest_trans', (0.0, 0.0, 0.0))),
            ]))
            track_refs.append(motion_track_ref)

        # ---- Track list ----
        track_list_data = _make_list_data(track_refs, endian)
        track_list_data_ref = alloc.add_memblock(track_list_data)
        alloc.ref_info[track_list_data_ref]['pool_index'] = 38  # Attribute
        alloc.ref_info[track_list_data_ref]['alignment'] = -1

        track_list_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationTrackList'), [
            (2, b'Int', 4, len(track_refs)),
            (3, b'Int', 4, len(track_refs)),
            (4, b'MemoryRef', 4, track_list_data_ref),
        ]))

        # ---- Binding ----
        # Bone index map: maps track_id → bone index in skeleton
        bones = skel_data['bones']
        bone_name_to_idx = {b['name']: i for i, b in enumerate(bones)}

        binding_indices = []
        for track in tracks:
            bone_idx = bone_name_to_idx.get(track['bone_name'], 0)
            binding_indices.append(bone_idx)
        if motion is not None:
            # Motion track maps to the root/motion bone
            binding_indices.append(bone_name_to_idx.get('Motion', 0))

        binding_map_data = struct.pack(endian + 'I' * len(binding_indices), *binding_indices)
        binding_map_ref = alloc.add_memblock(binding_map_data)
        alloc.ref_info[binding_map_ref]['pool_index'] = 1  # Default
        alloc.ref_info[binding_map_ref]['alignment'] = -1

        binding_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationBinding'), [
            (2, b'ObjectRef', 4, skel_ref),
            (3, b'MemoryRef', 4, binding_map_ref),
            (4, b'Int', 4, len(binding_indices)),
            (5, b'ObjectRef', 4, -1),  # NULL
            (6, b'ObjectRef', 4, -1),  # NULL
        ]))

        # ---- Binding list ----
        binding_list_data = _make_list_data([binding_ref], endian)
        binding_list_data_ref = alloc.add_memblock(binding_list_data)
        alloc.ref_info[binding_list_data_ref]['pool_index'] = 0  # Bootstrap
        alloc.ref_info[binding_list_data_ref]['alignment'] = -1

        binding_list_ref = alloc.add_object(_make_obj(self._ti(b'igAnimationBindingList'), [
            (2, b'Int', 4, 1),
            (3, b'Int', 4, 1),
            (4, b'MemoryRef', 4, binding_list_data_ref),
        ]))

        # ---- Transition definition list (empty) ----
        trans_def_list_ref = self._build_empty_list(
            alloc, b'igAnimationTransitionDefinitionList'
        )

        # ---- Animation object ----
        anim_ref = alloc.add_object(_make_obj(self._ti(b'igAnimation'), [
            (2, b'String', 4, anim_data['name']),
            (3, b'Int', 4, 0),
            (4, b'ObjectRef', 4, binding_list_ref),
            (5, b'ObjectRef', 4, track_list_ref),
            (6, b'ObjectRef', 4, trans_def_list_ref),
            (7, b'Long', 8, 0),            # start offset
            (8, b'Long', 8, 0),            # loop offset
            (9, b'Long', 8, duration_ns),   # duration
            (10, b'ObjectRef', 4, -1),      # bitmask (NULL for now)
        ]))

        return anim_ref

    # ------------------------------------------------------------------
    # Motion track (TransformSequence1_5)
    # ------------------------------------------------------------------

    def _build_motion_track(self, alloc, motion, endian):
        """Build an igTransformSequence1_5 for root motion.

        Args:
            motion: dict with 'quaternions' (list of (w,x,y,z)),
                    'translations' (list of (x,y,z)),
                    'timestamps_ns' (list of int64),
                    'duration_ns' (int64), 'offset_ns' (int64)
        Returns:
            ref index of the igTransformSequence1_5 object
        """
        quats = motion['quaternions']
        trans = motion['translations']
        times = motion['timestamps_ns']
        num_keys = len(quats)

        # Quaternion list data
        quat_data = bytearray()
        for q in quats:
            quat_data.extend(struct.pack(endian + '4f', *q))
        quat_data_ref = alloc.add_memblock(bytes(quat_data))
        alloc.ref_info[quat_data_ref]['pool_index'] = 12  # Texture pool
        alloc.ref_info[quat_data_ref]['alignment'] = -1

        quat_list_ref = alloc.add_object(_make_obj(self._ti(b'igQuaternionfList'), [
            (2, b'Int', 4, num_keys),
            (3, b'Int', 4, num_keys),
            (4, b'MemoryRef', 4, quat_data_ref),
        ]))

        # Translation list data
        trans_data = bytearray()
        for t in trans:
            trans_data.extend(struct.pack(endian + '3f', *t))
        trans_data_ref = alloc.add_memblock(bytes(trans_data))
        alloc.ref_info[trans_data_ref]['pool_index'] = 12  # Texture pool
        alloc.ref_info[trans_data_ref]['alignment'] = -1

        trans_list_ref = alloc.add_object(_make_obj(self._ti(b'igVec3fList'), [
            (2, b'Int', 4, num_keys),
            (3, b'Int', 4, num_keys),
            (4, b'MemoryRef', 4, trans_data_ref),
        ]))

        # Timestamp list data
        time_data = struct.pack(endian + 'q' * num_keys, *times)
        time_data_ref = alloc.add_memblock(time_data)
        alloc.ref_info[time_data_ref]['pool_index'] = 0  # Bootstrap
        alloc.ref_info[time_data_ref]['alignment'] = -1

        time_list_ref = alloc.add_object(_make_obj(self._ti(b'igLongList'), [
            (2, b'Int', 4, num_keys),
            (3, b'Int', 4, num_keys),
            (4, b'MemoryRef', 4, time_data_ref),
        ]))

        offset_ns = motion.get('offset_ns', 0)
        duration_ns = motion.get('duration_ns', 0)

        ts_ref = alloc.add_object(_make_obj(self._ti(b'igTransformSequence1_5'), [
            (2, b'ObjectRef', 4, trans_list_ref),
            (3, b'ObjectRef', 4, quat_list_ref),
            (4, b'ObjectRef', 4, -1),   # NULL (scale list)
            (5, b'ObjectRef', 4, -1),   # NULL
            (6, b'Double', 8, -1.0),    # weight
            (7, b'Bool', 1, 0),
            (8, b'Enum', 4, 0),
            (10, b'Vec3f', 12, (0.0, 0.0, 0.0)),
            (11, b'ObjectRef', 4, time_list_ref),
            (12, b'ObjectRef', 4, -1),  # NULL
            (13, b'ObjectRef', 4, -1),  # NULL
            (14, b'ObjectRef', 4, -1),  # NULL
            (15, b'UnsignedChar', 1, 3),
            (16, b'UnsignedCharArray', 3, b'\x01\x03\x00'),
            (17, b'Long', 8, offset_ns),
            (18, b'Long', 8, duration_ns),
            (19, b'ObjectRef', 4, -1),  # NULL
        ]))

        return ts_ref

    # ------------------------------------------------------------------
    # List helpers
    # ------------------------------------------------------------------

    def _build_object_list(self, alloc, type_name, item_refs, endian):
        """Build an igObjectList subclass (e.g., igSkeletonList) containing items."""
        if isinstance(type_name, str):
            type_name = type_name.encode('ascii')
        count = len(item_refs)
        data = _make_list_data(item_refs, endian)
        data_ref = alloc.add_memblock(data)
        alloc.ref_info[data_ref]['pool_index'] = 0  # Bootstrap
        alloc.ref_info[data_ref]['alignment'] = -1

        list_ref = alloc.add_object(_make_obj(self._ti(type_name), [
            (2, b'Int', 4, count),
            (3, b'Int', 4, count),
            (4, b'MemoryRef', 4, data_ref),
        ]))
        return list_ref

    def _build_empty_list(self, alloc, type_name):
        """Build an empty igObjectList subclass (count=0, no data)."""
        if isinstance(type_name, str):
            type_name = type_name.encode('ascii')
        return alloc.add_object(_make_obj(self._ti(type_name), [
            (2, b'Int', 4, 0),
            (3, b'Int', 4, 0),
            (4, b'MemoryRef', 4, -1),  # NULL
        ]))
