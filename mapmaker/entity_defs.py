"""Entity classname definitions, default properties, and visual presets."""

# Entity classnames used by X-Men Legends / XML2 / MUA
# Each entry: (id, label, description)
ENTITY_CLASSNAMES = [
    ('monsterspawnerent', "Monster Spawner", "NPC/enemy spawner"),
    ('playerstartent', "Player Start", "Player spawn point"),
    ('zonelinkent', "Zone Link", "Zone transition trigger"),
    ('lightent', "Light", "Dynamic light source"),
    ('actionent', "Action", "FX/animation trigger"),
    ('gameent', "Game Entity", "General purpose trigger"),
    ('physent', "Physics", "Physical/breakable object"),
    ('doorent', "Door", "Door or gate"),
    ('cameramagnetent', "Camera Magnet", "Camera control zone"),
    ('waypointent', "Waypoint", "AI waypoint"),
    ('tileent', "Tile", "Breakable tile/wall (uses tilemodelfolder)"),
    ('scripttriggerent', "Script Trigger", "Script-activated area trigger"),
    ('inventoryent', "Inventory", "Pickup item (health, energy, etc.)"),
    ('affectableharment', "Harm Area", "Area damage zone"),
    ('moverent', "Mover", "Moving platform or object"),
    ('powertriggerent', "Power Trigger", "Superpower-activated trigger"),
    ('waterent', "Water", "Water volume entity"),
    ('treasureent', "Treasure", "Treasure/collectible drop"),
    ('enabletargetent', "Enable Target", "Conditional enable/disable target"),
    ('projectileent', "Projectile", "Projectile template entity"),
    ('harmtargetent', "Harm Target", "Targetable harm zone"),
    ('rememberent', "Remember", "State persistence entity"),
    ('actor', "Actor", "NPC actor (cutscene/scripted)"),
    ('ent', "Null", "Empty/null entity"),
]

# Precache type options
PRECACHE_TYPES = [
    ('conversation', "Conversation", "Conversation file"),
    ('dialog', "Dialog", "Dialog file"),
    ('fx', "FX", "Visual effect"),
    ('model', "Model", "3D model"),
    ('script', "Script", "BehavEd script"),
    ('sound', "Sound", "Sound file"),
]

# Visual settings per classname: (empty_display_type, color_rgba)
# Colors are used for the Empty's viewport display color
ENTITY_VISUALS = {
    'playerstartent':    ('ARROWS',       (0.0, 0.9, 0.2, 1.0)),   # Green
    'monsterspawnerent': ('SINGLE_ARROW', (1.0, 0.5, 0.0, 1.0)),   # Orange
    'zonelinkent':       ('CUBE',         (0.2, 0.4, 1.0, 1.0)),   # Blue
    'lightent':          ('SPHERE',       (1.0, 0.9, 0.2, 1.0)),   # Yellow
    'actionent':         ('PLAIN_AXES',   (0.9, 0.2, 0.9, 1.0)),   # Magenta
    'gameent':           ('PLAIN_AXES',   (0.9, 0.2, 0.2, 1.0)),   # Red
    'physent':           ('CUBE',         (0.5, 0.5, 0.5, 1.0)),   # Gray
    'doorent':           ('CUBE',         (0.6, 0.35, 0.1, 1.0)),  # Brown
    'cameramagnetent':   ('CONE',         (0.2, 0.8, 0.8, 1.0)),   # Cyan
    'waypointent':       ('CIRCLE',       (0.8, 0.8, 0.0, 1.0)),   # Gold
    'tileent':           ('CUBE',         (0.6, 0.6, 0.3, 1.0)),   # Olive (breakable tiles)
    'scripttriggerent':  ('PLAIN_AXES',   (0.8, 0.3, 0.8, 1.0)),   # Light magenta
    'inventoryent':      ('SPHERE',       (0.2, 0.9, 0.6, 1.0)),   # Teal
    'affectableharment': ('PLAIN_AXES',   (1.0, 0.3, 0.0, 1.0)),   # Dark orange
    'moverent':          ('ARROWS',       (0.3, 0.3, 0.9, 1.0)),   # Med blue
    'powertriggerent':   ('PLAIN_AXES',   (0.7, 0.2, 1.0, 1.0)),   # Purple
    'waterent':          ('CUBE',         (0.2, 0.4, 0.9, 1.0)),   # Water blue
    'treasureent':       ('SPHERE',       (1.0, 0.85, 0.0, 1.0)),  # Gold
    'enabletargetent':   ('PLAIN_AXES',   (0.5, 0.8, 0.2, 1.0)),   # Lime
    'projectileent':     ('SINGLE_ARROW', (1.0, 0.0, 0.0, 1.0)),   # Bright red
    'harmtargetent':     ('PLAIN_AXES',   (0.9, 0.4, 0.1, 1.0)),   # Burnt orange
    'rememberent':       ('PLAIN_AXES',   (0.4, 0.4, 0.6, 1.0)),   # Muted blue-gray
    'actor':             ('SINGLE_ARROW', (0.2, 0.7, 0.3, 1.0)),   # Green
    'ent':               ('PLAIN_AXES',   (0.7, 0.7, 0.7, 1.0)),   # Light gray
}

# Default empty display size for entity empties
ENTITY_EMPTY_SIZE = 30.0

# Default properties added when creating a new entity def of a given classname
# NOTE: nocollide is NOT in these dicts — it's handled by the dedicated field
#       on MM_EntityDef (default=False). Entries here are for custom properties.
ENTITY_DEFAULTS = {
    'monsterspawnerent': {
        'actcountremove': '1',
        'monster_spawnexactlocation': 'true',
        'checkteam': 'true',
        'instantspawn': 'true',
    },
    'playerstartent': {
        'allinone': 'true',
    },
    'zonelinkent': {
        'actinactivedelay': '2',
        'actleader': 'true',
        'actmatchteam': 'true',
        'actonuse': 'true',
        'boxcollision': 'true',
        'team': 'hero',
    },
    'lightent': {
        'lightcolor': '1 1 1',
        'lightradius': '200',
        'startoff': 'false',
    },
    'actionent': {
        'acttogglesloopfx': 'true',
        'loopfxstarton': 'true',
        'smartent': 'true',
    },
    'gameent': {
        'boxcollision': 'true',
        'team': 'hero',
    },
    'physent': {
        'health': '32000',
        'nogravity': 'true',
        'nopickup': 'true',
        'nopush': 'true',
        'structure': '2',
    },
    'doorent': {
        'health': '32000',
        'nogravity': 'true',
        'nopickup': 'true',
        'nopush': 'true',
        'structure': '2',
    },
    'cameramagnetent': {
        'actleader': 'true',
        'actmatchteam': 'true',
        'boxcollision': 'true',
        'cameramagtype': '0',
        'team': 'hero',
    },
    'waypointent': {},
    'ent': {},
}


def get_classname_from_entity_def(entity_def):
    """Get the classname for an entity definition, looking up from properties if needed."""
    return entity_def.classname


def get_visual_for_classname(classname):
    """Return (display_type, color) for a given entity classname."""
    return ENTITY_VISUALS.get(classname, ('PLAIN_AXES', (0.7, 0.7, 0.7, 1.0)))


# ---------------------------------------------------------------------------
# Premade entity templates — common game objects
# ---------------------------------------------------------------------------

# IMPORTANT: Model/script paths in entity defs do NOT include the 'models/' or
# 'scripts/' prefix. Those prefixes are added by the PKGB generator only.
# Reference: sanctuary1.engb entity definitions.

# Each template: (id, label, description, classname, dedicated_fields, properties_dict)
# dedicated_fields: dict of field_name -> value for character/model/nocollide/monster_name
# properties_dict: dict of key -> value for custom properties
ENTITY_PRESETS = [
    # --- Extraction Point (beacon model + activation script) ---
    (
        'extraction_point', "Extraction Point", "Xtraction save/teleport point (beacon)",
        'gameent',
        {'model': 'puzzles/beacon_xtraction', 'nocollide': True},
        {
            'actonuse': 'true',
            'actscript': 'common/extraction/exp_activate',
            'boxcollision': 'true',
            'loopfx': 'base/misc/extraction_loop',
            'loopfxstarton': 'true',
            'material': 'solid_metal',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'quickuse': 'true',
            'spawnscript': 'common/extraction/exp_spawn',
            'team': 'hero',
        },
    ),
    # --- Extraction Trigger (invisible touch zone around beacon) ---
    (
        'extraction_trigger', "Extraction Trigger", "Touch trigger for extraction point",
        'gameent',
        {'nocollide': False},
        {
            'actcountremove': '1',
            'acteffect': 'base/misc/extraction_burst',
            'actontouch': 'true',
            'actscript': 'common/extraction/exp_trig',
            'actteamplayer': 'true',
            'boxcollision': 'true',
            'team': 'hero',
        },
    ),
    # --- Mission Briefing (projector + auto holo globe) ---
    (
        'mission_briefing', "Mission Briefing", "Mission briefing computer terminal",
        'physent',
        {'model': 'town_center/mission_projector_town_a', 'nocollide': True},
        {
            'health': '32000',
            'material': 'solid_metal',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'structure': '2',
        },
    ),
    # --- Stash Terminal ---
    (
        'stash', "Stash Terminal", "S.T.A.S.H. equipment storage terminal",
        'gameent',
        {'model': 'town_center/stash_town_e', 'nocollide': True},
        {
            'actonuse': 'true',
            'actscript': 'stashMenu()',
            'boxcollision': 'true',
            'material': 'solid_metal',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'team': 'hero',
        },
    ),
    # --- Trivia Terminal ---
    (
        'trivia_terminal', "Trivia Terminal", "Trivia mini-game terminal",
        'physent',
        {'model': 'town_center/trivia_town_a', 'nocollide': True},
        {
            'health': '32000',
            'material': 'solid_metal',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'structure': '2',
        },
    ),
    # --- Blink Portal ---
    (
        'blink_portal', "Blink Portal", "Teleportation portal effect",
        'gameent',
        {'nocollide': True},
        {
            'actonuse': 'true',
            'actsoundloop': 'common/puzzleents/portalambient',
            'actscript': 'common/puzzleents/blinkportal',
            'boxcollision': 'true',
            'deatheffect': 'map/common/scr_blinkportal_c',
            'loopfx': 'map/common/scr_blinkportal',
            'spawnscript': 'common/puzzleents/blinkportalspawnscript',
            'startenabled': 'false',
            'team': 'hero',
            'toggleonact': 'true',
        },
    ),
    # --- Holographic Globe (placed above mission briefing) ---
    (
        'holo_globe', "Holographic Globe", "Holographic world display prop (auto-placed above mission briefing)",
        'physent',
        {'model': 'town_center/holoearth_town_a', 'nocollide': True},
        {
            'health': '32000',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'structure': '2',
        },
    ),
    # --- Console Terminal ---
    (
        'console', "Console Terminal", "Interactive computer console",
        'physent',
        {'model': 'town_center/extras_console_town_a', 'nocollide': True},
        {
            'health': '32000',
            'material': 'solid_metal',
            'nogravity': 'true',
            'nopickup': 'true',
            'nopush': 'true',
            'structure': '2',
        },
    ),
    # --- Touch Trigger (invisible walk-through trigger) ---
    (
        'touch_trigger', "Touch Trigger", "Invisible trigger activated by walking through",
        'gameent',
        {'nocollide': False},
        {
            'actleader': 'true',
            'actmatchteam': 'true',
            'actontouch': 'true',
            'boxcollision': 'true',
            'team': 'hero',
        },
    ),
    # --- Fire effects with damage ---
    (
        'fire_small', "Fire (Small)", "Small fire loop effect — hurts on contact",
        'actionent',
        {'nocollide': False},
        {
            'acttogglesloopfx': 'true',
            'dmg': '5',
            'dmgburntime': '3',
            'dmgradius': '30',
            'dmgtype': 'fire',
            'loopfx': 'map/common/fire_small',
            'loopfxstarton': 'true',
            'smartent': 'true',
        },
    ),
    (
        'fire_medium', "Fire (Medium)", "Medium fire loop effect — hurts on contact",
        'actionent',
        {'nocollide': False},
        {
            'acttogglesloopfx': 'true',
            'dmg': '10',
            'dmgburntime': '4',
            'dmgradius': '50',
            'dmgtype': 'fire',
            'loopfx': 'map/common/fire_medium',
            'loopfxstarton': 'true',
            'smartent': 'true',
        },
    ),
    (
        'fire_large', "Fire (Large)", "Large fire loop effect — hurts on contact",
        'actionent',
        {'nocollide': False},
        {
            'acttogglesloopfx': 'true',
            'dmg': '20',
            'dmgburntime': '5',
            'dmgradius': '80',
            'dmgtype': 'fire',
            'loopfx': 'map/common/fire_large',
            'loopfxstarton': 'true',
            'smartent': 'true',
        },
    ),
]

# Build enum items for the preset selector
ENTITY_PRESET_ITEMS = [(pid, label, desc) for pid, label, desc, *_ in ENTITY_PRESETS]
