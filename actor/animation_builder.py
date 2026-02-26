"""Build Blender Actions from ParsedAnimation data.

Creates animation Actions with quaternion rotation and location keyframes
for each bone track.

GENERAL ROTATION FORMULA:
Alchemy animation quaternions are ABSOLUTE local rotations in parent-bone
space. Alchemy uses the CONJUGATE quaternion convention vs Blender:

    desired_local_rot = conjugate(alchemy_anim_q)

Blender's pose.rotation_quaternion is a DELTA from the bone's rest pose:

    final_local_rot = rest_local_rot @ pose_q

Therefore the general formula is:

    pose_q = rest_q^{-1} @ conjugate(anim_q)

Where rest_q is the bone's actual rest-local quaternion read from the
armature data (NOT assumed to be conjugate(bind_q)).

This works regardless of how the armature was built (quaternion-oriented
bones, child-pointing bones, etc.). When rest_q == conjugate(bind_q), it
degrades to the old formula: pose_q = bind_q @ anim_q^{-1}.

For translations: Blender's pose.location is in the bone's rest-local
coordinate system, so we use the rest rotation inverse:

    pose_loc = rest_rot_inv @ (anim_trans - bind_trans)
"""

from typing import Dict, List, Optional, Tuple


def _compute_rest_local_data(armature_obj):
    """Precompute rest-local rotation data for each bone.

    Returns:
        Dict mapping bone_name -> (rest_rot_inv, rest_q):
        - rest_rot_inv: inverted 3x3 rotation matrix (for location conversion)
        - rest_q: rest-local Quaternion (for rotation conversion)
    """
    result = {}
    for bone in armature_obj.data.bones:
        if bone.parent:
            local_rest_mat = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            local_rest_mat = bone.matrix_local.copy()

        rest_rot_inv = local_rest_mat.to_3x3().inverted()
        rest_q = local_rest_mat.to_quaternion()
        result[bone.name] = (rest_rot_inv, rest_q)

    return result


def build_action(armature_obj, parsed_animation, bind_pose=None, fps=None,
                  bone_remap=None, target_bind_pose=None):
    """Create a Blender Action from a ParsedAnimation.

    Args:
        armature_obj: The Blender armature object.
        parsed_animation: ParsedAnimation instance.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz) from
                   afakeanim frame 0 of the SOURCE animation file. Used
                   as fallback for translation delta conversion.
        fps: Frames per second for time conversion. If None, uses the
             Blender scene's render FPS (respects fps_base).
        bone_remap: Optional dict mapping source bone names to target bone
                    names. Used for cross-game animation import (e.g.,
                    MUA -> XML2). If a source bone name is found in this
                    dict, the mapped name is used for armature lookup.
        target_bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz)
                          from the TARGET armature's original animation file.
                          When importing cross-character animations, this
                          keeps bone positions relative to the target
                          character's skeleton. Falls back to bind_pose if
                          not provided.

    Returns:
        The created bpy.types.Action, or None on failure.
    """
    import bpy
    from mathutils import Quaternion, Vector

    # Use scene FPS if not explicitly provided
    if fps is None:
        fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base

    action_name = parsed_animation.name or "Action"
    action = bpy.data.actions.new(name=action_name)

    # Ensure animation data exists
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()

    # Precompute bind-pose translation maps.
    # For cross-character animation, prefer target_bind_pose so bone
    # positions are relative to the TARGET character's skeleton.
    bind_trans_map = {}
    if bind_pose:
        for bone_name, (quat_wxyz, trans_xyz) in bind_pose.items():
            bind_trans_map[bone_name] = Vector(trans_xyz)

    target_trans_map = {}
    if target_bind_pose:
        for bone_name, (quat_wxyz, trans_xyz) in target_bind_pose.items():
            target_trans_map[bone_name] = Vector(trans_xyz)

    # Precompute rest-local data (quaternion + rotation inverse)
    rest_data = _compute_rest_local_data(armature_obj)

    track_count = 0
    seen_bones = set()

    for track in parsed_animation.tracks:
        if not track.bone_name:
            continue

        # Skip duplicate tracks for the same bone (some anims have them)
        if track.bone_name in seen_bones:
            continue
        seen_bones.add(track.bone_name)

        # Apply bone name remapping if provided (cross-game import)
        bone_name = track.bone_name
        if bone_remap and bone_name in bone_remap:
            bone_name = bone_remap[bone_name]

        # Find the pose bone
        pb = armature_obj.pose.bones.get(bone_name)
        if pb is None:
            import logging
            logging.getLogger("igb_anim").debug(
                "Animation '%s': bone '%s' not found in armature, skipping track",
                parsed_animation.name, bone_name)
            continue

        if not track.keyframes:
            continue

        # Get rest-local data for this bone (use remapped name for armature lookup)
        rest_info = rest_data.get(bone_name)
        rest_rot_inv = rest_info[0] if rest_info else None
        rest_q = rest_info[1] if rest_info else None

        # Get bind-pose translation for this bone.
        # For cross-character animation, prefer target_bind_pose so bone
        # translations are computed relative to the TARGET character's
        # skeleton, not the source character's.
        bind_trans = None
        if target_trans_map:
            bind_trans = target_trans_map.get(bone_name)
            if bind_trans is None:
                bind_trans = target_trans_map.get(track.bone_name)
        if bind_trans is None:
            bind_trans = bind_trans_map.get(track.bone_name)
            if bind_trans is None and bone_name != track.bone_name:
                bind_trans = bind_trans_map.get(bone_name)

        # Insert rotation keyframes using general formula:
        # pose_q = rest_q^{-1} @ conjugate(anim_q)
        _insert_quaternion_keyframes(action, track, fps, rest_q,
                                     bone_name_override=bone_name)

        # Insert location keyframes using bind-pose translation delta
        _insert_location_keyframes(action, track, fps, rest_rot_inv, bind_trans,
                                   bone_name_override=bone_name)

        track_count += 1

    # Store metadata
    action["igb_duration_ms"] = parsed_animation.duration_ms
    action["igb_track_count"] = track_count
    action["igb_anim_name"] = parsed_animation.name

    return action


def build_all_actions(armature_obj, animations, bind_pose=None, fps=None,
                      bone_remap=None, target_bind_pose=None):
    """Build Actions for all animations and return the list.

    Args:
        armature_obj: The Blender armature object.
        animations: List of ParsedAnimation.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz).
        fps: Frames per second. If None, uses scene render FPS.
        bone_remap: Optional dict mapping source bone names to target names.
        target_bind_pose: Dict from TARGET armature's original anim file.

    Returns:
        List of created bpy.types.Action objects.
    """
    actions = []
    for anim in animations:
        action = build_action(armature_obj, anim, bind_pose=bind_pose, fps=fps,
                              bone_remap=bone_remap,
                              target_bind_pose=target_bind_pose)
        if action:
            actions.append(action)
    return actions


def set_active_action(armature_obj, action):
    """Set the active action on an armature.

    Args:
        armature_obj: The Blender armature object.
        action: The bpy.types.Action to activate.
    """
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    armature_obj.animation_data.action = action


def _insert_quaternion_keyframes(action, track, fps, rest_q=None,
                                  bone_name_override=None):
    """Insert quaternion rotation keyframes for a track.

    Uses the general formula to convert Alchemy absolute local quaternions
    to Blender pose deltas:

        pose_q = rest_q^{-1} @ conjugate(anim_q)

    This works regardless of bone orientation. The final local rotation is
    always conjugate(anim_q), matching the Alchemy animation.

    Args:
        action: Blender Action to add keyframes to.
        track: Animation track with keyframes.
        fps: Frames per second for time conversion.
        rest_q: Bone's rest-local Quaternion from the armature.
        bone_name_override: If provided, use this as the bone name in the
                           data path instead of track.bone_name.
    """
    from mathutils import Quaternion

    bone_name = bone_name_override or track.bone_name
    data_path = f'pose.bones["{bone_name}"].rotation_quaternion'

    # Create fcurves for W, X, Y, Z components
    fcurves = []
    for idx in range(4):
        fc = action.fcurves.new(data_path=data_path, index=idx)
        fc.keyframe_points.add(len(track.keyframes))
        fcurves.append(fc)

    # Precompute rest_q inverse
    rest_q_inv = rest_q.inverted() if rest_q is not None else None

    prev_q = None
    for ki, kf in enumerate(track.keyframes):
        frame = kf.time_ms / (1000.0 / fps) if fps > 0 else 0

        # Alchemy quaternion (w, x, y, z) — already in Blender WXYZ order
        aq = Quaternion(kf.quaternion)

        # General formula: pose_q = rest_q^{-1} @ conjugate(anim_q)
        if rest_q_inv is not None:
            q = rest_q_inv @ aq.conjugated()
        else:
            q = aq

        # Ensure shortest-path interpolation: q and -q are the same rotation,
        # but if the sign flips between consecutive keyframes, Blender's
        # per-component linear interpolation passes through near-zero (identity),
        # causing visible jitter. Fix: negate q when dot(prev, q) < 0.
        if prev_q is not None and prev_q.dot(q) < 0:
            q = Quaternion((-q.w, -q.x, -q.y, -q.z))
        prev_q = q

        for idx in range(4):
            point = fcurves[idx].keyframe_points[ki]
            point.co = (frame, q[idx])
            point.interpolation = 'LINEAR'

    # Update fcurves
    for fc in fcurves:
        fc.update()


def _insert_location_keyframes(action, track, fps, rest_rot_inv=None,
                                bind_trans=None, bone_name_override=None):
    """Insert location keyframes for a track.

    Converts Alchemy translations to Blender pose.location deltas:
        delta_trans = alchemy_translation - bind_translation
        pose_loc = rest_rot_inv @ delta_trans

    The bind_translation is subtracted first to compute the delta from the
    bind pose (which is the rest position). This delta is then transformed
    from Alchemy parent-local space to Blender bone rest-local space.

    For the bind pose, delta_trans = (0,0,0), so pose_loc = (0,0,0).
    For animations with root motion, delta_trans is the additional offset.

    Args:
        bone_name_override: If provided, use this as the bone name in the
                           data path instead of track.bone_name.
    """
    from mathutils import Vector

    # Compute delta translations and check if any are meaningful
    has_meaningful_delta = False
    for kf in track.keyframes:
        at = Vector(kf.translation)
        if bind_trans is not None:
            delta = at - bind_trans
        else:
            delta = at
        if delta.length > 1e-6:
            has_meaningful_delta = True
            break

    if not has_meaningful_delta and track.is_constant:
        return

    bone_name = bone_name_override or track.bone_name
    data_path = f'pose.bones["{bone_name}"].location'

    fcurves = []
    for idx in range(3):
        fc = action.fcurves.new(data_path=data_path, index=idx)
        fc.keyframe_points.add(len(track.keyframes))
        fcurves.append(fc)

    for ki, kf in enumerate(track.keyframes):
        frame = kf.time_ms / (1000.0 / fps) if fps > 0 else 0

        # Alchemy translation in parent bone space
        at = Vector(kf.translation)

        # Compute delta from bind pose
        if bind_trans is not None:
            delta = at - bind_trans
        else:
            delta = at

        # Convert delta to bone rest-local space
        if rest_rot_inv is not None:
            t = rest_rot_inv @ delta
        else:
            t = delta

        for idx in range(3):
            point = fcurves[idx].keyframe_points[ki]
            point.co = (frame, t[idx])
            point.interpolation = 'LINEAR'

    for fc in fcurves:
        fc.update()
