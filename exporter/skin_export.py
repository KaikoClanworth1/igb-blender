"""From-scratch skin export for IGB actor files.

Builds a complete skin IGB file without any template, using SkinBuilder
to construct all meta-objects, scene graph, skeleton, and geometry data
from scratch. This eliminates template dependency and all associated
patching issues.

Pipeline:
    1. Extract skeleton data from armature custom properties
    2. Extract BMS palette from armature custom properties
    3. Extract mesh data from Blender (positions, normals, UVs, blend data)
    4. Extract material/texture data
    5. Build complete IGB via SkinBuilder.build_skin()
    6. Write output IGB
"""

import json
import math
import os
import struct
from typing import Dict, List, Optional, Tuple


def export_skin(filepath, mesh_objs, armature_obj, operator=None, swap_rb=False,
                texture_mode='dxt5'):
    """Export Blender skin mesh(es) to an IGB file using from-scratch builder.

    Args:
        filepath: Output .igb file path.
        mesh_objs: List of (mesh_obj, is_outline) tuples.
        armature_obj: Blender armature object (with igb_* custom properties).
        operator: Blender operator for reporting.
        swap_rb: If True, swap R/B channels in DXT for MUA PC BGR565 encoding.
        texture_mode: 'dxt5' for DXT5 compressed, 'clut' for 256-color palette (universal).

    Returns:
        True on success, False on failure.
    """
    from .skin_builder import SkinBuilder
    from .mesh_extractor import extract_skin_mesh, extract_mesh

    # Normalize mesh_objs
    if not isinstance(mesh_objs, list):
        mesh_objs = [(mesh_objs, False)]

    # ---- 1. Extract skeleton data from armature custom properties ----
    skeleton_data = _extract_skeleton_from_armature(armature_obj)
    if skeleton_data is None:
        _report(operator, 'ERROR',
                "No skeleton data found on armature. "
                "Re-import the actor with a skin file to store skeleton data.")
        return False

    # ---- 2. Apply export scale to skeleton data ----
    # Combine igb_export_scale with armature's actual object scale
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    from mathutils import Vector
    obj_scale = armature_obj.matrix_world.to_scale()
    obj_scale_uniform = (obj_scale.x + obj_scale.y + obj_scale.z) / 3.0
    scale_factor = custom_scale * obj_scale_uniform
    _apply_scale_to_skeleton(skeleton_data, scale_factor)

    # NOTE: No axis rotation is applied to skeleton data here.
    # The rig converter already bakes the game-space rotation (Rz90 @ arm_world_rot)
    # into translations and inv_joint matrices via _get_game_rotation().
    # _transform_mesh_for_export applies the same rotation to mesh vertices.
    # Both are already consistent — no additional rotation needed.

    # ---- 3. Build BMS palette ----
    bms_palette = _extract_bms_palette(armature_obj, skeleton_data)

    _report(operator, 'INFO',
            f"Skeleton: {len(skeleton_data['bones'])} bones, "
            f"{skeleton_data['joint_count']} joints, "
            f"BMS palette: {len(bms_palette)} entries")

    # ---- 3. Build submesh data for the builder ----
    submeshes = []
    total_verts = 0
    total_tris = 0

    for mesh_obj, is_outline in mesh_objs:
        if mesh_obj is None or mesh_obj.type != 'MESH':
            continue

        # Extract mesh data
        # Both main and outline meshes need blend data for skinned animation.
        # Outlines are skinned too (they deform with the character).
        skel_adapter = _SkeletonAdapter(skeleton_data)

        # Check if this mesh has vertex groups (needed for skin extraction)
        has_vertex_groups = bool(mesh_obj.vertex_groups)
        if has_vertex_groups:
            mesh_export = extract_skin_mesh(
                mesh_obj, armature_obj, skel_adapter,
                bms_indices=bms_palette, uv_v_flip=True
            )
        else:
            # Fallback for meshes without vertex groups
            mesh_export = extract_mesh(mesh_obj, uv_v_flip=True)

        # IGB skin files store vertices in armature/bind-pose space.
        # igTransform nodes (e.g., on Bishop's drawn guns) are scene graph
        # metadata — they do NOT offset vertex positions.  We skip baking
        # matrix_local here; vertices from extract_skin_mesh are already
        # in the correct space.

        # Apply armature world rotation + export scale to vertex data
        _transform_mesh_for_export(mesh_export, armature_obj)

        num_verts = len(mesh_export.positions)
        num_tris = len(mesh_export.indices) // 3
        has_blend = bool(mesh_export.blend_weights)
        # Check if blend data is actually meaningful (not all zeros)
        weighted_verts = 0
        if has_blend:
            weighted_verts = sum(
                1 for w in mesh_export.blend_weights if any(x > 0 for x in w))

        # ALL geometry under igBlendMatrixSelect MUST be skinned (have blend
        # weights/indices).  Unskinned meshes (format 0x10003) will be invisible
        # because the game's skin renderer expects blend data on every geometry.
        # If a mesh has no blend data, auto-assign all vertices to BMS bone 0
        # (typically Bip01) so it at least renders.  This commonly happens when
        # the user adds a segment mesh that lacks vertex groups.
        if num_verts > 0 and not has_blend:
            _report(operator, 'WARNING',
                    f"Mesh '{mesh_obj.name}' has no vertex groups / blend "
                    f"weights. Auto-assigning all {num_verts} vertices to "
                    f"bone index 0 (BMS[0]) so it renders under the skin. "
                    f"For proper deformation, add vertex groups matching "
                    f"skeleton bone names.")
            # BMS index 0 = first bone in BMS palette
            mesh_export.blend_weights = [(1.0, 0.0, 0.0, 0.0)] * num_verts
            mesh_export.blend_indices = [(0, 0, 0, 0)] * num_verts
            has_blend = True
            weighted_verts = num_verts

        _report(operator, 'INFO',
                f"{'Outline' if is_outline else 'Main'} mesh '{mesh_obj.name}': "
                f"{num_verts} verts, {num_tris} tris, "
                f"weighted={weighted_verts}/{num_verts}")

        if has_blend and weighted_verts == 0 and num_verts > 0:
            _report(operator, 'WARNING',
                    f"Mesh '{mesh_obj.name}' has vertex groups but ALL blend "
                    f"weights are zero! The mesh will not deform. Check that "
                    f"vertex group names match skeleton bone names.")

        # Extract material (with IGB custom properties for render state)
        material = _extract_material_props(mesh_obj)

        # Extract texture
        tex_name = _get_texture_name(mesh_obj)
        sub_dict = {
            'mesh': mesh_export,
            'material': material,
            'texture_name': tex_name,
            'is_outline': is_outline,
            'segment_name': mesh_obj.get('igb_segment_name', ''),
            'segment_flags': mesh_obj.get('igb_segment_flags', 0),
        }

        if texture_mode == 'clut':
            clut_result = _get_texture_clut(mesh_obj)
            sub_dict['clut_data'] = clut_result
            sub_dict['texture_levels'] = None
        else:
            sub_dict['texture_levels'] = _get_texture(mesh_obj, swap_rb=swap_rb)
            sub_dict['clut_data'] = None

        submeshes.append(sub_dict)

        total_verts += num_verts
        total_tris += num_tris

    if not submeshes:
        _report(operator, 'ERROR', "No valid mesh objects to export")
        return False

    # ---- 4. Build IGB via SkinBuilder ----
    # Use output filename stem as the "public" name (vanilla convention:
    # 0601.igb uses "0601" for igSkin name, geometry names, etc.)
    export_name = os.path.splitext(os.path.basename(filepath))[0]

    builder = SkinBuilder()
    writer = builder.build_skin(submeshes, skeleton_data, bms_palette,
                                export_name=export_name)

    # ---- 5. Write to disk ----
    writer.write(filepath)

    _report(operator, 'INFO',
            f"Exported skin to {os.path.basename(filepath)} "
            f"({total_verts} verts, {total_tris} tris, "
            f"{len(submeshes)} parts)")
    return True


# ============================================================================
# Export-time transforms (rotation + scale)
# ============================================================================

def _transform_mesh_for_export(mesh_export, armature_obj):
    """Transform vertex data from Blender armature-local space to game space.

    Applies:
    1. Armature world rotation — converts Y-up FBX imports to Z-up game space.
       For native XML2 actors (identity rotation) this is a no-op.
    2. Blender-to-XML2 axis conversion — for converted rigs (igb_converted_rig),
       applies +90° Z rotation to convert Blender convention (X=right, -Y=forward)
       to XML2 game convention (X=forward, Y=left).  Native IGB imports are already
       in game convention and don't need this.
    3. Export scale factor — stored as 'igb_export_scale' custom property
       on the armature by the rig converter.

    Modifies mesh_export in place (positions, normals, bounding box).
    """
    from mathutils import Vector, Quaternion, Matrix
    import math

    # Get armature world rotation
    arm_rot_q = armature_obj.matrix_world.to_quaternion()
    identity_q = Quaternion()
    has_rotation = arm_rot_q.rotation_difference(identity_q).angle > 0.001

    # For converted rigs with native skeleton data, add a +90° Z rotation
    # to convert from Blender axis convention to XML2 game convention.
    # Blender: X=right, -Y=forward, Z=up  →  XML2: X=forward, Y=left, Z=up
    # +90° Z rotation: (x, y, z) → (-y, x, z)
    converted_rig = armature_obj.get("igb_converted_rig", False)
    if converted_rig:
        # Build combined rotation: Rz(+90°) @ world_rotation
        rz90 = Quaternion((0, 0, 1), math.radians(90))
        combined_q = rz90 @ arm_rot_q
        arm_rot_q = combined_q
        has_rotation = True

    # Get export scale factor: combine igb_export_scale custom property
    # with the armature's actual object scale (uniform axis average).
    # This way, if the user scales the armature in Object Mode, the
    # exported mesh reflects that scale.
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    obj_scale = armature_obj.matrix_world.to_scale()
    obj_scale_uniform = (obj_scale.x + obj_scale.y + obj_scale.z) / 3.0
    scale_factor = custom_scale * obj_scale_uniform
    has_scale = abs(scale_factor - 1.0) > 0.001

    if not has_rotation and not has_scale:
        return  # Nothing to do

    arm_rot_mat = arm_rot_q.to_matrix()  # 3x3

    # Transform vertex positions: rotate then scale
    for i, (x, y, z) in enumerate(mesh_export.positions):
        v = Vector((x, y, z))
        if has_rotation:
            v = arm_rot_mat @ v
        if has_scale:
            v = v * scale_factor
        mesh_export.positions[i] = (v.x, v.y, v.z)

    # Transform normals: rotate only (normals don't scale)
    if has_rotation and mesh_export.normals:
        for i, (nx, ny, nz) in enumerate(mesh_export.normals):
            n = arm_rot_mat @ Vector((nx, ny, nz))
            mesh_export.normals[i] = (n.x, n.y, n.z)

    # Recompute bounding box
    if mesh_export.positions:
        xs = [p[0] for p in mesh_export.positions]
        ys = [p[1] for p in mesh_export.positions]
        zs = [p[2] for p in mesh_export.positions]
        mesh_export.bbox_min = (min(xs), min(ys), min(zs))
        mesh_export.bbox_max = (max(xs), max(ys), max(zs))


def _apply_scale_to_skeleton(skeleton_data, scale_factor):
    """Apply export scale factor to skeleton translations and inv_bind matrices.

    For translations: multiply by scale_factor.
    For inv_bind matrices: scale the translation row (last row in row-major).
    The inverse of a scaled bind matrix [R | S*p] is [R^T | S*(-R^T*p)],
    so the translation in the inverse is multiplied by S.
    """
    if abs(scale_factor - 1.0) <= 0.001:
        return

    for bone in skeleton_data['bones']:
        # Scale translations
        tx, ty, tz = bone['translation']
        bone['translation'] = (tx * scale_factor, ty * scale_factor,
                               tz * scale_factor)

        # Scale inv_joint_matrix translation row
        ijm = bone.get('inv_joint_matrix')
        if ijm is not None:
            m = list(ijm)
            # Row-major: last row = [tx, ty, tz, 1.0] at indices 12, 13, 14
            m[12] *= scale_factor
            m[13] *= scale_factor
            m[14] *= scale_factor
            bone['inv_joint_matrix'] = m


def _apply_rotation_to_skeleton(skeleton_data, armature_obj):
    """Apply axis rotation to skeleton data to match mesh vertex rotation.

    _transform_mesh_for_export() applies Rz(+90°) @ arm_world_rot to vertices
    for converted rigs. The skeleton translations and inv_joint matrices must
    receive the same rotation so the exported skeleton matches the rotated mesh.

    For native IGB imports (identity rotation, igb_converted_rig=False), this
    is a no-op.

    Translations are parent-local offsets used in accumulated-translation mode
    (no FK rotation). To produce correct rotated world positions when summing
    translations, each translation vector must be rotated.

    Inv_joint matrices map world → bone space. After rotating world by R:
        new_IJM = old_IJM @ R^{-1}  (column-major / Blender convention)
    """
    from mathutils import Quaternion, Matrix, Vector
    import math

    # Compute the same rotation as _transform_mesh_for_export
    arm_rot_q = armature_obj.matrix_world.to_quaternion()

    converted_rig = armature_obj.get("igb_converted_rig", False)
    if converted_rig:
        rz90 = Quaternion((0, 0, 1), math.radians(90))
        combined_q = rz90 @ arm_rot_q
    else:
        combined_q = arm_rot_q

    # Check if rotation is significant
    identity_q = Quaternion()
    if combined_q.rotation_difference(identity_q).angle < 0.001:
        return  # No rotation needed (native XML2 import case)

    rot_mat_3x3 = combined_q.to_matrix()  # 3x3
    rot_mat_4x4 = rot_mat_3x3.to_4x4()
    rot_inv_4x4 = rot_mat_4x4.inverted()

    for bone in skeleton_data['bones']:
        # Rotate translations (parent-local offsets)
        tx, ty, tz = bone['translation']
        v = rot_mat_3x3 @ Vector((tx, ty, tz))
        bone['translation'] = (v.x, v.y, v.z)

        # Rotate inv_joint matrices
        ijm = bone.get('inv_joint_matrix')
        if ijm is not None:
            # Convert row-major IGB → Blender column-major (transpose)
            ijm_cm = Matrix((
                (ijm[0], ijm[4], ijm[8],  ijm[12]),
                (ijm[1], ijm[5], ijm[9],  ijm[13]),
                (ijm[2], ijm[6], ijm[10], ijm[14]),
                (ijm[3], ijm[7], ijm[11], ijm[15]),
            ))

            # new_IJM = old_IJM @ R^{-1}
            new_ijm_cm = ijm_cm @ rot_inv_4x4

            # Convert back to row-major (transpose)
            bone['inv_joint_matrix'] = [
                new_ijm_cm[0][0], new_ijm_cm[1][0], new_ijm_cm[2][0], new_ijm_cm[3][0],
                new_ijm_cm[0][1], new_ijm_cm[1][1], new_ijm_cm[2][1], new_ijm_cm[3][1],
                new_ijm_cm[0][2], new_ijm_cm[1][2], new_ijm_cm[2][2], new_ijm_cm[3][2],
                new_ijm_cm[0][3], new_ijm_cm[1][3], new_ijm_cm[2][3], new_ijm_cm[3][3],
            ]


# ============================================================================
# Skeleton data extraction from armature custom properties
# ============================================================================

def _extract_skeleton_from_armature(armature_obj):
    """Extract skeleton data from armature custom properties.

    These properties are stored during import by _store_skin_skeleton_data()
    in actor_import.py.

    CRITICAL: Uses the stored bone info list (igb_skin_bone_info_list) which
    preserves the EXACT vanilla bone ordering, parent indices, bm_idx values,
    and flags. This avoids bone reordering issues from Blender's internal
    bone iteration order.

    Returns:
        dict with skeleton_data format, or None if data not found.
    """
    # Check for required properties
    if "igb_skin_bone_translations" not in armature_obj:
        return None

    name = armature_obj.get("igb_skin_skeleton_name", "")
    joint_count = armature_obj.get("igb_skin_joint_count", 0)

    # Parse per-bone data from JSON
    translations = json.loads(armature_obj["igb_skin_bone_translations"])
    inv_matrices_raw = json.loads(armature_obj["igb_skin_inv_joint_matrices"])

    # Use stored bone info list if available (preserves exact vanilla ordering)
    stored_bone_info = armature_obj.get("igb_skin_bone_info_list")
    if stored_bone_info:
        bone_info_list = json.loads(stored_bone_info)
        bones = []
        for bi in bone_info_list:
            bone_idx = bi['index']

            # Get translation from stored data
            if bone_idx < len(translations):
                trans = tuple(translations[bone_idx])
            else:
                trans = (0.0, 0.0, 0.0)

            # Get inv_joint_matrix from stored data
            inv_matrix = None
            if bone_idx < len(inv_matrices_raw) and inv_matrices_raw[bone_idx] is not None:
                inv_matrix = inv_matrices_raw[bone_idx]

            bones.append({
                'name': bi['name'],         # ORIGINAL vanilla name
                'index': bone_idx,
                'parent_idx': bi['parent_idx'],  # ORIGINAL parent index
                'bm_idx': bi['bm_idx'],     # ORIGINAL bm_idx (including -1!)
                'flags': bi['flags'],       # ORIGINAL flags
                'translation': trans,
                'inv_joint_matrix': inv_matrix,
            })

        if not bones:
            return None

        return {
            'name': name,
            'joint_count': joint_count,
            'bones': bones,
        }

    # Fallback: reconstruct from pose bone properties (legacy, less accurate)
    bones = []
    for pb in armature_obj.pose.bones:
        bone_idx = pb.get("igb_bone_index", -1)
        if bone_idx < 0:
            continue

        parent_idx = pb.get("igb_parent_idx", -1)
        bm_idx = pb.get("igb_skin_bm_idx", pb.get("igb_bm_idx", bone_idx))
        flags = pb.get("igb_flags", 0)

        if bone_idx < len(translations):
            trans = tuple(translations[bone_idx])
        else:
            trans = (0.0, 0.0, 0.0)

        inv_matrix = None
        if bone_idx < len(inv_matrices_raw) and inv_matrices_raw[bone_idx] is not None:
            inv_matrix = inv_matrices_raw[bone_idx]

        bones.append({
            'name': pb.name,
            'index': bone_idx,
            'parent_idx': parent_idx,
            'bm_idx': bm_idx,
            'flags': flags,
            'translation': trans,
            'inv_joint_matrix': inv_matrix,
        })

    bones.sort(key=lambda b: b['index'])

    if not bones:
        return None

    return {
        'name': name,
        'joint_count': joint_count,
        'bones': bones,
    }


def _extract_bms_palette(armature_obj, skeleton_data):
    """Extract or build the BMS palette.

    If stored during import, use it. Otherwise, build an identity palette
    for all deforming bones (those with bm_idx >= 0).
    """
    # Try stored palette first
    stored = armature_obj.get("igb_bms_palette")
    if stored:
        return json.loads(stored)

    # Build identity palette: [0, 1, 2, ..., joint_count-1]
    joint_count = skeleton_data.get('joint_count', 0)
    if joint_count > 0:
        return list(range(joint_count))

    # Fallback: collect all unique bm_idx values
    bm_indices = set()
    for bone in skeleton_data['bones']:
        bm = bone['bm_idx']
        if bm >= 0:
            bm_indices.add(bm)

    return sorted(bm_indices)


# ============================================================================
# Skeleton adapter for extract_skin_mesh compatibility
# ============================================================================

class _SkeletonAdapter:
    """Lightweight adapter that makes skeleton_data dict look like ParsedSkeleton.

    extract_skin_mesh() expects a ParsedSkeleton-like object with:
    - bones: list with .name, .index, .bm_idx attributes
    - build_bm_to_bone_map(): returns dict mapping bm_idx -> bone_name
    - get_effective_bm_idx(bone_idx): returns bm_idx
    """

    class _BoneAdapter:
        def __init__(self, bone_dict):
            self.name = bone_dict['name']
            self.index = bone_dict['index']
            self.bm_idx = bone_dict['bm_idx']
            self.parent_idx = bone_dict['parent_idx']
            self.flags = bone_dict['flags']
            self.translation = bone_dict['translation']
            self.inv_joint_matrix = bone_dict.get('inv_joint_matrix')

    def __init__(self, skeleton_data):
        self.name = skeleton_data.get('name', '')
        self.joint_count = skeleton_data.get('joint_count', 0)
        self.bones = [self._BoneAdapter(b) for b in skeleton_data['bones']]

    def get_effective_bm_idx(self, bone_idx):
        if bone_idx < len(self.bones):
            bm = self.bones[bone_idx].bm_idx
            return bm if bm >= 0 else bone_idx
        return bone_idx

    def build_bm_to_bone_map(self):
        mapping = {}
        for bone in self.bones:
            bm = self.get_effective_bm_idx(bone.index)
            mapping[bm] = bone.name
        return mapping


# ============================================================================
# Material / texture extraction
# ============================================================================

def _extract_material_props(mesh_obj):
    """Extract material properties from a Blender mesh object.

    Reads both core material properties (diffuse, specular, etc.) and
    IGB render state custom properties (blend, alpha, color, lighting,
    cull face) set by the IGB Materials panel.

    Returns:
        dict with material properties for the skin builder.
    """
    import bpy

    defaults = {
        'diffuse': (0.8, 0.8, 0.8, 1.0),
        'ambient': (0.8, 0.8, 0.8, 1.0),
        'specular': (0.0, 0.0, 0.0, 1.0),
        'emission': (0.0, 0.0, 0.0, 1.0),
        'shininess': 0.0,
        'flags': 31,
    }

    if not mesh_obj.data.materials:
        return dict(defaults)

    mat = mesh_obj.data.materials[0]
    if mat is None:
        return dict(defaults)

    # Try to read from custom properties stored during import
    diffuse = mat.get("igb_diffuse")
    if diffuse:
        result = {
            'diffuse': tuple(diffuse),
            'ambient': tuple(mat.get("igb_ambient", (0.8, 0.8, 0.8, 1.0))),
            'specular': tuple(mat.get("igb_specular", (0.0, 0.0, 0.0, 1.0))),
            'emission': tuple(mat.get("igb_emission", (0.0, 0.0, 0.0, 1.0))),
            'shininess': mat.get("igb_shininess", 0.0),
            'flags': mat.get("igb_flags", 31),
        }
    elif mat.use_nodes and mat.node_tree:
        # Fallback: extract from Principled BSDF
        result = dict(defaults)
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                bc = node.inputs.get('Base Color')
                if bc:
                    c = bc.default_value
                    result['diffuse'] = (c[0], c[1], c[2], c[3])
                    result['ambient'] = (c[0], c[1], c[2], c[3])
                break
    else:
        result = dict(defaults)

    # Read IGB render state custom properties (set by IGB Materials panel)
    # These override the skin builder's default render state attrs.
    _read_igb_render_state(mat, result)

    return result


def _read_igb_render_state(mat, result):
    """Read IGB render state custom properties from a Blender material.

    Populates result dict with 'color_attr', 'blend_state', 'blend_func',
    'alpha_state', 'alpha_func', 'lighting', 'cull_face' sub-dicts
    if the corresponding igb_* properties are found on the material.
    """
    # Color attribute (tint)
    if "igb_color_r" in mat:
        result['color_attr'] = (
            mat.get("igb_color_r", 1.0),
            mat.get("igb_color_g", 1.0),
            mat.get("igb_color_b", 1.0),
            mat.get("igb_color_a", 1.0),
        )

    # Blend state
    if "igb_blend_enabled" in mat:
        result['blend_enabled'] = bool(mat["igb_blend_enabled"])
        result['blend_src'] = mat.get("igb_blend_src", 4)
        result['blend_dst'] = mat.get("igb_blend_dst", 5)

    # Alpha test
    if "igb_alpha_test_enabled" in mat:
        result['alpha_test_enabled'] = bool(mat["igb_alpha_test_enabled"])
        result['alpha_func'] = mat.get("igb_alpha_func", 6)
        result['alpha_ref'] = mat.get("igb_alpha_ref", 0.5)

    # Lighting
    if "igb_lighting_enabled" in mat:
        result['lighting_enabled'] = bool(mat["igb_lighting_enabled"])

    # Cull face
    if "igb_cull_face_enabled" in mat:
        result['cull_face_enabled'] = bool(mat["igb_cull_face_enabled"])
        result['cull_face_mode'] = mat.get("igb_cull_face_mode", 0)


def _get_texture(mesh_obj, swap_rb=False):
    """Extract texture data from a Blender mesh object's material.

    Uses the same approach as the map file exporter:
    1. Find Image Texture via BSDF Base Color link (or fallback any TEX_IMAGE)
    2. Extract RGBA pixels with Y-flip (Blender OpenGL → DXT/IGB convention)
    3. Ensure power-of-2 dimensions
    4. DXT5 compress with mipmaps

    Returns list of (compressed_dxt5_bytes, width, height) tuples, or None.
    """
    import bpy
    from ..utils.dxt_compress import compress_with_mipmaps

    if not mesh_obj.data.materials:
        return None

    mat = mesh_obj.data.materials[0]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None

    # Find image: check BSDF Base Color input first, then fallback to any TEX_IMAGE
    bl_image = None
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color = node.inputs.get('Base Color')
            if base_color is not None and base_color.is_linked:
                for link in base_color.links:
                    if link.from_node.type == 'TEX_IMAGE' and link.from_node.image:
                        bl_image = link.from_node.image
                        break
            break

    if bl_image is None:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                bl_image = node.image
                break

    if bl_image is None:
        return None

    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None

    # Extract RGBA pixels with Y-flip (Blender bottom-up → DXT top-down)
    pixels = list(bl_image.pixels)
    num_pixels = w * h
    rgba = bytearray(num_pixels * 4)
    for i in range(num_pixels):
        src_y = i // w
        src_x = i % w
        dst_y = h - 1 - src_y
        dst_idx = (dst_y * w + src_x) * 4
        src_idx = i * 4
        rgba[dst_idx + 0] = max(0, min(255, int(pixels[src_idx + 0] * 255 + 0.5)))
        rgba[dst_idx + 1] = max(0, min(255, int(pixels[src_idx + 1] * 255 + 0.5)))
        rgba[dst_idx + 2] = max(0, min(255, int(pixels[src_idx + 2] * 255 + 0.5)))
        rgba[dst_idx + 3] = max(0, min(255, int(pixels[src_idx + 3] * 255 + 0.5)))

    # Ensure power-of-2 dimensions (required for DXT compression)
    new_w = _next_power_of_2(w)
    new_h = _next_power_of_2(h)
    if new_w != w or new_h != h:
        new_rgba = bytearray(new_w * new_h * 4)
        for y in range(new_h):
            src_y = min(y * h // new_h, h - 1)
            for x in range(new_w):
                src_x = min(x * w // new_w, w - 1)
                src_off = (src_y * w + src_x) * 4
                dst_off = (y * new_w + x) * 4
                new_rgba[dst_off:dst_off + 4] = rgba[src_off:src_off + 4]
        rgba = new_rgba
        w, h = new_w, new_h

    return compress_with_mipmaps(bytes(rgba), w, h, swap_rb=swap_rb)


def _get_texture_clut(mesh_obj):
    """Extract texture as CLUT palette + indices for universal PS2-style format.

    Same image extraction as _get_texture, but quantizes to 256 colors
    instead of DXT-compressing. Returns (palette_data, index_data, w, h) or None.
    """
    import bpy
    from ..utils.clut_compress import quantize_rgba_to_clut

    if not mesh_obj.data.materials:
        return None

    mat = mesh_obj.data.materials[0]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None

    # Find image (same logic as _get_texture)
    bl_image = None
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color = node.inputs.get('Base Color')
            if base_color is not None and base_color.is_linked:
                for link in base_color.links:
                    if link.from_node.type == 'TEX_IMAGE' and link.from_node.image:
                        bl_image = link.from_node.image
                        break
            break

    if bl_image is None:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                bl_image = node.image
                break

    if bl_image is None:
        return None

    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None

    # Extract RGBA pixels with Y-flip (Blender bottom-up → IGB top-down)
    pixels = list(bl_image.pixels)
    num_pixels = w * h
    rgba = bytearray(num_pixels * 4)
    for i in range(num_pixels):
        src_y = i // w
        src_x = i % w
        dst_y = h - 1 - src_y
        dst_idx = (dst_y * w + src_x) * 4
        src_idx = i * 4
        rgba[dst_idx + 0] = max(0, min(255, int(pixels[src_idx + 0] * 255 + 0.5)))
        rgba[dst_idx + 1] = max(0, min(255, int(pixels[src_idx + 1] * 255 + 0.5)))
        rgba[dst_idx + 2] = max(0, min(255, int(pixels[src_idx + 2] * 255 + 0.5)))
        rgba[dst_idx + 3] = max(0, min(255, int(pixels[src_idx + 3] * 255 + 0.5)))

    # Ensure power-of-2 dimensions
    new_w = _next_power_of_2(w)
    new_h = _next_power_of_2(h)
    if new_w != w or new_h != h:
        new_rgba = bytearray(new_w * new_h * 4)
        for y in range(new_h):
            src_y = min(y * h // new_h, h - 1)
            for x in range(new_w):
                src_x = min(x * w // new_w, w - 1)
                src_off = (src_y * w + src_x) * 4
                dst_off = (y * new_w + x) * 4
                new_rgba[dst_off:dst_off + 4] = rgba[src_off:src_off + 4]
        rgba = new_rgba
        w, h = new_w, new_h

    palette_data, index_data = quantize_rgba_to_clut(bytes(rgba), w, h)
    return (palette_data, index_data, w, h)


def _next_power_of_2(n):
    """Return the smallest power of 2 >= n."""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def _get_texture_name(mesh_obj):
    """Get the texture image name from a mesh object's material."""
    import bpy

    if not mesh_obj.data.materials:
        return ''

    mat = mesh_obj.data.materials[0]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return ''

    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            return node.image.name

    return ''


# ============================================================================
# Helpers
# ============================================================================

def _report(operator, level, msg):
    """Report a message through the operator if available."""
    if operator:
        operator.report({level}, msg)
