"""PropertyGroups for the IGB Actors panel."""

import bpy
from bpy.props import (
    StringProperty, BoolProperty, IntProperty,
    CollectionProperty, FloatProperty, EnumProperty,
)
from bpy.types import PropertyGroup


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
