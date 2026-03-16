"""Typed property schemas per entity classname.

Defines sections with typed fields so the UI can render proper widgets
(checkboxes, dropdowns, number fields) instead of raw key-value strings.
"""

# Each schema: dict of classname -> { 'sections': [ { 'label', 'icon', 'properties': [ ... ] } ] }
# Property types: 'bool', 'int', 'float', 'string', 'enum'

PROPERTY_SCHEMAS = {
    'monsterspawnerent': {
        'sections': [
            {
                'label': "Spawn Behavior",
                'icon': 'GHOST_ENABLED',
                'properties': [
                    {'key': 'instantspawn', 'type': 'bool', 'default': 'true',
                     'label': "Instant Spawn", 'desc': "Spawn immediately on zone load"},
                    {'key': 'actcountremove', 'type': 'int', 'default': '1',
                     'label': "Activation Count", 'desc': "-1 = infinite respawn"},
                    {'key': 'monster_spawnexactlocation', 'type': 'bool', 'default': 'true',
                     'label': "Exact Location", 'desc': "Spawn at exact entity position"},
                    {'key': 'checkteam', 'type': 'bool', 'default': 'true',
                     'label': "Check Team", 'desc': "Verify team before activating"},
                ],
            },
            {
                'label': "Conversation",
                'icon': 'OUTLINER',
                'properties': [
                    {'key': 'monster_actonuse', 'type': 'bool', 'default': 'false',
                     'label': "Act on Use", 'desc': "Enable interaction (talk)"},
                    {'key': 'monster_actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run on interaction"},
                ],
            },
        ],
    },
    'zonelinkent': {
        'sections': [
            {
                'label': "Destination",
                'icon': 'TRACKING_FORWARDS_SINGLE',
                'properties': [
                    {'key': 'destzone', 'type': 'string', 'default': '',
                     'label': "Dest Zone", 'desc': "Target zone name"},
                    {'key': 'destzonelink', 'type': 'string', 'default': '',
                     'label': "Dest Link", 'desc': "Target zone link entity name"},
                ],
            },
            {
                'label': "Activation",
                'icon': 'PLAY',
                'properties': [
                    {'key': 'actontouch', 'type': 'bool', 'default': 'false',
                     'label': "On Touch", 'desc': "Activate when walked into"},
                    {'key': 'actonuse', 'type': 'bool', 'default': 'true',
                     'label': "On Use", 'desc': "Activate when interacted with"},
                    {'key': 'actinactivedelay', 'type': 'int', 'default': '2',
                     'label': "Inactive Delay", 'desc': "Seconds before reactivation"},
                    {'key': 'actleader', 'type': 'bool', 'default': 'true',
                     'label': "Leader Only", 'desc': "Only leader activates"},
                    {'key': 'actmatchteam', 'type': 'bool', 'default': 'true',
                     'label': "Match Team", 'desc': "Activator must match team"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision", 'desc': "Use box collision shape"},
                    {'key': 'team', 'type': 'enum', 'default': 'hero',
                     'items': ['hero', 'enemy', 'neutral'],
                     'label': "Team", 'desc': "Activation team filter"},
                ],
            },
        ],
    },
    'playerstartent': {
        'sections': [
            {
                'label': "Spawn Options",
                'icon': 'PLAY',
                'properties': [
                    {'key': 'allinone', 'type': 'bool', 'default': 'false',
                     'label': "All in One", 'desc': "All players spawn at this point (required for co-op)"},
                    {'key': 'default', 'type': 'bool', 'default': 'false',
                     'label': "Default Start", 'desc': "Default spawn when no prevzone matches"},
                ],
            },
            {
                'label': "Zone Link",
                'icon': 'LINKED',
                'properties': [
                    {'key': 'prevzone', 'type': 'string', 'default': '',
                     'label': "Previous Zone", 'desc': "Spawn here when arriving from this zone"},
                    {'key': 'prevlink', 'type': 'string', 'default': '',
                     'label': "Previous Link", 'desc': "Zone link entity name in previous zone"},
                ],
            },
        ],
    },
    'gameent': {
        'sections': [
            {
                'label': "Activation",
                'icon': 'PLAY',
                'properties': [
                    {'key': 'actontouch', 'type': 'bool', 'default': 'false',
                     'label': "On Touch", 'desc': "Activate when walked into"},
                    {'key': 'actonuse', 'type': 'bool', 'default': 'false',
                     'label': "On Use", 'desc': "Activate when interacted with"},
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run on activation"},
                    {'key': 'spawnscript', 'type': 'string', 'default': '',
                     'label': "Spawn Script", 'desc': "Script to run on spawn"},
                    {'key': 'actleader', 'type': 'bool', 'default': 'false',
                     'label': "Leader Only", 'desc': "Only leader activates"},
                    {'key': 'actmatchteam', 'type': 'bool', 'default': 'false',
                     'label': "Match Team", 'desc': "Activator must match team"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision", 'desc': "Use box collision shape"},
                    {'key': 'team', 'type': 'enum', 'default': 'hero',
                     'items': ['hero', 'enemy', 'neutral'],
                     'label': "Team", 'desc': "Entity team"},
                ],
            },
            {
                'label': "Effects",
                'icon': 'SHADERFX',
                'properties': [
                    {'key': 'loopfx', 'type': 'string', 'default': '',
                     'label': "Loop FX", 'desc': "Looping visual effect path"},
                    {'key': 'loopfxstarton', 'type': 'bool', 'default': 'false',
                     'label': "FX Start On", 'desc': "Loop FX starts active"},
                    {'key': 'acteffect', 'type': 'string', 'default': '',
                     'label': "Act Effect", 'desc': "One-shot FX on activation"},
                ],
            },
        ],
    },
    'actionent': {
        'sections': [
            {
                'label': "Action FX",
                'icon': 'SHADERFX',
                'properties': [
                    {'key': 'acttogglesloopfx', 'type': 'bool', 'default': 'true',
                     'label': "Toggle Loop FX", 'desc': "Activation toggles loop FX"},
                    {'key': 'loopfx', 'type': 'string', 'default': '',
                     'label': "Loop FX", 'desc': "Looping visual effect path"},
                    {'key': 'loopfxstarton', 'type': 'bool', 'default': 'true',
                     'label': "FX Start On", 'desc': "Loop FX starts active"},
                    {'key': 'smartent', 'type': 'bool', 'default': 'true',
                     'label': "Smart Entity", 'desc': "Use smart entity logic"},
                ],
            },
            {
                'label': "Damage",
                'icon': 'ERROR',
                'properties': [
                    {'key': 'dmg', 'type': 'int', 'default': '0',
                     'label': "Damage", 'desc': "Damage amount"},
                    {'key': 'dmgradius', 'type': 'int', 'default': '0',
                     'label': "Damage Radius", 'desc': "Damage area radius"},
                    {'key': 'dmgburntime', 'type': 'int', 'default': '0',
                     'label': "Burn Time", 'desc': "Burn duration in seconds"},
                    {'key': 'dmgtype', 'type': 'enum', 'default': '',
                     'items': ['', 'fire', 'ice', 'electric', 'radiation', 'psychic', 'physical'],
                     'label': "Damage Type", 'desc': "Type of damage dealt"},
                ],
            },
        ],
    },
    'physent': {
        'sections': [
            {
                'label': "Physics",
                'icon': 'PHYSICS',
                'properties': [
                    {'key': 'health', 'type': 'int', 'default': '32000',
                     'label': "Health", 'desc': "Object health (32000 = indestructible)"},
                    {'key': 'structure', 'type': 'enum', 'default': '2',
                     'items': ['0', '1', '2', '3'],
                     'label': "Structure", 'desc': "Structure tier (2 = standard prop)"},
                    {'key': 'nogravity', 'type': 'bool', 'default': 'true',
                     'label': "No Gravity", 'desc': "Disable gravity"},
                    {'key': 'nopickup', 'type': 'bool', 'default': 'true',
                     'label': "No Pickup", 'desc': "Cannot be picked up"},
                    {'key': 'nopush', 'type': 'bool', 'default': 'true',
                     'label': "No Push", 'desc': "Cannot be pushed"},
                    {'key': 'material', 'type': 'enum', 'default': '',
                     'items': ['', 'solid_metal', 'solid_stone', 'solid_wood',
                               'solid_glass', 'solid_ice', 'solid_organic'],
                     'label': "Material", 'desc': "Physics material type"},
                ],
            },
        ],
    },
    'doorent': {
        'sections': [
            {
                'label': "Door Properties",
                'icon': 'MESH_CUBE',
                'properties': [
                    {'key': 'health', 'type': 'int', 'default': '32000',
                     'label': "Health", 'desc': "Door health (32000 = indestructible)"},
                    {'key': 'structure', 'type': 'enum', 'default': '2',
                     'items': ['0', '1', '2', '3'],
                     'label': "Structure", 'desc': "Structure tier"},
                    {'key': 'nogravity', 'type': 'bool', 'default': 'true',
                     'label': "No Gravity"},
                    {'key': 'nopickup', 'type': 'bool', 'default': 'true',
                     'label': "No Pickup"},
                    {'key': 'nopush', 'type': 'bool', 'default': 'true',
                     'label': "No Push"},
                    {'key': 'material', 'type': 'enum', 'default': '',
                     'items': ['', 'solid_metal', 'solid_stone', 'solid_wood',
                               'solid_glass', 'solid_ice', 'solid_organic'],
                     'label': "Material", 'desc': "Physics material type"},
                ],
            },
        ],
    },
    'inventoryent': {
        'sections': [
            {
                'label': "Pickup",
                'icon': 'FUND',
                'properties': [
                    {'key': 'invtype', 'type': 'enum', 'default': 'health',
                     'items': ['health', 'energy', 'xp', 'xtreme', 'shield'],
                     'label': "Pickup Type", 'desc': "What this item restores"},
                    {'key': 'invcount', 'type': 'int', 'default': '25',
                     'label': "Amount", 'desc': "Amount restored on pickup"},
                    {'key': 'respawntime', 'type': 'int', 'default': '30',
                     'label': "Respawn Time", 'desc': "Seconds before respawning (0 = no respawn)"},
                ],
            },
        ],
    },
    'treasureent': {
        'sections': [
            {
                'label': "Treasure",
                'icon': 'FUND',
                'properties': [
                    {'key': 'treasuretype', 'type': 'enum', 'default': 'comic',
                     'items': ['comic', 'techbit', 'gear', 'random'],
                     'label': "Treasure Type", 'desc': "Type of treasure drop"},
                    {'key': 'comicindex', 'type': 'int', 'default': '0',
                     'label': "Comic Index", 'desc': "Comic book index number"},
                ],
            },
        ],
    },
    'cameramagnetent': {
        'sections': [
            {
                'label': "Camera Control",
                'icon': 'VIEW_CAMERA',
                'properties': [
                    {'key': 'cameramagtype', 'type': 'enum', 'default': '0',
                     'items': ['0', '1', '2', '3'],
                     'label': "Magnet Type", 'desc': "Camera behavior type"},
                    {'key': 'actleader', 'type': 'bool', 'default': 'true',
                     'label': "Leader Only"},
                    {'key': 'actmatchteam', 'type': 'bool', 'default': 'true',
                     'label': "Match Team"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                    {'key': 'team', 'type': 'enum', 'default': 'hero',
                     'items': ['hero', 'enemy', 'neutral'],
                     'label': "Team"},
                ],
            },
        ],
    },
    'scripttriggerent': {
        'sections': [
            {
                'label': "Script Trigger",
                'icon': 'SCRIPT',
                'properties': [
                    {'key': 'actontouch', 'type': 'bool', 'default': 'true',
                     'label': "On Touch"},
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run on activation"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                    {'key': 'actleader', 'type': 'bool', 'default': 'false',
                     'label': "Leader Only"},
                    {'key': 'team', 'type': 'enum', 'default': 'hero',
                     'items': ['hero', 'enemy', 'neutral'],
                     'label': "Team"},
                ],
            },
        ],
    },
    'moverent': {
        'sections': [
            {
                'label': "Mover Properties",
                'icon': 'ARROW_LEFTRIGHT',
                'properties': [
                    {'key': 'speed', 'type': 'int', 'default': '100',
                     'label': "Speed", 'desc': "Movement speed"},
                    {'key': 'moverdist', 'type': 'int', 'default': '0',
                     'label': "Distance", 'desc': "Movement distance"},
                    {'key': 'moveraxis', 'type': 'enum', 'default': 'z',
                     'items': ['x', 'y', 'z'],
                     'label': "Axis", 'desc': "Movement axis"},
                ],
            },
        ],
    },
    'waterent': {
        'sections': [
            {
                'label': "Water Volume",
                'icon': 'MOD_FLUIDSIM',
                'properties': [
                    {'key': 'waterdmg', 'type': 'int', 'default': '0',
                     'label': "Water Damage", 'desc': "Damage per second in water"},
                    {'key': 'waterheight', 'type': 'int', 'default': '0',
                     'label': "Water Height", 'desc': "Height of water surface"},
                ],
            },
        ],
    },
    'powertriggerent': {
        'sections': [
            {
                'label': "Power Trigger",
                'icon': 'FORCE_FORCE',
                'properties': [
                    {'key': 'power', 'type': 'string', 'default': '',
                     'label': "Required Power", 'desc': "Power name required to activate"},
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                ],
            },
        ],
    },
    'affectableharment': {
        'sections': [
            {
                'label': "Harm Area",
                'icon': 'ERROR',
                'properties': [
                    {'key': 'dmg', 'type': 'int', 'default': '10',
                     'label': "Damage", 'desc': "Damage per tick"},
                    {'key': 'dmgradius', 'type': 'int', 'default': '50',
                     'label': "Damage Radius", 'desc': "Area of effect radius"},
                    {'key': 'dmgtype', 'type': 'enum', 'default': '',
                     'items': ['', 'fire', 'ice', 'electric', 'radiation', 'psychic', 'physical'],
                     'label': "Damage Type"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                ],
            },
        ],
    },
    'enabletargetent': {
        'sections': [
            {
                'label': "Enable/Disable",
                'icon': 'CHECKBOX_HLT',
                'properties': [
                    {'key': 'startenabled', 'type': 'bool', 'default': 'true',
                     'label': "Start Enabled", 'desc': "Entity starts enabled"},
                    {'key': 'target', 'type': 'string', 'default': '',
                     'label': "Target", 'desc': "Name of entity to enable/disable"},
                ],
            },
        ],
    },
    'tileent': {
        'sections': [
            {
                'label': "Breakable Tile",
                'icon': 'MESH_GRID',
                'properties': [
                    {'key': 'tilemodelfolder', 'type': 'string', 'default': '',
                     'label': "Model Folder", 'desc': "Folder containing tile model variants"},
                    {'key': 'health', 'type': 'int', 'default': '100',
                     'label': "Health", 'desc': "Tile health before breaking"},
                    {'key': 'structure', 'type': 'enum', 'default': '1',
                     'items': ['0', '1', '2', '3'],
                     'label': "Structure", 'desc': "Structure tier"},
                ],
            },
        ],
    },
    'waypointent': {
        'sections': [
            {
                'label': "Waypoint",
                'icon': 'CURVE_PATH',
                'properties': [
                    {'key': 'nextwaypoint', 'type': 'string', 'default': '',
                     'label': "Next Waypoint", 'desc': "Name of next waypoint in chain"},
                ],
            },
        ],
    },
    'rememberent': {
        'sections': [
            {
                'label': "State Persistence",
                'icon': 'FILE_CACHE',
                'properties': [
                    {'key': 'statename', 'type': 'string', 'default': '',
                     'label': "State Name", 'desc': "Persistent state variable name"},
                ],
            },
        ],
    },
    'projectileent': {
        'sections': [
            {
                'label': "Projectile",
                'icon': 'EMPTY_SINGLE_ARROW',
                'properties': [
                    {'key': 'speed', 'type': 'int', 'default': '500',
                     'label': "Speed", 'desc': "Projectile speed"},
                    {'key': 'dmg', 'type': 'int', 'default': '10',
                     'label': "Damage"},
                    {'key': 'dmgtype', 'type': 'enum', 'default': '',
                     'items': ['', 'fire', 'ice', 'electric', 'radiation', 'psychic', 'physical'],
                     'label': "Damage Type"},
                ],
            },
        ],
    },
    'harmtargetent': {
        'sections': [
            {
                'label': "Harm Target",
                'icon': 'ERROR',
                'properties': [
                    {'key': 'harmdamage', 'type': 'int', 'default': '10',
                     'label': "Damage", 'desc': "Damage dealt to targets"},
                    {'key': 'harmradius', 'type': 'int', 'default': '50',
                     'label': "Radius", 'desc': "Harm effect radius"},
                ],
            },
        ],
    },
    # --- MUA-only classnames ---
    'interactent': {
        'sections': [
            {
                'label': "Interaction",
                'icon': 'HAND',
                'properties': [
                    {'key': 'actonuse', 'type': 'bool', 'default': 'true',
                     'label': "On Use", 'desc': "Activate when interacted with"},
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run on interaction"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                ],
            },
        ],
    },
    'pressureplateent': {
        'sections': [
            {
                'label': "Pressure Plate",
                'icon': 'MESH_PLANE',
                'properties': [
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run when triggered"},
                    {'key': 'boxcollision', 'type': 'bool', 'default': 'true',
                     'label': "Box Collision"},
                    {'key': 'team', 'type': 'enum', 'default': 'hero',
                     'items': ['hero', 'enemy', 'neutral'],
                     'label': "Team"},
                ],
            },
        ],
    },
    'ropeent': {
        'sections': [
            {
                'label': "Rope",
                'icon': 'CURVE_BEZCURVE',
                'properties': [
                    {'key': 'ropelength', 'type': 'int', 'default': '200',
                     'label': "Length", 'desc': "Rope length in game units"},
                    {'key': 'ropespeed', 'type': 'int', 'default': '100',
                     'label': "Swing Speed", 'desc': "Swing speed"},
                ],
            },
        ],
    },
    'orbent': {
        'sections': [
            {
                'label': "Orb",
                'icon': 'SPHERE',
                'properties': [
                    {'key': 'orbtype', 'type': 'enum', 'default': 'health',
                     'items': ['health', 'energy', 'xtreme', 'shield'],
                     'label': "Orb Type", 'desc': "What the orb restores"},
                    {'key': 'orbvalue', 'type': 'int', 'default': '10',
                     'label': "Value", 'desc': "Amount restored on pickup"},
                ],
            },
        ],
    },
    'laserent': {
        'sections': [
            {
                'label': "Laser",
                'icon': 'LIGHT_AREA',
                'properties': [
                    {'key': 'dmg', 'type': 'int', 'default': '10',
                     'label': "Damage", 'desc': "Damage on contact"},
                    {'key': 'dmgtype', 'type': 'enum', 'default': 'electric',
                     'items': ['', 'fire', 'ice', 'electric', 'radiation'],
                     'label': "Damage Type"},
                ],
            },
        ],
    },
    'holdent': {
        'sections': [
            {
                'label': "Hold Point",
                'icon': 'PINNED',
                'properties': [
                    {'key': 'holdtime', 'type': 'int', 'default': '0',
                     'label': "Hold Time", 'desc': "Seconds AI holds position (0 = indefinite)"},
                ],
            },
        ],
    },
    'challengeent': {
        'sections': [
            {
                'label': "Challenge",
                'icon': 'TROPHY',
                'properties': [
                    {'key': 'challengetype', 'type': 'string', 'default': '',
                     'label': "Challenge Type", 'desc': "Type of challenge"},
                    {'key': 'actscript', 'type': 'string', 'default': '',
                     'label': "Act Script", 'desc': "Script to run for challenge"},
                ],
            },
        ],
    },
    'guidedprojectileent': {
        'sections': [
            {
                'label': "Guided Projectile",
                'icon': 'EMPTY_SINGLE_ARROW',
                'properties': [
                    {'key': 'speed', 'type': 'int', 'default': '300',
                     'label': "Speed", 'desc': "Projectile speed"},
                    {'key': 'turnrate', 'type': 'int', 'default': '90',
                     'label': "Turn Rate", 'desc': "Degrees per second turning"},
                    {'key': 'dmg', 'type': 'int', 'default': '20',
                     'label': "Damage"},
                ],
            },
        ],
    },
}


def get_schema(classname):
    """Return the property schema for a classname, or None."""
    return PROPERTY_SCHEMAS.get(classname)


def get_schema_keys(classname):
    """Return the set of all property keys defined in a schema."""
    schema = PROPERTY_SCHEMAS.get(classname)
    if not schema:
        return set()
    keys = set()
    for section in schema['sections']:
        for prop_def in section['properties']:
            keys.add(prop_def['key'])
    return keys
