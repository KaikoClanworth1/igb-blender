"""VMC4B ↔ XML2/MUA skeleton compatibility bridge.

Provides operators to:
1. Auto-configure VMC4B bone bindings for XML2/MUA armatures
2. Enable orientation correction so VMC4B works with non-Unity bone rolls
3. Export Actions as from-scratch IGB animation files

The core problem: XML2 armatures use 3ds Max Biped bone orientations (from
inv_joint matrices), which differ from the Unity Humanoid orientations that
VMC4B was designed for.  VMC4B's conversion formula uses the bone's rest
orientation (accumulated ``parent_quaternion``) to transform VMC rotations.
With XML2 orientations, the conversion produces wrong results ("left is down,
right is up").

Solution: We patched VMC4B to support ``parent_quat_overrides``.  When the
target armature has ``vmc_use_standard_orientations`` set, VMC4B recomputes
each bone's ``parent_quaternion`` using roll=0 (standard orientation),
effectively ignoring the actual bone rolls.  This makes the conversion work
correctly for any armature regardless of its native bone orientations.
"""

import os
import logging

_log = logging.getLogger("igb_vmc_bridge")


# ============================================================================
# VMC Humanoid → XML2 Bone Mapping
# ============================================================================

VMC_TO_XML2 = {
    # Torso
    "Hips":           "Bip01 Pelvis",
    "Spine":          "Bip01 Spine",
    "Chest":          "Bip01 Spine1",
    "UpperChest":     "Bip01 Spine2",
    "Neck":           "Bip01 Neck",
    "Head":           "Bip01 Head",

    # Left arm
    "LeftShoulder":   "Bip01 L Clavicle",
    "LeftUpperArm":   "Bip01 L UpperArm",
    "LeftLowerArm":   "Bip01 L Forearm",
    "LeftHand":       "Bip01 L Hand",

    # Left fingers
    "LeftThumbProximal":      "Bip01 L Finger0",
    "LeftThumbIntermediate":  "Bip01 L Finger01",
    "LeftMiddleProximal":     "Bip01 L Finger1",
    "LeftMiddleIntermediate": "Bip01 L Finger11",

    # Right arm
    "RightShoulder":  "Bip01 R Clavicle",
    "RightUpperArm":  "Bip01 R UpperArm",
    "RightLowerArm":  "Bip01 R Forearm",
    "RightHand":      "Bip01 R Hand",

    # Right fingers
    "RightThumbProximal":      "Bip01 R Finger0",
    "RightThumbIntermediate":  "Bip01 R Finger01",
    "RightMiddleProximal":     "Bip01 R Finger1",
    "RightMiddleIntermediate": "Bip01 R Finger11",

    # Left leg
    "LeftUpperLeg":   "Bip01 L Thigh",
    "LeftLowerLeg":   "Bip01 L Calf",
    "LeftFoot":       "Bip01 L Foot",
    "LeftToes":       "Bip01 L Toe0",

    # Right leg
    "RightUpperLeg":  "Bip01 R Thigh",
    "RightLowerLeg":  "Bip01 R Calf",
    "RightFoot":      "Bip01 R Foot",
    "RightToes":      "Bip01 R Toe0",
}

# Reverse mapping: XML2 bone name → VMC humanoid name
_XML2_TO_VMC = {v: k for k, v in VMC_TO_XML2.items()}


# ============================================================================
# VMC4B Setup
# ============================================================================

def setup_vmc4b_bindings(armature_obj):
    """Configure VMC4B to work with an XML2/MUA armature.

    1. Points VMC4B target at this armature
    2. Binds VMC humanoid bone names to XML2 Bip01 bone names
    3. Sets the ``vmc_use_standard_orientations`` flag so VMC4B uses
       roll-corrected parent_quaternion values for conversion

    After calling this, the user just needs to press Connect in VMC4B's
    panel to start receiving motion capture data.

    Args:
        armature_obj: Blender armature object with XML2/MUA bone names.

    Returns:
        (bound_count, skipped_names)
    """
    import bpy

    scene = bpy.context.scene
    bone_names = {b.name for b in armature_obj.data.bones}

    # 1. Point VMC4B at this armature
    if hasattr(scene, 'vmc4b_target_armature'):
        scene.vmc4b_target_armature = armature_obj.name

    # 2. Bind VMC humanoid bones to XML2 bones
    bound = 0
    skipped = []

    for vmc_name, xml2_name in VMC_TO_XML2.items():
        prop_name = f"vmc4b_bones_{vmc_name}"
        if not hasattr(scene, prop_name):
            continue

        if xml2_name in bone_names:
            setattr(scene, prop_name, xml2_name)
            bound += 1
        else:
            setattr(scene, prop_name, "")
            skipped.append(vmc_name)

    # 3. Enable orientation correction for non-standard bone rolls
    armature_obj["vmc_use_standard_orientations"] = True

    # 4. Set all pose bones to quaternion rotation mode
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'

    _log.info("VMC4B configured for '%s': %d bones bound, %d skipped",
              armature_obj.name, bound, len(skipped))
    return bound, skipped


def cleanup_vmc_setup(armature_obj):
    """Remove VMC4B configuration from the armature.

    Returns:
        True if cleanup was performed.
    """
    had_flag = "vmc_use_standard_orientations" in armature_obj
    if had_flag:
        del armature_obj["vmc_use_standard_orientations"]
    return had_flag


# ============================================================================
# Export Action as IGB
# ============================================================================

def export_action_as_igb(armature_obj, action, output_path, game='xml2',
                         reference_path=None, anim_name=None, fps=30.0):
    """Export a single Blender Action as a from-scratch IGB animation file.

    Reads the armature's skeleton data (bone hierarchy, translations) and
    samples the Action's f-curves to build a complete animation IGB.

    Args:
        armature_obj: Blender armature object with XML2/MUA skeleton.
        action: The Blender Action to export.
        output_path: File path for output .igb file.
        game: 'xml2' or 'mua'.
        reference_path: Path to a reference IGB for schema extraction.
                       If None, uses the armature's igb_anim_file property.
        anim_name: Name for the animation. If None, uses the Action name.
        fps: Frames per second for sampling.

    Returns:
        Path to the written file, or None on failure.
    """
    from .igb_anim_builder import IGBAnimationBuilder

    if reference_path is None:
        reference_path = armature_obj.get("igb_anim_file", "")
    if not reference_path or not os.path.exists(reference_path):
        _log.error("No reference IGB file available for schema extraction")
        return None

    if anim_name is None:
        anim_name = action.get("igb_anim_name", action.name)

    # ---- Extract skeleton data from the armature ----
    skeleton_data = _extract_skeleton_from_armature(armature_obj, game)
    if skeleton_data is None:
        _log.error("Failed to extract skeleton data from armature")
        return None

    # ---- Sample the Action to get track keyframes ----
    tracks, motion_track = _sample_action_tracks(
        armature_obj, action, skeleton_data, fps
    )

    if not tracks:
        _log.error("No animation tracks could be sampled from action '%s'",
                    action.name)
        return None

    # ---- Compute duration ----
    frame_range = action.frame_range
    duration_sec = (frame_range[1] - frame_range[0]) / fps if fps > 0 else 0.033

    # ---- Build IGB ----
    builder = IGBAnimationBuilder(game, reference_path=reference_path)
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

    return output_path


# ============================================================================
# Internal helpers
# ============================================================================

def _extract_skeleton_from_armature(armature_obj, game='xml2'):
    """Extract skeleton data dict from a Blender armature."""
    import json
    from .rig_converter import XML2_SKELETON, NATIVE_TRANSLATIONS

    skel_name = armature_obj.get("igb_skeleton_name",
                                  armature_obj.name + "_skel")

    stored_translations = armature_obj.get("igb_skin_bone_translations")
    if stored_translations and isinstance(stored_translations, str):
        try:
            trans_data = json.loads(stored_translations)
        except json.JSONDecodeError:
            trans_data = None
    else:
        trans_data = None

    bone_list = []
    xml2_names = [entry[0] for entry in XML2_SKELETON]
    armature_bone_names = {b.name for b in armature_obj.data.bones}
    xml2_match_count = sum(1 for n in xml2_names if n in armature_bone_names)

    if xml2_match_count >= 10:
        for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
            trans = [0.0, 0.0, 0.0]
            if trans_data and name in trans_data:
                trans = trans_data[name][:3]
            elif idx in NATIVE_TRANSLATIONS:
                trans = NATIVE_TRANSLATIONS[idx][:3]
            bone_list.append({
                'name': name,
                'parent': parent_idx,
                'flags': flags,
                'matrix': trans,
            })
    else:
        ordered_bones = []
        _walk_bones(armature_obj.data.bones, ordered_bones)
        bone_name_to_idx = {b.name: i for i, b in enumerate(ordered_bones)}
        for i, bone in enumerate(ordered_bones):
            parent_idx = bone_name_to_idx.get(
                bone.parent.name, -1) if bone.parent else -1
            trans = [0.0, 0.0, 0.0]
            if trans_data and bone.name in trans_data:
                trans = trans_data[bone.name][:3]
            elif bone.parent:
                delta = bone.head_local - bone.parent.head_local
                trans = [delta.x, delta.y, delta.z]
            flags = 0x02 if i > 0 else 0x40
            bone_list.append({
                'name': bone.name,
                'parent': parent_idx,
                'flags': flags,
                'matrix': trans,
            })

    if not bone_list:
        return None

    return {'name': skel_name, 'bones': bone_list}


def _walk_bones(bones, result):
    """Walk bone hierarchy depth-first."""
    for bone in bones:
        if bone.parent is None:
            _walk_bone_recursive(bone, result)


def _walk_bone_recursive(bone, result):
    result.append(bone)
    for child in bone.children:
        _walk_bone_recursive(child, result)


def _sample_action_tracks(armature_obj, action, skeleton_data, fps):
    """Sample a Blender Action's f-curves into track keyframes for IGB export.

    Converts Blender pose quaternions and locations to Alchemy format:
        anim_q = conjugate(rest_q @ pose_q)
        anim_trans = bind_trans + rest_rot @ pose_loc
    """
    from mathutils import Quaternion, Vector

    rest_data = _compute_rest_local_data(armature_obj)

    bind_trans_map = {}
    bones = skeleton_data['bones']
    for bone in bones:
        if bone['name']:
            bind_trans_map[bone['name']] = Vector(bone['matrix'][:3])

    bones_with_data = set()
    for fc in action.fcurves:
        if fc.data_path.startswith('pose.bones["'):
            end = fc.data_path.index('"]', 12)
            bone_name = fc.data_path[12:end]
            bones_with_data.add(bone_name)

    frame_range = action.frame_range
    frame_start = frame_range[0]
    frame_end = frame_range[1]
    duration_sec = (frame_end - frame_start) / fps if fps > 0 else 0.033

    sample_rate = 30
    num_samples = max(2, int(duration_sec * sample_rate) + 2)

    bone_name_order = [b['name'] for b in bones if b['name'] in bones_with_data]
    for name in sorted(bones_with_data):
        if name not in bone_name_order and name != 'Motion':
            bone_name_order.append(name)

    tracks = []
    motion_track = None

    for track_id, bone_name in enumerate(bone_name_order):
        if bone_name == 'Motion':
            continue

        quat_fcurves = _get_fcurves(action, bone_name, 'rotation_quaternion', 4)
        loc_fcurves = _get_fcurves(action, bone_name, 'location', 3)

        if not quat_fcurves and not loc_fcurves:
            continue

        rest_info = rest_data.get(bone_name)
        rest_rot = rest_info[0] if rest_info else None
        rest_q = rest_info[1] if rest_info else None
        bind_trans = bind_trans_map.get(bone_name)

        keyframes = []
        rest_quat_xyzw = (0.0, 0.0, 0.0, 1.0)
        rest_trans_xyz = (0.0, 0.0, 0.0)

        for si in range(num_samples):
            time_sec = min(si / float(sample_rate), duration_sec)
            frame = frame_start + time_sec * fps

            if quat_fcurves and any(fc is not None for fc in quat_fcurves):
                w = quat_fcurves[0].evaluate(frame) if quat_fcurves[0] else 1.0
                x = quat_fcurves[1].evaluate(frame) if quat_fcurves[1] else 0.0
                y = quat_fcurves[2].evaluate(frame) if quat_fcurves[2] else 0.0
                z = quat_fcurves[3].evaluate(frame) if quat_fcurves[3] else 0.0
                pose_q = Quaternion((w, x, y, z))
            else:
                pose_q = Quaternion((1.0, 0.0, 0.0, 0.0))

            if loc_fcurves and any(fc is not None for fc in loc_fcurves):
                lx = loc_fcurves[0].evaluate(frame) if loc_fcurves[0] else 0.0
                ly = loc_fcurves[1].evaluate(frame) if loc_fcurves[1] else 0.0
                lz = loc_fcurves[2].evaluate(frame) if loc_fcurves[2] else 0.0
                pose_loc = Vector((lx, ly, lz))
            else:
                pose_loc = Vector((0.0, 0.0, 0.0))

            if rest_q is not None:
                alchemy_q = (rest_q @ pose_q).conjugated()
            else:
                alchemy_q = pose_q.conjugated()

            quat_xyzw = (alchemy_q.x, alchemy_q.y, alchemy_q.z, alchemy_q.w)

            if rest_rot is not None and bind_trans is not None:
                anim_trans = bind_trans + rest_rot @ pose_loc
                trans = (anim_trans.x, anim_trans.y, anim_trans.z)
            elif bind_trans is not None:
                anim_trans = bind_trans + pose_loc
                trans = (anim_trans.x, anim_trans.y, anim_trans.z)
            else:
                trans = tuple(pose_loc)

            keyframes.append((time_sec, quat_xyzw, trans))

        if keyframes:
            rest_quat_xyzw = keyframes[0][1]
            rest_trans_xyz = keyframes[0][2]

        tracks.append({
            'bone_name': bone_name,
            'track_id': track_id,
            'rest_quat': rest_quat_xyzw,
            'rest_trans': rest_trans_xyz,
            'keyframes': keyframes,
        })

    return tracks, motion_track


def _compute_rest_local_data(armature_obj):
    """Precompute rest-local rotation data for reverse conversion."""
    result = {}
    for bone in armature_obj.data.bones:
        if bone.parent:
            local_rest_mat = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            local_rest_mat = bone.matrix_local.copy()
        rest_rot = local_rest_mat.to_3x3()
        rest_q = local_rest_mat.to_quaternion()
        result[bone.name] = (rest_rot, rest_q)
    return result


def _get_fcurves(action, bone_name, prop_name, count):
    """Get F-curves for a bone property from an Action."""
    data_path = f'pose.bones["{bone_name}"].{prop_name}'
    result = [None] * count
    found = False
    for fc in action.fcurves:
        if fc.data_path == data_path and 0 <= fc.array_index < count:
            result[fc.array_index] = fc
            found = True
    return result if found else []
