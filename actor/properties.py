"""PropertyGroups for the IGB Actors panel."""

import bpy
from bpy.props import (
    StringProperty, BoolProperty, IntProperty,
    CollectionProperty, FloatProperty, EnumProperty,
)
from bpy.types import PropertyGroup


# ---------------------------------------------------------------------------
# Per-armature state persistence
# ---------------------------------------------------------------------------
# Stores skins/animations lists keyed by armature name, so switching
# between actors preserves each one's panel state.
_armature_state = {}  # { armature_name: { 'skins': [...], 'skins_index': int,
                       #                    'animations': [...], 'animations_index': int } }

# Simple module-level string tracking the previous armature name.
# (Can't use id(self) because Blender re-wraps PropertyGroups on each access.)
_prev_armature = ''


def _save_armature_state(props, armature_name):
    """Snapshot the current skins + animations lists for the given armature."""
    if not armature_name:
        return
    skins_data = []
    for item in props.skins:
        skins_data.append({
            'name': item.name,
            'skin_code': item.skin_code,
            'object_name': item.object_name,
            'filepath': item.filepath,
            'is_visible': item.is_visible,
        })
    anims_data = []
    for item in props.animations:
        anims_data.append({
            'name': item.name,
            'action_name': item.action_name,
            'duration_ms': item.duration_ms,
            'frame_count': item.frame_count,
            'track_count': item.track_count,
        })
    _armature_state[armature_name] = {
        'skins': skins_data,
        'skins_index': props.skins_index,
        'animations': anims_data,
        'animations_index': props.animations_index,
    }


def _restore_armature_state(props, armature_name):
    """Restore skins + animations lists for the given armature (if saved)."""
    state = _armature_state.get(armature_name)
    if state is None:
        # No saved state — clear lists
        props.skins.clear()
        props.skins_index = 0
        props.animations.clear()
        props.animations_index = 0
        return

    props.skins.clear()
    for s in state['skins']:
        item = props.skins.add()
        item.name = s['name']
        item.skin_code = s['skin_code']
        item.object_name = s['object_name']
        item.filepath = s['filepath']
        item.is_visible = s['is_visible']
    props.skins_index = state['skins_index']

    props.animations.clear()
    for a in state['animations']:
        item = props.animations.add()
        # Set action_name FIRST (via internal key) to avoid triggering
        # the name-update callback before action_name is populated.
        item["action_name"] = a['action_name']
        item["name"] = a['name']
        item.duration_ms = a['duration_ms']
        item.frame_count = a['frame_count']
        item.track_count = a['track_count']
    props.animations_index = state['animations_index']


def _on_active_armature_update(self, context):
    """Called when active_armature changes — save old state, restore new."""
    global _prev_armature
    prev = _prev_armature
    new = self.active_armature

    if prev == new:
        return

    # Save the outgoing armature's state
    _save_armature_state(self, prev)

    # Restore the incoming armature's state
    _restore_armature_state(self, new)

    # Track for next transition
    _prev_armature = new


class ACTOR_SkinItem(PropertyGroup):
    """A skin variant in the actor's skin list."""
    name: StringProperty(name="Variant", default="")
    skin_code: StringProperty(name="Skin Code", default="")
    object_name: StringProperty(name="Object", default="")
    filepath: StringProperty(name="File Path", default="", subtype='FILE_PATH')
    is_visible: BoolProperty(name="Visible", default=True)


def _on_anim_name_update(self, context):
    """Sync Blender Action name when animation list name changes."""
    import bpy
    action = bpy.data.actions.get(self.action_name)
    if action is None:
        return
    # Only rename if the new name is non-empty and different
    new_name = self.name.strip()
    if not new_name or new_name == action.name:
        return
    action.name = new_name
    action["igb_anim_name"] = action.name  # Blender may add .001 suffix
    self["action_name"] = action.name  # Update reference without triggering updates


class ACTOR_AnimItem(PropertyGroup):
    """An animation in the actor's animation list."""
    name: StringProperty(name="Name", default="", update=_on_anim_name_update)
    action_name: StringProperty(name="Action", default="")
    duration_ms: FloatProperty(name="Duration (ms)", default=0.0)
    frame_count: IntProperty(name="Frames", default=0)
    track_count: IntProperty(name="Tracks", default=0)


class ACTOR_SceneProperties(PropertyGroup):
    """Scene-level properties for the actor system."""
    game_dir: StringProperty(
        name="Game Directory",
        description="Root game data directory (contains actors/ and data/ folders)",
        subtype='DIR_PATH',
        default="",
    )

    anim_file: StringProperty(
        name="Animation File",
        description="Path to the character animation .igb file",
        subtype='FILE_PATH',
        default="",
    )

    character_search: StringProperty(
        name="Search",
        description="Search characters by name",
        default="",
    )

    active_armature: StringProperty(
        name="Active Armature",
        description="Name of the currently active actor armature",
        default="",
        update=_on_active_armature_update,
    )

    skins: CollectionProperty(type=ACTOR_SkinItem)
    skins_index: IntProperty(name="Active Skin", default=0)

    animations: CollectionProperty(type=ACTOR_AnimItem)
    animations_index: IntProperty(name="Active Animation", default=0)

    import_skins: BoolProperty(
        name="Import Skins",
        description="Import skin geometry files",
        default=True,
    )

    import_animations: BoolProperty(
        name="Import Animations",
        description="Import animation data",
        default=True,
    )

    import_materials: BoolProperty(
        name="Import Materials",
        description="Import materials and textures on skins",
        default=True,
    )

    game_preset: EnumProperty(
        name="Game",
        description="Game profile for correct texture colors and bone conventions",
        items=[
            ('auto', "Auto-Detect", "Detect game from file metadata"),
            ('xml2_pc', "XML2 PC", "X-Men Legends II PC"),
            ('mua_pc', "MUA PC", "Marvel Ultimate Alliance PC (swap R/B)"),
        ],
        default='auto',
    )


_classes = (
    ACTOR_SkinItem,
    ACTOR_AnimItem,
    ACTOR_SceneProperties,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.igb_actor = bpy.props.PointerProperty(type=ACTOR_SceneProperties)


def unregister():
    del bpy.types.Scene.igb_actor
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
