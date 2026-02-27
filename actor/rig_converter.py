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
# Bone Name Mapping: Unity Humanoid -> XML2
# ============================================================================

# Maps various Unity Humanoid naming conventions to XML2 Bip01 names.
# Includes both spaced ("Left UpperArm") and camelCase ("LeftUpperArm")
# variants, plus common aliases.
UNITY_TO_XML2 = {}

_UNITY_MAPPING_RAW = [
    # Spine chain
    (["Hips", "hips"],                                        "Bip01 Pelvis"),
    (["Spine", "spine"],                                      "Bip01 Spine"),
    (["Chest", "chest"],                                      "Bip01 Spine1"),
    (["UpperChest", "Upper Chest", "upperchest", "upper_chest"], "Bip01 Spine2"),
    (["Neck", "neck"],                                        "Bip01 Neck"),
    (["Head", "head"],                                        "Bip01 Head"),

    # Left arm — Unity Humanoid + VRChat naming
    (["LeftShoulder", "Left Shoulder", "Left shoulder",
      "leftshoulder", "left_shoulder", "L Shoulder"],         "Bip01 L Clavicle"),
    (["LeftUpperArm", "Left UpperArm", "Left Upper Arm",
      "leftupperarm", "left_upper_arm", "L UpperArm",
      "Left arm"],                                            "Bip01 L UpperArm"),
    (["LeftLowerArm", "Left LowerArm", "Left Lower Arm",
      "leftlowerarm", "left_lower_arm", "L LowerArm",
      "LeftForeArm", "Left ForeArm",
      "Left elbow"],                                          "Bip01 L Forearm"),
    (["LeftHand", "Left Hand", "lefthand", "left_hand",
      "L Hand", "Left wrist"],                                "Bip01 L Hand"),

    # Left fingers — XML2 Finger0/01 = thumb, Finger1/11 = middle finger
    # Thumb → Finger0 / Finger01
    (["LeftThumbProximal", "Left Thumb Proximal",
      "leftthumbproximal", "left_thumb_proximal",
      "L Thumb1", "LeftThumb1",
      "Thumb0_L"],                                            "Bip01 L Finger0"),
    (["LeftThumbIntermediate", "Left Thumb Intermediate",
      "leftthumbintermediate", "left_thumb_intermediate",
      "L Thumb2", "LeftThumb2",
      "Thumb1_L"],                                            "Bip01 L Finger01"),
    # Middle finger → Finger1 / Finger11 (center finger = best representative)
    (["LeftMiddleProximal", "Left Middle Proximal",
      "leftmiddleproximal", "left_middle_proximal",
      "MiddleFinger1_L"],                                     "Bip01 L Finger1"),
    (["LeftMiddleIntermediate", "Left Middle Intermediate",
      "leftmiddleintermediate", "left_middle_intermediate",
      "MiddleFinger2_L"],                                     "Bip01 L Finger11"),

    # Right arm — Unity Humanoid + VRChat naming
    (["RightShoulder", "Right Shoulder", "Right shoulder",
      "rightshoulder", "right_shoulder", "R Shoulder"],       "Bip01 R Clavicle"),
    (["RightUpperArm", "Right UpperArm", "Right Upper Arm",
      "rightupperarm", "right_upper_arm", "R UpperArm",
      "Right arm"],                                           "Bip01 R UpperArm"),
    (["RightLowerArm", "Right LowerArm", "Right Lower Arm",
      "rightlowerarm", "right_lower_arm", "R LowerArm",
      "RightForeArm", "Right ForeArm",
      "Right elbow"],                                         "Bip01 R Forearm"),
    (["RightHand", "Right Hand", "righthand", "right_hand",
      "R Hand", "Right wrist"],                               "Bip01 R Hand"),

    # Right fingers — XML2 Finger0/01 = thumb, Finger1/11 = middle finger
    # Thumb → Finger0 / Finger01
    (["RightThumbProximal", "Right Thumb Proximal",
      "rightthumbproximal", "right_thumb_proximal",
      "R Thumb1", "RightThumb1",
      "Thumb0_R"],                                            "Bip01 R Finger0"),
    (["RightThumbIntermediate", "Right Thumb Intermediate",
      "rightthumbintermediate", "right_thumb_intermediate",
      "R Thumb2", "RightThumb2",
      "Thumb1_R"],                                            "Bip01 R Finger01"),
    # Middle finger → Finger1 / Finger11 (center finger = best representative)
    (["RightMiddleProximal", "Right Middle Proximal",
      "rightmiddleproximal", "right_middle_proximal",
      "MiddleFinger1_R"],                                     "Bip01 R Finger1"),
    (["RightMiddleIntermediate", "Right Middle Intermediate",
      "rightmiddleintermediate", "right_middle_intermediate",
      "MiddleFinger2_R"],                                     "Bip01 R Finger11"),

    # Left leg — Unity Humanoid + VRChat naming
    (["LeftUpperLeg", "Left UpperLeg", "Left Upper Leg",
      "leftupperleg", "left_upper_leg", "L UpperLeg",
      "LeftThigh", "Left Thigh",
      "Left leg"],                                            "Bip01 L Thigh"),
    (["LeftLowerLeg", "Left LowerLeg", "Left Lower Leg",
      "leftlowerleg", "left_lower_leg", "L LowerLeg",
      "LeftCalf", "Left Calf",
      "Left knee"],                                           "Bip01 L Calf"),
    (["LeftFoot", "Left Foot", "leftfoot", "left_foot",
      "L Foot", "Left ankle"],                                "Bip01 L Foot"),
    (["LeftToes", "Left Toes", "lefttoes", "left_toes",
      "L Toe", "LeftToe", "Left Toe",
      "Left toe"],                                            "Bip01 L Toe0"),

    # Right leg — Unity Humanoid + VRChat naming
    (["RightUpperLeg", "Right UpperLeg", "Right Upper Leg",
      "rightupperleg", "right_upper_leg", "R UpperLeg",
      "RightThigh", "Right Thigh",
      "Right leg"],                                           "Bip01 R Thigh"),
    (["RightLowerLeg", "Right LowerLeg", "Right Lower Leg",
      "rightlowerleg", "right_lower_leg", "R LowerLeg",
      "RightCalf", "Right Calf",
      "Right knee"],                                          "Bip01 R Calf"),
    (["RightFoot", "Right Foot", "rightfoot", "right_foot",
      "R Foot", "Right ankle"],                               "Bip01 R Foot"),
    (["RightToes", "Right Toes", "righttoes", "right_toes",
      "R Toe", "RightToe", "Right Toe",
      "Right toe"],                                           "Bip01 R Toe0"),
]

# Build the flat lookup dict
for aliases, xml2_name in _UNITY_MAPPING_RAW:
    for alias in aliases:
        UNITY_TO_XML2[alias] = xml2_name

# Bones whose vertex weights should merge INTO the mapped target when
# the source bone doesn't have a direct XML2 equivalent.
# Maps Unity bone name aliases -> XML2 bone to merge weights into.
MERGE_WEIGHT_TARGETS = {}

_MERGE_RAW = [
    # --- LEFT thumb distal -> merge into L Finger01 ---
    (["LeftThumbDistal", "Left Thumb Distal",
      "Thumb2_L"],                                            "Bip01 L Finger01"),

    # --- LEFT proximal finger bones (index/ring/pinky) -> merge into L Finger1 ---
    (["LeftIndexProximal", "Left Index Proximal",
      "leftindexproximal", "left_index_proximal",
      "L Index1", "LeftIndex1", "IndexFinger1_L",
      "LeftRingProximal", "Left Ring Proximal",
      "leftringproximal", "left_ring_proximal",
      "RingFinger1_L",
      "LeftLittleProximal", "Left Little Proximal",
      "leftlittleproximal", "left_little_proximal",
      "LittleFinger1_L"],                                     "Bip01 L Finger1"),

    # --- LEFT intermediate/distal finger bones -> merge into L Finger11 ---
    (["LeftIndexIntermediate", "Left Index Intermediate",
      "leftindexintermediate", "left_index_intermediate",
      "L Index2", "LeftIndex2", "IndexFinger2_L",
      "LeftIndexDistal", "Left Index Distal",
      "IndexFinger3_L",
      "LeftMiddleDistal", "Left Middle Distal",
      "MiddleFinger3_L",
      "LeftRingIntermediate", "Left Ring Intermediate",
      "RingFinger2_L",
      "LeftRingDistal", "Left Ring Distal",
      "RingFinger3_L",
      "LeftLittleIntermediate", "Left Little Intermediate",
      "LittleFinger2_L",
      "LeftLittleDistal", "Left Little Distal",
      "LittleFinger3_L"],                                     "Bip01 L Finger11"),

    # --- RIGHT thumb distal -> merge into R Finger01 ---
    (["RightThumbDistal", "Right Thumb Distal",
      "Thumb2_R"],                                            "Bip01 R Finger01"),

    # --- RIGHT proximal finger bones (index/ring/pinky) -> merge into R Finger1 ---
    (["RightIndexProximal", "Right Index Proximal",
      "rightindexproximal", "right_index_proximal",
      "R Index1", "RightIndex1", "IndexFinger1_R",
      "RightRingProximal", "Right Ring Proximal",
      "rightringproximal", "right_ring_proximal",
      "RingFinger1_R",
      "RightLittleProximal", "Right Little Proximal",
      "rightlittleproximal", "right_little_proximal",
      "LittleFinger1_R"],                                     "Bip01 R Finger1"),

    # --- RIGHT intermediate/distal finger bones -> merge into R Finger11 ---
    (["RightIndexIntermediate", "Right Index Intermediate",
      "rightindexintermediate", "right_index_intermediate",
      "R Index2", "RightIndex2", "IndexFinger2_R",
      "RightIndexDistal", "Right Index Distal",
      "IndexFinger3_R",
      "RightMiddleDistal", "Right Middle Distal",
      "MiddleFinger3_R",
      "RightRingIntermediate", "Right Ring Intermediate",
      "RingFinger2_R",
      "RightRingDistal", "Right Ring Distal",
      "RingFinger3_R",
      "RightLittleIntermediate", "Right Little Intermediate",
      "LittleFinger2_R",
      "RightLittleDistal", "Right Little Distal",
      "LittleFinger3_R"],                                     "Bip01 R Finger11"),

    # Eye / Jaw / Hair physics -> merge into Head
    (["LeftEye", "Left Eye", "RightEye", "Right Eye",
      "Eye_L", "Eye_R",
      "Jaw", "jaw"],                                          "Bip01 Head"),

    # Toe roots -> merge into Foot
    (["ToeRoot_L"],                                           "Bip01 L Foot"),
    (["ToeRoot_R"],                                           "Bip01 R Foot"),
]

for aliases, target in _MERGE_RAW:
    for alias in aliases:
        MERGE_WEIGHT_TARGETS[alias] = target

# Mixamo prefix
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
# Profile Detection
# ============================================================================

def detect_rig_profile(armature_obj):
    """Detect the source rig naming convention.

    Supports:
    - Mixamo (bones prefixed with 'mixamorig:')
    - Unity Humanoid standard (LeftUpperArm, RightFoot, etc.)
    - VRChat / Unity short form (Left arm, Left elbow, Thumb0_L, etc.)

    Args:
        armature_obj: Blender armature object.

    Returns:
        'mixamo', 'unity', or None (unknown).
    """
    bone_names = [b.name for b in armature_obj.data.bones]
    bone_name_set = set(bone_names)

    # Check for Mixamo prefix
    mixamo_count = sum(1 for n in bone_names if n.startswith(MIXAMO_PREFIX))
    if mixamo_count >= 3:
        return 'mixamo'

    # Check for Unity Humanoid standard names
    unity_indicators = {'Hips', 'Spine', 'Head', 'LeftUpperArm', 'RightUpperArm',
                        'LeftHand', 'RightHand', 'LeftFoot', 'RightFoot',
                        'LeftUpperLeg', 'RightUpperLeg'}
    unity_count = sum(1 for n in bone_name_set if n in unity_indicators)
    if unity_count >= 3:
        return 'unity'

    # Check for VRChat / Unity short-form names
    # These are all in the UNITY_TO_XML2 mapping, so we can check if any
    # bone name matches a known alias.
    vrchat_indicators = {'Left arm', 'Right arm', 'Left elbow', 'Right elbow',
                         'Left wrist', 'Right wrist', 'Left leg', 'Right leg',
                         'Left knee', 'Right knee', 'Left ankle', 'Right ankle',
                         'Left shoulder', 'Right shoulder', 'Left toe', 'Right toe',
                         'Thumb0_L', 'Thumb0_R', 'IndexFinger1_L', 'IndexFinger1_R'}
    vrchat_count = sum(1 for n in bone_name_set if n in vrchat_indicators)
    if vrchat_count >= 3:
        return 'unity'  # Use 'unity' profile — same lookup table

    # Broad fallback: check if ANY bone name is in the UNITY_TO_XML2 dict
    matched = sum(1 for n in bone_name_set if n in UNITY_TO_XML2)
    if matched >= 5:
        return 'unity'

    return None


# ============================================================================
# Bone Rename Map Builder
# ============================================================================

def build_rename_map(armature_obj, profile):
    """Build a mapping from current bone names to XML2 bone names.

    Args:
        armature_obj: Blender armature object.
        profile: 'unity', 'mixamo', or 'auto'.

    Returns:
        Dict mapping old_name -> new_name for bones that can be mapped.
    """
    rename_map = {}
    bone_names = [b.name for b in armature_obj.data.bones]

    for old_name in bone_names:
        # Strip Mixamo prefix if applicable
        if profile == 'mixamo' and old_name.startswith(MIXAMO_PREFIX):
            stripped = old_name[len(MIXAMO_PREFIX):]
        else:
            stripped = old_name

        # Look up in Unity -> XML2 mapping
        xml2_name = UNITY_TO_XML2.get(stripped)
        if xml2_name:
            rename_map[old_name] = xml2_name

    return rename_map


def build_merge_map(armature_obj, profile, rename_map):
    """Build a mapping for bones whose weights should merge into a target.

    Args:
        armature_obj: Blender armature object.
        profile: 'unity' or 'mixamo'.
        rename_map: Already-built rename map (to exclude already-mapped bones).

    Returns:
        Dict mapping old_bone_name -> xml2_target_name for weight merging.
    """
    merge_map = {}
    bone_names = [b.name for b in armature_obj.data.bones]

    for old_name in bone_names:
        if old_name in rename_map:
            continue  # Already directly mapped

        # Strip Mixamo prefix
        if profile == 'mixamo' and old_name.startswith(MIXAMO_PREFIX):
            stripped = old_name[len(MIXAMO_PREFIX):]
        else:
            stripped = old_name

        # Check explicit merge targets first
        target = MERGE_WEIGHT_TARGETS.get(stripped)
        if target:
            merge_map[old_name] = target
            continue

        # Pattern-based merging for VRChat dynamic bones
        # Hair physics chains (J_Sec_Hair*, J_Bip_Hair*, Hair_*, etc.)
        lower = stripped.lower()
        if ('hair' in lower or 'j_sec_' in lower or 'j_bip_' in lower
                or 'j_adj_' in lower):
            merge_map[old_name] = "Bip01 Head"
            continue

        # Any remaining breast/chest physics bones -> Spine2
        if 'breast' in lower or 'bust' in lower:
            merge_map[old_name] = "Bip01 Spine2"
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
                target_pose='T_POSE'):
    """Convert an armature from Unity/Mixamo naming to XML2 Bip01 convention.

    This is the main entry point. It:
    0. Applies object-level rotation/scale and optionally auto-scales
    1. Detects or uses the specified rig profile
    2. Renames bones and vertex groups
    3. Merges extra vertex weights into nearest mapped bones
    4. Removes unmapped bones
    5. Creates missing XML2 bones as dummies
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

    # ---- 1. Detect profile ----
    if profile == 'AUTO':
        detected = detect_rig_profile(armature_obj)
        if detected is None:
            return {'success': False,
                    'error': "Could not detect rig type. Select Unity or Mixamo manually.",
                    'mapped': 0, 'added': 0, 'removed': 0}
        profile = detected
    else:
        profile = profile.lower()

    # ---- 2. Build rename and merge maps ----
    rename_map = build_rename_map(armature_obj, profile)
    merge_map = build_merge_map(armature_obj, profile, rename_map)

    if not rename_map:
        return {'success': False,
                'error': f"No bones matched the {profile} naming convention.",
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
    all_xml2_names = {entry[0] for entry in XML2_SKELETON if entry[0]}
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

    # Remove unmapped bones (those not renamed to an XML2 name)
    current_names = {eb.name for eb in edit_bones}
    bones_to_delete = []
    for eb in edit_bones:
        if eb.name not in all_xml2_names and eb.name not in {"", "Bone_000"}:
            bones_to_delete.append(eb.name)

    for name in bones_to_delete:
        eb = edit_bones.get(name)
        if eb:
            edit_bones.remove(eb)
            removed_count += 1

    # ---- 6. Create missing XML2 bones ----
    added_count = 0
    current_names = {eb.name for eb in edit_bones}

    # Build parent name lookup from XML2_SKELETON
    xml2_by_name = {}
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        parent_name = XML2_SKELETON[parent_idx][0] if parent_idx >= 0 else None
        xml2_by_name[name] = (idx, parent_idx, parent_name, bm_idx, flags)

    # Helper: compute a small bone length based on existing bones
    bone_lengths = [eb.length for eb in edit_bones if eb.length > 0.001]
    avg_bone_len = sum(bone_lengths) / len(bone_lengths) if bone_lengths else 0.05

    # Process in index order so parents are created before children
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
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
            parent_bone_name = XML2_SKELETON[parent_idx][0]
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

    # Fix parent relationships for ALL XML2 bones (some renamed ones may
    # need re-parenting to match the XML2 hierarchy)
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue

        if parent_idx >= 0:
            parent_name = XML2_SKELETON[parent_idx][0]
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
    _store_skeleton_properties(armature_obj, inv_matrices, translations)

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


def setup_bip01_rig(armature_obj, auto_scale=True, target_height=68.0):
    """Setup an existing Bip01 rig for IGB skin export.

    Unlike convert_rig(), this does NOT rename or reparent bones — it assumes
    the armature already uses XML2 bone naming (Bip01, Bip01 Pelvis, etc.).
    It creates any missing XML2 bones, fixes the parent hierarchy, computes
    inverse joint matrices and bone translations, and stores all required
    custom properties for the skin exporter.

    Use this for:
    - Native XML2 rigs from other importers or manual creation
    - Rigs that already have Bip01 naming but lack IGB export properties
    - Re-setting up a rig whose properties were lost or need refreshing

    Args:
        armature_obj: Blender armature object with XML2 bone names.
        auto_scale: If True, auto-scale rig to match XML2 character proportions.
        target_height: Target character height in game units (default ~68).

    Returns:
        dict with keys: success (bool), added (int), error (str or None).
    """
    import bpy
    from mathutils import Vector

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {'success': False, 'added': 0,
                'error': "No armature selected"}

    # Check that it actually has Bip01
    if "Bip01" not in armature_obj.data.bones:
        return {'success': False, 'added': 0,
                'error': "Armature has no 'Bip01' bone. "
                         "Use Convert Rig for non-XML2 rigs."}

    # ---- 0. Apply object transforms and auto-scale ----
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

    # Create missing XML2 bones
    current_names = {eb.name for eb in edit_bones}
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name in current_names:
            continue

        eb = edit_bones.new(display_name)
        small_len = avg_bone_len * 0.3

        if parent_idx >= 0:
            parent_bone_name = XML2_SKELETON[parent_idx][0]
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

    # Fix parent hierarchy for ALL XML2 bones
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue
        if parent_idx >= 0:
            parent_name = XML2_SKELETON[parent_idx][0]
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

    _store_skeleton_properties(armature_obj, inv_matrices, translations)

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

def _store_skeleton_properties(armature_obj, inv_matrices, translations):
    """Store all IGB skeleton custom properties on the armature.

    This makes the armature compatible with the from-scratch skin exporter.
    """
    import bpy

    # Skeleton-level properties
    armature_obj["igb_skin_skeleton_name"] = ""
    armature_obj["igb_skin_joint_count"] = XML2_JOINT_COUNT
    armature_obj["igb_bone_count"] = len(XML2_SKELETON)

    # Bone translations (indexed by bone index)
    armature_obj["igb_skin_bone_translations"] = json.dumps(translations)

    # Inverse joint matrices (indexed by bone index)
    armature_obj["igb_skin_inv_joint_matrices"] = json.dumps(inv_matrices)

    # Complete bone info list (the authoritative source for export)
    bone_info_list = []
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        bone_info_list.append({
            'name': name,
            'index': idx,
            'parent_idx': parent_idx,
            'bm_idx': bm_idx,
            'flags': flags,
        })
    armature_obj["igb_skin_bone_info_list"] = json.dumps(bone_info_list)

    # BMS palette: identity [0, 1, 2, ..., 31] for 32 deforming bones
    bms_palette = list(range(XML2_JOINT_COUNT))
    armature_obj["igb_bms_palette"] = json.dumps(bms_palette)

    # Flag: rig was converted from non-XML2 convention.  The exporter uses
    # this to apply the 90° Z axis rotation to mesh vertices (converting
    # Blender convention to XML2 game convention).  Skeleton data is computed
    # with the same rotation baked in, so it just needs the normal export scale.
    armature_obj["igb_converted_rig"] = True

    # Per-bone metadata on pose bones
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[display_name]
            pb["igb_bone_index"] = idx
            pb["igb_parent_idx"] = parent_idx
            pb["igb_skin_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_flags"] = flags
