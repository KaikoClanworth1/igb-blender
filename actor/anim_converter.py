"""Animation Converter — retarget any Blender animation to XML2/MUA IGB.

Non-destructive: samples world-space rotations from the source armature
and decomposes them into XML2-local Alchemy quaternions without modifying
the source rig.
"""

import logging
import math
import os

from mathutils import Quaternion, Vector, Matrix

from .rig_converter import (
    build_rename_map,
    get_skeleton_for_game,
    NATIVE_BONE_ROTATIONS,
    NATIVE_TRANSLATIONS,
    XML2_SKELETON,
)

_log = logging.getLogger(__name__)

# Cache for native world rotations (static data, computed once)
_native_world_cache = {}


# ============================================================================
# Native XML2 World Rotations
# ============================================================================

def compute_xml2_native_world_rotations(skeleton=None):
    """Compute world-space rotations for each XML2 bone in the native T-pose.

    Uses NATIVE_BONE_ROTATIONS (local rest quaternions, Alchemy convention)
    and the skeleton hierarchy.

    Returns:
        Dict[str, Quaternion]: bone_name -> world rotation (Blender convention)
    """
    if skeleton is None:
        skeleton = XML2_SKELETON

    cache_key = id(skeleton)
    if cache_key in _native_world_cache:
        return _native_world_cache[cache_key]

    world_rots = {}

    for name, idx, parent_idx, bm_idx, flags in skeleton:
        if not name:
            world_rots[""] = Quaternion((1, 0, 0, 0))
            continue

        native_q_xyzw = NATIVE_BONE_ROTATIONS.get(name)
        if native_q_xyzw is not None:
            # NATIVE_BONE_ROTATIONS: (x, y, z, w) in Alchemy convention
            # Conjugate to get Blender convention
            x, y, z, w = native_q_xyzw
            local_q = Quaternion((w, x, y, z)).conjugated()
        else:
            # Non-deforming bones (root, Bip01, Motion) — identity
            local_q = Quaternion((1, 0, 0, 0))

        parent_name = skeleton[parent_idx][0] if parent_idx >= 0 else ""
        parent_world = world_rots.get(parent_name, Quaternion((1, 0, 0, 0)))

        world_rots[name] = parent_world @ local_q

    _native_world_cache[cache_key] = world_rots
    return world_rots


# ============================================================================
# Scale Detection
# ============================================================================

def detect_scale_factor(armature_obj, bone_mapping):
    """Detect scale factor between source armature and XML2 skeleton.

    Compares pelvis-to-head distance in both skeletons.

    Returns:
        float: source_units * factor = xml2_units
    """
    src_pelvis = src_head = None
    for src_name, xml2_name in bone_mapping.items():
        if xml2_name == 'Bip01 Pelvis':
            src_pelvis = src_name
        elif xml2_name == 'Bip01 Head':
            src_head = src_name

    if not src_pelvis or not src_head:
        return 1.0

    pelvis_bone = armature_obj.data.bones.get(src_pelvis)
    head_bone = armature_obj.data.bones.get(src_head)
    if not pelvis_bone or not head_bone:
        return 1.0

    src_dist = (head_bone.head_local - pelvis_bone.head_local).length
    if src_dist < 0.001:
        return 1.0

    # XML2 native distance: sum translation lengths Spine(3) → Head(7)
    xml2_dist = 0.0
    for idx in (3, 4, 5, 6, 7):
        t = NATIVE_TRANSLATIONS.get(idx, [0, 0, 0])
        xml2_dist += Vector(t).length

    if xml2_dist < 0.001:
        return 1.0

    return xml2_dist / src_dist


# ============================================================================
# World-Space Sampling
# ============================================================================

def sample_world_transforms(context, armature_obj, action, bone_mapping,
                            sample_rate=30):
    """Sample world-space bone rotations and positions from source armature.

    Non-destructive: temporarily assigns the action, samples, restores.

    Args:
        context: Blender context
        armature_obj: Source armature object
        action: Blender Action to sample
        bone_mapping: dict[source_name -> xml2_name]
        sample_rate: Samples per second

    Returns:
        (tracks_data, root_positions, duration_sec)
        tracks_data: dict[xml2_name] -> list of (time_sec, world_quat)
        root_positions: list of (time_sec, world_position)
        duration_sec: float
    """
    import bpy

    # Save current state
    if not armature_obj.animation_data:
        armature_obj.animation_data_create()
    orig_action = armature_obj.animation_data.action
    orig_frame = context.scene.frame_current

    # Assign the action
    armature_obj.animation_data.action = action

    fps = context.scene.render.fps
    frame_start, frame_end = action.frame_range
    duration_sec = (frame_end - frame_start) / fps if fps > 0 else 0.0

    num_samples = max(2, int(duration_sec * sample_rate) + 1)

    tracks_data = {}   # xml2_name -> [(time_sec, world_quat)]
    root_positions = []  # [(time_sec, Vector)]

    # Find source bone for pelvis (root motion)
    pelvis_src = None
    for src_name, xml2_name in bone_mapping.items():
        if xml2_name == 'Bip01 Pelvis':
            pelvis_src = src_name
            break

    try:
        for si in range(num_samples):
            t = si / float(sample_rate) if sample_rate > 0 else 0.0
            t = min(t, duration_sec)
            frame = frame_start + t * fps

            context.scene.frame_set(int(round(frame)),
                                    subframe=frame - int(round(frame)))
            depsgraph = context.evaluated_depsgraph_get()
            eval_obj = armature_obj.evaluated_get(depsgraph)

            for src_name, xml2_name in bone_mapping.items():
                pose_bone = eval_obj.pose.bones.get(src_name)
                if pose_bone is None:
                    continue

                world_mat = eval_obj.matrix_world @ pose_bone.matrix
                world_rot = world_mat.to_quaternion()

                if xml2_name not in tracks_data:
                    tracks_data[xml2_name] = []
                tracks_data[xml2_name].append((t, world_rot))

            # Root position from pelvis
            if pelvis_src:
                pb = eval_obj.pose.bones.get(pelvis_src)
                if pb:
                    world_pos = (eval_obj.matrix_world @ pb.matrix).to_translation()
                    root_positions.append((t, world_pos.copy()))
    finally:
        # Restore original state
        armature_obj.animation_data.action = orig_action
        context.scene.frame_set(orig_frame)

    return tracks_data, root_positions, duration_sec


# ============================================================================
# Retargeting
# ============================================================================

def retarget_to_alchemy(tracks_data, root_positions, scale_factor,
                        skeleton=None):
    """Convert world-space rotations to Alchemy local quaternions.

    Processes bones in skeleton order, tracking animated parent world
    rotations for correct decomposition.

    Args:
        tracks_data: dict[xml2_name] -> [(time_sec, world_quat)]
        root_positions: [(time_sec, Vector)]
        scale_factor: source * factor = game units

    Returns:
        (tracks, motion_track) for IGBAnimationBuilder
    """
    if skeleton is None:
        skeleton = XML2_SKELETON

    native_world = compute_xml2_native_world_rotations(skeleton)

    # Build parent name lookup
    parent_map = {}
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        if parent_idx >= 0:
            parent_map[name] = skeleton[parent_idx][0]
        else:
            parent_map[name] = None

    # Determine number of samples from any track
    num_samples = 0
    sample_times = []
    for samples in tracks_data.values():
        if len(samples) > num_samples:
            num_samples = len(samples)
            sample_times = [s[0] for s in samples]
            break
    if num_samples < 2:
        sample_times = [0.0, 0.001]
        num_samples = 2

    tracks = []
    track_id = 0

    # Per-frame retargeted world rotations (for parent chain)
    # retargeted_world[frame_idx][bone_name] = Quaternion
    retargeted_worlds = [{} for _ in range(num_samples)]

    # Initialize root and Bip01 (non-deforming, always identity)
    for si in range(num_samples):
        retargeted_worlds[si][""] = Quaternion((1, 0, 0, 0))
        retargeted_worlds[si]["Bip01"] = Quaternion((1, 0, 0, 0))

    for name, idx, parent_idx, bm_idx, flags in skeleton:
        if bm_idx < 0:
            continue  # Skip root, Bip01, Motion (non-deforming)

        native_trans = NATIVE_TRANSLATIONS.get(idx, [0, 0, 0])
        parent_name = parent_map.get(name)

        if name in tracks_data:
            samples = tracks_data[name]
            keyframes = []

            for si in range(min(len(samples), num_samples)):
                time_sec, source_world_rot = samples[si]

                # Get parent's animated world rotation
                parent_world = retargeted_worlds[si].get(
                    parent_name, Quaternion((1, 0, 0, 0)))

                # Decompose: what local rotation produces this world rotation?
                local_rot = parent_world.inverted() @ source_world_rot

                # Convert to Alchemy convention (conjugate)
                alchemy_q = local_rot.conjugated()
                quat_xyzw = (alchemy_q.x, alchemy_q.y, alchemy_q.z, alchemy_q.w)

                keyframes.append((time_sec, quat_xyzw, tuple(native_trans)))

                # Store for children
                retargeted_worlds[si][name] = source_world_rot

        else:
            # No source data — use native rest rotation
            native_q_xyzw = NATIVE_BONE_ROTATIONS.get(name, (0, 0, 0, 1))
            x, y, z, w = native_q_xyzw
            native_local_blender = Quaternion((w, x, y, z)).conjugated()

            keyframes = []
            for si in range(num_samples):
                t = sample_times[si] if si < len(sample_times) else 0.0

                # Propagate world rotation for children
                parent_world = retargeted_worlds[si].get(
                    parent_name, Quaternion((1, 0, 0, 0)))
                retargeted_worlds[si][name] = parent_world @ native_local_blender

                keyframes.append((t, native_q_xyzw, tuple(native_trans)))

        rest_quat = keyframes[0][1]
        rest_trans = keyframes[0][2]

        tracks.append({
            'bone_name': name,
            'track_id': track_id,
            'rest_quat': rest_quat,
            'rest_trans': rest_trans,
            'keyframes': keyframes,
        })
        track_id += 1

    # Motion track (root translation from pelvis)
    # Format expected by _build_motion_track: dict with
    # 'quaternions', 'translations', 'timestamps_ns', 'duration_ns', 'offset_ns'
    motion_track = None
    if root_positions and len(root_positions) >= 2:
        rz90 = Quaternion((0, 0, 1), math.radians(90))

        quats = []
        trans = []
        times_ns = []
        for time_sec, pos in root_positions:
            scaled = pos * scale_factor
            game_pos = rz90 @ scaled
            quats.append((1.0, 0.0, 0.0, 0.0))  # identity (w,x,y,z)
            trans.append((game_pos.x, game_pos.y, game_pos.z))
            times_ns.append(int(time_sec * 1_000_000_000))

        duration_ns = times_ns[-1] - times_ns[0] if times_ns else 0
        motion_track = {
            'quaternions': quats,
            'translations': trans,
            'timestamps_ns': times_ns,
            'duration_ns': duration_ns,
            'offset_ns': 0,
        }

    return tracks, motion_track


# ============================================================================
# Main Entry Point
# ============================================================================

def convert_animation(context, armature_obj, action, output_path,
                      game='xml2', reference_path=None,
                      anim_name=None, scale_factor=None,
                      sample_rate=30):
    """Convert and export an animation from any rig to XML2/MUA IGB.

    Non-destructive: does not modify the source armature.

    Args:
        context: Blender context
        armature_obj: Source armature (any rig convention)
        action: Blender Action to export
        output_path: Output .igb file path
        game: 'xml2' or 'mua'
        reference_path: Path to any game animation IGB (for schema)
        anim_name: Name for the animation in the IGB
        scale_factor: Override scale (auto-detected if None)
        sample_rate: Samples per second (default 30)

    Returns:
        output_path on success

    Raises:
        RuntimeError: on failure
    """
    game_upper = game.upper()
    skeleton = get_skeleton_for_game(game_upper)

    # 1. Build bone mapping (non-destructive)
    bone_mapping = build_rename_map(armature_obj, target_game=game_upper)
    if not bone_mapping:
        raise RuntimeError(
            "No bones could be mapped to XML2 skeleton. "
            "Ensure the armature uses a recognized naming convention.")

    _log.info("Mapped %d source bones to XML2", len(bone_mapping))

    # 2. Auto-detect scale
    if scale_factor is None:
        scale_factor = detect_scale_factor(armature_obj, bone_mapping)
    _log.info("Scale factor: %.4f", scale_factor)

    # 3. Sample world-space transforms
    tracks_data, root_positions, duration_sec = sample_world_transforms(
        context, armature_obj, action, bone_mapping,
        sample_rate=sample_rate,
    )
    _log.info("Sampled %.2fs animation (%d tracks)", duration_sec,
              len(tracks_data))

    # 4. Retarget to Alchemy
    tracks, motion_track = retarget_to_alchemy(
        tracks_data, root_positions, scale_factor, skeleton=skeleton,
    )

    # 5. Build skeleton data
    skeleton_data = _build_skeleton_data(skeleton)

    # 6. Export via from-scratch builder
    from .igb_anim_builder import IGBAnimationBuilder

    if anim_name is None:
        anim_name = action.name

    builder = IGBAnimationBuilder(game.lower(), reference_path=reference_path)
    builder.build(
        skeleton_data=skeleton_data,
        animations=[{
            'name': anim_name,
            'duration_sec': duration_sec,
            'tracks': tracks,
            'motion_track': motion_track,
        }],
        output_path=output_path,
    )

    _log.info("Wrote %s (%d bytes)", output_path, os.path.getsize(output_path))
    return output_path


def _build_skeleton_data(skeleton):
    """Build skeleton_data dict for IGBAnimationBuilder from skeleton definition."""
    bones = []
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        trans = NATIVE_TRANSLATIONS.get(idx, [0, 0, 0])
        bones.append({
            'name': name,
            'parent': parent_idx,
            'flags': flags,
            'matrix': list(trans[:3]),
        })
    return {'name': '', 'bones': bones}
