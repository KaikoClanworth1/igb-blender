"""Scale a character's animation IGB file for resized characters.

THE missing piece for properly-resized characters, proven by in-game
testing: animation tracks REPLACE the skin skeleton's root translations
with absolute values authored for the native 41.82-unit biped (idle drives
Bip01 Z to ~38; the Pelvis track carries explicit zeros). No skin-side
data survives that — vanilla small/huge characters (6601, 9301) work
because their own animation sets are authored at their size.

Approach: byte-surgical patching via from_reader (the proven byte-perfect
round-trip path). The file structure stays 100% original; only the
scale-carrying payloads are swapped:
  - igSkeleton bone translations (N x 3 floats)        [same size]
  - igEnbayaAnimationSource blobs (decode -> scale translations ->
    re-encode)                                          [size changes]
  - igAnimationTrack rest translation (Vec3f field)    [same size]
  - igTransformSequence1_5 translation lists (motion)  [same size]
Rotations are untouched — they're scale-free.

Pure file -> file; no Blender data needed.
"""

import os
import struct

from ..igb_format.igb_reader import IGBReader
from ..igb_format.igb_writer import from_reader, EntryDef
from .enbaya import EnbayaStream, decompress_enbaya_to_tracks
from .enbaya_encoder import compress_enbaya

ENBAYA_SIGNATURES = {'xml2': 0x10079C10, 'mua': 0x100A8BAC}


def _get_field(obj, slot):
    for sl, val, _fi in obj._raw_fields:
        if sl == slot:
            return val
    return None


def _scale_float_block(writer, reader, ref, factor):
    """Scale every float in a memory block in place (size unchanged)."""
    blk = reader.objects[ref]
    if blk is None or not blk.data:
        return
    n = len(blk.data) // 4
    vals = struct.unpack(reader.header.endian + f'{n}f', blk.data[:n * 4])
    new_data = struct.pack(reader.header.endian + f'{n}f',
                           *(v * factor for v in vals))
    mdef = writer.objects[ref]
    mdef.data = new_data
    # Same size — patch inside raw_data to preserve original padding bytes
    if mdef.raw_data is not None and len(mdef.raw_data) >= len(new_data):
        raw = bytearray(mdef.raw_data)
        raw[:len(new_data)] = new_data
        mdef.raw_data = bytes(raw)
    else:
        mdef.raw_data = None


def _replace_memblock(writer, reader, ref, new_data):
    """Replace a memory block's bytes, handling size changes.

    Memory dir entries are SHARED between blocks of the same size/pool —
    when the size changes, the entry is cloned for this ref so other
    blocks keep their original entry.
    """
    mdef = writer.objects[ref]
    mdef.data = new_data
    mdef.raw_data = None

    old_size = writer.ref_info[ref]['mem_size']
    new_size = len(new_data)
    if new_size == old_size:
        return
    writer.ref_info[ref]['mem_size'] = new_size

    s = 1 if reader.header.version <= 5 else 0
    ent_idx = writer.index_map[ref]
    ent_type, fields = reader.entries[ent_idx]
    # fields items: (slot, value, ...) — take positionally
    new_vals = [new_size if f[0] == 7 + s else f[1] for f in fields]
    writer.entries.append(EntryDef(ent_type, new_vals))
    writer.index_map[ref] = len(writer.entries) - 1


def scale_anim_file(source_path, output_path, factor, game='xml2'):
    """Scale every animation + the skeleton in an anim IGB by `factor`.

    Args:
        source_path: The character's animation .igb (e.g. 15_xxx.igb).
        output_path: Where to write the scaled file.
        factor: Uniform scale (e.g. 0.65 for a 65% character).
        game: 'xml2' or 'mua' (enbaya signature).

    Returns:
        (success, message, skipped) — skipped lists animations whose
        enbaya blobs could not be decoded (left at original scale).
    """
    reader = IGBReader(source_path)
    reader.read()
    writer = from_reader(reader)
    endian = reader.header.endian
    s_off = 1 if reader.header.version <= 5 else 0
    signature = ENBAYA_SIGNATURES.get(game, 0x10079C10)

    # ---- 1. Skeleton bind translations (igSkeleton slot 3: N x Vec3f) ----
    skel_count = 0
    for skel in reader.get_objects_by_type(b'igSkeleton'):
        mref = _get_field(skel, 3 + s_off)
        if isinstance(mref, int) and mref >= 0:
            _scale_float_block(writer, reader, mref, factor)
            skel_count += 1

    # ---- 2. Enbaya blobs: decode -> scale translations -> re-encode ----
    scaled_blobs = 0
    skipped = []
    for src_obj in reader.get_objects_by_type(b'igEnbayaAnimationSource'):
        bref = _get_field(src_obj, 2 + s_off)
        if not isinstance(bref, int) or bref < 0:
            continue
        blob = bytes(reader.objects[bref].data)
        try:
            hdr = EnbayaStream(blob, endian=endian)
            if not (1 <= hdr.track_count <= 500 and hdr.duration > 0):
                raise ValueError("implausible enbaya header")
            fps = hdr.sample_rate if 10 <= hdr.sample_rate <= 240 else 30.0
            tracks = decompress_enbaya_to_tracks(blob, endian=endian, fps=fps)
            track_keyframes = []
            for track in tracks:
                kfs = []
                for time_ms, quat_wxyz, trans in track:
                    q = quat_wxyz
                    kfs.append((
                        time_ms / 1000.0,
                        (q[1], q[2], q[3], q[0]),          # wxyz -> xyzw
                        (trans[0] * factor, trans[1] * factor,
                         trans[2] * factor),
                    ))
                track_keyframes.append(kfs)
            # Header stores doubled quantization error (see enbaya_encoder)
            qe = hdr.quantization_error / 2.0
            if not (1e-6 <= qe <= 0.25):
                qe = 0.005
            new_blob = compress_enbaya(
                track_keyframes, hdr.duration,
                sample_rate=int(round(fps)),
                quantization_error=qe,
                signature=signature,
            )
            _replace_memblock(writer, reader, bref, new_blob)
            scaled_blobs += 1
        except Exception as exc:
            skipped.append(f'blob@{bref} ({exc})')

    # ---- 3. igAnimationTrack rest translations (slot 5: Vec3f field) ----
    # Patched IN PLACE inside the original raw field bytes — re-serializing
    # objects can change string padding and shift every downstream buffer.
    rest_count = 0
    for tr_obj in reader.get_objects_by_type(b'igAnimationTrack'):
        odef = writer.objects[tr_obj.index]
        if odef is None or odef.raw_bytes is None:
            continue
        # Field byte offset: fields serialize sequentially, 4-byte aligned,
        # in _raw_fields order (mirrors the reader's sequential parse)
        pos = 0
        target_pos = None
        for slot, _val, fi in tr_obj._raw_fields:
            size = fi.size
            if slot == 5 + s_off and fi.short_name == b'Vec3f':
                target_pos = pos
                break
            pos += (size + 3) & ~3
        if target_pos is None or target_pos + 12 > len(odef.raw_bytes):
            continue
        raw = bytearray(odef.raw_bytes)
        x, y, z = struct.unpack_from(endian + '3f', raw, target_pos)
        struct.pack_into(endian + '3f', raw, target_pos,
                         x * factor, y * factor, z * factor)
        odef.raw_bytes = bytes(raw)
        rest_count += 1

    # ---- 4. Motion sequences (igTransformSequence1_5 translation lists) --
    motion_count = 0
    for seq in reader.get_objects_by_type(b'igTransformSequence1_5'):
        vlist_ref = _get_field(seq, 2 + s_off)  # _xlateList -> igVec3fList
        if not isinstance(vlist_ref, int) or vlist_ref < 0:
            continue
        vlist = reader.objects[vlist_ref]
        if vlist is None:
            continue
        mref = _get_field(vlist, 4 + s_off)
        if isinstance(mref, int) and mref >= 0:
            _scale_float_block(writer, reader, mref, factor)
            motion_count += 1

    writer.write(output_path)

    msg = (f"Scaled x{factor:.3f}: {scaled_blobs} animation blob(s), "
           f"{skel_count} skeleton(s), {rest_count} track rests, "
           f"{motion_count} motion sequence(s) -> "
           f"{os.path.basename(output_path)}")
    if skipped:
        msg += f"  [{len(skipped)} blob(s) left unscaled]"
    return True, msg, skipped


def fix_menu_pelvis(source_path, pelvis_offset_z,
                    anim_names=('menu_idle', 'menu_action', 'menu_goodbye'),
                    game='xml2'):
    """Bake the skin's Pelvis offset into the MENU animations' pelvis keys.

    The character select evaluates animations with full pose data, so the
    explicit zero keys on the Pelvis track override the skin's bind offset
    there (in-game the combiner treats them as no-data — gameplay grounds
    fine). Patching the offset INTO the menu anims' pelvis translations
    grounds the select screen; these animations are menu-only, so gameplay
    is untouched.

    Patches IN PLACE (writes '<file>.bak' first, once).

    Args:
        source_path: The character's animation .igb (e.g. 15_ironman.igb).
        pelvis_offset_z: The skin's Pelvis Z offset in game units
            (negative, e.g. -11.48 — read from the armature's stored data).
        anim_names: Animation names to patch.
        game: 'xml2' or 'mua'.

    Returns:
        (success, message)
    """
    import shutil

    backup = source_path + '.bak'
    if not os.path.exists(backup):
        shutil.copy2(source_path, backup)

    reader = IGBReader(source_path)
    reader.read()
    writer = from_reader(reader)
    endian = reader.header.endian
    s_off = 1 if reader.header.version <= 5 else 0
    signature = ENBAYA_SIGNATURES.get(game, 0x10079C10)

    patched = []
    for anim_obj in reader.get_objects_by_type(b'igAnimation'):
        name = _get_field(anim_obj, 2 + s_off)
        if name not in anim_names:
            continue

        # Track list -> find the Pelvis track's stream id + the shared blob
        tl_ref = _get_field(anim_obj, 5 + s_off)
        tl = reader.objects[tl_ref]
        count = _get_field(tl, 2 + s_off)
        mref = _get_field(tl, 4 + s_off)
        refs = struct.unpack(endian + 'i' * count,
                             reader.objects[mref].data[:4 * count])

        pelvis_stream = None
        blob_ref = None
        for ref in refs:
            track = reader.objects[ref]
            bone_name = _get_field(track, 2 + s_off)
            ets_ref = next((v for sl, v, fi in track._raw_fields
                            if fi.short_name == b'ObjectRef'), None)
            if ets_ref is None or ets_ref < 0:
                continue
            ets = reader.objects[ets_ref]
            if ets.type_name != b'igEnbayaTransformSource':
                continue
            if blob_ref is None:
                src_ref = _get_field(ets, 3 + s_off)
                if isinstance(src_ref, int) and src_ref >= 0:
                    src = reader.objects[src_ref]
                    blob_ref = _get_field(src, 2 + s_off)
            if bone_name == 'Bip01 Pelvis':
                pelvis_stream = _get_field(ets, 2 + s_off)

        if pelvis_stream is None or blob_ref is None:
            continue

        blob = bytes(reader.objects[blob_ref].data)
        hdr = EnbayaStream(blob, endian=endian)
        fps = hdr.sample_rate if 10 <= hdr.sample_rate <= 240 else 30.0
        tracks = decompress_enbaya_to_tracks(blob, endian=endian, fps=fps)

        track_keyframes = []
        for ti, track in enumerate(tracks):
            kfs = []
            for time_ms, quat_wxyz, trans in track:
                q = quat_wxyz
                t = trans
                if ti == pelvis_stream:
                    t = (t[0], t[1], t[2] + pelvis_offset_z)
                kfs.append((time_ms / 1000.0,
                            (q[1], q[2], q[3], q[0]), t))
            track_keyframes.append(kfs)

        qe = hdr.quantization_error / 2.0
        if not (1e-6 <= qe <= 0.25):
            qe = 0.005
        new_blob = compress_enbaya(
            track_keyframes, hdr.duration,
            sample_rate=int(round(fps)),
            quantization_error=qe,
            signature=signature,
        )
        _replace_memblock(writer, reader, blob_ref, new_blob)
        patched.append(name)

    if not patched:
        return False, ("No menu animations found/patched — check the file "
                       "is the character's anim set")

    writer.write(source_path)
    return True, (f"Patched pelvis offset {pelvis_offset_z:+.2f} into "
                  f"{', '.join(patched)} (backup: {os.path.basename(backup)})")
