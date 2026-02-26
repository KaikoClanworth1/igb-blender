"""Orchestrate full actor import: skeleton + skins + animations.

Import pipeline:
1. Parse animation/skeleton file (NN_charactername.igb)
2. Extract skeleton -> build armature
3. Find skin files via herostat/npcstat or manual selection
4. Import each skin -> mesh with vertex groups -> parent to armature
5. Extract animations -> build Blender Actions
"""

import json
import os
from typing import List, Optional, Tuple

from .sg_skeleton import extract_skeleton, ParsedSkeleton
from .sg_animation import extract_animations, extract_animation_names, ParsedAnimation
from .armature_builder import build_armature
from .animation_builder import build_all_actions
from .skinning import assign_vertex_groups, parent_to_armature


def import_actor(context, anim_filepath, skin_filepaths=None,
                 game_dir=None, operator=None, options=None):
    """Import a full actor with skeleton, skins, and animations.

    Args:
        context: Blender context.
        anim_filepath: Path to the animation/skeleton .igb file
                       (e.g., actors/03_wolverine.igb).
        skin_filepaths: List of (variant_name, filepath) tuples for skin files.
                        If None, attempts auto-detection from herostat.
        game_dir: Root game directory for herostat lookup.
        operator: Blender operator for reporting.
        options: Dict of import options:
                 - import_skins: bool (default True)
                 - import_animations: bool (default True)
                 - import_materials: bool (default True)
                 - game_preset: str (default 'auto')

    Returns:
        Tuple of (armature_obj, skin_objects, actions) or None on failure.
    """
    import bpy
    import time

    if options is None:
        options = {}

    t0 = time.perf_counter()

    # ---- 1. Parse animation file ----
    from ..igb_format.igb_reader import IGBReader
    from ..game_profiles import detect_profile, get_profile

    game_preset = options.get('game_preset', 'auto')

    reader = IGBReader(anim_filepath)
    reader.read()

    if game_preset == 'auto':
        profile = detect_profile(reader)
    else:
        profile = get_profile(game_preset)

    # ---- 2. Extract skeleton ----
    skeleton = extract_skeleton(reader)
    if skeleton is None:
        if operator:
            operator.report({'ERROR'}, "No skeleton found in animation file")
        return None

    # Derive actor name from filename
    anim_basename = os.path.splitext(os.path.basename(anim_filepath))[0]

    # Create collection for this actor
    actor_coll = bpy.data.collections.new(anim_basename)
    context.scene.collection.children.link(actor_coll)

    # ---- 3. Extract bind-pose data (ALWAYS needed for armature) ----
    # The bind pose (afakeanim frame 0) defines bone positions and
    # orientations. This is REQUIRED for correct armature building,
    # regardless of whether animations are imported.
    parsed_anims_early = extract_animations(reader, skeleton)
    bind_pose = _extract_bind_pose(parsed_anims_early)

    # ---- 3b. Pre-extract inverse joint matrices from skin skeleton ----
    # Skin files (e.g. 0303.igb) have inverse joint matrices that provide
    # the authoritative bind-pose world positions for each bone. These are
    # indexed by bm_idx (blend matrix index) and give exact positions
    # matching the original FBX/3ds Max skeleton.
    inv_joint_data = None
    skin_data_for_armature = None
    if skin_filepaths:
        first_skin_path = skin_filepaths[0][1] if skin_filepaths else None
        if first_skin_path and os.path.exists(first_skin_path):
            inv_joint_data = _extract_inv_joint_data(first_skin_path)

            # Also extract skin vertex data as fallback (if no bind_pose)
            if not bind_pose:
                skin_data_for_armature = _extract_skin_data(
                    first_skin_path, skeleton, profile
                )

    # ---- 4. Build armature (using inv_joint + bind-pose hybrid, or fallback) ----
    armature_obj = build_armature(
        context, skeleton, anim_basename, actor_coll,
        skin_data=skin_data_for_armature,
        bind_pose=bind_pose,
        inv_joint_data=inv_joint_data,
    )
    armature_obj["igb_anim_file"] = anim_filepath

    t_skel = time.perf_counter()

    # ---- 5. Import skins ----
    skin_objects = []
    if options.get('import_skins', True) and skin_filepaths:
        # Import only the first skin by default
        first_only = options.get('first_skin_only', True)
        to_import = skin_filepaths[:1] if first_only else skin_filepaths

        for variant_name, skin_path in to_import:
            if not os.path.exists(skin_path):
                if operator:
                    operator.report({'WARNING'}, f"Skin file not found: {skin_path}")
                continue

            mesh_objs = _import_skin_file(
                context, skin_path, skeleton, armature_obj,
                actor_coll, variant_name, profile, options
            )
            if mesh_objs:
                for mobj in mesh_objs:
                    # Store template path for skin export
                    mobj["igb_skin_template"] = skin_path
                    skin_objects.append((variant_name, mobj))

        # Hide all but the first skin
        for i, (vname, obj) in enumerate(skin_objects):
            if i > 0:
                obj.hide_viewport = True
                obj.hide_render = True

    t_skins = time.perf_counter()

    # ---- 6. Build animations ----
    actions = []
    if options.get('import_animations', True):
        # Reuse animations if already extracted for bind-pose
        parsed_anims = parsed_anims_early if parsed_anims_early else extract_animations(reader, skeleton)
        actions = build_all_actions(armature_obj, parsed_anims, bind_pose=bind_pose)

        # Don't auto-activate any animation — keep rest pose (T-pose)
        # matching the skin mesh. Users can select animations from the panel.
        # Setting an animation here would deform the skeleton away from T-pose.

    t_anims = time.perf_counter()

    # ---- Report ----
    if operator:
        msg = (
            f"Imported actor '{anim_basename}': "
            f"{len(skeleton.bones)} bones, "
            f"{len(skin_objects)} skins, "
            f"{len(actions)} animations "
            f"({t_skel - t0:.2f}s skel, "
            f"{t_skins - t_skel:.2f}s skins, "
            f"{t_anims - t_skins:.2f}s anims)"
        )
        operator.report({'INFO'}, msg)

    return armature_obj, skin_objects, actions


def _import_skin_file(context, filepath, skeleton, armature_obj,
                      collection, variant_name, profile, options):
    """Import a single skin .igb file and parent to armature.

    Returns:
        List of Blender mesh Objects, or None on failure.
    """
    import bpy
    from ..igb_format.igb_reader import IGBReader
    from ..scene_graph.sg_classes import SceneGraph
    from ..scene_graph.sg_geometry import extract_geometry
    from ..importer.mesh_builder import build_mesh

    reader = IGBReader(filepath)
    reader.read()

    # Extract the skin file's own skeleton for bm_idx mapping.
    # Skin skeletons have proper bm_idx values (e.g., bone 26 "L Thigh"
    # has bm_idx=24) while animation skeletons have bm_idx=-1 for all bones.
    # We need the skin skeleton to correctly map vertex blend indices to bones.
    skin_skeleton = extract_skeleton(reader)

    # Build scene graph (skin files use igSkin._skinnedGraph)
    sg = SceneGraph(reader)
    sg.build()

    # Collect geometry with a simple visitor
    collector = _SkinGeometryCollector(reader, profile)
    sg.walk(collector)

    if not collector.instances:
        return None

    # Use skin skeleton for vertex group assignment (has correct bm_idx),
    # but fall back to animation skeleton for bone name resolution
    vgroup_skeleton = skin_skeleton if skin_skeleton else skeleton

    # Build meshes for each geometry instance
    skin_basename = os.path.splitext(os.path.basename(filepath))[0]
    mesh_name = f"{skin_basename}_{variant_name}"

    mesh_objects = []
    for i, (attr_idx, transform, state) in enumerate(collector.instances):
        geom = extract_geometry(reader, collector.geom_attrs[attr_idx], profile)
        if geom is None:
            continue

        mesh_opts = {
            'import_normals': options.get('import_normals', True),
            'import_uvs': options.get('import_uvs', True),
            'import_vertex_colors': options.get('import_vertex_colors', True),
        }

        # Use node name from scene graph for better naming
        node_name = state.get('node_name', '')
        is_outline = state.get('is_outline', False)
        if is_outline:
            part_name = f"{mesh_name}_outline"
        elif node_name and len(collector.instances) > 1:
            part_name = f"{mesh_name}_{node_name}"
        elif len(collector.instances) > 1:
            part_name = f"{mesh_name}_{i:03d}"
        else:
            part_name = mesh_name
        mesh_obj = build_mesh(
            geom, part_name,
            transform=None,
            options=mesh_opts,
            profile=profile,
        )
        if mesh_obj is None:
            continue

        # Assign vertex groups from blend data using skin skeleton's bm_idx
        bms_indices = state.get('bms_indices')
        assign_vertex_groups(mesh_obj, geom, vgroup_skeleton, bms_indices)

        # Apply transform if present
        if transform:
            _apply_transform(mesh_obj, transform)

        # Parent to armature with Armature modifier
        parent_to_armature(mesh_obj, armature_obj)

        collection.objects.link(mesh_obj)
        # Unlink from default scene collection if needed
        if mesh_obj.name in context.scene.collection.objects:
            context.scene.collection.objects.unlink(mesh_obj)

        # Store metadata for export
        mesh_obj["igb_is_outline"] = is_outline
        mesh_obj["igb_geom_part_index"] = i

        # Import materials if requested
        if options.get('import_materials', True):
            _import_materials_for_mesh(reader, mesh_obj, state, profile)

        mesh_objects.append(mesh_obj)

    # Store skeleton data on armature for from-scratch export (no template needed)
    if skin_skeleton and mesh_objects:
        _store_skin_skeleton_data(armature_obj, skin_skeleton, collector)

    return mesh_objects if mesh_objects else None


def _store_skin_skeleton_data(armature_obj, skin_skeleton, collector):
    """Store skin skeleton data on the armature for from-scratch IGB export.

    This enables the from-scratch skin builder to reconstruct the complete
    skeleton without needing the original skin file as a template.

    Stores:
        On armature:
            igb_skin_skeleton_name: Skeleton name string
            igb_skin_joint_count: Joint count from skeleton
            igb_skin_bone_translations: JSON list of [x,y,z] per bone
            igb_skin_inv_joint_matrices: JSON list of 16-float lists per bone
                                         (null for bones without inv_joint)
            igb_bms_palette: JSON list of int (BMS palette indices)

        On pose bones:
            igb_skin_bm_idx: bm_idx from the SKIN skeleton (may differ from anim skel)

    Args:
        armature_obj: Blender armature object.
        skin_skeleton: ParsedSkeleton from the skin file.
        collector: _SkinGeometryCollector with BMS data from scene graph walk.
    """
    # Store skeleton-level data
    armature_obj["igb_skin_skeleton_name"] = skin_skeleton.name or ""
    armature_obj["igb_skin_joint_count"] = skin_skeleton.joint_count

    # Collect per-bone data as lists (indexed by bone.index)
    translations = []
    inv_joint_matrices = []
    for bone in skin_skeleton.bones:
        translations.append(list(bone.translation))
        if bone.inv_joint_matrix is not None:
            inv_joint_matrices.append(list(bone.inv_joint_matrix))
        else:
            inv_joint_matrices.append(None)

    armature_obj["igb_skin_bone_translations"] = json.dumps(translations)
    armature_obj["igb_skin_inv_joint_matrices"] = json.dumps(inv_joint_matrices)

    # Store the COMPLETE bone info list as JSON — this is the authoritative
    # source for skeleton data during export. Storing individual properties
    # on pose bones is fragile (Blender reorders bones, names get mangled).
    bone_info_list = []
    for bone in skin_skeleton.bones:
        bone_info_list.append({
            'name': bone.name,
            'index': bone.index,
            'parent_idx': bone.parent_idx,
            'bm_idx': bone.bm_idx,        # ORIGINAL bm_idx, including -1!
            'flags': bone.flags,
        })
    armature_obj["igb_skin_bone_info_list"] = json.dumps(bone_info_list)

    # Also store per-bone bm_idx from the skin skeleton on pose bones
    # (still needed for blend weight extraction in extract_skin_mesh)
    for bone in skin_skeleton.bones:
        pb_name = bone.name if bone.name else f"Bone_{bone.index:03d}"
        if pb_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[pb_name]
            pb["igb_skin_bm_idx"] = skin_skeleton.get_effective_bm_idx(bone.index)

    # Extract and store BMS palette from the first geometry instance that has one
    bms_palette = None
    for _, _, state in collector.instances:
        bms = state.get('bms_indices')
        if bms is not None:
            bms_palette = bms
            break

    if bms_palette is not None:
        armature_obj["igb_bms_palette"] = json.dumps(bms_palette)


def _import_materials_for_mesh(reader, mesh_obj, state, profile):
    """Import materials for a skin mesh from scene graph state."""
    from ..scene_graph.sg_materials import extract_material, extract_texture_bind
    from ..importer.material_builder import build_material

    mat_obj = state.get('material_obj')
    texbind_obj = state.get('texbind_obj')

    if mat_obj is None:
        return

    parsed_mat = extract_material(reader, mat_obj, profile)
    parsed_tex = None

    if texbind_obj:
        parsed_tex = extract_texture_bind(reader, texbind_obj, profile)

    blender_mat = build_material(parsed_mat, parsed_tex, name=mesh_obj.name)
    if blender_mat:
        mesh_obj.data.materials.append(blender_mat)


def _extract_skin_data(filepath, skeleton, profile):
    """Extract raw vertex data from a skin file for bone position computation.

    Returns a list of (positions, blend_weights, blend_indices, bms_indices)
    tuples, one per geometry attr in the skin.
    """
    from ..igb_format.igb_reader import IGBReader
    from ..scene_graph.sg_classes import SceneGraph
    from ..scene_graph.sg_geometry import extract_geometry

    reader = IGBReader(filepath)
    reader.read()

    sg = SceneGraph(reader)
    sg.build()

    collector = _SkinGeometryCollector(reader, profile)
    sg.walk(collector)

    if not collector.instances:
        return None

    skin_data = []
    for attr_idx, transform, state in collector.instances:
        geom = extract_geometry(reader, collector.geom_attrs[attr_idx], profile)
        if geom is None:
            continue
        bms_indices = state.get('bms_indices')
        skin_data.append((
            geom.positions,
            geom.blend_weights,
            geom.blend_indices,
            bms_indices,
        ))

    return skin_data if skin_data else None


def _extract_inv_joint_data(filepath):
    """Extract inverse joint matrices from a skin file's skeleton.

    Skin files contain igSkeleton with _invJointArray — these matrices
    are indexed by bm_idx and provide exact world-space bind positions
    when inverted.

    Args:
        filepath: Path to the skin .igb file.

    Returns:
        Dict mapping bone_name -> inv_joint_matrix (16-float tuple), or None.
    """
    from ..igb_format.igb_reader import IGBReader
    from .sg_skeleton import extract_skeleton

    reader = IGBReader(filepath)
    reader.read()
    skin_skel = extract_skeleton(reader)
    if skin_skel is None:
        return None

    result = {}
    for bone in skin_skel.bones:
        if bone.inv_joint_matrix is not None:
            result[bone.name] = bone.inv_joint_matrix

    return result if result else None


def _extract_bind_pose(parsed_anims):
    """Extract bind-pose quaternion and translation per bone from animations.

    Looks for 'afakeanim' first (the canonical bind pose), then falls back to
    the first animation's frame 0 data.

    Args:
        parsed_anims: List of ParsedAnimation instances.

    Returns:
        Dict mapping bone_name -> (quat_wxyz_tuple, trans_xyz_tuple), or None.
    """
    # Prefer afakeanim (the canonical bind/reference pose)
    target_anim = None
    for anim in parsed_anims:
        if anim.name == "afakeanim":
            target_anim = anim
            break

    # Fallback: use first animation with tracks
    if target_anim is None:
        for anim in parsed_anims:
            if anim.tracks:
                target_anim = anim
                break

    if target_anim is None:
        return None

    bind_pose = {}
    for track in target_anim.tracks:
        if not track.bone_name or not track.keyframes:
            continue
        kf = track.keyframes[0]
        bind_pose[track.bone_name] = (kf.quaternion, kf.translation)

    return bind_pose if bind_pose else None


def _apply_transform(mesh_obj, transform):
    """Apply a row-major 4x4 transform to a Blender object."""
    import mathutils
    # Convert row-major to Blender column-major Matrix
    m = mathutils.Matrix((
        (transform[0], transform[4], transform[8], transform[12]),
        (transform[1], transform[5], transform[9], transform[13]),
        (transform[2], transform[6], transform[10], transform[14]),
        (transform[3], transform[7], transform[11], transform[15]),
    ))
    mesh_obj.matrix_world = m


class _SkinGeometryCollector:
    """Simple visitor to collect geometry from a skin scene graph."""

    def __init__(self, reader, profile):
        self.reader = reader
        self.profile = profile
        self.instances = []  # (attr_index, transform, state_dict)
        self.geom_attrs = {}  # attr_index -> IGBObject

        # Material state tracking
        self._material_obj = None
        self._texbind_obj = None
        self._bms_indices = None

    def visit_material_attr(self, attr, parent):
        self._material_obj = attr

    def visit_texture_bind_attr(self, attr, parent):
        self._texbind_obj = attr

    def visit_blend_matrix_select(self, attr, parent):
        """Track igBlendMatrixSelect for bone index remapping."""
        from .skinning import extract_bms_indices
        self._bms_indices = extract_bms_indices(self.reader, attr)

    def visit_geometry_attr(self, attr, transform, parent):
        # Get the parent node's name (igGeometry inherits igNamedObject, name at slot 2)
        node_name = ""
        if parent is not None:
            for slot, val, fi in parent._raw_fields:
                if slot == 2 and fi.short_name == b"String" and isinstance(val, (str, bytes)):
                    node_name = val.decode('utf-8', errors='replace') if isinstance(val, bytes) else val
                    break

        # Detect outline meshes: named with "_outline" suffix, or
        # has black material with no texture (front-face culling outline technique)
        is_outline = "_outline" in node_name.lower()

        state = {
            'material_obj': self._material_obj,
            'texbind_obj': self._texbind_obj,
            'bms_indices': self._bms_indices,
            'node_name': node_name,
            'is_outline': is_outline,
        }
        idx = attr.index
        self.geom_attrs[idx] = attr
        self.instances.append((idx, transform, state))


def resolve_skin_files(char_name, game_dir):
    """Resolve skin file paths for a character from herostat/npcstat.

    Args:
        char_name: Character animation file name (e.g., '03_wolverine').
        game_dir: Root game data directory.

    Returns:
        List of (variant_name, filepath) tuples, or empty list.
    """
    from ..mapmaker.game_database import get_character_db

    db = get_character_db(game_dir)
    if not db:
        return []

    # Find character by characteranims field
    char_info = None
    for info in db.values():
        if info.characteranims == char_name:
            char_info = info
            break

    if char_info is None:
        return []

    actors_dir = os.path.join(game_dir, "actors")
    skins = []

    # Default skin
    if char_info.skin:
        default_path = os.path.join(actors_dir, f"{char_info.skin}.igb")
        if os.path.exists(default_path):
            skins.append(("default", default_path))

    # Variant skins from extra_fields
    char_id = char_info.skin[:2] if len(char_info.skin) >= 2 else ""
    extra = getattr(char_info, 'extra_fields', {})
    for key, val in extra.items():
        if key.startswith("skin_") and key != "skin":
            variant_name = key[5:]  # strip "skin_"
            variant_code = f"{char_id}{str(val).zfill(2)}"
            variant_path = os.path.join(actors_dir, f"{variant_code}.igb")
            if os.path.exists(variant_path):
                skins.append((variant_name, variant_path))

    return skins
