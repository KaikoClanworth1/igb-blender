"""Auto-rig converter: Unity Humanoid / Mixamo -> XML2 IGB skeleton.

Converts a Blender armature from Unity Humanoid or Mixamo bone naming to the
XML2 "Bip01" skeleton convention. After conversion, the armature has all the
custom properties needed by the from-scratch skin exporter (skin_export.py).

Usage:
    from igb_blender.actor.rig_converter import convert_rig
    result = convert_rig(armature_obj, profile='AUTO')
    # result = {'success': True, 'mapped': 30, 'added': 5, 'removed': 12}
"""

import json
from typing import Dict, List, Optional, Tuple

# ============================================================================
# XML2 Target Skeleton Definition (35 bones)
# ============================================================================

# Each entry: (name, index, parent_idx, bm_idx, flags)
# Non-deforming bones have bm_idx = -1
XML2_SKELETON = [
    ("",                       0,  -1,  -1, 0x40),  # unnamed root
    ("Bip01",                  1,   0,  -1, 0x02),
    ("Bip01 Pelvis",           2,   1,   0, 0x02),
    ("Bip01 Spine",            3,   2,   1, 0x02),
    ("Bip01 Spine1",           4,   3,   2, 0x02),
    ("Bip01 Spine2",           5,   4,   3, 0x02),
    ("Bip01 Neck",             6,   5,   4, 0x02),
    ("Bip01 Head",             7,   6,   5, 0x02),
    ("Bip01 Ponytail1",        8,   7,   6, 0x02),
    ("Bip01 Ponytail11",       9,   8,   7, 0x02),
    ("Bip01 L Clavicle",      10,   6,   8, 0x02),
    ("Bip01 L UpperArm",      11,  10,   9, 0x02),
    ("Bip01 L Forearm",        12,  11,  10, 0x02),
    ("Bip01 L Hand",           13,  12,  11, 0x02),
    ("Bip01 L Finger0",        14,  13,  12, 0x02),
    ("Bip01 L Finger01",       15,  14,  13, 0x02),
    ("Bip01 L Finger1",        16,  13,  14, 0x02),
    ("Bip01 L Finger11",       17,  16,  15, 0x02),
    ("Bip01 R Clavicle",      18,   6,  16, 0x02),
    ("Bip01 R UpperArm",      19,  18,  17, 0x02),
    ("Bip01 R Forearm",        20,  19,  18, 0x02),
    ("Bip01 R Hand",           21,  20,  19, 0x02),
    ("Bip01 R Finger0",        22,  21,  20, 0x02),
    ("Bip01 R Finger01",       23,  22,  21, 0x02),
    ("Bip01 R Finger1",        24,  21,  22, 0x02),
    ("Bip01 R Finger11",       25,  24,  23, 0x02),
    ("Bip01 L Thigh",          26,   3,  24, 0x02),
    ("Bip01 L Calf",           27,  26,  25, 0x02),
    ("Bip01 L Foot",           28,  27,  26, 0x02),
    ("Bip01 L Toe0",           29,  28,  27, 0x02),
    ("Bip01 R Thigh",          30,   3,  28, 0x02),
    ("Bip01 R Calf",           31,  30,  29, 0x02),
    ("Bip01 R Foot",           32,  31,  30, 0x02),
    ("Bip01 R Toe0",           33,  32,  31, 0x02),
    ("Motion",                 34,   0,  -1, 0x42),
]

# Set of all XML2 bone names (for quick lookup)
XML2_BONE_NAMES = {entry[0] for entry in XML2_SKELETON}

# Joint count = number of deforming bones (bm_idx >= 0)
XML2_JOINT_COUNT = sum(1 for _, _, _, bm, _ in XML2_SKELETON if bm >= 0)


# ============================================================================
# MUA Extended Skeleton (XML2 base + FX bones)
# ============================================================================
# FX bones are non-deforming effect attachment points used by MUA's engine.
# The engine looks up bones by name to attach particles, weapon visuals, and
# scripted effects. All parented to Bip01 by default — users can reparent
# in Blender for per-character customization.

# (name, parent_name, bm_idx, flags)
MUA_FX_BONES = [
    ("Gun1", "Bip01", -1, 0x02),
    ("fx01", "Bip01", -1, 0x02),
    ("fx02", "Bip01", -1, 0x02),
    ("fx03", "Bip01", -1, 0x02),
    ("fx04", "Bip01", -1, 0x02),
    ("fx05", "Bip01", -1, 0x02),
    ("fx06", "Bip01", -1, 0x02),
    ("fx07", "Bip01", -1, 0x02),
    ("fx08", "Bip01", -1, 0x02),
    ("fx09", "Bip01", -1, 0x02),
    ("fx10", "Bip01", -1, 0x02),
    ("fx11", "Bip01", -1, 0x02),
    ("fx12", "Bip01", -1, 0x02),
    ("fx13", "Bip01", -1, 0x02),
]

MUA_FX_BONE_NAMES = {name for name, _, _, _ in MUA_FX_BONES}

# Build MUA_SKELETON = XML2_SKELETON + FX bones (auto-indexed)
def _build_mua_skeleton():
    """Build MUA skeleton by appending FX bones to XML2 skeleton."""
    # Start with XML2 base
    skeleton = list(XML2_SKELETON)
    # Build name→index lookup from XML2
    name_to_idx = {entry[0]: entry[1] for entry in XML2_SKELETON}
    next_idx = len(XML2_SKELETON)
    for fx_name, parent_name, bm_idx, flags in MUA_FX_BONES:
        parent_idx = name_to_idx.get(parent_name, 0)
        skeleton.append((fx_name, next_idx, parent_idx, bm_idx, flags))
        name_to_idx[fx_name] = next_idx
        next_idx += 1
    return skeleton

MUA_SKELETON = _build_mua_skeleton()
MUA_BONE_NAMES = {entry[0] for entry in MUA_SKELETON}
MUA_JOINT_COUNT = sum(1 for _, _, _, bm, _ in MUA_SKELETON if bm >= 0)  # = 32 (same as XML2)


def get_skeleton_for_game(game: str):
    """Return the skeleton definition for the specified target game.

    Args:
        game: 'XML2' or 'MUA'

    Returns:
        List of (name, index, parent_idx, bm_idx, flags) tuples.
    """
    if game == 'MUA':
        return MUA_SKELETON
    return XML2_SKELETON


def get_bone_names_for_game(game: str):
    """Return set of bone names for the specified target game."""
    if game == 'MUA':
        return MUA_BONE_NAMES
    return XML2_BONE_NAMES


# ============================================================================
# Universal Bone Name Normalization (CATS-style)
# ============================================================================
# Adapts the normalization approach from the CATS Blender Plugin to
# standardize bone names from ANY rig format before matching against aliases.

# Prefixes stripped during normalization (order matters — first match wins)
_STRIP_PREFIXES = [
    ('ValveBiped_', ''), ('Valvebiped_', ''),
    ('Bip1_', 'Bip_'), ('Bip01_', 'Bip_'), ('Bip001_', 'Bip_'),
    ('Bip02_', 'Bip_'), ('Bip01', ''),
    ('Character1_', ''), ('HLP_', ''), ('JD_', ''), ('JU_', ''),
    ('Armature|', ''), ('Bone_', ''), ('Cf_S_', ''), ('Cf_J_', ''),
    ('Joint_', ''), ('Def_C_', ''), ('Def_', ''), ('DEF_', ''),
    ('Chr_', ''), ('B_', ''), ('G_', ''), ('C_', ''),
]
_STRIP_SUFFIXES = [
    ('_Bone', ''), ('_Bn', ''), ('_Le', '_L'), ('_Ri', '_R'), ('_', ''),
]
_REPLACEMENTS = [
    (' ', '_'), ('-', '_'), ('.', '_'), (':', '_'),
    ('____', '_'), ('___', '_'), ('__', '_'),
    ('_Le_', '_L_'), ('_Ri_', '_R_'),
    ('LEFT', 'Left'), ('RIGHT', 'Right'),
]


def _normalize_bone_name(name):
    """Normalize a bone name to a standard form for alias matching.

    Adapted from the CATS Blender Plugin normalization pipeline.
    Capitalizes segments, strips common prefixes/suffixes, normalizes
    separators so bone names from any rig converge to matchable forms.
    """
    # Capitalize first letter of each underscore-delimited segment
    parts = name.split('_')
    name = '_'.join(s[:1].upper() + s[1:] for s in parts)

    # Apply character replacements
    for old, new in _REPLACEMENTS:
        name = name.replace(old, new)

    # Strip known prefixes (repeat to handle chained prefixes like
    # ValveBiped_Bip01_ → strip ValveBiped_ → strip Bip01_)
    for _ in range(2):
        for prefix, repl in _STRIP_PREFIXES:
            if name.startswith(prefix):
                name = repl + name[len(prefix):]
                break

    # Strip known suffixes (first match only)
    for suffix, repl in _STRIP_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)] + repl
            break

    # Remove leading digit segment (e.g. "01_Spine" → "Spine")
    parts = name.split('_')
    if len(parts) > 1 and parts[0].isdigit():
        name = parts[1]

    # Remove trailing S0, _Jnt
    if name.endswith('S0'):
        name = name[:-2]
    if name.endswith('_Jnt'):
        name = name[:-4]

    # Strip mixamorig prefix (after : → _ replacement)
    if name.startswith('Mixamorig_'):
        name = name[len('Mixamorig_'):]

    return name


# ============================================================================
# Comprehensive Bone Alias → XML2 Mapping
# ============================================================================
# Aliases are in NORMALIZED form (post-normalization).
# Uses \L / \Left templates expanded into L/R and Left/Right variants.
# Derived from CATS Blender Plugin bone tables + Unity/VRChat/MMD conventions.

def _expand_sides(template_key, aliases, xml2_l, xml2_r):
    """Expand \\L / \\Left templates into Left/Right alias entries."""
    entries = []
    for side_data in [('Left', 'L', xml2_l), ('Right', 'R', xml2_r)]:
        full, short, xml2 = side_data
        expanded_aliases = []
        for a in aliases:
            expanded_aliases.append(
                a.replace('\\Left', full).replace('\\left', full.lower())
                 .replace('\\Lf', full[:1]).replace('\\L', short).replace('\\l', short.lower()))
        entries.append((expanded_aliases, xml2))
    return entries


# Template aliases for bones with left/right sides.
# Each entry: (CATS template key, [normalized aliases with \L/\Left], xml2_L, xml2_R)
_SIDED_ALIASES = [
    # Shoulder / Clavicle
    ('\\Left_Shoulder', [
        '\\Left_Shoulder', '\\LeftShoulder', 'Shoulder_\\L', '\\LShoulder',
        '\\L_Shoulder', 'Bip_\\L_Clavicle', '\\L_Clavicle', '\\Left_Clavicle',
        '\\LCollar', '\\LeftCollar', '\\L_Collar', 'Clavicle_\\L',
        'ShoulderArm_\\L', 'Bip_Collar_\\L', 'Shol_\\L',
        'J_Bip_\\L_Shoulder', 'Bip_Clavicle_\\L', 'Clav_\\L',
        '\\LClavicle', 'Bip_\\L_Arm', 'J_\\L_Collar', 'J_\\L_Shoulder',
        'Collarbone_\\L', 'J_Clavicle_\\L', '\\L_Clav',
    ], 'Bip01 L Clavicle', 'Bip01 R Clavicle'),

    # Upper arm
    ('\\Left_Arm', [
        '\\Left_Arm', '\\LeftArm', 'Arm_\\L', '\\LArm',
        'Bip_\\L_UpperArm', 'Upper_Arm_\\L', 'UpperArm_\\L',
        '\\Left_Upper_Arm', '\\L_UpperArm', '\\LeftUpArm',
        'Uparm_\\L', '\\L_Arm', 'Arm_Upper_\\L',
        'J_Bip_\\L_UpperArm', '\\LUpperArm', '\\LShldr',
        'Bip_UpperArm_\\L', '\\L_Shldr', 'Arm_Stretch_\\L',
        'J_Shoulder_\\L', '\\L_Uparm', 'Bip_\\L_Arm1',
    ], 'Bip01 L UpperArm', 'Bip01 R UpperArm'),

    # Forearm / Elbow
    ('\\Left_Elbow', [
        '\\Left_Elbow', '\\LeftElbow', 'Elbow_\\L', '\\L_Elbow',
        'Bip_\\L_ForeArm', 'Fore_Arm_\\L', 'ForeArm_\\L',
        '\\LForeArm', '\\L_ForeArm', '\\LeftLowArm', '\\Left_Forearm',
        '\\LeftForeArm', 'Lower_Arm_\\L', 'LowerArm_\\L',
        'Arm_Lower_\\L', 'J_Bip_\\L_LowerArm', 'Bip_Forearm_\\L',
        '\\LElbow', 'Forearm_Stretch_\\L', 'J_Elbow_\\L',
        '\\L_LowerArm', '\\L_Forearm', 'Bip_\\L_Arm2',
        'Bip_LowerArm_\\L', '\\L_Hiji',
    ], 'Bip01 L Forearm', 'Bip01 R Forearm'),

    # Hand / Wrist
    ('\\Left_Wrist', [
        '\\Left_Wrist', '\\LeftWrist', 'Wrist_\\L', '\\LHand',
        'Bip_\\L_Hand', 'Hand_\\L', '\\LeftHand', '\\Left_Hand',
        '\\L_Hand', 'Palm_\\L', 'J_Bip_\\L_Hand', 'Bip_Hand_\\L',
        'J_\\L_Wrist', '\\L_Wrist', '\\LWrist', 'J_Te_\\L', 'J_Wrist_\\L',
    ], 'Bip01 L Hand', 'Bip01 R Hand'),

    # Thigh / Upper leg
    ('\\Left_Leg', [
        '\\Left_Leg', '\\Left_Foot', '\\LeftLeg', 'Leg_\\L',
        'Bip_\\L_Thigh', 'Upper_Leg_\\L', '\\LThigh', 'Thigh_\\L',
        '\\L_Thigh', '\\LeftUpLeg', '\\Left_Thigh', '\\L_Hip',
        'UpperLeg_\\L', 'J_Bip_\\L_UpperLeg', 'Bip_Thigh_\\L',
        'Bip_Hip_\\L', 'Thigh_Stretch_\\L', 'J_Hip_\\L',
        '\\LUpLeg', '\\L_UpperLeg', 'Bip_\\L_Leg', '\\L_Leg',
        '\\LeftHip', '\\L_Momo',
    ], 'Bip01 L Thigh', 'Bip01 R Thigh'),

    # Calf / Knee / Lower leg
    ('\\Left_Knee', [
        '\\Left_Knee', '\\LeftKnee', 'Knee_\\L', '\\LLeg',
        '\\LShin', 'Shin_\\L', '\\L_Calf', 'Calf_\\L',
        '\\LeftLowLeg', '\\Left_Shin', 'Lower_Leg_\\L', 'LowerLeg_\\L',
        'Bip_\\L_Calf', 'J_Bip_\\L_LowerLeg', 'Bip_Leg_\\L',
        'Leg_Stretch_\\L', 'J_Knee_\\L', '\\LCalf', '\\L_LowerLeg',
        'Bip_Knee_\\L', '\\L_Sune', 'Bip_\\L_Leg1',
    ], 'Bip01 L Calf', 'Bip01 R Calf'),

    # Foot / Ankle
    ('\\Left_Ankle', [
        '\\Left_Ankle', '\\LeftAnkle', 'Ankle_\\L', '\\L_Ankle',
        'Bip_\\L_Foot', 'Foot_\\L', '\\LFoot', '\\L_Foot',
        '\\LeftFoot', 'J_Bip_\\L_Foot', 'Bip_Foot_\\L',
        '\\LAnkle', '\\Left_Heel', 'J_Ankle_\\L',
    ], 'Bip01 L Foot', 'Bip01 R Foot'),

    # Toe
    ('\\Left_Toe', [
        '\\Left_Toe', '\\Left_Toes', '\\LeftToe', 'LegTip_\\L',
        'Bip_\\L_Toe0', 'Toe_\\L', '\\LToe', '\\L_Toe',
        '\\LeftToeBase', 'Toes_\\L', 'J_Bip_\\L_ToeBase',
        'Bip_Toe_\\L', 'J_Ball_\\L', '\\LToeBase', '\\LToe0',
        '\\L_Toes', '\\L_Toe0', 'ToeTip_\\L',
    ], 'Bip01 L Toe0', 'Bip01 R Toe0'),

    # Thumb (proximal → Finger0, intermediate → Finger01)
    ('Thumb0_\\L', [
        'Thumb0_\\L', 'Thumb_0_\\L', '\\LThumb1', 'Thumb1_\\L',
        'Finger0_\\L', 'Bip_\\L_Finger0', 'J_Bip_\\L_Thumb1',
        'ThumbFinger1_\\L', 'Thumb_Proximal_\\L', 'Thumb_A_\\L',
        '\\LFingerThumb1',
    ], 'Bip01 L Finger0', 'Bip01 R Finger0'),
    ('Thumb1_\\L', [
        'Thumb1_\\L', 'Thumb_1_\\L', '\\LThumb2', 'Thumb2_\\L',
        'Finger01_\\L', 'Bip_\\L_Finger01', 'J_Bip_\\L_Thumb2',
        'ThumbFinger2_\\L', 'Thumb_Intermediate_\\L', 'Thumb_B_\\L',
        '\\LFingerThumb2',
    ], 'Bip01 L Finger01', 'Bip01 R Finger01'),

    # Middle finger (proximal → Finger1, intermediate → Finger11)
    ('MiddleFinger1_\\L', [
        'MiddleFinger1_\\L', 'Middle1_\\L', 'Middle_Proximal_\\L',
        'Bip_\\L_Finger1', 'J_Bip_\\L_Middle1', 'MiddleFinger_A_\\L',
        '\\LFingerMiddle1', 'Mid1_\\L',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('MiddleFinger2_\\L', [
        'MiddleFinger2_\\L', 'Middle2_\\L', 'Middle_Intermediate_\\L',
        'Bip_\\L_Finger11', 'J_Bip_\\L_Middle2', 'MiddleFinger_B_\\L',
        '\\LFingerMiddle2', 'Mid2_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),
]

# Center (non-sided) aliases → XML2
_CENTER_ALIASES = {
    # Hips / Pelvis
    'Bip01 Pelvis': [
        'Hips', 'LowerBody', 'Lower_Body', 'Pelvis', 'Bip_Pelvis',
        'Hip', 'Waist', 'Root', 'J_Bip_C_Hips', 'Root_X',
        'HipN', 'J_Kosi', 'Kosi', 'J_Hip',
    ],
    # Spine
    'Bip01 Spine': [
        'Spine', 'UpperBody', 'Upper_Body', 'Bip_Spine',
        'Spine_Lower', 'Abdomen', 'Spine0', 'SpineA', 'SpA',
        'J_Bip_C_Spine', 'BODY1', 'WaistN', 'Torso_1',
    ],
    # Chest (Spine1)
    'Bip01 Spine1': [
        'Chest', 'Bip_Spine1', 'Bip_Chest', 'Spine_Upper',
        'Spine1', 'SpineB', 'SpB', 'J_Bip_C_Chest', 'BODY2',
        'BustN', 'Bust', 'Ribs', 'Torso_2', 'ChestLower',
    ],
    # Upper Chest (Spine2)
    'Bip01 Spine2': [
        'Upper_Chest', 'UpperChest', 'Bip_Spine2',
        'Spine2', 'SpineC', 'SpC', 'J_Bip_C_UpperChest',
        'Chest_Def', 'SpineTop',
    ],
    # Neck
    'Bip01 Neck': [
        'Neck', 'Bip_Neck', 'Bip_Neck1', 'Head_Neck_Lower',
        'Head_Neck', 'J_Bip_C_Neck', 'NECK', 'NeckN', 'Kubi',
        'J_Kubi', 'NeckLower', 'Neck_X',
    ],
    # Head
    'Bip01 Head': [
        'Head', 'Bip_Head', 'Bip_Head1', 'J_Bip_C_Head',
        'HEAD', 'HeadN', 'Head_01', 'J_Head', 'Head_X', 'J_Kao',
    ],
}

# Build the flat lookup: normalized_alias (lowercase) → XML2 name
ALIAS_TO_XML2 = {}

# Add center aliases
for xml2_name, aliases in _CENTER_ALIASES.items():
    for alias in aliases:
        ALIAS_TO_XML2[alias.lower()] = xml2_name

# Add sided aliases (expand \L/\Left templates)
for _, aliases, xml2_l, xml2_r in _SIDED_ALIASES:
    for entry_list in _expand_sides(_, aliases, xml2_l, xml2_r):
        expanded_aliases, xml2_name = entry_list
        for alias in expanded_aliases:
            ALIAS_TO_XML2[alias.lower()] = xml2_name

# ---- Unity Humanoid camelCase names (not covered by CATS templates) ----
_UNITY_DIRECT = {
    # Spine
    'upperchest': 'Bip01 Spine2',
    # Arms
    'leftshoulder': 'Bip01 L Clavicle', 'rightshoulder': 'Bip01 R Clavicle',
    'leftupperarm': 'Bip01 L UpperArm', 'rightupperarm': 'Bip01 R UpperArm',
    'leftlowerarm': 'Bip01 L Forearm', 'rightlowerarm': 'Bip01 R Forearm',
    'leftforearm': 'Bip01 L Forearm', 'rightforearm': 'Bip01 R Forearm',
    'lefthand': 'Bip01 L Hand', 'righthand': 'Bip01 R Hand',
    # Legs
    'leftupperleg': 'Bip01 L Thigh', 'rightupperleg': 'Bip01 R Thigh',
    'leftlowerleg': 'Bip01 L Calf', 'rightlowerleg': 'Bip01 R Calf',
    'leftfoot': 'Bip01 L Foot', 'rightfoot': 'Bip01 R Foot',
    'lefttoes': 'Bip01 L Toe0', 'righttoes': 'Bip01 R Toe0',
    'lefttoebase': 'Bip01 L Toe0', 'righttoebase': 'Bip01 R Toe0',
    'lefttoe': 'Bip01 L Toe0', 'righttoe': 'Bip01 R Toe0',
    # Fingers (thumb → Finger0/01, middle → Finger1/11)
    'leftthumbproximal': 'Bip01 L Finger0',
    'rightthumbproximal': 'Bip01 R Finger0',
    'leftthumbintermediate': 'Bip01 L Finger01',
    'rightthumbintermediate': 'Bip01 R Finger01',
    'leftmiddleproximal': 'Bip01 L Finger1',
    'rightmiddleproximal': 'Bip01 R Finger1',
    'leftmiddleintermediate': 'Bip01 L Finger11',
    'rightmiddleintermediate': 'Bip01 R Finger11',
}
for _k, _v in _UNITY_DIRECT.items():
    ALIAS_TO_XML2.setdefault(_k, _v)

# Also add underscore variants (left_upper_arm, etc.)
for _k, _v in list(_UNITY_DIRECT.items()):
    # Insert underscores before uppercase letters
    import re
    _under = re.sub(r'([a-z])([A-Z])', r'\1_\2', _k).lower()
    ALIAS_TO_XML2.setdefault(_under, _v)

# ---- Legacy compat: keep UNITY_TO_XML2 as an alias for the new mapping ----
UNITY_TO_XML2 = ALIAS_TO_XML2

# ============================================================================
# FX Bone Aliases (MUA effect attachment bones)
# ============================================================================
# Aliases for common modder naming variations → standard MUA FX bone names.
# Stored in separate dict (not ALIAS_TO_XML2) since FX bones only exist in MUA.
ALIAS_TO_MUA_FX = {}
_FX_ALIAS_TABLE = {
    'Gun1':  ['gun1', 'gun_1', 'gun01', 'weapon_r', 'weapon_right'],
    'fx01':  ['fx1', 'fx_01', 'fx_1', 'effect01', 'effect1'],
    'fx02':  ['fx2', 'fx_02', 'fx_2', 'effect02', 'effect2'],
    'fx03':  ['fx3', 'fx_03', 'fx_3', 'effect03', 'effect3'],
    'fx04':  ['fx4', 'fx_04', 'fx_4', 'effect04', 'effect4'],
    'fx05':  ['fx5', 'fx_05', 'fx_5', 'effect05', 'effect5'],
    'fx06':  ['fx6', 'fx_06', 'fx_6', 'effect06', 'effect6'],
    'fx07':  ['fx7', 'fx_07', 'fx_7', 'effect07', 'effect7'],
    'fx08':  ['fx8', 'fx_08', 'fx_8', 'effect08', 'effect8'],
    'fx09':  ['fx9', 'fx_09', 'fx_9', 'effect09', 'effect9'],
    'fx10':  ['fx_10', 'effect10'],
    'fx11':  ['fx_11', 'effect11'],
    'fx12':  ['fx_12', 'effect12'],
    'fx13':  ['fx_13', 'effect13'],
}
for _fx_std, _fx_aliases in _FX_ALIAS_TABLE.items():
    # Map the standard name itself
    ALIAS_TO_MUA_FX[_fx_std.lower()] = _fx_std
    for _alias in _fx_aliases:
        ALIAS_TO_MUA_FX[_alias.lower()] = _fx_std


# ============================================================================
# Merge Weight Targets (bones that merge into XML2 targets)
# ============================================================================
MERGE_WEIGHT_TARGETS = {}

_MERGE_SIDED = [
    # Thumb distal → Finger01
    ('Thumb2_\\L', [
        'Thumb2_\\L', 'Thumb_2_\\L', '\\LThumb3', 'Thumb3_\\L',
        'Thumb_Distal_\\L', 'ThumbFinger3_\\L', 'Thumb_C_\\L',
        'J_Bip_\\L_Thumb3', 'Bip_\\L_Finger02',
    ], 'Bip01 L Finger01', 'Bip01 R Finger01'),

    # Index finger (proximal/intermediate/distal) → Finger1 / Finger11
    ('IndexFinger1_\\L', [
        'IndexFinger1_\\L', 'Index1_\\L', 'Fore1_\\L',
        'Index_Proximal_\\L', 'J_Bip_\\L_Index1',
        'Bip_\\L_Finger1', '\\LFingerIndex1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('IndexFinger2_\\L', [
        'IndexFinger2_\\L', 'IndexFinger3_\\L',
        'Index2_\\L', 'Index3_\\L', 'Fore2_\\L', 'Fore3_\\L',
        'Index_Intermediate_\\L', 'Index_Distal_\\L',
        'J_Bip_\\L_Index2', 'J_Bip_\\L_Index3',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Ring finger → Finger1 / Finger11
    ('RingFinger1_\\L', [
        'RingFinger1_\\L', 'Third1_\\L', 'Ring1_\\L',
        'Ring_Proximal_\\L', 'J_Bip_\\L_Ring1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('RingFinger2_\\L', [
        'RingFinger2_\\L', 'RingFinger3_\\L',
        'Third2_\\L', 'Third3_\\L', 'Ring2_\\L', 'Ring3_\\L',
        'Ring_Intermediate_\\L', 'Ring_Distal_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Little finger → Finger1 / Finger11
    ('LittleFinger1_\\L', [
        'LittleFinger1_\\L', 'Little1_\\L', 'Pinky1_\\L',
        'Little_Proximal_\\L', 'J_Bip_\\L_Little1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('LittleFinger2_\\L', [
        'LittleFinger2_\\L', 'LittleFinger3_\\L',
        'Little2_\\L', 'Little3_\\L', 'Pinky2_\\L', 'Pinky3_\\L',
        'Little_Intermediate_\\L', 'Little_Distal_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Middle finger distal → Finger11
    ('MiddleFinger3_\\L', [
        'MiddleFinger3_\\L', 'Middle3_\\L', 'Mid3_\\L',
        'Middle_Distal_\\L', 'J_Bip_\\L_Middle3',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),
]

# Expand sided merge targets
for _, aliases, xml2_l, xml2_r in _MERGE_SIDED:
    for entry_list in _expand_sides(_, aliases, xml2_l, xml2_r):
        expanded_aliases, xml2_name = entry_list
        for alias in expanded_aliases:
            MERGE_WEIGHT_TARGETS[alias.lower()] = xml2_name

# Non-sided merge targets
for alias in ['LeftEye', 'Left_Eye', 'RightEye', 'Right_Eye',
              'Eye_L', 'Eye_R', 'Jaw', 'jaw']:
    MERGE_WEIGHT_TARGETS[alias.lower()] = 'Bip01 Head'

# Unity Humanoid camelCase merge targets
_UNITY_MERGE_DIRECT = {
    'leftthumbdistal': 'Bip01 L Finger01',
    'rightthumbdistal': 'Bip01 R Finger01',
    'leftindexproximal': 'Bip01 L Finger1',
    'rightindexproximal': 'Bip01 R Finger1',
    'leftindexintermediate': 'Bip01 L Finger11',
    'rightindexintermediate': 'Bip01 R Finger11',
    'leftindexdistal': 'Bip01 L Finger11',
    'rightindexdistal': 'Bip01 R Finger11',
    'leftmiddledistal': 'Bip01 L Finger11',
    'rightmiddledistal': 'Bip01 R Finger11',
    'leftringproximal': 'Bip01 L Finger1',
    'rightringproximal': 'Bip01 R Finger1',
    'leftringintermediate': 'Bip01 L Finger11',
    'rightringintermediate': 'Bip01 R Finger11',
    'leftringdistal': 'Bip01 L Finger11',
    'rightringdistal': 'Bip01 R Finger11',
    'leftlittleproximal': 'Bip01 L Finger1',
    'rightlittleproximal': 'Bip01 R Finger1',
    'leftlittleintermediate': 'Bip01 L Finger11',
    'rightlittleintermediate': 'Bip01 R Finger11',
    'leftlittledistal': 'Bip01 L Finger11',
    'rightlittledistal': 'Bip01 R Finger11',
}
for _k, _v in _UNITY_MERGE_DIRECT.items():
    MERGE_WEIGHT_TARGETS.setdefault(_k, _v)

# Mixamo prefix (kept for legacy detection)
MIXAMO_PREFIX = "mixamorig:"

# ============================================================================
# Native XML2 Bone Rotations (extracted from 0601.igb)
# ============================================================================
# Quaternions (w, x, y, z) in ALCHEMY convention (conjugate of Blender).
# These represent each deforming bone's world-space orientation in the native
# XML2 Biped skeleton.  Game animations are authored against these, so
# converted rigs MUST use them in inv_joint_matrices — but use the CUSTOM
# model's bone positions so the skeleton matches the mesh proportions.
#
# CRITICAL: These are Alchemy convention.  Must conjugate before passing
# to Blender's Quaternion.to_matrix() to get the correct rotation.
NATIVE_BONE_ROTATIONS = {
    "Bip01 Pelvis":     ( 0.500001,  0.499999,  0.500000,  0.499999),
    "Bip01 Spine":      ( 0.494167,  0.494167,  0.505766,  0.505766),
    "Bip01 Spine1":     ( 0.483543,  0.483543,  0.515932,  0.515932),
    "Bip01 Spine2":     ( 0.483543,  0.483543,  0.515932,  0.515932),
    "Bip01 Neck":       ( 0.546961,  0.546962,  0.448144,  0.448144),
    "Bip01 Head":       ( 0.499999,  0.500000,  0.500000,  0.500001),
    "Bip01 Ponytail1":  ( 0.435534,  0.435537, -0.557052, -0.557055),
    "Bip01 Ponytail11": ( 0.505759,  0.505761, -0.494172, -0.494174),
    "Bip01 L Clavicle": ( 0.681713,  0.039113,  0.008246, -0.730527),
    "Bip01 L UpperArm": ( 0.688073, -0.006872,  0.006476, -0.725580),
    "Bip01 L Forearm":  ( 0.704559, -0.007019,  0.006316, -0.709582),
    "Bip01 L Hand":     ( 0.503358,  0.493035,  0.506019, -0.497485),
    "Bip01 L Finger0":  ( 0.870648,  0.015735, -0.020128, -0.491242),
    "Bip01 L Finger01": ( 0.675922,  0.008735, -0.024009, -0.736531),
    "Bip01 L Finger1":  ( 0.493886,  0.502507,  0.497015, -0.506498),
    "Bip01 L Finger11": ( 0.493886,  0.502507,  0.497015, -0.506498),
    "Bip01 R Clavicle": ( 0.039113,  0.681714, -0.730526,  0.008246),
    "Bip01 R UpperArm": ( 0.006872, -0.688074,  0.725579, -0.006476),
    "Bip01 R Forearm":  ( 0.007019, -0.704561,  0.709581, -0.006316),
    "Bip01 R Hand":     ( 0.493036,  0.503359, -0.497484,  0.506018),
    "Bip01 R Finger0":  ( 0.015735,  0.870649, -0.491241, -0.020128),
    "Bip01 R Finger01": ( 0.008735,  0.675923, -0.736529, -0.024009),
    "Bip01 R Finger1":  ( 0.502508,  0.493887, -0.506497,  0.497014),
    "Bip01 R Finger11": ( 0.502508,  0.493887, -0.506497,  0.497014),
    "Bip01 L Thigh":    ( 0.527514, -0.477845, -0.594117,  0.374726),
    "Bip01 L Calf":     ( 0.494748, -0.427121, -0.631568,  0.417031),
    "Bip01 L Foot":     ( 0.574484, -0.412272, -0.574484,  0.412272),
    "Bip01 L Toe0":     ( 0.697742, -0.697742, -0.114702, -0.114702),
    "Bip01 R Thigh":    ( 0.477846, -0.527513, -0.374727,  0.594116),
    "Bip01 R Calf":     ( 0.427122, -0.494747, -0.417032,  0.631567),
    "Bip01 R Foot":     ( 0.412273, -0.574483, -0.412273,  0.574483),
    "Bip01 R Toe0":     ( 0.697742, -0.697742,  0.114700,  0.114700),
}

# ============================================================================
# Native XML2 Inverse Joint Matrices (extracted from 0601.igb) — reference
# ============================================================================
# Kept for reference/debugging. These are the exact values from 0601.igb.
# The converter computes custom inv_joint_matrices from native rotations +
# custom bone positions instead of using these directly.
NATIVE_INV_JOINT_MATRICES = {
    "Bip01 Pelvis": [0.00000000, 1.00000000, -0.00000339, 0.00000000, -0.00000139, 0.00000339, 1.00000000, 0.00000000, 1.00000000, -0.00000000, 0.00000139, 0.00000000, -41.81953049, -0.68984902, -0.00005566, 1.00000000],
    "Bip01 Spine": [-0.02319670, 0.99973089, 0.00000077, 0.00000000, -0.00000009, -0.00000078, 0.99999994, 0.00000000, 0.99973089, 0.02319670, 0.00000011, 0.00000000, -42.10088348, -1.82178116, -0.00000367, 1.00000000],
    "Bip01 Spine1": [-0.06474347, 0.99790192, 0.00000077, 0.00000000, -0.00000017, -0.00000078, 1.00000000, 0.00000000, 0.99790192, 0.06474347, 0.00000022, 0.00000000, -45.47655869, -3.71112752, -0.00000891, 1.00000000],
    "Bip01 Spine2": [-0.06474347, 0.99790192, 0.00000077, 0.00000000, -0.00000017, -0.00000078, 1.00000000, 0.00000000, 0.99790192, 0.06474347, 0.00000022, 0.00000000, -51.36871719, -3.70284390, -0.00000889, 1.00000000],
    "Bip01 Neck": [0.19666788, 0.98047018, 0.00000072, 0.00000000, 0.00000035, -0.00000080, 0.99999994, 0.00000000, 0.98047018, -0.19666788, -0.00000050, 0.00000000, -60.61355972, 12.47044086, 0.00003597, 1.00000000],
    "Bip01 Head": [-0.00000139, 1.00000000, 0.00000077, 0.00000000, -0.00000004, -0.00000077, 1.00000000, 0.00000000, 1.00000000, 0.00000139, 0.00000004, 0.00000000, -64.28015137, -0.17490363, 0.00000089, 1.00000000],
    "Bip01 Ponytail1": [-0.24123503, -0.97046673, -0.00000469, 0.00000000, -0.00000044, -0.00000473, 1.00000000, 0.00000000, -0.97046673, 0.24123503, 0.00000071, 0.00000000, 63.14309311, -18.90842247, -0.00005206, 1.00000000],
    "Bip01 Ponytail11": [0.02317266, -0.99973148, -0.00000477, 0.00000000, 0.00000009, -0.00000477, 1.00000000, 0.00000000, -0.99973148, -0.02317266, -0.00000002, 0.00000000, 56.71431351, -4.09063768, -0.00001097, 1.00000000],
    "Bip01 L Clavicle": [-0.06747600, -0.99537432, -0.06838905, 0.00000000, 0.99666429, -0.07039971, 0.04128037, 0.00000000, -0.04590398, -0.06537550, 0.99680436, 0.00000000, 2.44098711, 4.26298046, -59.22934723, 1.00000000],
    "Bip01 L UpperArm": [-0.05301731, -0.99859303, 0.00106044, 0.00000000, 0.99841511, -0.05302788, -0.01885467, 0.00000000, 0.01888438, 0.00005913, 0.99982166, 0.00000000, -7.28284168, 0.28444195, -59.03387833, 1.00000000],
    "Bip01 L Forearm": [-0.00709340, -0.99997425, 0.00106044, 0.00000000, 0.99979705, -0.00711213, -0.01885467, 0.00000000, 0.01886173, 0.00092648, 0.99982160, 0.00000000, -17.51569366, -0.52064860, -59.03387451, 1.00000000],
    "Bip01 L Hand": [-0.00709340, -0.00185669, -0.99997306, 0.00000000, 0.99979705, 0.01884900, -0.00712714, 0.00000000, 0.01886173, -0.99982053, 0.00172261, 0.00000000, -26.03530884, 59.03343964, -0.56765550, 1.00000000],
    "Bip01 L Finger0": [0.51655138, -0.85603207, 0.01958970, 0.00000000, 0.85476512, 0.51686633, 0.04717601, 0.00000000, -0.05050867, -0.00762430, 0.99869418, 0.00000000, -20.10997581, -11.68594742, -59.72957230, 1.00000000],
    "Bip01 L Finger01": [-0.08610736, -0.99609321, 0.01958970, 0.00000000, 0.99525416, -0.08510716, 0.04717601, 0.00000000, -0.04532391, 0.02355844, 0.99869412, 0.00000000, -24.77327156, 3.57737637, -59.72956467, 1.00000000],
    "Bip01 L Finger1": [-0.00712774, -0.00079757, -0.99997425, 0.00000000, 0.99981070, -0.01810670, -0.00711213, 0.00000000, -0.01810056, -0.99983561, 0.00092648, 0.00000000, -27.42947769, 60.08722687, -0.18117857, 1.00000000],
    "Bip01 L Finger11": [-0.00712774, -0.00079757, -0.99997425, 0.00000000, 0.99981070, -0.01810670, -0.00711213, 0.00000000, -0.01810056, -0.99983561, 0.00092648, 0.00000000, -29.26063347, 60.08722687, -0.18117855, 1.00000000],
    "Bip01 R Clavicle": [-0.06747202, -0.99537456, 0.06838889, 0.00000000, -0.99666458, 0.07039573, 0.04128073, 0.00000000, -0.04590407, -0.06537549, -0.99680430, 0.00000000, 2.44098473, 4.26298046, 59.22934341, 1.00000000],
    "Bip01 R UpperArm": [-0.05301315, -0.99859321, -0.00106036, 0.00000000, -0.99841529, 0.05302371, -0.01885459, 0.00000000, 0.01888429, 0.00005914, -0.99982160, 0.00000000, -7.28284407, 0.28444102, 59.03387451, 1.00000000],
    "Bip01 R Forearm": [-0.00708922, -0.99997431, -0.00106036, 0.00000000, -0.99979711, 0.00710796, -0.01885459, 0.00000000, 0.01886164, 0.00092648, -0.99982160, 0.00000000, -17.51569748, -0.52064961, 59.03387451, 1.00000000],
    "Bip01 R Hand": [-0.00708922, -0.00185661, 0.99997312, 0.00000000, -0.99979711, -0.01884892, -0.00712297, 0.00000000, 0.01886164, -0.99982053, -0.00172261, 0.00000000, -26.03531075, 59.03344345, 0.56765658, 1.00000000],
    "Bip01 R Finger0": [0.51655495, -0.85602993, -0.01958990, 0.00000000, -0.85476297, -0.51686996, 0.04717602, 0.00000000, -0.05050875, -0.00762435, -0.99869412, 0.00000000, -20.10997772, -11.68595028, 59.72956848, 1.00000000],
    "Bip01 R Finger01": [-0.08610324, -0.99609357, -0.01958990, 0.00000000, -0.99525458, 0.08510298, 0.04717602, 0.00000000, -0.04532399, 0.02355844, -0.99869412, 0.00000000, -24.77327538, 3.57737494, 59.72956848, 1.00000000],
    "Bip01 R Finger1": [-0.00712357, -0.00079764, 0.99997431, 0.00000000, -0.99981076, 0.01810678, -0.00710796, 0.00000000, -0.01810064, -0.99983561, -0.00092648, 0.00000000, -27.42948151, 60.08723068, 0.18117964, 1.00000000],
    "Bip01 R Finger11": [-0.00712357, -0.00079764, 0.99997431, 0.00000000, -0.99981076, 0.01810678, -0.00710796, 0.00000000, -0.01810064, -0.99983561, -0.00092648, 0.00000000, -29.26063538, 60.08722687, 0.18117964, 1.00000000],
    "Bip01 L Thigh": [0.01321209, 0.96313661, 0.26868817, 0.00000000, 0.17244528, 0.26249057, -0.94940054, 0.00000000, -0.98493052, 0.05887756, -0.16262026, 0.00000000, 40.60599899, -4.00070333, 9.77672863, 1.00000000],
    "Bip01 L Calf": [-0.14558448, 0.95216161, 0.26868808, 0.00000000, 0.12686184, 0.28730601, -0.94940054, 0.00000000, -0.98117846, -0.10413171, -0.16262028, 0.00000000, 22.17516518, -0.35356724, 9.77672958, 1.00000000],
    "Bip01 L Foot": [0.00000002, 0.94737417, 0.32012862, 0.00000000, 0.00000005, 0.32012862, -0.94737411, 0.00000000, -1.00000000, 0.00000003, -0.00000004, 0.00000000, 5.25708008, -1.20160520, 8.99331665, 1.00000000],
    "Bip01 L Toe0": [0.94737417, -0.00000002, 0.32012862, 0.00000000, 0.32012862, -0.00000005, -0.94737411, 0.00000000, 0.00000003, 1.00000000, -0.00000004, 0.00000000, -6.83172464, -0.06356285, 8.99331665, 1.00000000],
    "Bip01 R Thigh": [0.01321281, 0.96313775, -0.26868421, 0.00000000, -0.17244513, -0.26248658, -0.94940174, 0.00000000, -0.98493057, 0.05887754, 0.16262017, 0.00000000, 40.60599899, -4.00070381, -9.77673244, 1.00000000],
    "Bip01 R Calf": [-0.14558396, 0.95216280, -0.26868418, 0.00000000, -0.12686238, -0.28730205, -0.94940174, 0.00000000, -0.98117846, -0.10413173, 0.16262020, 0.00000000, 22.17516518, -0.35356766, -9.77673244, 1.00000000],
    "Bip01 R Foot": [0.00000002, 0.94737542, -0.32012478, 0.00000000, 0.00000004, -0.32012478, -0.94737536, 0.00000000, -1.00000000, 0.00000000, -0.00000005, 0.00000000, 5.25708008, -1.20160663, -8.99331856, 1.00000000],
    "Bip01 R Toe0": [0.94737542, -0.00000002, -0.32012478, 0.00000000, -0.32012478, -0.00000004, -0.94737536, 0.00000000, 0.00000000, 1.00000000, -0.00000005, 0.00000000, -6.83172512, -0.06356285, -8.99331951, 1.00000000],
}

# ============================================================================
# Native XML2 Bone Translations (extracted from 0601.igb)
# ============================================================================
# Each entry is [x, y, z] indexed by bone index (0-34).  These are the exact
# parent-local offsets the game FK engine uses to reconstruct bone positions.
NATIVE_TRANSLATIONS = {
    0: [0.00000000, 0.00000000, 0.00000000],
    1: [0.68984902, 0.00000000, 41.81953049],
    2: [0.00000000, -0.00000000, 0.00000000],
    3: [0.31228638, 0.15484005, -0.00000162],
    4: [3.49065018, -0.00468764, -0.00000001],
    5: [5.89215851, -0.00828373, -0.00000002],
    6: [10.40359020, -0.00188103, -0.00000001],
    7: [2.44559216, 0.00000006, 0.00000000],
    8: [1.55949831, -3.29247999, -0.00000813],
    9: [9.51408577, -0.00931360, -0.00000003],
    10: [-2.27314448, 1.13265991, 0.31228313],
    11: [5.88065577, 0.00000033, -0.00000215],
    12: [10.23828030, 0.00000001, -0.00000022],
    13: [8.51961517, -0.00000005, -0.00000050],
    14: [1.09982407, 0.98525184, -2.20674801],
    15: [2.02486897, -0.00000018, -0.00000271],
    16: [3.59602070, 0.00110330, -0.33947095],
    17: [1.83115566, 0.00000141, -0.00000002],
    18: [-2.27314448, 1.13266170, -0.31227681],
    19: [5.88065577, 0.00000023, 0.00000099],
    20: [10.23827934, -0.00000002, -0.00000131],
    21: [8.51961327, 0.00000002, -0.00000147],
    22: [1.09982443, 0.98525357, 2.20674801],
    23: [2.02486849, 0.00000024, -0.00000041],
    24: [3.59602070, 0.00110484, 0.33947095],
    25: [1.83115411, 0.00000253, 0.00000000],
    26: [-0.30860963, -0.16205171, 3.32986927],
    27: [18.79184341, 0.00000009, -0.00000031],
    28: [18.40011024, 0.00000015, 0.00000021],
    29: [5.19351721, 5.63011932, -0.00000014],
    30: [-0.30860934, -0.16203320, -3.32987022],
    31: [18.79184151, 0.00000005, -0.00000035],
    32: [18.40011024, 0.00000021, 0.00000033],
    33: [5.19351721, 5.63011837, 0.00000071],
    34: [0.68984902, 0.00000000, 0.14953232],
}


# ============================================================================
# Bip Prefix Normalization
# ============================================================================

def _rename_bip_prefix(armature_obj, old_prefix, new_prefix):
    """Rename all bones from one Bip prefix to another (e.g. Bip001 → Bip01).

    Also updates vertex group names on child meshes so skinning stays intact.
    """
    import bpy

    # Build rename map: only rename bones that start with old_prefix
    rename_map = {}
    for bone in armature_obj.data.bones:
        if bone.name.startswith(old_prefix):
            new_name = new_prefix + bone.name[len(old_prefix):]
            rename_map[bone.name] = new_name

    if not rename_map:
        return

    # Rename bones in edit mode
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for old_name, new_name in rename_map.items():
        eb = armature_obj.data.edit_bones.get(old_name)
        if eb:
            eb.name = new_name
    bpy.ops.object.mode_set(mode='OBJECT')

    # Rename vertex groups on child meshes to match
    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        for vg in child.vertex_groups:
            if vg.name in rename_map:
                vg.name = rename_map[vg.name]


# ============================================================================
# Profile Detection
# ============================================================================

def _detect_bip_prefix(armature_obj):
    """Detect which Bip prefix the rig uses (Bip01, Bip001, etc.).

    Returns:
        The prefix string (e.g. "Bip01", "Bip001") or None if not a Biped rig.
    """
    bone_names = [b.name for b in armature_obj.data.bones]
    # Count bones by prefix — check common 3ds Max Biped variants
    for prefix in ("Bip01", "Bip001", "Bip002"):
        count = sum(1 for n in bone_names if n.startswith(prefix))
        if count >= 3:
            return prefix
    return None


def is_bip01_rig(armature_obj):
    """Check if an armature is a Bip01-style rig (already XML2 naming or variant).

    Detects rigs that use Bip01, Bip001, or similar 3ds Max Biped naming
    and should go through the setup path (not the convert path).

    Returns:
        True if the rig has Biped-style bones.
    """
    return _detect_bip_prefix(armature_obj) is not None


def detect_rig_profile(armature_obj):
    """Detect the source rig naming convention.

    Uses universal normalization to detect ANY supported rig type:
    - Bip01/Bip001 (already XML2/3dsMax naming → 'bip01')
    - Mixamo, Unity Humanoid, VRChat, Source Engine, MMD, etc. → 'universal'

    Args:
        armature_obj: Blender armature object.

    Returns:
        'bip01', 'universal', or None (unknown).
    """
    # Check for Bip01/Bip001 rig (already Biped naming)
    if is_bip01_rig(armature_obj):
        return 'bip01'

    # Universal detection: try normalizing each bone and looking up
    matched = 0
    for bone in armature_obj.data.bones:
        if _lookup_bone(bone.name) is not None:
            matched += 1
            if matched >= 3:
                return 'universal'

    return None


# ============================================================================
# Bone Rename Map Builder
# ============================================================================

def _lookup_bone(name, target_game='XML2'):
    """Try to match a bone name to a target skeleton bone using normalization.

    Tries: exact match → lowercase match → normalized match.
    When target_game='MUA', also checks FX bone aliases.
    Returns target bone name or None.
    """
    low = name.lower()

    # 1. Check MUA FX aliases first (if targeting MUA)
    if target_game == 'MUA':
        fx = ALIAS_TO_MUA_FX.get(low)
        if fx:
            return fx

    # 2. Exact lowercase match (handles Unity/VRChat names directly)
    xml2 = ALIAS_TO_XML2.get(low)
    if xml2:
        return xml2

    # 3. Normalize and try again (handles Bip001_, ValveBiped_, etc.)
    normalized = _normalize_bone_name(name)
    norm_low = normalized.lower()

    if target_game == 'MUA':
        fx = ALIAS_TO_MUA_FX.get(norm_low)
        if fx:
            return fx

    xml2 = ALIAS_TO_XML2.get(norm_low)
    if xml2:
        return xml2

    return None


def build_rename_map(armature_obj, profile=None, target_game='XML2'):
    """Build a mapping from current bone names to target skeleton bone names.

    Uses CATS-style normalization to match bones from ANY rig format.
    When target_game='MUA', also matches FX bone aliases.

    Args:
        armature_obj: Blender armature object.
        profile: Ignored (kept for API compatibility).
        target_game: 'XML2' or 'MUA' — determines which bones are valid targets.

    Returns:
        Dict mapping old_name -> new_name for bones that can be mapped.
    """
    rename_map = {}
    # Track which target bones are already claimed (prevent duplicates)
    claimed = set()
    valid_names = get_bone_names_for_game(target_game)

    for bone in armature_obj.data.bones:
        target_name = _lookup_bone(bone.name, target_game=target_game)
        if target_name and target_name in valid_names and target_name not in claimed:
            rename_map[bone.name] = target_name
            claimed.add(target_name)

    return rename_map


def build_merge_map(armature_obj, profile=None, rename_map=None):
    """Build a mapping for bones whose weights should merge into a target.

    Uses CATS-style normalization for universal matching.

    Args:
        armature_obj: Blender armature object.
        profile: Ignored (kept for API compatibility).
        rename_map: Already-built rename map (to exclude already-mapped bones).

    Returns:
        Dict mapping old_bone_name -> xml2_target_name for weight merging.
    """
    if rename_map is None:
        rename_map = {}

    merge_map = {}

    for bone in armature_obj.data.bones:
        if bone.name in rename_map:
            continue  # Already directly mapped

        # Try normalized lookup in merge targets
        lower = bone.name.lower()
        target = MERGE_WEIGHT_TARGETS.get(lower)
        if not target:
            normalized = _normalize_bone_name(bone.name)
            target = MERGE_WEIGHT_TARGETS.get(normalized.lower())

        if target:
            merge_map[bone.name] = target
            continue

        # Pattern-based merging for dynamic/physics bones
        lower_name = bone.name.lower()
        if ('hair' in lower_name or 'j_sec_' in lower_name
                or 'j_bip_' in lower_name or 'j_adj_' in lower_name):
            merge_map[bone.name] = "Bip01 Head"
            continue

        if 'breast' in lower_name or 'bust' in lower_name:
            merge_map[bone.name] = "Bip01 Spine2"
            continue

    return merge_map


# ============================================================================
# Transform Application & Auto-Scale
# ============================================================================

def _apply_transforms_and_scale(armature_obj, target_height=68.0, auto_scale=True):
    """Compute and store an export scale factor for XML2 proportions.

    Instead of modifying Blender data (which can break parent-child
    relationships and Armature modifiers), this stores a scale factor
    as a custom property on the armature. The skin exporter applies
    it at export time so the IGB file is at the correct scale.

    Args:
        armature_obj: Blender armature object.
        target_height: Target character height in game units (default ~80).
        auto_scale: If True, compute and store scale factor.
    """
    if not auto_scale or target_height <= 0:
        return

    # Measure current armature height (bone Z range in armature-local space,
    # accounting for object-level scale so we measure the VISUAL height)
    obj_scale_z = abs(armature_obj.scale.z) if armature_obj.scale.z != 0 else 1.0
    min_z = float('inf')
    max_z = float('-inf')
    for bone in armature_obj.data.bones:
        for vec in (bone.head_local, bone.tail_local):
            min_z = min(min_z, vec.z)
            max_z = max(max_z, vec.z)

    if min_z >= max_z:
        return

    current_height = (max_z - min_z) * obj_scale_z
    scale_factor = target_height / current_height

    # Only store if significantly different (>10 % off)
    if abs(scale_factor - 1.0) <= 0.1:
        return

    # Store on the armature — the export pipeline will apply this
    armature_obj["igb_export_scale"] = scale_factor


# ============================================================================
# Pose Detection & Reposing
# ============================================================================

# Arm-chain bones that need reposing when converting between A-pose and T-pose.
# These are the bones whose rest orientation changes between the two poses.
ARM_CHAIN_BONES = {
    "Bip01 L Clavicle", "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand",
    "Bip01 L Finger0", "Bip01 L Finger01", "Bip01 L Finger1", "Bip01 L Finger11",
    "Bip01 R Clavicle", "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand",
    "Bip01 R Finger0", "Bip01 R Finger01", "Bip01 R Finger1", "Bip01 R Finger11",
}


def _detect_source_pose(armature_obj, rename_map=None):
    """Detect whether the source rig is in T-pose or A-pose.

    Measures the elevation angle of UpperArm bones relative to the horizontal
    plane.  Uses PRE-rename bone names (the original Unity/Mixamo names) via
    the rename_map to find the UpperArm bones before they've been renamed.

    Args:
        armature_obj: Blender armature object (before rename).
        rename_map: Dict mapping old_name -> xml2_name.  If None, assumes
                    bones already have XML2 names.

    Returns:
        'T_POSE' if arms are nearly horizontal (< 15° from XY plane),
        'A_POSE' if arms are angled down (25-65° below horizontal),
        'UNKNOWN' if detection fails or angle is ambiguous.
    """
    import math

    # Build reverse map: xml2_name -> current_bone_name
    if rename_map:
        reverse = {v: k for k, v in rename_map.items()}
    else:
        reverse = {}

    angles = []
    for side_upper, side_fore in [
        ("Bip01 L UpperArm", "Bip01 L Forearm"),
        ("Bip01 R UpperArm", "Bip01 R Forearm"),
    ]:
        # Resolve to current bone names
        upper_name = reverse.get(side_upper, side_upper)
        fore_name = reverse.get(side_fore, side_fore)

        upper_bone = armature_obj.data.bones.get(upper_name)
        fore_bone = armature_obj.data.bones.get(fore_name)

        if upper_bone is None:
            continue

        # Arm direction: upperarm head -> forearm head (or upperarm tail)
        if fore_bone is not None:
            arm_dir = fore_bone.head_local - upper_bone.head_local
        else:
            arm_dir = upper_bone.tail_local - upper_bone.head_local

        if arm_dir.length < 0.0001:
            continue

        # Elevation angle from horizontal (XY plane in Blender Z-up)
        horizontal_len = math.sqrt(arm_dir.x ** 2 + arm_dir.y ** 2)
        elevation = math.degrees(math.atan2(abs(arm_dir.z), horizontal_len))
        angles.append(elevation)

    if not angles:
        return 'UNKNOWN'

    avg_angle = sum(angles) / len(angles)

    if avg_angle < 15.0:
        return 'T_POSE'
    elif 25.0 <= avg_angle <= 65.0:
        return 'A_POSE'
    else:
        return 'UNKNOWN'


def _repose_meshes(armature_obj, source_pose, target_pose):
    """Repose mesh vertices and shape keys from source pose to target pose.

    When source_pose != target_pose, this rotates the arm-chain bones in pose
    mode to match the target, then manually computes deformed vertex positions
    (because VRChat models have 100-200+ shape keys which prevent using
    bpy.ops.object.modifier_apply()).

    The algorithm:
    1. Measure the actual arm angle from the rest pose
    2. Compute the delta rotation needed to reach the target angle
    3. Set pose bone rotations for the arm chain
    4. For each child mesh:
       a. Compute deformed base mesh positions via weighted bone transforms
       b. Transform shape key offsets by per-vertex weighted rotation
       c. Write new positions back
    5. Apply pose as new rest pose on the armature

    Args:
        armature_obj: Blender armature (already renamed to XML2 bones).
        source_pose: 'T_POSE' or 'A_POSE'.
        target_pose: 'T_POSE' or 'A_POSE'.
    """
    import bpy
    import math
    from mathutils import Matrix, Quaternion, Vector

    if source_pose == target_pose:
        return

    # --- 1. Measure actual arm angle and compute delta ---
    # T-pose = 0° elevation, A-pose = ~45° elevation below horizontal.
    # We measure the actual angle rather than assuming exact 45°.
    arm_bones_info = []  # (bone_name, measured_angle, side)
    for side, upper_name, fore_name in [
        ('L', "Bip01 L UpperArm", "Bip01 L Forearm"),
        ('R', "Bip01 R UpperArm", "Bip01 R Forearm"),
    ]:
        upper_bone = armature_obj.data.bones.get(upper_name)
        fore_bone = armature_obj.data.bones.get(fore_name)
        if upper_bone is None:
            continue

        if fore_bone is not None:
            arm_dir = fore_bone.head_local - upper_bone.head_local
        else:
            arm_dir = upper_bone.tail_local - upper_bone.head_local

        if arm_dir.length < 0.0001:
            continue

        horizontal_len = math.sqrt(arm_dir.x ** 2 + arm_dir.y ** 2)
        elevation = math.atan2(arm_dir.z, horizontal_len)  # radians, negative = downward
        arm_bones_info.append((upper_name, elevation, side))

    if not arm_bones_info:
        return

    # Target elevation: T-pose = 0°, A-pose = -45° (arms angled down)
    if target_pose == 'T_POSE':
        target_elevation = 0.0
    else:
        target_elevation = math.radians(-45.0)

    # --- 2. Disconnect all bones so pose rotation works freely ---
    # Connected bones (use_connect=True) have their head locked to the
    # parent's tail, which prevents bpy.ops.pose.armature_apply() from
    # correctly applying the reposed positions as the new rest pose.
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in armature_obj.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode='OBJECT')

    # --- 3. Enter pose mode and set rotations ---
    bpy.ops.object.mode_set(mode='POSE')

    # Clear all pose transforms first
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.location = Vector((0, 0, 0))
        pb.scale = Vector((1, 1, 1))

    # Apply delta rotation to each arm's UpperArm bone.
    # The rotation is around the bone's local "forward" axis (the axis
    # pointing outward from shoulder, which is roughly ±Y in Blender space
    # for a character facing -Y).
    for upper_name, current_elevation, side in arm_bones_info:
        delta_angle = target_elevation - current_elevation

        pb = armature_obj.pose.bones.get(upper_name)
        if pb is None:
            continue

        # The arm extends outward along bone Y. We want to rotate the arm
        # up/down, which is a rotation around the arm's outward axis.
        # In the bone's local space, Y is along the bone, so we rotate
        # around the bone-local Y axis (roll).
        # Actually, for arms extending to the side, the up/down rotation
        # is around the forward axis (which is bone-local X or Z depending
        # on the bone's roll).
        #
        # Simpler approach: compute the rotation axis in armature space.
        # The arm points outward (roughly ±Y). We want to pitch it up/down.
        # Rotation axis = arm direction cross up vector = forward/back axis.
        bone = armature_obj.data.bones[upper_name]
        arm_dir = bone.tail_local - bone.head_local
        arm_dir.normalize()

        # Cross with up vector to get rotation axis
        up = Vector((0, 0, 1))
        rot_axis = arm_dir.cross(up)
        if rot_axis.length < 0.0001:
            continue
        rot_axis.normalize()

        # Convert to bone-local rotation axis
        bone_mat = bone.matrix_local.to_3x3()
        try:
            local_axis = bone_mat.inverted() @ rot_axis
        except ValueError:
            continue
        local_axis.normalize()

        # Apply the delta as a quaternion in bone-local space
        delta_q = Quaternion(local_axis, delta_angle)
        pb.rotation_quaternion = delta_q

    # Force dependency graph update so pose matrices are current
    bpy.context.view_layer.update()

    # --- 4. Deform mesh vertices manually ---
    for child in armature_obj.children:
        if child.type != 'MESH':
            continue

        mesh = child.data
        n_verts = len(mesh.vertices)

        # Find the armature modifier
        arm_mod = None
        for mod in child.modifiers:
            if mod.type == 'ARMATURE' and mod.object == armature_obj:
                arm_mod = mod
                break
        if arm_mod is None:
            continue

        # Precompute per-bone transform: pose_bone.matrix @ rest_bone.matrix_local.inverted()
        bone_transforms = {}
        for pb in armature_obj.pose.bones:
            bone = pb.bone
            try:
                rest_inv = bone.matrix_local.inverted()
            except ValueError:
                rest_inv = Matrix.Identity(4)
            bone_transforms[pb.name] = pb.matrix @ rest_inv

        # Precompute per-vertex weighted rotation (for shape key offsets)
        # and deformed positions (for base mesh)
        new_positions = [None] * n_verts
        per_vertex_matrices = [None] * n_verts

        for vi, vert in enumerate(mesh.vertices):
            # Gather bone weights for this vertex
            weighted_mat = Matrix.Identity(4) * 0  # zero matrix
            total_weight = 0.0

            for g in vert.groups:
                vg = child.vertex_groups[g.group]
                bt = bone_transforms.get(vg.name)
                if bt is None:
                    continue
                w = g.weight
                if w < 0.0001:
                    continue
                for r in range(4):
                    for c in range(4):
                        weighted_mat[r][c] += bt[r][c] * w
                total_weight += w

            if total_weight < 0.0001:
                # No valid bone weights — keep original position
                new_positions[vi] = vert.co.copy()
                per_vertex_matrices[vi] = Matrix.Identity(4)
                continue

            # Normalize
            for r in range(4):
                for c in range(4):
                    weighted_mat[r][c] /= total_weight

            per_vertex_matrices[vi] = weighted_mat
            new_positions[vi] = weighted_mat @ vert.co

        # Write deformed positions to base mesh
        for vi, pos in enumerate(new_positions):
            if pos is not None:
                mesh.vertices[vi].co = pos

        # Transform shape key offsets
        if mesh.shape_keys and mesh.shape_keys.key_blocks:
            basis = mesh.shape_keys.key_blocks[0]
            basis_coords = [Vector(v.co) for v in basis.data]

            # Update basis to match new deformed positions
            for vi, pos in enumerate(new_positions):
                if pos is not None:
                    basis.data[vi].co = pos

            # For each non-basis shape key, rotate the offset vector
            for sk in mesh.shape_keys.key_blocks[1:]:
                for vi in range(n_verts):
                    original_offset = Vector(sk.data[vi].co) - basis_coords[vi]
                    if original_offset.length < 0.00001:
                        # Zero offset — just update to new basis
                        sk.data[vi].co = new_positions[vi]
                        continue

                    # Rotate the offset by the per-vertex weighted rotation
                    mat = per_vertex_matrices[vi]
                    rot_mat = mat.to_3x3()
                    rotated_offset = rot_mat @ original_offset
                    sk.data[vi].co = new_positions[vi] + rotated_offset

        mesh.update()

    # --- 5. Apply pose as rest pose ---
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')


# ============================================================================
# Main Conversion Function
# ============================================================================

def convert_rig(armature_obj, profile='AUTO', auto_scale=True, target_height=68.0,
                target_pose='T_POSE', target_game='XML2'):
    """Convert an armature from Unity/Mixamo naming to XML2/MUA Bip01 convention.

    This is the main entry point. It:
    0. Applies object-level rotation/scale and optionally auto-scales
    1. Detects or uses the specified rig profile
    2. Renames bones and vertex groups
    3. Merges extra vertex weights into nearest mapped bones
    4. Removes unmapped bones
    5. Creates missing XML2 bones (+ FX bones for MUA)
    6. Detects source pose and reposes if needed
    7. Computes inverse joint matrices and bone translations
    8. Stores all required custom properties for IGB export

    Args:
        armature_obj: Blender armature object.
        profile: 'AUTO', 'UNITY', or 'MIXAMO'.
        auto_scale: If True, auto-scale rig to match XML2 character proportions.
        target_height: Target character height in game units (default ~80).
        target_pose: 'T_POSE' or 'A_POSE'. Target arm pose for export.
                     T_POSE recommended for XML2 animation compatibility.
        target_game: 'XML2' or 'MUA'. MUA adds 14 non-deforming FX bones.

    Returns:
        dict with keys: success (bool), mapped (int), added (int),
        removed (int), source_pose (str), error (str or None).
    """
    import bpy
    from mathutils import Matrix, Vector

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {'success': False, 'error': "No armature selected", 'mapped': 0, 'added': 0, 'removed': 0}

    # Check if already converted
    if armature_obj.get("igb_converted_rig", False):
        return {'success': False,
                'error': "Rig is already converted to XML2. Use a fresh import.",
                'mapped': 0, 'added': 0, 'removed': 0}

    # ---- 0. Apply object transforms and auto-scale ----
    _apply_transforms_and_scale(armature_obj, target_height, auto_scale)

    # ---- 0b. Rename Bip001/Bip002 → Bip01 if needed ----
    bip_prefix = _detect_bip_prefix(armature_obj)
    if bip_prefix and bip_prefix != "Bip01":
        _rename_bip_prefix(armature_obj, bip_prefix, "Bip01")

    # ---- 1. Detect profile ----
    if profile == 'AUTO':
        detected = detect_rig_profile(armature_obj)
        if detected is None:
            bone_names = [b.name for b in armature_obj.data.bones[:10]]
            return {'success': False,
                    'error': f"Could not detect rig type. Bones: {', '.join(bone_names)}... "
                             "Try running CATS 'Fix Model' first.",
                    'mapped': 0, 'added': 0, 'removed': 0}
        profile = detected
    else:
        profile = profile.lower()

    # ---- 2. Build rename and merge maps (universal normalization) ----
    rename_map = build_rename_map(armature_obj, target_game=target_game)
    merge_map = build_merge_map(armature_obj, rename_map=rename_map)

    if not rename_map:
        bone_names = [b.name for b in armature_obj.data.bones[:10]]
        return {'success': False,
                'error': f"No bones matched any known convention. "
                         f"Bones: {', '.join(bone_names)}...",
                'mapped': 0, 'added': 0, 'removed': 0}

    # ---- 2b. Detect source pose (before rename, using rename_map) ----
    source_pose = _detect_source_pose(armature_obj, rename_map)

    # ---- 3. Merge vertex weights for extra bones ----
    # Build reverse rename map: xml2_name -> current_bone_name
    # so merge targets resolve to the CURRENT vertex group name.
    # e.g., "Bip01 L Finger1" -> "MiddleFinger1_L" (current VG name)
    reverse_rename = {v: k for k, v in rename_map.items()}
    _merge_vertex_weights(armature_obj, merge_map, reverse_rename)

    # ---- 4. Rename vertex groups on all child meshes BEFORE bone rename ----
    # (vertex groups reference bones by name)
    _rename_vertex_groups(armature_obj, rename_map)

    # Also remove vertex groups for bones being deleted (not in rename or merge)
    target_skeleton = get_skeleton_for_game(target_game)
    all_target_names = {entry[0] for entry in target_skeleton if entry[0]}
    bones_to_remove = set()
    for bone in armature_obj.data.bones:
        if bone.name not in rename_map:
            bones_to_remove.add(bone.name)

    _remove_vertex_groups(armature_obj, bones_to_remove)

    # ---- 5. Enter edit mode: rename, remove, create bones ----
    # Ensure armature is active and selected
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones
    mapped_count = 0
    removed_count = 0

    # CRITICAL: Disconnect ALL bones before any reparenting.
    # When use_connect=True and we change a bone's parent, Blender SNAPS
    # the bone's head to the new parent's tail — destroying the correct
    # position.  Native XML2 bones are never connected, so this is also
    # structurally correct.
    for eb in edit_bones:
        eb.use_connect = False

    # Rename mapped bones
    for old_name, new_name in rename_map.items():
        eb = edit_bones.get(old_name)
        if eb:
            eb.name = new_name
            mapped_count += 1

    # Remove unmapped bones (those not renamed to a target skeleton name)
    current_names = {eb.name for eb in edit_bones}
    bones_to_delete = []
    for eb in edit_bones:
        if eb.name not in all_target_names and eb.name not in {"", "Bone_000"}:
            bones_to_delete.append(eb.name)

    for name in bones_to_delete:
        eb = edit_bones.get(name)
        if eb:
            edit_bones.remove(eb)
            removed_count += 1

    # ---- 6. Create missing target skeleton bones ----
    added_count = 0
    current_names = {eb.name for eb in edit_bones}

    # Build parent name lookup from target skeleton
    skel_by_name = {}
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        parent_name = target_skeleton[parent_idx][0] if parent_idx >= 0 else None
        skel_by_name[name] = (idx, parent_idx, parent_name, bm_idx, flags)

    # Helper: compute a small bone length based on existing bones
    bone_lengths = [eb.length for eb in edit_bones if eb.length > 0.001]
    avg_bone_len = sum(bone_lengths) / len(bone_lengths) if bone_lengths else 0.05

    # Process in index order so parents are created before children
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        if not name:
            display_name = "Bone_000"
        else:
            display_name = name

        if display_name in current_names:
            continue  # Already exists (was renamed from source bone)

        # Create the bone
        eb = edit_bones.new(display_name)
        small_len = avg_bone_len * 0.3  # dummy bones are small

        # Find parent bone
        if parent_idx >= 0:
            parent_bone_name = target_skeleton[parent_idx][0]
            if not parent_bone_name:
                parent_bone_name = "Bone_000"
            parent_eb = edit_bones.get(parent_bone_name)
            if parent_eb:
                eb.parent = parent_eb

                # Smart placement: place dummy at parent's TAIL (end)
                # so it chains naturally after existing bones
                eb.head = parent_eb.tail.copy()

                # Special cases for better visual placement
                if name == "Bip01 Spine2":
                    # UpperChest: place between Chest (Spine1) and Neck
                    neck_eb = edit_bones.get("Bip01 Neck")
                    if neck_eb:
                        # Midpoint between parent tail and neck head
                        eb.head = (parent_eb.tail + neck_eb.head) * 0.5
                    # else stays at parent tail
                elif name.startswith("Bip01 Ponytail"):
                    # Ponytails: place behind head, extending upward
                    head_eb = edit_bones.get("Bip01 Head")
                    if head_eb:
                        eb.head = head_eb.tail.copy()
                        if name == "Bip01 Ponytail11":
                            # Second ponytail extends further
                            pony1 = edit_bones.get("Bip01 Ponytail1")
                            if pony1:
                                eb.head = pony1.tail.copy()
                elif name == "" or name == "Bip01":
                    # Root/Bip01: at origin
                    eb.head = Vector((0, 0, 0))
                elif name == "Motion":
                    # Motion: at origin
                    eb.head = Vector((0, 0, 0))

                # Compute tail: extend in same direction as parent
                parent_dir = (parent_eb.tail - parent_eb.head)
                if parent_dir.length > 0.001:
                    eb.tail = eb.head + parent_dir.normalized() * small_len
                else:
                    eb.tail = eb.head + Vector((0, small_len, 0))
            else:
                eb.head = Vector((0, 0, 0))
                eb.tail = Vector((0, small_len, 0))
        else:
            # Root bone (no parent)
            eb.head = Vector((0, 0, 0))
            eb.tail = Vector((0, small_len, 0))

        eb.use_connect = False
        added_count += 1
        current_names.add(display_name)

    # Fix parent relationships for ALL target skeleton bones (some renamed
    # ones may need re-parenting to match the target hierarchy)
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue

        if parent_idx >= 0:
            parent_name = target_skeleton[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            parent_eb = edit_bones.get(parent_display)
            if parent_eb and eb.parent != parent_eb:
                eb.parent = parent_eb

    # ---- 7. Position non-deforming structural bones ----
    # Bip01 must be co-located with Pelvis (native XML2 convention).
    # This ensures correct FK pivot for animations and proper head/optic
    # beam placement.  Motion bone anchors at ground level for walk cycles.
    pelvis_eb = edit_bones.get("Bip01 Pelvis")
    bip01_eb = edit_bones.get("Bip01")
    if pelvis_eb and bip01_eb:
        bip01_eb.head = pelvis_eb.head.copy()
        bip01_eb.tail = bip01_eb.head + Vector((0, 0, avg_bone_len * 0.3))

    motion_eb = edit_bones.get("Motion")
    if motion_eb and pelvis_eb:
        motion_eb.head = Vector((pelvis_eb.head.x, pelvis_eb.head.y, 0.0))
        motion_eb.tail = motion_eb.head + Vector((0, avg_bone_len * 0.3, 0))

    bpy.ops.object.mode_set(mode='OBJECT')

    # ---- 7b. Repose if source pose != target pose ----
    # Must happen AFTER bone rename + hierarchy setup but BEFORE computing
    # bind matrices, so that arm bone positions reflect the target pose.
    if source_pose != 'UNKNOWN' and source_pose != target_pose:
        _repose_meshes(armature_obj, source_pose, target_pose)

    # ---- 8. Compute inverse joint matrices from rest pose ----
    # T-pose target: use hardcoded NATIVE_BONE_ROTATIONS (animation compat)
    # A-pose target: compute rotations from actual bone orientations
    use_native = (target_pose == 'T_POSE')
    inv_matrices = _compute_inv_joint_matrices(armature_obj, use_native)

    # ---- 9. Compute bone translations ----
    translations = _compute_bone_translations(armature_obj, use_native)

    # ---- 9b. Force non-deforming root bones to native translations ----
    # Bip01 Z must be exactly ~41.82 game units for correct menu placement,
    # head tracking, and animation compatibility.
    export_scale = armature_obj.get("igb_export_scale", 1.0)
    _force_native_root_translations(translations, export_scale)

    # ---- 10. Store all custom properties ----
    _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game=target_game)

    # Store target game for export pipeline
    armature_obj["igb_target_game"] = target_game

    # Determine if axis rotation is needed during export.
    # If bones were identity-renamed (Bip01 Pelvis → Bip01 Pelvis), the rig
    # was already in XML2 orientation and does NOT need the +90° Z rotation.
    # If bones were actually renamed (Hips → Bip01 Pelvis), the rig came from
    # a non-XML2 convention and needs the rotation.
    actually_renamed = sum(1 for old, new in rename_map.items() if old != new)
    armature_obj["igb_converted_rig"] = actually_renamed > 0

    # ---- 11. Update bone display to show XML2 orientations ----
    # Set bone tails based on native XML2 rotations so the skeleton
    # visually reflects the game's bone layout in the Blender viewport.
    # This is purely cosmetic — all export data is already stored above.
    _update_bone_display(armature_obj)

    return {
        'success': True,
        'mapped': mapped_count,
        'added': added_count,
        'removed': removed_count,
        'source_pose': source_pose,
        'error': None,
    }


def setup_bip01_rig(armature_obj, auto_scale=True, target_height=68.0,
                    target_game='XML2'):
    """Setup an existing Bip01 rig for IGB skin export.

    Unlike convert_rig(), this does NOT rename or reparent bones — it assumes
    the armature already uses XML2 bone naming (Bip01, Bip01 Pelvis, etc.).
    It creates any missing XML2 bones (+ FX bones for MUA), fixes the parent
    hierarchy, computes inverse joint matrices and bone translations, and
    stores all required custom properties for the skin exporter.

    Use this for:
    - Native XML2 rigs from other importers or manual creation
    - Rigs that already have Bip01 naming but lack IGB export properties
    - Re-setting up a rig whose properties were lost or need refreshing

    Args:
        armature_obj: Blender armature object with XML2 bone names.
        auto_scale: If True, auto-scale rig to match XML2 character proportions.
        target_height: Target character height in game units (default ~68).
        target_game: 'XML2' or 'MUA'. MUA adds 14 non-deforming FX bones.

    Returns:
        dict with keys: success (bool), added (int), error (str or None).
    """
    import bpy
    from mathutils import Vector

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {'success': False, 'added': 0,
                'error': "No armature selected"}

    # Detect Biped prefix variant (Bip01, Bip001, etc.)
    bip_prefix = _detect_bip_prefix(armature_obj)
    if bip_prefix is None:
        return {'success': False, 'added': 0,
                'error': "Armature has no Bip01/Bip001 bones. "
                         "Use Convert Rig for non-XML2 rigs."}

    # ---- 0. Rename Bip001 → Bip01 if needed ----
    if bip_prefix != "Bip01":
        _rename_bip_prefix(armature_obj, bip_prefix, "Bip01")

    # ---- 0b. Apply object transforms and auto-scale ----
    _apply_transforms_and_scale(armature_obj, target_height, auto_scale)

    # ---- 1. Enter edit mode: create missing bones, fix hierarchy ----
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones
    added_count = 0
    newly_created = set()  # Track which bones we create (vs already existed)

    # Disconnect all bones (XML2 bones are never connected)
    for eb in edit_bones:
        eb.use_connect = False

    # Compute a reference bone length for dummies
    bone_lengths = [eb.length for eb in edit_bones if eb.length > 0.001]
    avg_bone_len = (sum(bone_lengths) / len(bone_lengths)
                    if bone_lengths else 0.05)

    # Create missing target skeleton bones (XML2 or MUA)
    skeleton = get_skeleton_for_game(target_game)
    current_names = {eb.name for eb in edit_bones}
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        if display_name in current_names:
            continue

        eb = edit_bones.new(display_name)
        small_len = avg_bone_len * 0.3

        if parent_idx >= 0:
            parent_bone_name = skeleton[parent_idx][0]
            if not parent_bone_name:
                parent_bone_name = "Bone_000"
            parent_eb = edit_bones.get(parent_bone_name)
            if parent_eb:
                eb.parent = parent_eb
                eb.head = parent_eb.tail.copy()

                # Smart placement for common bones
                if name == "Bip01 Spine2":
                    neck_eb = edit_bones.get("Bip01 Neck")
                    if neck_eb:
                        eb.head = (parent_eb.tail + neck_eb.head) * 0.5
                elif name.startswith("Bip01 Ponytail"):
                    head_eb = edit_bones.get("Bip01 Head")
                    if head_eb:
                        eb.head = head_eb.tail.copy()
                        if name == "Bip01 Ponytail11":
                            pony1 = edit_bones.get("Bip01 Ponytail1")
                            if pony1:
                                eb.head = pony1.tail.copy()

                parent_dir = parent_eb.tail - parent_eb.head
                if parent_dir.length > 0.001:
                    eb.tail = eb.head + parent_dir.normalized() * small_len
                else:
                    eb.tail = eb.head + Vector((0, small_len, 0))
            else:
                eb.head = Vector((0, 0, 0))
                eb.tail = Vector((0, small_len, 0))
        else:
            eb.head = Vector((0, 0, 0))
            eb.tail = Vector((0, small_len, 0))

        eb.use_connect = False
        added_count += 1
        newly_created.add(display_name)
        current_names.add(display_name)

    # Fix parent hierarchy for ALL target skeleton bones
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue
        if parent_idx >= 0:
            parent_name = skeleton[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            parent_eb = edit_bones.get(parent_display)
            if parent_eb and eb.parent != parent_eb:
                eb.parent = parent_eb

    # Position structural bones — ONLY if they were newly created.
    # Existing bones (Bip01, Motion, Bone_000) are already in the correct
    # position and must NOT be moved.  Bip01 controls the model's facing
    # direction, and Motion / Bone_000 must stay at the origin.
    pelvis_eb = edit_bones.get("Bip01 Pelvis")

    if "Bip01" in newly_created:
        bip01_eb = edit_bones.get("Bip01")
        if pelvis_eb and bip01_eb:
            bip01_eb.head = pelvis_eb.head.copy()
            bip01_eb.tail = (bip01_eb.head
                             + Vector((0, 0, avg_bone_len * 0.3)))

    if "Motion" in newly_created:
        motion_eb = edit_bones.get("Motion")
        if motion_eb:
            motion_eb.head = Vector((0, 0, 0))
            motion_eb.tail = Vector((0, avg_bone_len * 0.3, 0))

    if "Bone_000" in newly_created:
        bone000_eb = edit_bones.get("Bone_000")
        if bone000_eb:
            bone000_eb.head = Vector((0, 0, 0))
            bone000_eb.tail = Vector((0, avg_bone_len * 0.3, 0))

    bpy.ops.object.mode_set(mode='OBJECT')

    # ---- 2. Compute and store skeleton data ----
    # Always use native T-pose rotations (NATIVE_BONE_ROTATIONS) since
    # existing Bip01 rigs are expected to be in T-pose orientation.
    use_native = True
    inv_matrices = _compute_inv_joint_matrices(armature_obj, use_native)
    translations = _compute_bone_translations(armature_obj, use_native)

    export_scale = armature_obj.get("igb_export_scale", 1.0)
    _force_native_root_translations(translations, export_scale)

    _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game=target_game)

    # Store target game for export pipeline
    armature_obj["igb_target_game"] = target_game

    # CRITICAL: Override igb_converted_rig to False for existing Bip01 rigs.
    # _store_skeleton_properties() sets it to True (which tells the exporter
    # to apply +90° Z rotation to mesh vertices for Blender→game conversion).
    # But an existing Bip01 rig is already in the correct XML2 orientation —
    # the mesh does NOT need any rotation applied during export.
    armature_obj["igb_converted_rig"] = False

    # NOTE: Do NOT call _update_bone_display() here.  The bones are
    # already oriented correctly in the existing Bip01 rig — rewriting
    # tails would rotate them to match hardcoded XML2 orientations and
    # break the visual pose that's already correct.

    return {
        'success': True,
        'added': added_count,
        'error': None,
    }


def validate_bip01_rig(armature_obj):
    """Validate a Bip01 rig's skeleton data for export readiness.

    Read-only check — does not modify the armature. Returns a list of
    (level, message) tuples describing any issues found. An empty list
    means the rig is fully valid and ready for export.

    Levels: 'ERROR' (will break export), 'WARNING' (may cause issues).

    Args:
        armature_obj: Blender armature object to validate.

    Returns:
        list of (level, message) tuples.
    """
    issues = []

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return [('ERROR', "Not an armature object")]

    bones = armature_obj.data.bones

    # 1. Missing bones
    missing = []
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name not in bones:
            missing.append(display_name)
    if missing:
        if len(missing) <= 3:
            issues.append(('ERROR', f"Missing bones: {', '.join(missing)}"))
        else:
            issues.append(('ERROR',
                           f"Missing {len(missing)} bones: "
                           f"{', '.join(missing[:3])}..."))

    # 2. Wrong parent hierarchy
    hierarchy_fixes = 0
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        bone = bones.get(display_name)
        if bone is None:
            continue
        if parent_idx >= 0:
            expected_parent = XML2_SKELETON[parent_idx][0]
            if not expected_parent:
                expected_parent = "Bone_000"
            actual_parent = bone.parent.name if bone.parent else None
            if actual_parent != expected_parent:
                hierarchy_fixes += 1
    if hierarchy_fixes:
        issues.append(('WARNING',
                       f"{hierarchy_fixes} bone(s) have wrong parent"))

    # 3. Connected bones (XML2 requires all disconnected)
    connected = sum(1 for b in bones if b.use_connect)
    if connected:
        issues.append(('WARNING',
                       f"{connected} bone(s) are connected (should be disconnected)"))

    # 4. Missing armature-level properties
    required_props = [
        'igb_skin_bone_info_list',
        'igb_skin_inv_joint_matrices',
        'igb_skin_bone_translations',
        'igb_bms_palette',
    ]
    missing_props = [p for p in required_props if p not in armature_obj]
    if missing_props:
        issues.append(('ERROR',
                       f"Missing skeleton data: {', '.join(missing_props)}"))

    # 5. Stale bone counts
    bone_count = armature_obj.get("igb_bone_count", 0)
    joint_count = armature_obj.get("igb_skin_joint_count", 0)
    if bone_count != len(XML2_SKELETON):
        issues.append(('WARNING',
                       f"Bone count {bone_count} != expected {len(XML2_SKELETON)}"))
    if joint_count != XML2_JOINT_COUNT:
        issues.append(('WARNING',
                       f"Joint count {joint_count} != expected {XML2_JOINT_COUNT}"))

    # 6. Missing per-bone metadata
    missing_meta = 0
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[display_name]
            if "igb_bone_index" not in pb:
                missing_meta += 1
    if missing_meta:
        issues.append(('WARNING',
                       f"{missing_meta} bone(s) missing IGB metadata"))

    # 7. Mesh children without Armature modifier
    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        has_mod = any(
            m.type == 'ARMATURE' and m.object == armature_obj
            for m in child.modifiers
        )
        if not has_mod:
            issues.append(('WARNING',
                           f"Mesh '{child.name}' has no Armature modifier"))

    return issues


# ============================================================================
# Vertex Group Operations
# ============================================================================

def _rename_vertex_groups(armature_obj, rename_map):
    """Rename vertex groups on all child meshes according to rename_map."""
    import bpy

    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        for vg in child.vertex_groups:
            if vg.name in rename_map:
                vg.name = rename_map[vg.name]


def _remove_vertex_groups(armature_obj, bone_names_to_remove):
    """Remove vertex groups for deleted bones on all child meshes."""
    import bpy

    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        to_remove = [vg for vg in child.vertex_groups if vg.name in bone_names_to_remove]
        for vg in to_remove:
            child.vertex_groups.remove(vg)


def _merge_vertex_weights(armature_obj, merge_map, reverse_rename=None):
    """Merge vertex weights from extra bones into their target bones.

    For each vertex, adds the weight from the source bone's vertex group
    to the target bone's vertex group, then removes the source group.

    CRITICAL: merge_map targets use XML2 names (e.g., "Bip01 L Finger1"),
    but vertex groups still use the original names (e.g., "MiddleFinger1_L").
    reverse_rename resolves XML2 names back to current VG names.

    Args:
        armature_obj: Blender armature object.
        merge_map: Dict mapping source_bone_name -> target_xml2_name.
        reverse_rename: Dict mapping xml2_name -> current_bone_name (optional).
    """
    import bpy

    if not merge_map:
        return
    if reverse_rename is None:
        reverse_rename = {}

    for child in armature_obj.children:
        if child.type != 'MESH':
            continue

        mesh = child.data

        for src_name, xml2_target in merge_map.items():
            src_vg = child.vertex_groups.get(src_name)
            if src_vg is None:
                continue

            # Resolve XML2 target name to the CURRENT vertex group name.
            # e.g., "Bip01 L Finger1" -> "MiddleFinger1_L" (pre-rename name)
            current_target_name = reverse_rename.get(xml2_target, xml2_target)

            # Try current name first, then XML2 name as fallback
            target_vg = child.vertex_groups.get(current_target_name)
            if target_vg is None:
                target_vg = child.vertex_groups.get(xml2_target)
            if target_vg is None:
                # Create with the CURRENT name so rename step works later
                target_vg = child.vertex_groups.new(name=current_target_name)

            # Transfer weights
            src_idx = src_vg.index
            for vert in mesh.vertices:
                src_weight = 0.0
                for g in vert.groups:
                    if g.group == src_idx:
                        src_weight = g.weight
                        break
                if src_weight > 0.0:
                    try:
                        target_vg.add([vert.index], src_weight, 'ADD')
                    except Exception:
                        pass

            # Remove source vertex group
            child.vertex_groups.remove(src_vg)


# ============================================================================
# Visual Display
# ============================================================================

def _update_bone_display(armature_obj):
    """Update bone tails to reflect native XML2 orientations in the viewport.

    After conversion, the bone HEAD positions are correct (model proportions),
    but the tails still point in the original VRChat/Unity directions.  This
    function sets each bone's tail to point along the native XML2 bone axis
    so the skeleton visually matches the game layout.

    Bone lengths are computed from the distance to the nearest child bone
    (matching the XML2 hierarchy) so bones look natural and proportional.

    This is purely cosmetic — all export data (inv_bind, translations) is
    already stored as custom properties and is not affected by tail changes.
    """
    import bpy
    import math
    from mathutils import Quaternion, Vector

    rz90_inv = Quaternion((0, 0, 1), math.radians(-90))

    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones

    # Build XML2 parent->children map for natural bone length computation
    xml2_children = {}  # parent_name -> [child_name, ...]
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        if parent_idx >= 0:
            parent_name = XML2_SKELETON[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            display_name = name if name else "Bone_000"
            xml2_children.setdefault(parent_display, []).append(display_name)

    # Compute a fallback bone length from the armature's Z extent
    min_z = float('inf')
    max_z = float('-inf')
    for eb in edit_bones:
        min_z = min(min_z, eb.head.z)
        max_z = max(max_z, eb.head.z)
    armature_height = max(max_z - min_z, 0.1)
    default_bone_len = armature_height * 0.03  # ~3% of height

    for eb in edit_bones:
        # Compute bone length from distance to nearest child
        children = xml2_children.get(eb.name, [])
        bone_len = 0.0
        if children:
            min_child_dist = float('inf')
            for child_name in children:
                child_eb = edit_bones.get(child_name)
                if child_eb:
                    dist = (child_eb.head - eb.head).length
                    if dist > 0.001:
                        min_child_dist = min(min_child_dist, dist)
            if min_child_dist < float('inf'):
                bone_len = min_child_dist * 0.8  # 80% of distance to child

        if bone_len < 0.001:
            bone_len = default_bone_len

        native_q = NATIVE_BONE_ROTATIONS.get(eb.name)
        if native_q:
            # Convert native rotation: Alchemy -> Blender, game space -> armature
            q_blender = Quaternion(native_q).conjugated()
            q_armature = rz90_inv @ q_blender
            rot_mat = q_armature.to_matrix()

            # Bone Y axis = tail direction in Blender convention
            bone_dir = rot_mat @ Vector((0, 1, 0))
            eb.tail = eb.head + bone_dir * bone_len
        else:
            # Non-deforming bones (Bone_000, Bip01, Motion): point upward
            eb.tail = eb.head + Vector((0, 0, bone_len))

    bpy.ops.object.mode_set(mode='OBJECT')


# ============================================================================
# Matrix / Translation Computation
# ============================================================================

def _get_game_rotation(armature_obj):
    """Get the combined rotation that converts bone positions from Blender
    armature-local space to XML2 game space.

    Includes:
    1. Armature world rotation (handles FBX -90° X for Y-up → Z-up)
    2. +90° Z rotation to convert Blender axis convention (X=right, -Y=forward)
       to XML2 game convention (X=forward, Y=left)

    Returns:
        4x4 rotation Matrix.
    """
    import math
    from mathutils import Quaternion

    world_q = armature_obj.matrix_world.to_quaternion()
    rz90 = Quaternion((0, 0, 1), math.radians(90))
    game_q = rz90 @ world_q
    return game_q.to_matrix().to_4x4()


def _compute_bone_rotation_alchemy(bone, game_rot):
    """Derive a bone's bind-pose rotation in Alchemy convention from its rest matrix.

    Used for A-pose export where we can't use the hardcoded T-pose
    NATIVE_BONE_ROTATIONS.  Instead, we compute the rotation from the bone's
    actual rest-pose orientation in game space.

    Args:
        bone: Blender bone (from armature.data.bones).
        game_rot: 4x4 rotation matrix from Blender-local to game space.

    Returns:
        Quaternion in Blender convention (already conjugated from Alchemy).
    """
    from mathutils import Quaternion

    # bone.matrix_local is the bone's rest transform in armature space.
    # Convert to game space and extract the rotation.
    game_mat = game_rot @ bone.matrix_local
    q_blender = game_mat.to_quaternion()
    return q_blender


def _build_native_bind_matrix(name, bone, game_rot, use_native_rotations=True):
    """Build a bind matrix using native XML2 orientation + custom bone position.

    For deforming bones: native rotation + rig's game-space bone position.
    For non-deforming / unmapped: identity rotation at game-space position.

    When use_native_rotations=False (A-pose export), computes the bone's
    actual orientation from its rest matrix instead of using hardcoded
    T-pose quaternions.

    CRITICAL — Quaternion convention:
    NATIVE_BONE_ROTATIONS are in Alchemy convention (conjugate of Blender).
    Must conjugate before use in Blender's Quaternion.to_matrix().

    Args:
        name: XML2 bone name.
        bone: Blender bone.
        game_rot: 4x4 rotation matrix from Blender-local to game space.
        use_native_rotations: If True, use T-pose NATIVE_BONE_ROTATIONS.
                              If False, compute from actual bone orientation.

    Returns:
        4x4 Matrix: bone's bind transform in game space (unscaled).
    """
    from mathutils import Matrix, Quaternion

    bone_pos_game = game_rot @ bone.head_local

    if use_native_rotations:
        native_q = NATIVE_BONE_ROTATIONS.get(name)
        if native_q:
            # Conjugate: Alchemy convention -> Blender convention
            q_blender = Quaternion(native_q).conjugated()
            rot_4x4 = q_blender.to_matrix().to_4x4()
            return Matrix.Translation(bone_pos_game) @ rot_4x4
        else:
            return Matrix.Translation(bone_pos_game)
    else:
        # A-pose: compute rotation from actual bone orientation
        if name in NATIVE_BONE_ROTATIONS:  # only deforming bones
            q_blender = _compute_bone_rotation_alchemy(bone, game_rot)
            rot_4x4 = q_blender.to_matrix().to_4x4()
            return Matrix.Translation(bone_pos_game) @ rot_4x4
        else:
            return Matrix.Translation(bone_pos_game)


def _compute_inv_joint_matrices(armature_obj, use_native_rotations=True):
    """Compute inverse joint matrices from rotations + custom positions.

    For each deforming bone (bm_idx >= 0), builds a bind matrix from:
        - Rotation (native T-pose or computed from actual bone orientation)
        - game_rot @ bone.head_local (rig's own position in game space)

    When use_native_rotations=True (T-pose target), uses NATIVE_BONE_ROTATIONS.
    When use_native_rotations=False (A-pose target), computes from bone rest pose.

    Uses custom bone positions so the skeleton matches the mesh proportions.
    The exporter scales both mesh and skeleton by igb_export_scale.

    Stored in ROW-MAJOR order (Alchemy convention).

    Args:
        armature_obj: Blender armature object.
        use_native_rotations: If True, use T-pose rotations. If False, compute
                              from actual bone orientations (A-pose).

    Returns:
        List of 35 entries (one per XML2 bone), each either a 16-float list
        or None for non-deforming bones.
    """
    n_bones = len(XML2_SKELETON)
    result = [None] * n_bones

    game_rot = _get_game_rotation(armature_obj)

    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        if bm_idx < 0:
            continue

        display_name = name if name else "Bone_000"
        bone = armature_obj.data.bones.get(display_name)
        if bone is None:
            continue

        bind_game = _build_native_bind_matrix(name, bone, game_rot,
                                               use_native_rotations)

        try:
            inv_bind = bind_game.inverted()
        except ValueError:
            continue

        # Convert from Blender column-major to Alchemy row-major (transpose)
        row_major = []
        for row in range(4):
            for col in range(4):
                row_major.append(inv_bind[col][row])

        result[idx] = row_major

    return result


def _compute_bone_translations(armature_obj, use_native_rotations=True):
    """Compute bone translations (parent-local offsets) from custom positions.

    Alchemy FK reconstructs bone world positions as:
        child_world = parent_world_pos + parent_world_rot @ child_translation

    translation = inv(parent_bind) @ child_game_pos  (4D point, take xyz)

    For root bones (parent_idx < 0): translation = game-space world position.

    Args:
        armature_obj: Blender armature object.
        use_native_rotations: If True, use T-pose rotations for parent bind.
                              If False, compute from actual bone orientations.

    Returns:
        List of 35 [x, y, z] lists, indexed by bone index.
    """
    n_bones = len(XML2_SKELETON)
    result = [[0.0, 0.0, 0.0]] * n_bones

    game_rot = _get_game_rotation(armature_obj)

    xml2_by_idx = {}
    for entry_name, entry_idx, entry_parent, entry_bm, entry_flags in XML2_SKELETON:
        xml2_by_idx[entry_idx] = (entry_name, entry_parent)

    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        bone = armature_obj.data.bones.get(display_name)
        if bone is None:
            result[idx] = [0.0, 0.0, 0.0]
            continue

        bone_pos_game = game_rot @ bone.head_local

        if parent_idx < 0:
            result[idx] = [bone_pos_game.x, bone_pos_game.y, bone_pos_game.z]
        else:
            parent_entry = xml2_by_idx.get(parent_idx)
            if parent_entry is None:
                result[idx] = [0.0, 0.0, 0.0]
                continue

            parent_name, _ = parent_entry
            parent_display = parent_name if parent_name else "Bone_000"
            parent_bone = armature_obj.data.bones.get(parent_display)

            if parent_bone is None:
                result[idx] = [0.0, 0.0, 0.0]
                continue

            parent_bind = _build_native_bind_matrix(
                parent_name, parent_bone, game_rot, use_native_rotations)
            try:
                local_pos = parent_bind.inverted() @ bone_pos_game
                result[idx] = [local_pos.x, local_pos.y, local_pos.z]
            except ValueError:
                parent_pos_game = game_rot @ parent_bone.head_local
                delta = bone_pos_game - parent_pos_game
                result[idx] = [delta.x, delta.y, delta.z]

    return result


def _force_native_root_translations(translations, export_scale):
    """Force non-deforming root bone translations to native game-unit values.

    The game engine expects Bip01 at a very specific Z height (~41.82 game
    units / "inches").  This is required for correct character select menu
    positioning, head-tracking, optic beams, and animation compatibility.

    Pelvis (idx 2) is recomputed so that its FK-reconstructed position still
    matches the model's actual pelvis height (preserving inv_bind consistency).

    Translations are stored UNSCALED here — the exporter multiplies by
    export_scale later.  So we store native_game_units / export_scale.

    Args:
        translations: list of 35 [x,y,z] — modified in place.
        export_scale: float (e.g. 66.79).
    """
    if export_scale < 0.001:
        return

    # Current Pelvis game-space position via FK (unscaled).
    # FK chain: Root(0,0,0) -> Bip01 at T[1] -> Pelvis at T[1]+T[2]
    # (Bip01 has no native rotation, so identity applies.)
    pelvis_unscaled = [translations[1][i] + translations[2][i] for i in range(3)]

    # Force Bip01 (idx 1) and Motion (idx 34) to exact native values.
    native_t1 = NATIVE_TRANSLATIONS[1]    # [0.6898, 0, 41.8195] game units
    native_t34 = NATIVE_TRANSLATIONS[34]  # [0.6898, 0, 0.1495]  game units

    translations[0] = [0.0, 0.0, 0.0]
    translations[1] = [v / export_scale for v in native_t1]
    translations[34] = [v / export_scale for v in native_t34]

    # Recompute Pelvis (idx 2) as offset from forced Bip01 so that the
    # FK chain still reconstructs the actual pelvis position.
    translations[2] = [pelvis_unscaled[i] - translations[1][i] for i in range(3)]


# ============================================================================
# Custom Property Storage
# ============================================================================

def _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game='XML2'):
    """Store all IGB skeleton custom properties on the armature.

    This makes the armature compatible with the from-scratch skin exporter.
    Uses the target game's skeleton definition (XML2=35 bones, MUA=49 bones).
    """
    import bpy

    skeleton = get_skeleton_for_game(target_game)
    joint_count = sum(1 for _, _, _, bm, _ in skeleton if bm >= 0)

    # Skeleton-level properties
    armature_obj["igb_skin_skeleton_name"] = ""
    armature_obj["igb_skin_joint_count"] = joint_count
    armature_obj["igb_bone_count"] = len(skeleton)

    # Bone translations (indexed by bone index)
    # translations is a list from _compute_bone_translations (35 entries for XML2).
    # For MUA, extend with zeros for FX bone indices (35-48).
    n_skel = len(skeleton)
    extended_trans = list(translations)  # copy
    while len(extended_trans) < n_skel:
        extended_trans.append([0.0, 0.0, 0.0])
    armature_obj["igb_skin_bone_translations"] = json.dumps(extended_trans)

    # Inverse joint matrices (indexed by bone index)
    # FX bones have bm_idx=-1 so they don't get inv_joint entries.
    # Extend with None for FX bone indices.
    extended_inv = list(inv_matrices)  # copy
    while len(extended_inv) < n_skel:
        extended_inv.append(None)
    armature_obj["igb_skin_inv_joint_matrices"] = json.dumps(extended_inv)

    # Complete bone info list (the authoritative source for export)
    bone_info_list = []
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        bone_info_list.append({
            'name': name,
            'index': idx,
            'parent_idx': parent_idx,
            'bm_idx': bm_idx,
            'flags': flags,
        })
    armature_obj["igb_skin_bone_info_list"] = json.dumps(bone_info_list)

    # BMS palette: identity [0, 1, 2, ..., 31] for 32 deforming bones
    bms_palette = list(range(joint_count))
    armature_obj["igb_bms_palette"] = json.dumps(bms_palette)

    # Flag: rig was converted from non-XML2 convention.  The exporter uses
    # this to apply the 90° Z axis rotation to mesh vertices (converting
    # Blender convention to XML2 game convention).  Skeleton data is computed
    # with the same rotation baked in, so it just needs the normal export scale.
    armature_obj["igb_converted_rig"] = True

    # Per-bone metadata on pose bones
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        if display_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[display_name]
            pb["igb_bone_index"] = idx
            pb["igb_parent_idx"] = parent_idx
            pb["igb_skin_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_flags"] = flags
            # Tag FX bones for easy identification
            if name in MUA_FX_BONE_NAMES:
                pb["igb_fx_bone"] = True
