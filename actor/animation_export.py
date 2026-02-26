"""Export Blender Actions as IGB animation files.

Uses a template-based approach: reads an existing animation IGB file,
patches the keyframe data, and writes the result.  This preserves all
metadata, bindings, and structure.

EXPORT CAPABILITIES:
- Root motion tracks (igTransformSequence1_5): patched in place.
- Bone animation tracks (igEnbayaTransformSource): recompressed via the
  enbaya encoder (actor/enbaya_encoder.py).  All bone tracks sharing one
  igEnbayaAnimationSource are collected, converted to Alchemy format,
  compressed, and the blob is replaced.

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


def _update_entry_mem_size(reader, writer, ref_index, new_size):
    """Update the _memSize field in the igMemoryDirEntry for a replaced block.

    When a MemoryBlockDef is replaced with different-sized data, the
    directory entry's _memSize field must be updated to match.  Without
    this, the game reads the wrong size and crashes.
    """
    entry_idx = writer.index_map[ref_index]
    # Find the position of slot 7 (_memSize) in the entry's field list
    _ent_type, fields = reader.entries[entry_idx]
    for pos, field_tuple in enumerate(fields):
        if field_tuple[0] == 7:  # slot 7 = _memSize
            writer.entries[entry_idx].field_values[pos] = new_size
            break
    # Also update ref_info for internal consistency
    writer.ref_info[ref_index]['mem_size'] = new_size


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

    # Safety: warn if output would overwrite the template
    if os.path.normcase(os.path.abspath(filepath)) == os.path.normcase(os.path.abspath(template_path)):
        if operator:
            operator.report({'ERROR'},
                            "Output path is the same as the template file. "
                            "Choose a different filename to avoid overwriting "
                            "the original.")
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

    # Count how many template animations have patchable tracks
    patchable_uncompressed = [
        name for name, info in anim_obj_map.items() if info['tracks']
    ]
    patchable_enbaya = [
        name for name, info in anim_obj_map.items()
        if info['enbaya_track_details']
    ]

    _log.info("Template has %d animations total: %d with uncompressed tracks, "
              "%d with enbaya tracks",
              len(anim_obj_map), len(patchable_uncompressed), len(patchable_enbaya))

    # For each action, find the matching template animation and patch it.
    # Enbaya blobs are processed separately (after this loop) because a
    # single blob can be shared across multiple animations.
    patched_count = 0
    skipped_no_match = []
    fps = context.scene.render.fps

    _log.info("Exporting %d action(s). Action igb_anim_names: %s",
              len(actions_to_export),
              [a.get("igb_anim_name", a.name) for a in actions_to_export])
    _log.info("Template animation names: %s", list(anim_obj_map.keys()))

    # Phase 1: patch uncompressed tracks + collect enbaya work per blob
    # blob_ref -> {track_id: (bone_name, action)}
    blob_track_map = {}
    blob_duration_ns = {}   # blob_ref -> max duration_ns seen
    matched_anims = set()

    for action in actions_to_export:
        anim_name = action.get("igb_anim_name", action.name)

        # Find matching template animation
        anim_info = anim_obj_map.get(anim_name)
        if anim_info is None:
            skipped_no_match.append(anim_name)
            continue

        success = False

        # Patch uncompressed tracks (root motion)
        if anim_info['tracks']:
            success = _patch_animation(
                reader, writer, anim_info, action, armature_obj,
                rest_data, bind_trans_map, endian, fps
            )

        # Collect enbaya tracks for deferred blob processing
        for track_id, bone_name, blob_ref in anim_info['enbaya_track_details']:
            if blob_ref not in blob_track_map:
                blob_track_map[blob_ref] = {}
                blob_duration_ns[blob_ref] = 0
            # First action to claim a track_id wins (shared tracks are identical)
            if track_id not in blob_track_map[blob_ref]:
                blob_track_map[blob_ref][track_id] = (bone_name, action)
            # Track the longest duration for this blob
            dur = anim_info['duration_ns']
            if dur > blob_duration_ns[blob_ref]:
                blob_duration_ns[blob_ref] = dur
            success = True  # mark as having enbaya content

        if success:
            matched_anims.add(anim_name)
            patched_count += 1

    # Phase 2: process each enbaya blob once with ALL its tracks merged
    enbaya_blobs_ok = 0
    for blob_ref, track_map in blob_track_map.items():
        ok = _patch_enbaya_blob(
            reader, writer, blob_ref, track_map,
            blob_duration_ns[blob_ref],
            armature_obj, rest_data, bind_trans_map, endian, fps
        )
        if ok:
            enbaya_blobs_ok += 1

    if enbaya_blobs_ok > 0:
        _log.info("Patched %d enbaya blob(s)", enbaya_blobs_ok)

    if patched_count == 0:
        if operator:
            msg = "No animations could be exported."
            if skipped_no_match:
                msg += (f" {len(skipped_no_match)} animation(s) have no matching "
                        f"template entry.")
            operator.report({'WARNING'}, msg)
        return {'CANCELLED'}

    # Write output
    try:
        writer.write(filepath)
    except Exception as exc:
        if operator:
            operator.report({'ERROR'}, f"Failed to write file: {exc}")
        return {'CANCELLED'}

    if operator:
        msg = (f"Exported {patched_count} animation(s) to "
               f"{os.path.basename(filepath)}")
        if enbaya_blobs_ok > 0:
            msg += f" ({enbaya_blobs_ok} enbaya blob(s) recompressed)"
        if skipped_no_match:
            msg += f" ({len(skipped_no_match)} skipped: no template match)"
            _log.warning("Skipped animations (no template match): %s",
                         skipped_no_match[:10])
        operator.report({'INFO'}, msg)
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
        'enbaya_track_details': [(track_id, bone_name, blob_ref), ...],
        'duration_ns': int,
    }

    Note: enbaya_track_details has per-track blob_ref because a single
    animation can span multiple blobs, and blobs can be shared across
    multiple animations.
    """
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    result = {}

    for i, obj in enumerate(reader.objects):
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igAnimation"):
            continue

        name = ""
        track_list_ref = None
        duration_ns = 0

        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"String":
                name = val if isinstance(val, str) else ""
            elif fi.short_name == b"Long" and slot == 9:
                duration_ns = val
            elif fi.short_name == b"ObjectRef" and val != -1:
                ref = reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    if (ref.is_type(b"igAnimationTrackList") or
                            (slot == 5 and ref.is_type(b"igObjectList"))):
                        track_list_ref = val

        if not name or track_list_ref is None:
            continue

        # Parse tracks: find both uncompressed and enbaya sources
        tracks = []               # uncompressed igTransformSequence1_5
        enbaya_track_details = [] # (track_id, bone_name, blob_ref)

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
                        elif src.is_type(b"igEnbayaTransformSource"):
                            # Extract track_id and per-track blob_ref
                            track_id = -1
                            blob_ref = None
                            eas_ref = None
                            for s, v, f in src._raw_fields:
                                if f.short_name == b"Int" and s == 2:
                                    track_id = v
                                elif f.short_name == b"ObjectRef" and v != -1:
                                    eas_ref = v
                            if eas_ref is not None:
                                eas_obj = reader.resolve_ref(eas_ref)
                                if isinstance(eas_obj, IGBObject):
                                    for s, v, f in eas_obj._raw_fields:
                                        if f.short_name == b"MemoryRef" and v != -1:
                                            blob_ref = v
                                            break
                            if track_id >= 0 and blob_ref is not None:
                                enbaya_track_details.append(
                                    (track_id, bone_name, blob_ref))

        result[name] = {
            'anim_obj_index': i,
            'tracks': tracks,
            'enbaya_track_details': enbaya_track_details,
            'duration_ns': duration_ns,
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

    # Replace memory block and update its directory entry size
    mem_idx = mem_ref_val
    # Resolve through ref_info to get the actual memory block index
    if mem_idx < len(reader.ref_info) and not reader.ref_info[mem_idx]['is_object']:
        writer.objects[mem_idx] = MemoryBlockDef(new_data)
        _update_entry_mem_size(reader, writer, mem_idx, len(new_data))


def _patch_enbaya_blob(reader, writer, blob_ref, track_map, duration_ns,
                       armature_obj, rest_data, bind_trans_map, endian, fps):
    """Compress all tracks for one enbaya blob and replace it in the writer.

    A single blob can be shared across multiple animations.  This function
    receives the MERGED track map (track_id -> (bone_name, action)) from
    all animations that reference this blob, and compresses them into one
    replacement blob.

    Args:
        reader: IGBReader (template).
        writer: from_reader(reader) writer object.
        blob_ref: MemoryRef index of the enbaya blob to replace.
        track_map: Dict {track_id: (bone_name, action)} — merged from all
                   animations sharing this blob.
        duration_ns: Longest animation duration in nanoseconds.
        armature_obj: Blender Armature object.
        rest_data: Dict from _compute_rest_local_data.
        bind_trans_map: Dict bone_name -> Vector(bind_translation).
        endian: '<' or '>'.
        fps: Scene frames-per-second.

    Returns:
        True if the blob was successfully replaced.
    """
    from mathutils import Quaternion, Vector
    from ..igb_format.igb_writer import MemoryBlockDef
    from .enbaya_encoder import compress_enbaya
    from ..igb_format.igb_objects import IGBMemoryBlock
    from .enbaya import EnbayaStream

    if not track_map:
        return False

    # Read the original blob to get sample_rate and quantization_error
    orig_block = reader.resolve_ref(blob_ref)
    if not isinstance(orig_block, IGBMemoryBlock):
        _log.warning("Enbaya blob ref %d not resolvable", blob_ref)
        return False

    # Handle blobs with missing/empty data (aliased or unresolved refs)
    if not orig_block.data or orig_block.mem_size == 0:
        _log.warning("Enbaya blob ref %d has no data (mem_size=%d), skipping",
                      blob_ref, orig_block.mem_size)
        return False

    orig_data = bytes(orig_block.data[:orig_block.mem_size])
    if len(orig_data) < 80:
        _log.warning("Enbaya blob ref %d too small (%d bytes)", blob_ref, len(orig_data))
        return False

    orig_header = EnbayaStream(orig_data, endian=endian)

    # Validate the enbaya header — encrypted/scrambled blobs get defaults.
    # Use stricter thresholds to catch garbage values that are technically
    # in range but obviously wrong (e.g., duration=2.5e-43).
    header_valid = (
        orig_header.track_count >= 1 and orig_header.track_count <= 500 and
        orig_header.sample_rate >= 10 and orig_header.sample_rate <= 240 and
        orig_header.duration >= 0.01 and orig_header.duration <= 300 and
        orig_header.quantization_error >= 1e-6 and
        orig_header.quantization_error <= 0.5
    )

    # Compute duration from action frame range or animation metadata
    if duration_ns > 0:
        duration_sec = duration_ns / 1_000_000_000.0
    elif header_valid:
        duration_sec = orig_header.duration
    else:
        duration_sec = 0.0

    if header_valid:
        sample_rate = orig_header.sample_rate
        qe_orig = orig_header.quantization_error / 2.0
    else:
        _log.info("Enbaya blob %d: unreadable header, using defaults "
                   "(tracks=%s, sr=%s, dur=%s, qe=%s)",
                   blob_ref, orig_header.track_count, orig_header.sample_rate,
                   orig_header.duration, orig_header.quantization_error)
        sample_rate = 30
        qe_orig = 0.002

    # Build sorted track list: [(track_id, bone_name, action), ...]
    # The blob needs tracks 0..max_track_id.
    max_track_id = max(track_map.keys())
    num_tracks = max_track_id + 1

    # Determine frame range from all contributing actions
    frame_start = None
    frame_end = None
    seen_actions = set()
    for track_id, (bone_name, action) in track_map.items():
        if id(action) in seen_actions:
            continue
        seen_actions.add(id(action))
        for fc in action.fcurves:
            for kfp in fc.keyframe_points:
                fr = kfp.co[0]
                if frame_start is None or fr < frame_start:
                    frame_start = fr
                if frame_end is None or fr > frame_end:
                    frame_end = fr

    if frame_start is None:
        frame_start = 0
        frame_end = max(1.0, duration_sec * fps)
    else:
        if duration_sec <= 0 and fps > 0:
            duration_sec = (frame_end - frame_start) / fps

    # Ensure duration is sane (at least 1 frame)
    if duration_sec <= 0:
        duration_sec = 1.0 / sample_rate  # single frame fallback

    num_samples = max(2, int(duration_sec * sample_rate) + 2)

    # Sample keyframes for each track slot 0..num_tracks-1
    encoder_input = [[] for _ in range(num_tracks)]

    for track_id in range(num_tracks):
        if track_id in track_map:
            bone_name, action = track_map[track_id]
        else:
            # Gap in track_ids — fill with identity
            bone_name, action = "", None

        quat_fcurves = _get_fcurves(action, bone_name,
                                     'rotation_quaternion', 4) if action else []
        loc_fcurves = _get_fcurves(action, bone_name,
                                    'location', 3) if action else []

        rest_info = rest_data.get(bone_name)
        rest_rot = rest_info[0] if rest_info else None
        rest_q = rest_info[1] if rest_info else None
        bind_trans = bind_trans_map.get(bone_name)

        for si in range(num_samples):
            time_sec = min(si / float(sample_rate), duration_sec)
            frame = frame_start + time_sec * fps

            # Sample pose quaternion
            if quat_fcurves and any(fc is not None for fc in quat_fcurves):
                w = quat_fcurves[0].evaluate(frame) if quat_fcurves[0] else 1.0
                x = quat_fcurves[1].evaluate(frame) if quat_fcurves[1] else 0.0
                y = quat_fcurves[2].evaluate(frame) if quat_fcurves[2] else 0.0
                z = quat_fcurves[3].evaluate(frame) if quat_fcurves[3] else 0.0
                pose_q = Quaternion((w, x, y, z))
            else:
                pose_q = Quaternion((1.0, 0.0, 0.0, 0.0))

            # Sample pose location
            if loc_fcurves and any(fc is not None for fc in loc_fcurves):
                lx = loc_fcurves[0].evaluate(frame) if loc_fcurves[0] else 0.0
                ly = loc_fcurves[1].evaluate(frame) if loc_fcurves[1] else 0.0
                lz = loc_fcurves[2].evaluate(frame) if loc_fcurves[2] else 0.0
                pose_loc = Vector((lx, ly, lz))
            else:
                pose_loc = Vector((0.0, 0.0, 0.0))

            # Reverse rotation: anim_q = conjugate(rest_q @ pose_q)
            if rest_q is not None:
                alchemy_q = (rest_q @ pose_q).conjugated()
            else:
                alchemy_q = pose_q.conjugated()

            quat_xyzw = (alchemy_q.x, alchemy_q.y, alchemy_q.z, alchemy_q.w)

            # Reverse translation: anim_trans = bind_trans + rest_rot @ pose_loc
            if rest_rot is not None and bind_trans is not None:
                anim_trans = bind_trans + rest_rot @ pose_loc
                trans = (anim_trans.x, anim_trans.y, anim_trans.z)
            elif bind_trans is not None:
                anim_trans = bind_trans + pose_loc
                trans = (anim_trans.x, anim_trans.y, anim_trans.z)
            else:
                trans = tuple(pose_loc)

            encoder_input[track_id].append((time_sec, quat_xyzw, trans))

    # Compress with the enbaya encoder
    try:
        new_blob = compress_enbaya(
            encoder_input, duration_sec,
            sample_rate=sample_rate,
            quantization_error=qe_orig,
        )
    except Exception as exc:
        _log.error("Enbaya compression failed for blob %d: %s", blob_ref, exc)
        return False

    # Replace the memory block in the writer and update directory entry size
    mem_idx = blob_ref
    if mem_idx < len(reader.ref_info) and not reader.ref_info[mem_idx]['is_object']:
        writer.objects[mem_idx] = MemoryBlockDef(new_blob)
        _update_entry_mem_size(reader, writer, mem_idx, len(new_blob))
        _log.info("Enbaya blob %d replaced: %d tracks, %d→%d bytes",
                  blob_ref, num_tracks, len(orig_data), len(new_blob))
        return True

    _log.warning("Could not replace enbaya blob ref %d", blob_ref)
    return False
