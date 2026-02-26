"""Assign blend weights from ParsedGeometry to Blender vertex groups.

Maps vertex blend indices through the igBlendMatrixSelect remapping
(if present) to skeleton bone names, then creates Blender vertex groups
and assigns weights.
"""

from typing import Dict, List, Optional, Tuple

from .sg_skeleton import ParsedSkeleton


def assign_vertex_groups(mesh_obj, geometry, skeleton, bms_indices=None):
    """Assign blend weights to vertex groups on a mesh.

    Args:
        mesh_obj: Blender mesh Object.
        geometry: ParsedGeometry with blend_weights and blend_indices.
        skeleton: ParsedSkeleton for bone name resolution.
        bms_indices: Optional list of int from igBlendMatrixSelect
                     (maps local vertex blend index -> global blend matrix index).
                     If None, vertex blend indices map directly to bone indices.
    """
    if not geometry.blend_weights or not geometry.blend_indices:
        return

    # Build blend_matrix_index -> bone_name mapping
    bm_to_bone = skeleton.build_bm_to_bone_map()

    # Create vertex groups for all bones
    existing_groups = {vg.name for vg in mesh_obj.vertex_groups}
    for bone in skeleton.bones:
        bone_name = bone.name if bone.name else f"Bone_{bone.index:03d}"
        if bone_name not in existing_groups:
            mesh_obj.vertex_groups.new(name=bone_name)

    # Assign weights
    num_verts = min(len(geometry.blend_weights), len(geometry.blend_indices))

    for vi in range(num_verts):
        weights = geometry.blend_weights[vi]
        indices = geometry.blend_indices[vi]

        for w, bi in zip(weights, indices):
            if w <= 0.0:
                continue

            # Map through BlendMatrixSelect if available
            if bms_indices is not None and bi < len(bms_indices):
                global_bm_idx = bms_indices[bi]
            else:
                global_bm_idx = bi

            # Look up bone name
            bone_name = bm_to_bone.get(global_bm_idx)
            if bone_name is None:
                # Fallback: use bone index directly if within range
                if global_bm_idx < len(skeleton.bones):
                    bone_name = skeleton.bones[global_bm_idx].name
                    if not bone_name:
                        bone_name = f"Bone_{global_bm_idx:03d}"
                else:
                    continue

            vg = mesh_obj.vertex_groups.get(bone_name)
            if vg is not None:
                vg.add([vi], w, 'REPLACE')


def parent_to_armature(mesh_obj, armature_obj):
    """Parent a mesh to an armature with an Armature modifier.

    Args:
        mesh_obj: Blender mesh Object.
        armature_obj: Blender armature Object.
    """
    import bpy

    mesh_obj.parent = armature_obj

    # Add Armature modifier if not already present
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object == armature_obj:
            return  # Already has this modifier

    mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = armature_obj


def extract_bms_indices(reader, bms_obj):
    """Extract blend matrix indices from an igBlendMatrixSelect object.

    The _blendMatrixIndices field is an igIntList (inherits igDataList, NOT
    igObjectList). In XML2 Alchemy 2.5, it appears at slot 10 of the BMS.

    Args:
        reader: IGBReader instance.
        bms_obj: IGBObject of type igBlendMatrixSelect.

    Returns:
        List of int (blend matrix indices), or None.
    """
    import struct
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    endian = reader.header.endian

    for slot, val, fi in bms_obj._raw_fields:
        if fi.short_name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject) and ref.is_type(b"igIntList"):
                # igIntList: data stored as MemoryRef of int32s
                for s2, v2, f2 in ref._raw_fields:
                    if f2.short_name == b"MemoryRef" and v2 != -1:
                        block = reader.resolve_ref(v2)
                        if isinstance(block, IGBMemoryBlock) and block.data:
                            n = block.mem_size // 4
                            values = list(struct.unpack_from(
                                endian + "i" * n, block.data, 0
                            ))
                            return values
    return None
