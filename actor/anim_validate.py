"""Animation export validation tools.

Standalone utilities for validating exported animation IGB files:
1. Binary structure comparison (template vs export)
2. Animation data round-trip comparison (original vs re-imported)

Usage (from command line):
    python -m igb_blender.actor.anim_validate compare original.igb exported.igb
    python -m igb_blender.actor.anim_validate roundtrip original.igb exported.igb

Usage (from Python):
    from igb_blender.actor.anim_validate import compare_structure, compare_animations
    issues = compare_structure("original.igb", "exported.igb")
    diffs = compare_animations("original.igb", "exported.igb")
"""

import struct
import sys
import math
from typing import List, Tuple, Dict, Optional


def compare_structure(original_path: str, exported_path: str) -> List[str]:
    """Compare the binary structure of two IGB files.

    Verifies that the exported file has the same structural layout
    as the original template:
    - Same number of objects and memory blocks
    - Same entry types in same order
    - All _memSize values match actual memory block sizes
    - Header counts are consistent

    Args:
        original_path: Path to the original template IGB.
        exported_path: Path to the exported IGB.

    Returns:
        List of issue strings. Empty list = all checks passed.
    """
    from ..igb_format.igb_reader import IGBReader

    issues = []

    try:
        orig = IGBReader(original_path)
        orig.read()
    except Exception as exc:
        issues.append(f"Failed to read original: {exc}")
        return issues

    try:
        export = IGBReader(exported_path)
        export.read()
    except Exception as exc:
        issues.append(f"Failed to read export: {exc}")
        return issues

    # 1. Header version and flags
    if orig.header.version != export.header.version:
        issues.append(
            f"Version mismatch: orig={orig.header.version}, "
            f"export={export.header.version}"
        )

    if orig.header.verflags != export.header.verflags:
        issues.append(
            f"VerFlags mismatch: orig=0x{orig.header.verflags:08X}, "
            f"export=0x{export.header.verflags:08X}"
        )

    # 2. Object/memory block counts
    orig_obj_count = sum(1 for ri in orig.ref_info if ri['is_object'])
    orig_mem_count = sum(1 for ri in orig.ref_info if not ri['is_object'])
    exp_obj_count = sum(1 for ri in export.ref_info if ri['is_object'])
    exp_mem_count = sum(1 for ri in export.ref_info if not ri['is_object'])

    if orig_obj_count != exp_obj_count:
        issues.append(
            f"Object count mismatch: orig={orig_obj_count}, "
            f"export={exp_obj_count}"
        )

    if orig_mem_count != exp_mem_count:
        issues.append(
            f"Memory block count mismatch: orig={orig_mem_count}, "
            f"export={exp_mem_count}"
        )

    if len(orig.ref_info) != len(export.ref_info):
        issues.append(
            f"Total ref count mismatch: orig={len(orig.ref_info)}, "
            f"export={len(export.ref_info)}"
        )

    # 3. Entry types in same order
    if len(orig.entries) != len(export.entries):
        issues.append(
            f"Entry count mismatch: orig={len(orig.entries)}, "
            f"export={len(export.entries)}"
        )
    else:
        for i, ((otype, ofields), (etype, efields)) in enumerate(
                zip(orig.entries, export.entries)):
            if otype != etype:
                issues.append(
                    f"Entry[{i}] type mismatch: orig={otype}, export={etype}"
                )

    # 4. Meta-object counts
    if len(orig.meta_objects) != len(export.meta_objects):
        issues.append(
            f"Meta-object count mismatch: orig={len(orig.meta_objects)}, "
            f"export={len(export.meta_objects)}"
        )

    # 5. Meta-field counts
    if len(orig.meta_fields) != len(export.meta_fields):
        issues.append(
            f"Meta-field count mismatch: orig={len(orig.meta_fields)}, "
            f"export={len(export.meta_fields)}"
        )

    # 6. Ref_info is_object flags match
    min_refs = min(len(orig.ref_info), len(export.ref_info))
    for i in range(min_refs):
        if orig.ref_info[i]['is_object'] != export.ref_info[i]['is_object']:
            issues.append(
                f"Ref[{i}] type mismatch: orig={'obj' if orig.ref_info[i]['is_object'] else 'mem'}, "
                f"export={'obj' if export.ref_info[i]['is_object'] else 'mem'}"
            )

    # 7. Verify _memSize in entries matches actual memory block data
    from ..igb_format.igb_objects import IGBMemoryBlock
    for i, ri in enumerate(export.ref_info):
        if ri['is_object']:
            continue
        block = export.objects[i]
        if isinstance(block, IGBMemoryBlock):
            declared_size = ri.get('mem_size', -1)
            actual_size = block.mem_size
            if declared_size != actual_size and declared_size != -1:
                issues.append(
                    f"MemBlock[{i}] size mismatch: entry says {declared_size}, "
                    f"block has {actual_size}"
                )

    return issues


def compare_animations(original_path: str, exported_path: str,
                       qe_tolerance: float = 0.01) -> List[str]:
    """Compare animation data between original and exported IGB files.

    Parses both files, extracts all animations, and compares keyframe
    data per bone per animation. Reports differences beyond tolerance.

    Args:
        original_path: Path to the original IGB.
        exported_path: Path to the exported IGB.
        qe_tolerance: Maximum allowed difference per component
            (default 0.01, accounts for Enbaya quantization).

    Returns:
        List of difference strings. Empty list = data matches.
    """
    from ..igb_format.igb_reader import IGBReader
    from .sg_skeleton import extract_skeleton
    from .sg_animation import extract_animations

    diffs = []

    # Parse original
    try:
        orig_reader = IGBReader(original_path)
        orig_reader.read()
        orig_skel = extract_skeleton(orig_reader)
        orig_anims = extract_animations(orig_reader, orig_skel)
    except Exception as exc:
        diffs.append(f"Failed to parse original: {exc}")
        return diffs

    # Parse exported
    try:
        exp_reader = IGBReader(exported_path)
        exp_reader.read()
        exp_skel = extract_skeleton(exp_reader)
        exp_anims = extract_animations(exp_reader, exp_skel)
    except Exception as exc:
        diffs.append(f"Failed to parse export: {exc}")
        return diffs

    # Build name→animation maps
    orig_map = {a.name: a for a in orig_anims}
    exp_map = {a.name: a for a in exp_anims}

    # Check same set of animations
    orig_names = set(orig_map.keys())
    exp_names = set(exp_map.keys())

    missing = orig_names - exp_names
    extra = exp_names - orig_names

    if missing:
        diffs.append(f"Missing animations in export: {sorted(missing)}")
    if extra:
        diffs.append(f"Extra animations in export: {sorted(extra)}")

    # Compare each animation
    for name in sorted(orig_names & exp_names):
        orig_anim = orig_map[name]
        exp_anim = exp_map[name]

        # Compare duration
        dur_diff = abs(orig_anim.duration_ms - exp_anim.duration_ms)
        if dur_diff > 1.0:  # 1ms tolerance
            diffs.append(
                f"[{name}] Duration mismatch: "
                f"orig={orig_anim.duration_ms:.1f}ms, "
                f"export={exp_anim.duration_ms:.1f}ms "
                f"(diff={dur_diff:.1f}ms)"
            )

        # Build bone→track maps
        orig_tracks = {t.bone_name: t for t in orig_anim.tracks}
        exp_tracks = {t.bone_name: t for t in exp_anim.tracks}

        orig_bones = set(orig_tracks.keys())
        exp_bones = set(exp_tracks.keys())

        missing_bones = orig_bones - exp_bones
        if missing_bones:
            diffs.append(
                f"[{name}] Missing bones in export: "
                f"{sorted(missing_bones)[:5]}..."
            )

        # Compare keyframes per bone
        for bone_name in sorted(orig_bones & exp_bones):
            orig_track = orig_tracks[bone_name]
            exp_track = exp_tracks[bone_name]

            orig_kfs = orig_track.keyframes
            exp_kfs = exp_track.keyframes

            if len(orig_kfs) != len(exp_kfs):
                diffs.append(
                    f"[{name}/{bone_name}] Keyframe count: "
                    f"orig={len(orig_kfs)}, export={len(exp_kfs)}"
                )
                continue

            for ki in range(len(orig_kfs)):
                okf = orig_kfs[ki]
                ekf = exp_kfs[ki]

                # Compare quaternion (WXYZ)
                for ci, comp in enumerate(['qw', 'qx', 'qy', 'qz']):
                    diff = abs(okf.quaternion[ci] - ekf.quaternion[ci])
                    if diff > qe_tolerance:
                        diffs.append(
                            f"[{name}/{bone_name}] kf[{ki}].{comp}: "
                            f"orig={okf.quaternion[ci]:.6f}, "
                            f"export={ekf.quaternion[ci]:.6f} "
                            f"(diff={diff:.6f})"
                        )
                        break  # One diff per keyframe is enough

                # Compare translation
                for ci, comp in enumerate(['tx', 'ty', 'tz']):
                    diff = abs(okf.translation[ci] - ekf.translation[ci])
                    if diff > qe_tolerance:
                        diffs.append(
                            f"[{name}/{bone_name}] kf[{ki}].{comp}: "
                            f"orig={okf.translation[ci]:.6f}, "
                            f"export={ekf.translation[ci]:.6f} "
                            f"(diff={diff:.6f})"
                        )
                        break

    return diffs


def compare_enbaya_signatures(filepath: str) -> List[str]:
    """Check that all Enbaya blobs in a file have valid game signatures.

    Args:
        filepath: Path to the IGB file.

    Returns:
        List of issue strings. Empty = all signatures valid.
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock
    from .enbaya import EnbayaStream

    KNOWN_SIGNATURES = {
        0x10079C10: "XML2",
        0x100A8BAC: "MUA",
    }

    issues = []

    reader = IGBReader(filepath)
    reader.read()

    # Find all igEnbayaAnimationSource objects
    for obj in reader.objects:
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igEnbayaAnimationSource"):
            continue

        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"MemoryRef" and val != -1:
                block = reader.resolve_ref(val)
                if isinstance(block, IGBMemoryBlock) and block.data and block.mem_size >= 80:
                    blob_data = bytes(block.data[:block.mem_size])
                    header = EnbayaStream(blob_data, endian=reader.header.endian)

                    sig = header.signature
                    if sig == 0:
                        issues.append(
                            f"Enbaya blob at ref {val}: signature=0x00000000 "
                            f"(missing — should be game-specific)"
                        )
                    elif sig not in KNOWN_SIGNATURES:
                        issues.append(
                            f"Enbaya blob at ref {val}: signature=0x{sig:08X} "
                            f"(unknown — expected XML2 or MUA)"
                        )

    return issues


def validate_export(original_path: str, exported_path: str,
                    verbose: bool = True) -> bool:
    """Run all validation checks on an exported animation file.

    Args:
        original_path: Path to the original template IGB.
        exported_path: Path to the exported IGB.
        verbose: Print results to stdout.

    Returns:
        True if all checks pass, False if any issues found.
    """
    all_ok = True

    if verbose:
        print(f"Validating: {exported_path}")
        print(f"  Template: {original_path}")
        print()

    # 1. Structure comparison
    if verbose:
        print("=== Structure Comparison ===")
    struct_issues = compare_structure(original_path, exported_path)
    if struct_issues:
        all_ok = False
        if verbose:
            for issue in struct_issues:
                print(f"  FAIL: {issue}")
    elif verbose:
        print("  PASS: All structural checks passed")

    # 2. Enbaya signature check
    if verbose:
        print("\n=== Enbaya Signature Check ===")
    sig_issues = compare_enbaya_signatures(exported_path)
    if sig_issues:
        all_ok = False
        if verbose:
            for issue in sig_issues:
                print(f"  FAIL: {issue}")
    elif verbose:
        print("  PASS: All Enbaya signatures valid")

    # 3. Animation data comparison
    if verbose:
        print("\n=== Animation Data Comparison ===")
    anim_diffs = compare_animations(original_path, exported_path)
    if anim_diffs:
        all_ok = False
        if verbose:
            for diff in anim_diffs[:20]:  # Limit output
                print(f"  DIFF: {diff}")
            if len(anim_diffs) > 20:
                print(f"  ... and {len(anim_diffs) - 20} more differences")
    elif verbose:
        print("  PASS: All animation data matches within tolerance")

    if verbose:
        print()
        print("RESULT:", "ALL PASSED" if all_ok else "ISSUES FOUND")

    return all_ok


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python -m igb_blender.actor.anim_validate compare <original.igb> <exported.igb>")
        print("  python -m igb_blender.actor.anim_validate roundtrip <original.igb> <exported.igb>")
        print("  python -m igb_blender.actor.anim_validate signatures <file.igb>")
        print("  python -m igb_blender.actor.anim_validate full <original.igb> <exported.igb>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "compare" and len(sys.argv) >= 4:
        issues = compare_structure(sys.argv[2], sys.argv[3])
        for issue in issues:
            print(f"  ISSUE: {issue}")
        if not issues:
            print("  All structure checks passed.")
        sys.exit(1 if issues else 0)

    elif cmd == "roundtrip" and len(sys.argv) >= 4:
        diffs = compare_animations(sys.argv[2], sys.argv[3])
        for diff in diffs:
            print(f"  DIFF: {diff}")
        if not diffs:
            print("  All animation data matches.")
        sys.exit(1 if diffs else 0)

    elif cmd == "signatures" and len(sys.argv) >= 3:
        issues = compare_enbaya_signatures(sys.argv[2])
        for issue in issues:
            print(f"  ISSUE: {issue}")
        if not issues:
            print("  All Enbaya signatures valid.")
        sys.exit(1 if issues else 0)

    elif cmd == "full" and len(sys.argv) >= 4:
        ok = validate_export(sys.argv[2], sys.argv[3])
        sys.exit(0 if ok else 1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
