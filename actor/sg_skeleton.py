"""Parse igSkeleton from an IGB file.

Extracts bone hierarchy, translations, and (if present) inverse joint matrices
from igAnimationDatabase -> igSkeletonList -> igSkeleton objects.

XML2 v6 field layout (verified from game files):
    igSkeleton:
        slot 2: String (name)
        slot 3: MemoryRef (_boneTranslationArray -> Vec3f[])
        slot 4: ObjectRef (_boneInfoList -> igSkeletonBoneInfoList)
        slot 5: MemoryRef (_invJointArray -> Matrix44f[], may be empty)
        slot 6: Int (_jointCount, may be 0)

    igSkeletonBoneInfo:
        slot 2: String (name, from igNamedObject)
        slot 3: Int (_parentIdx, -1 for root)
        slot 4: Int (_bmIdx, blend matrix index, -1 if unused)
        slot 5: Int (_flags)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock


@dataclass
class ParsedBone:
    """A single bone in the skeleton hierarchy."""
    name: str
    index: int
    parent_idx: int         # -1 for root
    bm_idx: int             # blend matrix index (-1 = same as bone index)
    flags: int
    translation: Tuple[float, float, float]  # local offset from parent
    inv_joint_matrix: Optional[Tuple[float, ...]] = None  # 16 floats row-major, or None


@dataclass
class ParsedSkeleton:
    """Complete skeleton extracted from an IGB file."""
    name: str
    bones: List[ParsedBone]
    joint_count: int

    def find_bone_by_name(self, name: str) -> Optional[ParsedBone]:
        for bone in self.bones:
            if bone.name == name:
                return bone
        return None

    def get_children(self, bone_idx: int) -> List[int]:
        """Get indices of all direct children of a bone."""
        return [b.index for b in self.bones if b.parent_idx == bone_idx]

    def get_effective_bm_idx(self, bone_idx: int) -> int:
        """Get the effective blend matrix index for a bone.

        In XML2, bmIdx is always -1, meaning the bone index IS the
        blend matrix index.
        """
        bone = self.bones[bone_idx]
        if bone.bm_idx == -1:
            return bone_idx
        return bone.bm_idx

    def build_bm_to_bone_map(self) -> dict:
        """Build reverse mapping: blend_matrix_index -> bone_name."""
        mapping = {}
        for bone in self.bones:
            bm = self.get_effective_bm_idx(bone.index)
            mapping[bm] = bone.name
        return mapping


def extract_skeleton(reader) -> Optional[ParsedSkeleton]:
    """Extract the first skeleton from an IGB file.

    Looks for igAnimationDatabase -> igSkeletonList -> igSkeleton,
    or falls back to finding igSkeleton directly.

    Args:
        reader: IGBReader instance (already parsed).

    Returns:
        ParsedSkeleton or None if no skeleton found.
    """
    # Strategy 1: Find via igAnimationDatabase
    anim_dbs = reader.get_objects_by_type(b"igAnimationDatabase")
    if anim_dbs:
        skel = _find_skeleton_from_anim_db(reader, anim_dbs[0])
        if skel:
            return skel

    # Strategy 2: Find igSkeleton directly
    skeletons = reader.get_objects_by_type(b"igSkeleton")
    if skeletons:
        return _parse_skeleton(reader, skeletons[0])

    return None


def extract_all_skeletons(reader) -> List[ParsedSkeleton]:
    """Extract all skeletons from an IGB file."""
    results = []
    skeletons = reader.get_objects_by_type(b"igSkeleton")
    for skel_obj in skeletons:
        parsed = _parse_skeleton(reader, skel_obj)
        if parsed:
            results.append(parsed)
    return results


def _find_skeleton_from_anim_db(reader, anim_db_obj) -> Optional[ParsedSkeleton]:
    """Find skeleton through AnimationDatabase -> SkeletonList chain."""
    # igAnimationDatabase fields:
    #   slot 5: ObjectRef -> igSkeletonList
    skel_list_ref = None
    for slot, val, fi in anim_db_obj._raw_fields:
        if fi.short_name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject) and ref.is_type(b"igObjectList"):
                # Check if this list contains igSkeleton objects
                chain = ref.meta_object.get_inheritance_chain()
                if b"igSkeletonList" in chain or b"igObjectList" in chain:
                    items = reader.resolve_object_list(ref)
                    for item in items:
                        if isinstance(item, IGBObject) and item.is_type(b"igSkeleton"):
                            return _parse_skeleton(reader, item)

    return None


def _parse_skeleton(reader, skel_obj) -> Optional[ParsedSkeleton]:
    """Parse a single igSkeleton object into a ParsedSkeleton.

    Args:
        reader: IGBReader instance.
        skel_obj: IGBObject of type igSkeleton.

    Returns:
        ParsedSkeleton or None on failure.
    """
    endian = reader.header.endian
    name = ""
    trans_ref = None
    bone_list_ref = None
    inv_joint_ref = None
    joint_count = 0

    # Extract fields by slot
    for slot, val, fi in skel_obj._raw_fields:
        if fi.short_name == b"String":
            name = val if isinstance(val, str) else val.decode("utf-8", errors="replace")
        elif fi.short_name == b"MemoryRef":
            if slot == 3:
                trans_ref = val       # _boneTranslationArray
            elif slot == 5:
                inv_joint_ref = val   # _invJointArray
        elif fi.short_name == b"ObjectRef" and val != -1:
            if slot == 4:
                bone_list_ref = val   # _boneInfoList
        elif fi.short_name == b"Int":
            if slot == 6:
                joint_count = val     # _jointCount

    # Parse bone info list
    if bone_list_ref is None:
        return None

    bone_infos = _parse_bone_info_list(reader, bone_list_ref)
    if not bone_infos:
        return None

    num_bones = len(bone_infos)

    # Parse bone translations
    translations = _parse_vec3f_array(reader, endian, trans_ref, num_bones)

    # Parse inverse joint matrices (may be empty in XML2)
    inv_joints = _parse_matrix_array(reader, endian, inv_joint_ref, num_bones)

    # Build ParsedBone list
    # CRITICAL: The _invJointArray is indexed by bm_idx (blend matrix index),
    # NOT by sequential bone index. Bone with bm_idx=0 uses inv_joints[0], etc.
    # Non-deforming bones (bm_idx=-1) have no inverse joint matrix.
    bones = []
    for i, (bi_name, bi_parent, bi_bm, bi_flags) in enumerate(bone_infos):
        trans = translations[i] if i < len(translations) else (0.0, 0.0, 0.0)

        # Map inv_joint by bm_idx, not bone index
        if 0 <= bi_bm < len(inv_joints):
            inv_mat = inv_joints[bi_bm]
        else:
            inv_mat = None  # Non-deforming bone (bm_idx=-1) or out of range

        bones.append(ParsedBone(
            name=bi_name,
            index=i,
            parent_idx=bi_parent,
            bm_idx=bi_bm,
            flags=bi_flags,
            translation=trans,
            inv_joint_matrix=inv_mat,
        ))

    # Use actual bone count if joint_count was 0
    if joint_count == 0:
        joint_count = num_bones

    return ParsedSkeleton(
        name=name,
        bones=bones,
        joint_count=joint_count,
    )


def _parse_bone_info_list(reader, list_ref_index):
    """Parse igSkeletonBoneInfoList -> list of (name, parentIdx, bmIdx, flags)."""
    list_obj = reader.resolve_ref(list_ref_index)
    if not isinstance(list_obj, IGBObject):
        return []

    items = reader.resolve_object_list(list_obj)
    result = []

    for item in items:
        if not isinstance(item, IGBObject):
            continue

        bone_name = ""
        parent_idx = -1
        bm_idx = -1
        flags = 0

        for slot, val, fi in item._raw_fields:
            if fi.short_name == b"String":
                bone_name = val if isinstance(val, str) else val.decode("utf-8", errors="replace")
            elif fi.short_name == b"Int":
                if slot == 3:
                    parent_idx = val
                elif slot == 4:
                    bm_idx = val
                elif slot == 5:
                    flags = val

        result.append((bone_name, parent_idx, bm_idx, flags))

    return result


def _parse_vec3f_array(reader, endian, ref_index, expected_count):
    """Parse a MemoryRef to an array of Vec3f (12 bytes each)."""
    if ref_index is None or ref_index == -1:
        return []

    block = reader.resolve_ref(ref_index)
    if not isinstance(block, IGBMemoryBlock) or not block.data or block.mem_size == 0:
        return []

    count = block.mem_size // 12
    result = []
    for i in range(count):
        offset = i * 12
        if offset + 12 <= len(block.data):
            x, y, z = struct.unpack_from(endian + "fff", block.data, offset)
            result.append((x, y, z))

    return result


def _parse_matrix_array(reader, endian, ref_index, expected_count):
    """Parse a MemoryRef to an array of Matrix44f (64 bytes each)."""
    if ref_index is None or ref_index == -1:
        return []

    block = reader.resolve_ref(ref_index)
    if not isinstance(block, IGBMemoryBlock) or not block.data or block.mem_size == 0:
        return []

    count = block.mem_size // 64
    result = []
    for i in range(count):
        offset = i * 64
        if offset + 64 <= len(block.data):
            m = struct.unpack_from(endian + "f" * 16, block.data, offset)
            result.append(m)

    return result
