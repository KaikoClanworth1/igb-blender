"""PropertyGroups for Map Maker Tools — stored on bpy.types.Scene."""

import bpy
from bpy.props import (
    StringProperty, IntProperty, FloatProperty, BoolProperty,
    EnumProperty, FloatVectorProperty, CollectionProperty, PointerProperty,
)
from bpy.types import PropertyGroup

from .entity_defs import ENTITY_CLASSNAMES, PRECACHE_TYPES


# ---------------------------------------------------------------------------
# Entity property (key-value pair for advanced/custom attributes)
# ---------------------------------------------------------------------------

class MM_EntityProperty(PropertyGroup):
    """Single key=value property on an entity definition."""
    key: StringProperty(name="Key")
    value: StringProperty(name="Value")


# ---------------------------------------------------------------------------
# Entity definition (template)
# ---------------------------------------------------------------------------

class MM_EntityDef(PropertyGroup):
    """An entity definition (template). Maps to one <entity> element in ENGB."""

    entity_name: StringProperty(
        name="Name",
        description="Unique entity name (e.g. 'sp_beast01', 'player_start_all')",
        default="new_entity",
    )

    classname: EnumProperty(
        name="Class",
        description="Entity classname",
        items=ENTITY_CLASSNAMES,
        default='ent',
    )

    # --- Common dedicated fields for UI convenience ---
    character: StringProperty(
        name="Character",
        description="Character name (for monsterspawnerent, from npcstat/herostat)",
    )
    monster_name: StringProperty(
        name="Monster Name",
        description="Display name for NPC interaction prompts (lowercase, e.g. 'cyclops')",
    )
    model: StringProperty(
        name="Model",
        description="Model path (for physent/doorent, e.g. 'sanctuary/bridge_spire_san_e')",
    )
    nocollide: BoolProperty(
        name="No Collide",
        description="Disable collision for this entity",
        default=False,
    )

    # --- Overflow: arbitrary key-value pairs ---
    properties: CollectionProperty(type=MM_EntityProperty)
    properties_index: IntProperty()


# ---------------------------------------------------------------------------
# Precache entry
# ---------------------------------------------------------------------------

class MM_PrecacheEntry(PropertyGroup):
    """A precache entry. Maps to <precache filename='...' type='...' />."""
    filename: StringProperty(
        name="Filename",
        description="Resource path (e.g. 'act1/sanctuary/1_sanctuary1_0010')",
    )
    entry_type: EnumProperty(
        name="Type",
        description="Precache resource type",
        items=PRECACHE_TYPES,
        default='script',
    )


# ---------------------------------------------------------------------------
# Character entry (CHRB)
# ---------------------------------------------------------------------------

class MM_CharacterEntry(PropertyGroup):
    """Character list entry. Maps to <character name='...' /> in CHRB."""
    char_name: StringProperty(
        name="Character Name",
        description="Character name exactly as defined in herostat/npcstat",
    )


# ---------------------------------------------------------------------------
# Character database entry (from npcstat/herostat)
# ---------------------------------------------------------------------------

class MM_CharacterDBEntry(PropertyGroup):
    """Cached character entry from npcstat.engb / herostat.engb."""
    name_id: StringProperty(
        name="ID",
        description="Internal character name",
    )
    display_name: StringProperty(
        name="Name",
        description="Display name (charactername field)",
    )
    team: StringProperty(
        name="Team",
        description="Character team (hero/enemy)",
    )
    skin: StringProperty(
        name="Skin",
        description="Skin code (maps to actors/{skin}.igb)",
    )
    source: StringProperty(
        name="Source",
        description="Source file (herostat/npcstat)",
    )
    characteranims: StringProperty(
        name="Anims",
        description="Character animation set",
    )


# ---------------------------------------------------------------------------
# Model database entry (from models/ directory scan)
# ---------------------------------------------------------------------------

class MM_ModelDBEntry(PropertyGroup):
    """Cached model entry from game models/ directory."""
    rel_path: StringProperty(
        name="Path",
        description="Relative path without extension (e.g. sanctuary/bridge_spire_san_e)",
    )
    category: StringProperty(
        name="Category",
        description="Subdirectory category (e.g. sanctuary)",
    )
    display_name: StringProperty(
        name="Name",
        description="Model filename without extension",
    )


# ---------------------------------------------------------------------------
# Map settings (world entity + build config)
# ---------------------------------------------------------------------------

class MM_MapSettings(PropertyGroup):
    """Top-level map settings. The 'world' entity in ENGB + build config."""

    map_name: StringProperty(
        name="Map Name",
        description="Map zone name (e.g. 'sanctuary1')",
        default="my_map",
    )
    map_path: StringProperty(
        name="Map Path",
        description="Map path prefix (e.g. 'act1/sanctuary')",
        default="act1/custom",
    )
    act: IntProperty(name="Act", default=1, min=1, max=5)
    level: IntProperty(name="Level", default=1, min=1)
    zone_script: StringProperty(
        name="Zone Script",
        description="Zone script path (e.g. 'act1/sanctuary/sanctuary1'). Auto-set from map path + name if blank.",
    )
    soundfile: StringProperty(
        name="Sound File",
        description="Ambient sound profile name",
    )
    partylight: FloatVectorProperty(
        name="Party Light",
        description="Party light color",
        size=3, default=(0.65, 0.4, 0.16),
        subtype='COLOR', min=0.0, max=1.0,
    )
    partylightradius: FloatProperty(
        name="Party Light Radius",
        default=300.0, min=0.0,
    )
    combatlocked: BoolProperty(name="Combat Locked", default=True)
    nosave: BoolProperty(name="No Save", default=True)
    bigconvmap: BoolProperty(
        name="Big Conv Map",
        description="Use large conversation HUD layout (bigconvmap flag in PKGB)",
        default=False,
    )
    automap_path: StringProperty(
        name="Automap Path",
        description="Automap path prefix (e.g. 'automaps/act1/sanctuary/sanctuary1'). "
                    "Auto-set from map path + name if blank.",
    )

    # --- Navigation mesh ---
    nav_cellsize: IntProperty(
        name="Nav Cell Size",
        description="Navigation grid cell size in game units",
        default=40, min=1,
    )
    nav_max_slope: FloatProperty(
        name="Max Slope",
        description="Maximum walkable surface angle in degrees. "
                    "Steeper faces are excluded (walls, cliffs). "
                    "60 = lenient, 45 = strict",
        default=60.0, min=1.0, max=89.0, step=100,
        subtype='ANGLE',
    )
    nav_multi_layer: BoolProperty(
        name="Multi-Layer",
        description="Detect multiple elevation layers per cell "
                    "(bridges, balconies, stacked floors). "
                    "Disable for simple flat maps to speed up generation",
        default=True,
    )
    nav_layer_separation: FloatProperty(
        name="Layer Separation",
        description="Minimum vertical gap between elevation layers. "
                    "Surfaces closer than this are merged into one cell",
        default=10.0, min=1.0, soft_max=200.0,
    )

    # --- Actor preview ---
    show_previews: BoolProperty(
        name="Show Actor Previews",
        description="Auto-load actor IGB models when placing NPCs",
        default=True,
    )

    # --- Build paths ---
    output_dir: StringProperty(
        name="Output Directory",
        description="Directory to write generated XML and compiled XMLB files",
        subtype='DIR_PATH',
    )
    game_data_dir: StringProperty(
        name="Game Data Directory",
        description="Root of game data folder for deployment/testing",
        subtype='DIR_PATH',
    )


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

_classes = (
    MM_EntityProperty,
    MM_EntityDef,
    MM_PrecacheEntry,
    MM_CharacterEntry,
    MM_CharacterDBEntry,
    MM_ModelDBEntry,
    MM_MapSettings,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.mm_settings = PointerProperty(type=MM_MapSettings)
    bpy.types.Scene.mm_entity_defs = CollectionProperty(type=MM_EntityDef)
    bpy.types.Scene.mm_entity_defs_index = IntProperty(default=0)
    bpy.types.Scene.mm_precache = CollectionProperty(type=MM_PrecacheEntry)
    bpy.types.Scene.mm_precache_index = IntProperty(default=0)
    bpy.types.Scene.mm_characters = CollectionProperty(type=MM_CharacterEntry)
    bpy.types.Scene.mm_characters_index = IntProperty(default=0)

    # Character database (populated from npcstat/herostat)
    bpy.types.Scene.mm_char_db = CollectionProperty(type=MM_CharacterDBEntry)
    bpy.types.Scene.mm_char_db_index = IntProperty(default=0)
    bpy.types.Scene.mm_char_search = StringProperty(
        name="Search",
        description="Filter characters by name",
    )

    # Model database (populated from models/ directory scan)
    bpy.types.Scene.mm_model_db = CollectionProperty(type=MM_ModelDBEntry)
    bpy.types.Scene.mm_model_db_index = IntProperty(default=0)
    bpy.types.Scene.mm_model_search = StringProperty(
        name="Search",
        description="Filter models by name",
    )
    bpy.types.Scene.mm_model_filter_category = StringProperty(
        name="Category",
        description="Filter models by category/subdirectory",
    )


def unregister():
    del bpy.types.Scene.mm_model_filter_category
    del bpy.types.Scene.mm_model_search
    del bpy.types.Scene.mm_model_db_index
    del bpy.types.Scene.mm_model_db
    del bpy.types.Scene.mm_char_search
    del bpy.types.Scene.mm_char_db_index
    del bpy.types.Scene.mm_char_db
    del bpy.types.Scene.mm_characters_index
    del bpy.types.Scene.mm_characters
    del bpy.types.Scene.mm_precache_index
    del bpy.types.Scene.mm_precache
    del bpy.types.Scene.mm_entity_defs_index
    del bpy.types.Scene.mm_entity_defs
    del bpy.types.Scene.mm_settings

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
