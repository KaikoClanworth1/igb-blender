"""Game script command definitions for XML2 and MUA.

Pure data module — no UI dependencies. Each command is a dict with:
  name, params, desc, games, category

Used by the script editor to populate the command palette and generate
insertion snippets.
"""

# ---------------------------------------------------------------------------
# Category order (palette display order)
# ---------------------------------------------------------------------------

CATEGORIES = [
    'Characters',
    'Combat & Health',
    'Spawning',
    'Animation',
    'Camera',
    'Audio & Music',
    'UI & HUD',
    'Objectives & Missions',
    'Variables & State',
    'Movement & Physics',
    'Scripting & Flow',
    'Environment',
]

# ---------------------------------------------------------------------------
# Command definitions
# ---------------------------------------------------------------------------

def _c(name, params, desc, games, category):
    """Shorthand to build a command dict."""
    return {
        'name': name,
        'params': [{'name': p[0], 'type': p[1], 'desc': p[2]} for p in params],
        'desc': desc,
        'games': set(games),
        'category': category,
    }

S = 'str'
I = 'int'
F = 'float'
B = 'bool'

# fmt: off
SCRIPT_COMMANDS = [
    # ── Characters ──────────────────────────────────────────────────────
    _c('unlockCharacter', [('hero', S, 'Hero internal name')],
       'Adds a hero to the playable roster permanently', ['xml2', 'mua'], 'Characters'),
    _c('lockCharacter', [('hero', S, 'Hero internal name')],
       'Removes a hero from the roster', ['xml2'], 'Characters'),
    _c('lockCharacter', [('hero', S, 'Hero internal name'), ('lock', B, 'True to lock')],
       'Locks or unlocks a character from selection', ['mua'], 'Characters'),
    _c('setAvailable', [('hero', S, 'Hero name'), ('available', B, 'Visibility at extraction')],
       'Toggles selection visibility at Extraction Points', ['xml2'], 'Characters'),
    _c('isAvailable', [('hero', S, 'Hero name')],
       'Returns 1 if hero is unlocked, 0 if not', ['xml2'], 'Characters'),
    _c('unlockCostume', [('hero', S, 'Hero name'), ('skin', S, 'Skin name or index')],
       'Unlocks a specific skin for a hero', ['xml2'], 'Characters'),
    _c('lockCostume', [('hero', S, 'Hero name'), ('skin', S, 'Skin name or index')],
       'Relocks a specific skin', ['xml2'], 'Characters'),
    _c('setSkin', [('hero', S, 'Hero/actor name'), ('skin', S, 'Skin name or index')],
       'Instantly changes an active hero\'s 3D skin', ['xml2', 'mua'], 'Characters'),
    _c('getSkin', [('hero', S, 'Hero name')],
       'Returns the name/index of current skin', ['xml2'], 'Characters'),
    _c('unlockAllCostumes', [],
       'Unlocks every skin for every hero', ['xml2'], 'Characters'),
    _c('setLevel', [('hero', S, 'Hero name'), ('level', I, 'Target level')],
       'Sets character level; updates CStats', ['xml2'], 'Characters'),
    _c('getLevel', [('hero', S, 'Hero name')],
       'Returns the hero\'s current level', ['xml2'], 'Characters'),
    _c('addExperience', [('hero', S, 'Hero name'), ('xp', I, 'Amount of XP')],
       'Grants a specific amount of XP', ['xml2'], 'Characters'),
    _c('addCharacterStat', [('hero', S, 'Hero name'), ('points', I, 'Stat points')],
       'Adds unspent stat points', ['xml2'], 'Characters'),
    _c('resetStats', [('hero', S, 'Hero name')],
       'Refunds all spent stat points to the pool', ['xml2'], 'Characters'),
    _c('unlockPower', [('hero', S, 'Hero name'), ('power', S, 'Power name')],
       'Forces a power to unlock regardless of level', ['xml2'], 'Characters'),
    _c('setTeamLevel', [('level', I, 'Target level')],
       'Overrides the player\'s team level', ['mua'], 'Characters'),
    _c('addXtremePip', [('amount', I, 'Pip amount')],
       'Grants a portion of the Xtreme power meter', ['mua'], 'Characters'),
    _c('forceTeamChange', [('hero', S, 'Hero name')],
       'Forces the player to switch to this hero', ['xml2'], 'Characters'),
    _c('lockTeam', [('lock', B, 'True to lock')],
       'Prevents the player from changing heroes', ['xml2'], 'Characters'),
    _c('getHeroName', [('actor', S, 'Actor reference')],
       'Returns the string display name of a hero', ['mua'], 'Characters'),

    # ── Combat & Health ─────────────────────────────────────────────────
    _c('kill', [('target', S, 'Entity name')],
       'Sets target health to zero', ['xml2'], 'Combat & Health'),
    _c('revive', [('target', S, 'Entity name')],
       'Resurrects a fallen hero', ['xml2'], 'Combat & Health'),
    _c('damage', [('target', S, 'Entity name'), ('amount', I, 'Damage amount')],
       'Applies direct damage to an entity', ['xml2'], 'Combat & Health'),
    _c('heal', [('target', S, 'Entity name'), ('amount', I, 'Health amount')],
       'Restores health to an entity', ['xml2'], 'Combat & Health'),
    _c('invincible', [('target', S, 'Entity name'), ('on', B, 'True = invincible')],
       'Toggles invulnerability for a target', ['xml2'], 'Combat & Health'),
    _c('applyStatEffect', [('effect', S, 'Effect name'), ('target', S, 'Entity')],
       'Manually applies a buff/debuff effect', ['xml2'], 'Combat & Health'),
    _c('removeStatEffect', [('effect', S, 'Effect name'), ('target', S, 'Entity')],
       'Clears a specific status effect', ['xml2'], 'Combat & Health'),
    _c('setCheat', [('cheat', S, 'Cheat name'), ('on', B, 'Enable/disable')],
       'Toggles flags in the CCheats bitfield', ['xml2'], 'Combat & Health'),
    _c('killEntity', [('actor', S, 'Actor name')],
       'Removes an entity from the world immediately', ['mua'], 'Combat & Health'),
    _c('setHealth', [('actor', S, 'Actor name'), ('hp', I, 'Health value')],
       'Sets the health points for an actor', ['mua'], 'Combat & Health'),
    _c('getHealthMax', [('actor', S, 'Actor name')],
       'Returns the maximum health of an actor', ['mua'], 'Combat & Health'),
    _c('setInvulnerable', [('actor', S, 'Actor name'), ('on', B, 'True = invulnerable')],
       'Toggles damage immunity for the actor', ['mua'], 'Combat & Health'),
    _c('setAggression', [('actor', S, 'NPC name'), ('level', I, 'Aggression 0-100')],
       'Adjusts the NPC\'s AI aggression level', ['mua'], 'Combat & Health'),

    # ── Spawning ────────────────────────────────────────────────────────
    _c('spawn', [('entity', S, 'Entity name'), ('marker', S, 'Map locator')],
       'Spawns an entity at a map locator', ['xml2', 'mua'], 'Spawning'),
    _c('despawn', [('entity', S, 'Entity name')],
       'Removes an entity from the active pool', ['xml2'], 'Spawning'),
    _c('spawnEffect', [('fx', S, 'Effect name'), ('node', S, 'Trigger node')],
       'Plays a specific FX at the designated node', ['mua'], 'Spawning'),
    _c('spawnInventoryItem', [('item', S, 'Item name'), ('node', S, 'Trigger node')],
       'Drops a physical item at the map location', ['mua'], 'Spawning'),
    _c('giveItem', [('item', S, 'Item name'), ('count', I, 'Quantity')],
       'Adds equipment/consumables to inventory', ['xml2'], 'Spawning'),
    _c('dropItem', [('item', S, 'Item name')],
       'Forces the player to drop an item', ['xml2'], 'Spawning'),
    _c('giveTechbits', [('amount', I, 'Techbit amount')],
       'Modifies global techbit currency', ['xml2'], 'Spawning'),

    # ── Animation ───────────────────────────────────────────────────────
    _c('playAnim', [('entity', S, 'Entity name'), ('anim', S, 'Animation name')],
       'Forces an entity to play an animation sequence', ['xml2', 'mua'], 'Animation'),
    _c('stopAnim', [('entity', S, 'Entity name')],
       'Interrupts current animation', ['xml2'], 'Animation'),
    _c('setAnimSpeed', [('actor', S, 'Actor name'), ('speed', F, 'Multiplier')],
       'Multiplies animation playback speed', ['mua'], 'Animation'),
    _c('setScale', [('actor', S, 'Actor name'), ('scale', F, 'Scale factor')],
       'Resizes the actor\'s model scale', ['mua'], 'Animation'),
    _c('setAlpha', [('actor', S, 'Actor name'), ('alpha', F, 'Transparency 0-1')],
       'Sets the transparency of the actor model', ['mua'], 'Animation'),
    _c('addBolton', [('actor', S, 'Actor'), ('item', S, 'Model'), ('bone', S, 'Bone name')],
       'Attaches a model piece to an actor\'s bone', ['mua'], 'Animation'),

    # ── Camera ──────────────────────────────────────────────────────────
    _c('shakeCamera', [('intensity', F, 'Shake intensity'), ('duration', F, 'Duration (s)')],
       'Triggers camera shake', ['xml2'], 'Camera'),
    _c('setCameraTarget', [('target', S, 'Entity to track')],
       'Forces camera to track a specific object', ['xml2'], 'Camera'),
    _c('releaseCamera', [],
       'Returns camera control to the player', ['xml2'], 'Camera'),

    # ── Audio & Music ───────────────────────────────────────────────────
    _c('playMovie', [('movie', S, 'Movie filename')],
       'Plays a BIK cinematic file', ['xml2'], 'Audio & Music'),
    _c('playMusic', [('track', S, 'Music track name')],
       'Changes the background music track', ['xml2'], 'Audio & Music'),
    _c('stopMusic', [],
       'Fades out current music', ['xml2'], 'Audio & Music'),
    _c('playSound', [('sound', S, 'Sound effect name')],
       'Plays a specific sound effect', ['xml2'], 'Audio & Music'),
    _c('setMusicOverride', [('track', S, 'Track name')],
       'Forces a specific audio track to play', ['mua'], 'Audio & Music'),
    _c('showMovies', [('on', B, 'Enable playback')],
       'Enables playback of cinematic files', ['mua'], 'Audio & Music'),

    # ── UI & HUD ────────────────────────────────────────────────────────
    _c('fadeToBlack', [('duration', F, 'Fade duration (s)')],
       'Fades the screen out over time', ['xml2'], 'UI & HUD'),
    _c('fadeFromBlack', [('duration', F, 'Fade duration (s)')],
       'Fades the screen in over time', ['xml2'], 'UI & HUD'),
    _c('showHUD', [('on', B, 'True to show')],
       'Toggles the entire user interface', ['xml2'], 'UI & HUD'),
    _c('messageBox', [('text', S, 'Message text')],
       'Displays a pop-up text box', ['xml2'], 'UI & HUD'),
    _c('setTimer', [('name', S, 'Timer name'), ('seconds', I, 'Countdown seconds')],
       'Starts an on-screen countdown', ['xml2'], 'UI & HUD'),
    _c('stopTimer', [('name', S, 'Timer name')],
       'Removes an active countdown', ['xml2'], 'UI & HUD'),
    _c('setHudVisible', [('on', B, 'True to show')],
       'Toggles the visibility of the UI/HUD', ['mua'], 'UI & HUD'),
    _c('showHealthBar', [('actor', S, 'Boss actor'), ('on', B, 'Show bar')],
       'Displays the boss health bar UI', ['mua'], 'UI & HUD'),
    _c('lockControls', [('on', B, 'True to lock')],
       'Disables all player controller inputs', ['mua'], 'UI & HUD'),
    _c('enableInput', [('on', B, 'True to enable')],
       'Disables/Enables player controller input', ['xml2'], 'UI & HUD'),
    _c('setResolution', [('width', I, 'Screen width'), ('height', I, 'Screen height')],
       'Changes the engine\'s render resolution', ['xml2'], 'UI & HUD'),
    _c('toggleConsole', [],
       'Opens the internal engine debug terminal', ['xml2'], 'UI & HUD'),

    # ── Objectives & Missions ───────────────────────────────────────────
    _c('completeMission', [('mission_id', S, 'Mission ID')],
       'Marks a mission ID as complete', ['xml2'], 'Objectives & Missions'),
    _c('setObjective', [('obj_id', S, 'Objective ID'), ('state', I, '0=Hidden, 1=Active, 2=Done')],
       'Sets objective state', ['xml2'], 'Objectives & Missions'),
    _c('startConversation', [('convo', S, 'Conversation ID'), ('player', S, 'Player ref')],
       'Triggers a specific dialogue interaction', ['xml2'], 'Objectives & Missions'),
    _c('stopConversation', [],
       'Ends active dialogue immediately', ['xml2'], 'Objectives & Missions'),
    _c('loadMap', [('map_name', S, 'Map zone name')],
       'Teleports party to a new map file', ['xml2'], 'Objectives & Missions'),
    _c('saveGame', [('slot', I, 'Save slot number')],
       'Triggers a save to the specified slot', ['xml2'], 'Objectives & Missions'),
    _c('loadGame', [('slot', I, 'Save slot number')],
       'Loads the specified save slot', ['xml2'], 'Objectives & Missions'),
    _c('addSimulatorScore', [('points', I, 'Score points')],
       'Increments the current Simulator challenge score', ['mua'], 'Objectives & Missions'),
    _c('getComicMissionVillain', [],
       'Returns the string name of the mission\'s boss', ['mua'], 'Objectives & Missions'),
    _c('getComicMissionHero', [],
       'Returns the required hero for the Comic Mission', ['mua'], 'Objectives & Missions'),
    _c('setComicMissionHeroPlayer', [('player_id', I, 'Player slot')],
       'Assigns the required hero to a specific player slot', ['mua'], 'Objectives & Missions'),
    _c('checkpointSave', [],
       'Forces a checkpoint save to the profile', ['mua'], 'Objectives & Missions'),
    _c('actionFigureReward', [('figure_id', S, 'Figure ID')],
       'Grants the player a collectible action figure', ['mua'], 'Objectives & Missions'),

    # ── Variables & State ───────────────────────────────────────────────
    _c('setGlobal', [('var', S, 'Variable name'), ('value', S, 'Value')],
       'Sets a global script variable', ['xml2'], 'Variables & State'),
    _c('getGlobal', [('var', S, 'Variable name')],
       'Retrieves a global script variable', ['xml2'], 'Variables & State'),
    _c('ent_getproperty', [('entity', S, 'Entity name'), ('prop', S, 'Property name')],
       'Returns value of a C++ member variable', ['xml2'], 'Variables & State'),
    _c('ent_setproperty', [('entity', S, 'Entity'), ('prop', S, 'Property'), ('value', S, 'Value')],
       'Directly writes to a C++ member variable', ['xml2'], 'Variables & State'),
    _c('getZoneVar', [('var', S, 'Variable name')],
       'Retrieves a variable local to the current zone', ['mua'], 'Variables & State'),
    _c('setZoneVar', [('var', S, 'Variable name'), ('value', S, 'Value')],
       'Sets a variable local to the current zone', ['mua'], 'Variables & State'),
    _c('getGameVar', [('var', S, 'Variable name')],
       'Retrieves a global persistent variable', ['mua'], 'Variables & State'),
    _c('setGameVar', [('var', S, 'Variable name'), ('value', S, 'Value')],
       'Sets a global persistent variable', ['mua'], 'Variables & State'),

    # ── Movement & Physics ──────────────────────────────────────────────
    _c('teleport', [('entity', S, 'Entity name'), ('marker', S, 'Locator name')],
       'Moves an entity to a locator instantly', ['xml2'], 'Movement & Physics'),
    _c('setCollision', [('entity', S, 'Entity name'), ('on', B, 'Enable collision')],
       'Toggles physics collision for an object', ['xml2'], 'Movement & Physics'),
    _c('setGravity', [('gravity', F, 'Gravity value')],
       'Modifies map-wide gravity', ['xml2'], 'Movement & Physics'),
    _c('setGameSpeed', [('speed', F, 'Speed multiplier')],
       'Slows down or speeds up engine time', ['xml2'], 'Movement & Physics'),
    _c('setLightGroup', [('entity', S, 'Entity name'), ('group', I, 'Light group ID')],
       'Changes lighting group for a character', ['xml2'], 'Movement & Physics'),
    _c('setOrigin', [('actor', S, 'Actor'), ('x', F, 'X'), ('y', F, 'Y'), ('z', F, 'Z')],
       'Sets the XYZ position of an actor', ['mua'], 'Movement & Physics'),
    _c('setAngles', [('actor', S, 'Actor'), ('x', F, 'X'), ('y', F, 'Y'), ('z', F, 'Z')],
       'Sets the rotation angles of an actor', ['mua'], 'Movement & Physics'),
    _c('setVelocity', [('actor', S, 'Actor'), ('x', F, 'X'), ('y', F, 'Y'), ('z', F, 'Z')],
       'Applies a velocity vector to an actor', ['mua'], 'Movement & Physics'),
    _c('setNoClip', [('actor', S, 'Actor'), ('on', B, 'Enable noclip')],
       'Disables physical collision for the actor', ['mua'], 'Movement & Physics'),
    _c('setNoGravity', [('actor', S, 'Actor'), ('on', B, 'Disable gravity')],
       'Prevents gravity from affecting the actor', ['mua'], 'Movement & Physics'),
    _c('setGoal', [('actor', S, 'NPC name'), ('node', S, 'Destination node')],
       'Sets an AI navigation destination', ['mua'], 'Movement & Physics'),
    _c('setInvisible', [('actor', S, 'Actor'), ('on', B, 'Invisible')],
       'Makes an actor invisible to NPCs and players', ['mua'], 'Movement & Physics'),

    # ── Scripting & Flow ────────────────────────────────────────────────
    _c('wait', [('seconds', F, 'Duration in seconds')],
       'Pauses script execution', ['xml2'], 'Scripting & Flow'),
    _c('exec', [('script', S, 'Script file path')],
       'Executes an external script file', ['xml2'], 'Scripting & Flow'),
    _c('eval', [('code', S, 'Script code string')],
       'Evaluates a string as a script command', ['xml2'], 'Scripting & Flow'),
    _c('print', [('text', S, 'Debug text')],
       'Outputs text to the engine console/log', ['xml2'], 'Scripting & Flow'),
    _c('pauseGame', [('on', B, 'True to pause')],
       'Toggles the game\'s pause state', ['xml2'], 'Scripting & Flow'),
    _c('quitGame', [],
       'Exits the application', ['xml2'], 'Scripting & Flow'),
    _c('runscript', [('path', S, 'Script file path')],
       'Executes an external Python script file', ['mua'], 'Scripting & Flow'),
    _c('pause', [],
       'Pauses the internal game clock/logic', ['mua'], 'Scripting & Flow'),
    _c('resume', [],
       'Resumes the internal game clock/logic', ['mua'], 'Scripting & Flow'),
    _c('setSpecialMode', [('mode', I, 'Mode ID')],
       'Sets internal engine state (debug/special)', ['mua'], 'Scripting & Flow'),

    # ── Environment ─────────────────────────────────────────────────────
    _c('setWeather', [('weather', S, 'Weather preset name')],
       'Changes map environmental effects', ['xml2'], 'Environment'),
    _c('setAIState', [('entity', S, 'Entity name'), ('state', S, 'AI state name')],
       'Changes follower AI behavior', ['xml2'], 'Environment'),
    _c('setAIActive', [('actor', S, 'Actor name'), ('on', B, 'Enable AI')],
       'Enables or disables the AI for a specific NPC', ['mua'], 'Environment'),
    _c('setUnderWater', [('on', B, 'Enable water')],
       'Enables water physics/visuals for the zone', ['mua'], 'Environment'),
]
# fmt: on


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_commands_by_category(game_filter=None):
    """Return commands grouped by category, optionally filtered by game.

    Args:
        game_filter: 'xml2', 'mua', or None for all.

    Returns:
        dict mapping category name → list of command dicts, in CATEGORIES order.
    """
    result = {}
    for cat in CATEGORIES:
        result[cat] = []

    for cmd in SCRIPT_COMMANDS:
        if game_filter and game_filter not in cmd['games']:
            continue
        cat = cmd['category']
        if cat in result:
            result[cat].append(cmd)

    # Remove empty categories
    return {k: v for k, v in result.items() if v}


def get_snippet(cmd):
    """Return an insertion string for the command.

    Example: game.unlockCharacter('hero')
    """
    parts = []
    for p in cmd['params']:
        if p['type'] == 'str':
            parts.append(f"'{p['name']}'")
        elif p['type'] == 'bool':
            parts.append('True')
        elif p['type'] == 'int':
            parts.append('0')
        elif p['type'] == 'float':
            parts.append('0.0')
        else:
            parts.append(p['name'])
    return f"game.{cmd['name']}({', '.join(parts)})"


def get_doc_html(cmd):
    """Return a formatted HTML doc string for the command help panel."""
    game_str = ', '.join(sorted(g.upper() for g in cmd['games']))

    lines = [
        f"<h3 style='color: #e0c040; margin: 0;'>game.{cmd['name']}</h3>",
        f"<p style='color: #a0a0a0; margin: 4px 0;'>{cmd['desc']}</p>",
        f"<p style='color: #70a0d0; margin: 4px 0;'>Game: {game_str}</p>",
    ]

    if cmd['params']:
        lines.append("<table style='margin: 8px 0; color: #d0d0d0;'>")
        lines.append("<tr style='color: #e0c040;'>"
                     "<th align='left'>Param</th>"
                     "<th align='left'>Type</th>"
                     "<th align='left'>Description</th></tr>")
        for p in cmd['params']:
            lines.append(
                f"<tr><td style='padding-right:12px;'><b>{p['name']}</b></td>"
                f"<td style='padding-right:12px; color:#70a0d0;'>{p['type']}</td>"
                f"<td>{p['desc']}</td></tr>"
            )
        lines.append("</table>")
    else:
        lines.append("<p style='color: #808080;'><i>No parameters</i></p>")

    lines.append(f"<pre style='color: #80c080; margin: 8px 0;'>{get_snippet(cmd)}</pre>")

    return '\n'.join(lines)
