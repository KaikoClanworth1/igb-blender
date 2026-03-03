"""PropertyGroups for Menu Editor — stored on bpy.types.Scene."""

import bpy
from bpy.props import (
    StringProperty, IntProperty, FloatProperty, BoolProperty,
    EnumProperty, CollectionProperty, PointerProperty,
)
from bpy.types import PropertyGroup


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

MENU_ITEM_TYPES = [
    ('NONE', 'None', 'No type specified'),
    ('MENU_ITEM_MODEL', 'Model', ''),
    ('MENU_ITEM_TEXT', 'Text', ''),
    ('MENU_ITEM_TEXTBOX', 'TextBox', ''),
    ('MENU_ITEM_BAR', 'Bar', ''),
    ('MENU_ITEM_BINARY', 'Binary', ''),
    ('MENU_ITEM_LISTBOX', 'ListBox', ''),
    ('MENU_ITEM_LIST_CYCLE', 'List Cycle', ''),
    ('MENU_ITEM_LIST_ITEMS', 'List Items', ''),
    ('MENU_ITEM_LISTCHARS', 'List Chars', ''),
    ('MENU_ITEM_LISTCODEX', 'List Codex', ''),
    ('MENU_ITEM_CHAR_SUMMARY', 'Char Summary', ''),
    ('MENU_ITEM_SKILLS', 'Skills', ''),
]

MENU_TYPES = [
    ('NONE', 'None', 'No type specified'),
    ('MAIN_MENU', 'Main Menu', ''),
    ('PAUSE_MENU', 'Pause Menu', ''),
    ('OPTIONS_MENU', 'Options Menu', ''),
    ('CODEX_MENU', 'Codex Menu', ''),
    ('AUTOMAP_MENU', 'Automap Menu', ''),
    ('PDA_MENU', 'PDA Menu', ''),
    ('TEAM_MENU', 'Team Menu', ''),
    ('SHOP_MENU', 'Shop Menu', ''),
    ('DANGER_ROOM_MENU', 'Danger Room Menu', ''),
    ('REVIEW_MENU', 'Review Menu', ''),
    ('TRIVIA_MENU', 'Trivia Menu', ''),
    ('LOADING_MENU', 'Loading Menu', ''),
    ('MOVIE_MENU', 'Movie Menu', ''),
    ('CREDITS_MENU', 'Credits Menu', ''),
    ('CAMPAIGN_LOBBY_MENU', 'Campaign Lobby', ''),
    ('SVS_LIST_MENU', 'SVS List Menu', ''),
]

ONFOCUS_TYPES = [
    ('focus', 'Focus', ''),
    ('nofocus', 'No Focus', ''),
    ('list_focus', 'List Focus', ''),
    ('list_up_arrow', 'List Up Arrow', ''),
    ('list_down_arrow', 'List Down Arrow', ''),
]

PRECACHE_TYPES = [
    ('model', 'Model', ''),
    ('texture', 'Texture', ''),
    ('script', 'Script', ''),
    ('sound', 'Sound', ''),
    ('fx', 'FX', ''),
    ('xml', 'XML', ''),
    ('xml_resident', 'XML Resident', ''),
]


# ---------------------------------------------------------------------------
# Sub-structures
# ---------------------------------------------------------------------------

class ME_ItemAttr(PropertyGroup):
    """Arbitrary key-value attribute for non-standard item properties."""
    key: StringProperty(name="Key")
    value: StringProperty(name="Value")


class ME_OnFocus(PropertyGroup):
    """Focus/unfocus behavior entry on an item."""
    focus_type: EnumProperty(
        name="Type",
        items=ONFOCUS_TYPES,
        default='focus',
    )
    target_item: StringProperty(
        name="Target Item",
        description="Item name to apply the model/effect to",
    )
    model_ref: StringProperty(
        name="Model",
        description="IGB model path for focus/nofocus state",
    )
    loop_value: StringProperty(
        name="Loop",
        description="Animation loop timing",
    )


class ME_AnimMark(PropertyGroup):
    """Single keyframe in an animtext sequence."""
    alpha: FloatProperty(name="Alpha", min=0.0, max=1.0, default=0.0)
    time: FloatProperty(name="Time", min=0.0, default=0.0)


class ME_AnimText(PropertyGroup):
    """Animation keyframes for a named menu element."""
    anim_name: StringProperty(name="Name")
    marks: CollectionProperty(type=ME_AnimMark)


class ME_Precache(PropertyGroup):
    """Resource preloading entry."""
    filename: StringProperty(name="Filename")
    precache_type: EnumProperty(
        name="Type",
        items=PRECACHE_TYPES,
        default='model',
    )


# ---------------------------------------------------------------------------
# Main item PropertyGroup
# ---------------------------------------------------------------------------

class ME_Item(PropertyGroup):
    """One menu item. Links to a Blender Empty via object_name."""

    item_name: StringProperty(
        name="Name",
        description="Unique item identifier (must match IGB transform name)",
    )
    item_type: EnumProperty(
        name="Type",
        items=MENU_ITEM_TYPES,
        default='NONE',
    )
    model_ref: StringProperty(
        name="Model",
        description="IGB model path (e.g. ui/models/m_pda_option_screen)",
    )
    text: StringProperty(
        name="Text",
        description="Display text or localization key",
    )
    style: StringProperty(
        name="Style",
        description="Text style enum (e.g. STYLE_MENU_WHITE_MED)",
    )
    usecmd: StringProperty(
        name="Command",
        description="Lua command on selection (e.g. 'newgame', 'openmenu options')",
    )

    # Navigation
    nav_up: StringProperty(name="Up", description="Item name above")
    nav_down: StringProperty(name="Down", description="Item name below")
    nav_left: StringProperty(name="Left", description="Item name to the left")
    nav_right: StringProperty(name="Right", description="Item name to the right")

    # Flags
    neverfocus: BoolProperty(name="Never Focus", default=False)
    startactive: BoolProperty(name="Start Active", default=False)
    animate: BoolProperty(name="Animate", default=False)
    debug_only: BoolProperty(name="Debug Only", default=False)

    # Other common attrs
    animtext_scene: StringProperty(name="AnimText Scene")
    mode: StringProperty(name="Mode")

    # Blender linkage
    object_name: StringProperty(
        name="Object",
        description="Blender Empty object name",
    )
    has_transform: BoolProperty(
        name="Has Transform",
        description="Whether an igTransform was found in the layout IGB",
        default=False,
    )

    # Sub-structures
    onfocus_entries: CollectionProperty(type=ME_OnFocus)
    onfocus_index: IntProperty(default=0)
    extra_attrs: CollectionProperty(type=ME_ItemAttr)
    extra_attrs_index: IntProperty(default=0)


# ---------------------------------------------------------------------------
# Scene-level settings
# ---------------------------------------------------------------------------

class ME_Settings(PropertyGroup):
    """Menu Editor settings stored on the scene."""

    engb_path: StringProperty(
        name="ENGB Path",
        description="Path to the loaded menu ENGB file",
        subtype='FILE_PATH',
    )
    game_dir: StringProperty(
        name="Game Data Directory",
        description="Root game data folder (contains ui/menus/)",
        subtype='DIR_PATH',
    )
    igb_ref: StringProperty(
        name="IGB Ref",
        description="Layout IGB reference from ENGB igb= attribute",
    )
    menu_type: EnumProperty(
        name="Menu Type",
        items=MENU_TYPES,
        default='NONE',
    )
    is_loaded: BoolProperty(
        name="Loaded",
        default=False,
    )

    # Lossless JSON storage of the full ENGB element tree
    engb_json: StringProperty(
        name="ENGB JSON",
        description="Full ENGB XML tree serialized as JSON for lossless round-trip",
    )

    # View options
    show_background: BoolProperty(
        name="Show Background",
        description="Show background IGB geometry",
        default=True,
    )
    show_models: BoolProperty(
        name="Show Item Models",
        description="Show model meshes on item empties",
        default=True,
    )
    show_nav_arrows: BoolProperty(
        name="Show Nav Arrows",
        description="Show navigation link arrows between items",
        default=False,
    )
    show_debug_items: BoolProperty(
        name="Show Debug Items",
        description="Show items marked as debug-only",
        default=False,
    )
    item_search: StringProperty(
        name="Search",
        description="Filter items by name",
    )

    # Export options
    patch_igb_transforms: BoolProperty(
        name="Patch IGB Transforms",
        description="Write moved item positions into the layout IGB on deploy. "
                    "Only enable if you actually repositioned items",
        default=False,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    ME_ItemAttr,
    ME_OnFocus,
    ME_AnimMark,
    ME_AnimText,
    ME_Precache,
    ME_Item,
    ME_Settings,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.menu_settings = PointerProperty(type=ME_Settings)
    bpy.types.Scene.menu_items = CollectionProperty(type=ME_Item)
    bpy.types.Scene.menu_items_index = IntProperty(default=0)
    bpy.types.Scene.menu_animtexts = CollectionProperty(type=ME_AnimText)
    bpy.types.Scene.menu_precaches = CollectionProperty(type=ME_Precache)
    bpy.types.Scene.menu_precaches_index = IntProperty(default=0)


def unregister():
    del bpy.types.Scene.menu_precaches_index
    del bpy.types.Scene.menu_precaches
    del bpy.types.Scene.menu_animtexts
    del bpy.types.Scene.menu_items_index
    del bpy.types.Scene.menu_items
    del bpy.types.Scene.menu_settings

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
