"""Export Blender Actions as IGB animation files.

Uses a template-based approach: reads an existing animation IGB file,
patches the keyframe data in its igTransformSequence1_5 objects, and
writes the result. This preserves all metadata, bindings, and structure.

REVERSE ROTATION FORMULA:
    Blender pose_q is a delta from rest: final_local = rest_q @ pose_q
    Alchemy anim_q is absolute local (conjugate convention):
        desired_local = conjugate(anim_q)

    Given pose_q = rest_q^{-1} @ conjugate(anim_q):
        rest_q @ pose_q = conjugate(anim_q)
        anim_q = conjugate(rest_q @ pose_q)

REVERSE TRANSLATION FORMULA:
    Given pose_loc = rest_rot_inv @ (anim_trans - bind_trans):
        anim_trans = bind_trans + rest_rot @ pose_loc
"""

import struct
import os
import logging

_log = logging.getLogger("igb_anim_export")


def export_animations(context, filepath, operator=None):
    """Export animations from the active armature to an IGB file.

    Uses the actor's original animation IGB as template.

    Args:
        context: Blender context
        filepath: output .igb file path
        operator: the export operator (for error reporting)

    Returns:
        {'FINISHED'} or {'CANCELLED'}
    """
    import bpy

    props = context.scene.igb_actor
    armature_obj = bpy.data.objects.get(props.active_armature)
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        if operator:
            operator.report({'ERROR'}, "No active armature found")
        return {'CANCELLED'}

    # Get template file path
    template_path = armature_obj.get("igb_anim_file", "")
    if not template_path or not os.path.exists(template_path):
        if operator:
            operator.report({'ERROR'},
                            f"Template animation file not found: {template_path}")
        return {'CANCELLED'}

    # Read template
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_writer import from_reader
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    reader = IGBReader(template_path)
    reader.read()
    writer = from_reader(reader)
    endian = reader.header.endian

    # Collect all Actions to export
    actions_to_export = []
    for item in props.animations:
        action = bpy.data.actions.get(item.action_name)
        if action is not None:
            actions_to_export.append(action)

    if not actions_to_export:
        if operator:
            operator.report({'WARNING'}, "No animations to export")
        return {'CANCELLED'}

    # Precompute rest-local data for reverse conversion
    rest_data = _compute_rest_local_data(armature_obj)

    # Extract bind_pose from the template
    from .sg_skeleton import extract_skeleton
    from .sg_animation import extract_animations
    from .actor_import import _extract_bind_pose

    skeleton = extract_skeleton(reader)
    parsed_anims = extract_animations(reader, skeleton) if skeleton else []
    bind_pose = _extract_bind_pose(parsed_anims) if parsed_anims else None

    bind_trans_map = {}
    if bind_pose:
        from mathutils import Vector
        for bone_name, (quat_wxyz, trans_xyz) in bind_pose.items():
            bind_trans_map[bone_name] = Vector(trans_xyz)

    # Build a map: animation_name -> igAnimation object index in reader
    anim_obj_map = _build_anim_object_map(reader)

    # For each action, find the matching template animation and patch it
    patched_count = 0
    for action in actions_to_export:
        anim_name = action.get("igb_anim_name", action.name)

        # Find matching template animation
        anim_info = anim_obj_map.get(anim_name)
        if anim_info is None:
            _log.debug("No template animation '%s' found, skipping", anim_name)
            continue

        fps = context.scene.render.fps
        success = _patch_animation(
            reader, writer, anim_info, action, armature_obj,
            rest_data, bind_trans_map, endian, fps
        )
        if success:
            patched_count += 1

    if patched_count == 0:
        if operator:
            operator.report({'WARNING'},
                            "No animations matched the template. "
                            "Animation names must match the original file.")
        return {'CANCELLED'}

    # Write output
    writer.write(filepath)

    if operator:
        operator.report({'INFO'},
                        f"Exported {patched_count} animation(s) to "
                        f"{os.path.basename(filepath)}")
    return {'FINISHED'}


def _compute_rest_local_data(armature_obj):
    """Precompute rest-local rotation data for reverse conversion.

    Returns:
        Dict mapping bone_name -> (rest_rot_3x3, rest_q):
        - rest_rot_3x3: 3x3 rotation matrix (for location reverse)
        - rest_q: rest-local Quaternion (for rotation reverse)
    """
    from mathutils import Matrix
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


def _build_anim_object_map(reader):
    """Build a map from animation name -> dict of object references.

    Returns dict: anim_name -> {
        'anim_obj_index': int,
        'tracks': [(track_obj_index, source_obj_index, bone_name), ...],
    }
    """
    from ..igb_format.igb_objects import IGBObject

    result = {}

    for i, obj in enumerate(reader.objects):
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igAnimation"):
            continue

        name = ""
        track_list_ref = None

        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"String":
                name = val if isinstance(val, str) else ""
            elif fi.short_name == b"ObjectRef" and val != -1:
                ref = reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    if (ref.is_type(b"igAnimationTrackList") or
                            (slot == 5 and ref.is_type(b"igObjectList"))):
                        track_list_ref = val

        if not name or track_list_ref is None:
            continue

        # Parse tracks to find igTransformSequence1_5 sources
        tracks = []
        tl = reader.resolve_ref(track_list_ref)
        if isinstance(tl, IGBObject):
            track_objs = reader.resolve_object_list(tl)
            for ti, track_obj in enumerate(track_objs):
                if not isinstance(track_obj, IGBObject):
                    continue
                bone_name = ""
                source_ref = None
                for slot, val, fi in track_obj._raw_fields:
                    if fi.short_name == b"String":
                        bone_name = val if isinstance(val, str) else ""
                    elif fi.short_name == b"ObjectRef" and val != -1 and slot == 3:
                        source_ref = val

                if source_ref is not None:
                    src = reader.resolve_ref(source_ref)
                    if isinstance(src, IGBObject):
                        if (src.is_type(b"igTransformSequence1_5") or
                                src.is_type(b"igTransformSequence")):
                            tracks.append((track_obj.index, src.index, bone_name))

        result[name] = {
            'anim_obj_index': i,
            'tracks': tracks,
        }

    return result


def _patch_animation(reader, writer, anim_info, action, armature_obj,
                     rest_data, bind_trans_map, endian, fps):
    """Patch a single animation in the writer with data from a Blender Action.

    Returns True on success.
    """
    from mathutils import Quaternion, Vector, Matrix
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    tracks = anim_info['tracks']
    if not tracks:
        return False

    patched = 0
    for track_obj_idx, source_obj_idx, bone_name in tracks:
        if not bone_name:
            continue

        # Find the F-curves for this bone in the action
        quat_fcurves = _get_fcurves(action, bone_name, 'rotation_quaternion', 4)
        loc_fcurves = _get_fcurves(action, bone_name, 'location', 3)

        if not quat_fcurves and not loc_fcurves:
            continue

        # Get the igTransformSequence1_5 object from reader
        seq_obj = reader.objects[source_obj_idx]
        if not isinstance(seq_obj, IGBObject):
            continue

        # Find the memory block references for xlate, quat, time lists
        xlate_list_ref = None
        quat_list_ref = None
        time_list_ref = None

        for slot, val, fi in seq_obj._raw_fields:
            if fi.short_name == b"ObjectRef" and val != -1:
                if slot == 2:
                    xlate_list_ref = val
                elif slot == 3:
                    quat_list_ref = val
                elif slot == 11:
                    time_list_ref = val

        # Get rest data for this bone
        rest_info = rest_data.get(bone_name)
        rest_rot = rest_info[0] if rest_info else None
        rest_q = rest_info[1] if rest_info else None
        bind_trans = bind_trans_map.get(bone_name)

        # Sample keyframes from F-curves
        keyframes = _sample_fcurve_keyframes(
            action, bone_name, quat_fcurves, loc_fcurves, fps
        )

        if not keyframes:
            continue

        # Convert keyframes to Alchemy format
        alchemy_keyframes = _convert_keyframes_to_alchemy(
            keyframes, rest_q, rest_rot, bind_trans
        )

        # Pack and patch the memory blocks
        _patch_transform_sequence(
            reader, writer, quat_list_ref, xlate_list_ref, time_list_ref,
            alchemy_keyframes, endian
        )
        patched += 1

    return patched > 0


def _get_fcurves(action, bone_name, prop_name, count):
    """Get F-curves for a bone property from an Action.

    Returns list of fcurves [idx0, idx1, ...] or empty list.
    """
    data_path = f'pose.bones["{bone_name}"].{prop_name}'
    result = [None] * count
    found = False
    for fc in action.fcurves:
        if fc.data_path == data_path and 0 <= fc.array_index < count:
            result[fc.array_index] = fc
            found = True
    return result if found else []


def _sample_fcurve_keyframes(action, bone_name, quat_fcurves, loc_fcurves, fps):
    """Sample keyframes from F-curves.

    Returns list of (time_ms, quat_wxyz, trans_xyz) tuples.
    """
    # Collect all unique frame numbers from all fcurves
    frames = set()
    for fcs in (quat_fcurves, loc_fcurves):
        for fc in fcs:
            if fc is not None:
                for kfp in fc.keyframe_points:
                    frames.add(kfp.co[0])

    if not frames:
        return []

    frames = sorted(frames)
    keyframes = []

    for frame in frames:
        time_ms = frame * (1000.0 / fps) if fps > 0 else 0.0

        # Sample quaternion
        if quat_fcurves and any(fc is not None for fc in quat_fcurves):
            w = quat_fcurves[0].evaluate(frame) if quat_fcurves[0] else 1.0
            x = quat_fcurves[1].evaluate(frame) if quat_fcurves[1] else 0.0
            y = quat_fcurves[2].evaluate(frame) if quat_fcurves[2] else 0.0
            z = quat_fcurves[3].evaluate(frame) if quat_fcurves[3] else 0.0
            quat = (w, x, y, z)
        else:
            quat = (1.0, 0.0, 0.0, 0.0)

        # Sample location
        if loc_fcurves and any(fc is not None for fc in loc_fcurves):
            lx = loc_fcurves[0].evaluate(frame) if loc_fcurves[0] else 0.0
            ly = loc_fcurves[1].evaluate(frame) if loc_fcurves[1] else 0.0
            lz = loc_fcurves[2].evaluate(frame) if loc_fcurves[2] else 0.0
            loc = (lx, ly, lz)
        else:
            loc = (0.0, 0.0, 0.0)

        keyframes.append((time_ms, quat, loc))

    return keyframes


def _convert_keyframes_to_alchemy(keyframes, rest_q, rest_rot, bind_trans):
    """Convert Blender pose keyframes to Alchemy format.

    Reverses:
        pose_q = rest_q^{-1} @ conjugate(anim_q)
        → anim_q = conjugate(rest_q @ pose_q)

        pose_loc = rest_rot_inv @ (anim_trans - bind_trans)
        → anim_trans = bind_trans + rest_rot @ pose_loc

    Returns list of (time_ns, quat_xyzw, trans_xyz) in Alchemy convention.
    """
    from mathutils import Quaternion, Vector

    result = []
    for time_ms, pose_q_wxyz, pose_loc in keyframes:
        time_ns = int(time_ms * 1_000_000)

        # Reverse rotation
        pq = Quaternion(pose_q_wxyz)
        if rest_q is not None:
            alchemy_q = (rest_q @ pq).conjugated()
        else:
            alchemy_q = pq.conjugated()

        # Convert Blender WXYZ -> Alchemy XYZW
        quat_xyzw = (alchemy_q.x, alchemy_q.y, alchemy_q.z, alchemy_q.w)

        # Reverse translation
        pl = Vector(pose_loc)
        if rest_rot is not None and bind_trans is not None:
            anim_trans = bind_trans + rest_rot @ pl
            trans = (anim_trans.x, anim_trans.y, anim_trans.z)
        elif bind_trans is not None:
            anim_trans = bind_trans + pl
            trans = (anim_trans.x, anim_trans.y, anim_trans.z)
        else:
            trans = pose_loc

        result.append((time_ns, quat_xyzw, trans))

    return result


def _patch_transform_sequence(reader, writer, quat_list_ref, xlate_list_ref,
                              time_list_ref, alchemy_keyframes, endian):
    """Patch igTransformSequence1_5 memory blocks with new keyframe data.

    Each list (quat, xlate, time) is stored as:
        igQuaternionfList / igVec3fList / igLongList
            slot: Int (count)
            slot: MemoryRef (raw data)

    We patch the MemoryRef data with new values and update the count.
    """
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock
    from ..igb_format.igb_writer import MemoryBlockDef

    num_keys = len(alchemy_keyframes)

    # Pack quaternion data (XYZW, 4 floats per keyframe = 16 bytes)
    if quat_list_ref is not None:
        quat_list_obj = reader.resolve_ref(quat_list_ref)
        if isinstance(quat_list_obj, IGBObject):
            _patch_data_list(reader, writer, quat_list_obj, num_keys,
                             alchemy_keyframes,
                             lambda kf: struct.pack(endian + "ffff",
                                                    kf[1][0], kf[1][1],
                                                    kf[1][2], kf[1][3]),
                             16, endian)

    # Pack translation data (XYZ, 3 floats per keyframe = 12 bytes)
    if xlate_list_ref is not None:
        xlate_list_obj = reader.resolve_ref(xlate_list_ref)
        if isinstance(xlate_list_obj, IGBObject):
            _patch_data_list(reader, writer, xlate_list_obj, num_keys,
                             alchemy_keyframes,
                             lambda kf: struct.pack(endian + "fff",
                                                    kf[2][0], kf[2][1],
                                                    kf[2][2]),
                             12, endian)

    # Pack time data (nanoseconds, int64 per keyframe = 8 bytes)
    if time_list_ref is not None:
        time_list_obj = reader.resolve_ref(time_list_ref)
        if isinstance(time_list_obj, IGBObject):
            _patch_data_list(reader, writer, time_list_obj, num_keys,
                             alchemy_keyframes,
                             lambda kf: struct.pack(endian + "q", kf[0]),
                             8, endian)


def _patch_data_list(reader, writer, list_obj, num_keys, keyframes,
                     pack_func, elem_size, endian):
    """Patch a data list object (igVec3fList, igQuaternionfList, igLongList).

    Updates the count field and replaces the memory block data.
    """
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock
    from ..igb_format.igb_writer import MemoryBlockDef, ObjectDef

    mem_ref_val = None
    for slot, val, fi in list_obj._raw_fields:
        if fi.short_name == b"MemoryRef" and val != -1:
            mem_ref_val = val

    if mem_ref_val is None:
        return

    # Update count in the writer's ObjectDef
    writer_obj = writer.objects[list_obj.index]
    if isinstance(writer_obj, ObjectDef):
        for i, (slot, val, fd) in enumerate(writer_obj.raw_fields):
            if fd.short_name == b"Int":
                writer_obj.raw_fields[i] = (slot, num_keys, fd)
                break
        # Clear raw_bytes so writer re-serializes from patched fields
        writer_obj.raw_bytes = None

    # Pack new data
    new_data = b"".join(pack_func(kf) for kf in keyframes)

    # Replace memory block
    mem_idx = mem_ref_val
    # Resolve through ref_info to get the actual memory block index
    if mem_idx < len(reader.ref_info) and not reader.ref_info[mem_idx]['is_object']:
        writer.objects[mem_idx] = MemoryBlockDef(new_data)
