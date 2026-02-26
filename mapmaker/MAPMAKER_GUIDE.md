# Map Maker Tools — User Guide

> Create custom maps for X-Men Legends II directly inside Blender.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Map Settings](#map-settings)
4. [Entity System](#entity-system)
5. [Character Database](#character-database)
6. [Model Browser](#model-browser)
7. [Entity Placement & Actor Previews](#entity-placement--actor-previews)
8. [Conversation Maker](#conversation-maker)
9. [Precache & Characters](#precache--characters)
10. [Navigation Mesh](#navigation-mesh)
11. [Build Pipeline](#build-pipeline)
12. [ENGB Import](#engb-import)
13. [File Format Reference](#file-format-reference)

---

## Overview

The Map Maker is a set of Blender tools for authoring X-Men Legends II map zones.
It generates the five binary data files every map zone needs:

| File | Purpose |
|------|---------|
| `.engb` | Entity definitions and instances (NPCs, triggers, lights, etc.) |
| `.chrb` | Character list (which characters can appear in this zone) |
| `.navb` | Navigation grid (AI pathfinding cells) |
| `.boyb` | Buoy markers (usually empty) |
| `.pkgb` | Package manifest (lists all assets to load for this zone) |

All five are **XMLB binary** files (magic `0x11B1`). The Map Maker generates
standard XML, then compiles to XMLB using the bundled `xmlb.py` codec.

---

## Prerequisites

- **Blender 4.4+** with the IGB addon installed
- **Game data directory** — point the addon at the root of your extracted game files
  (the folder containing `actors/`, `data/`, `maps/`, `models/`, etc.)

---

## Map Settings

Found in **Map Maker > Map Settings**.

| Field | Description |
|-------|-------------|
| **Map Name** | Zone name (e.g. `sanctuary1`). Used as the base filename. |
| **Map Path** | Path prefix (e.g. `act1/sanctuary`). Used for deploy paths. |
| **Act / Level** | Game act (1-5) and level number. |
| **Zone Script** | BehavEd script path. Auto-generated from map path + name if blank. |
| **Sound File** | Ambient sound profile name. |
| **Party Light** | Color of the party's ambient light (RGB). |
| **Party Light Radius** | Radius of the party light. |
| **Combat Locked** | Whether the zone starts in combat-locked state. |
| **No Save** | Disable save points in this zone. |

### Build Paths

| Field | Description |
|-------|-------------|
| **Output Directory** | Where generated XML and compiled XMLB files are written. |
| **Game Data Directory** | Root of game data. Used for character DB, model browser, actor previews, and auto-deploy on Build All. |

---

## Entity System

### Entity Classnames

Every entity has a **classname** that determines its behavior in-game:

| Classname | Visual | Color | Purpose |
|-----------|--------|-------|---------|
| `monsterspawnerent` | Arrow | Orange | NPC/enemy spawner |
| `playerstartent` | Arrows | Green | Player spawn point |
| `zonelinkent` | Cube | Blue | Zone transition trigger |
| `lightent` | Sphere | Yellow | Dynamic light source |
| `actionent` | Axes | Magenta | FX/animation trigger |
| `gameent` | Axes | Red | General purpose trigger |
| `physent` | Cube | Gray | Physical/breakable object |
| `doorent` | Cube | Brown | Door or gate |
| `cameramagnetent` | Cone | Cyan | Camera control zone |
| `waypointent` | Circle | Gold | AI waypoint |
| `ent` | Axes | Light Gray | Empty/null entity |

### Entity Definitions

Found in **Map Maker > Entity Definitions**.

An entity definition is a *template*. It defines:
- **Name** — unique identifier (e.g. `sp_beast01`)
- **Classname** — one of the types above
- **Character** — character name for `monsterspawnerent` (links to npcstat/herostat)
- **Model** — model path for `physent`/`doorent`
- **No Collide** — whether the entity has collision
- **Custom Properties** — any additional key-value pairs (e.g. `actscript`, `team`, `health`)

Click **Apply Defaults** to auto-fill common properties for the selected classname.

### Custom Properties

Each entity def has an expandable **Custom Properties** list for any attributes
not covered by the dedicated fields. Common examples:

- `actscript` — BehavEd script to run on activation
- `team` — `hero` or `villain`
- `extents` — collision box size
- `health` — hit points
- `boxcollision` — use box collision
- `lightcolor` / `lightradius` — for `lightent`

---

## Character Database

Found in **Map Maker > Character Database** (collapsed by default).

### Loading the Database

1. Set **Game Data Directory** in Map Settings
2. Click **Load Character DB**

This decompiles `data/npcstat.engb` and `data/herostat.engb` from the game data
and builds a searchable list of all characters.

### Searching

Type in the search field to filter by character name, internal ID, or team.

### Character Info

Each entry shows:
- **Display name** (e.g. "Wolverine")
- **ID** (internal name, e.g. "Wolverine")
- **Team** (hero/enemy) with icon
- **Skin code** (e.g. "0303" — maps to `actors/0303.igb`)
- **Source** (herostat or npcstat)
- **Animation set**

### Picking a Character

1. Select an entity definition in the Entity Definitions panel
2. Select a character in the Character Database
3. Click **Pick for Active Entity Def**

This sets the entity def's character field and auto-adds the character to the
CHRB character list.

---

## Model Browser

Found in **Map Maker > Model Browser** (collapsed by default).

### Scanning Models

1. Set **Game Data Directory** in Map Settings
2. Click **Scan Models**

This walks the `models/` directory and catalogs all `.igb` files by category
(subdirectory).

### Filtering

- **Search** — filter by model filename
- **Category** — filter by subdirectory (e.g. `sanctuary`, `deadzone`, `jungle`)

### Picking a Model

1. Select an entity definition (typically `physent` or `doorent`)
2. Select a model in the browser
3. Click **Pick for Active Entity Def**

This sets the entity def's model path.

---

## Entity Placement & Actor Previews

Found in **Map Maker > Entity Placement**.

### Placing Entities

1. Select an entity definition in the Entity Definitions panel
2. Position the 3D cursor where you want the entity
3. Click **Place Entity**

This creates a color-coded Empty at the cursor position. The Empty's shape and
color match the entity classname.

### Actor Previews

When **Show Actor Previews** is enabled and you place a `monsterspawnerent`:
1. The system looks up the character in the database
2. Finds the actor `.igb` file via the skin code
3. Imports the actor mesh and parents it to the entity Empty

Preview meshes are tagged with `mm_preview = True` and are **automatically
excluded** from XML generation and XMLB compilation.

### Preview Controls

| Button | Action |
|--------|--------|
| **Refresh** | Strip all previews and re-import from actor files |
| **Strip All** | Remove all preview meshes from the scene |

### Managing Instances

- **Select By Type** — select all instances of the active entity def type
- **Remove Instance** — delete selected entity instance(s) and their preview children

---

## Conversation Maker

Found in **Map Maker > Conversations** (collapsed by default).

### Conversation Structure

X-Men Legends II conversations use a nested tree:

```
conversation
  startCondition (gates when this branch is available)
    participant (the NPC speaking)
      line (NPC dialogue)
        response (player choice)
          line (NPC reply to choice)
            response ...
```

### Creating a Conversation

1. Click **+** to add a new conversation
2. Set the **Name** and **Path** (path prefix for the game's conversations/ directory)
3. Click **Add Start Condition** to create a skeleton (startCondition + participant + line + response)

### Building the Tree

- Select a node in the tree list
- **Add Line+Response** — adds a line with a response child under the selected node
- **Line** — adds just a line node
- **Response** — adds just a response node
- **Remove Node** — deletes the selected node and all its descendants

### Node Properties

When you select a node, its editable properties appear below the tree:

**Start Condition:**
- Condition Script — BehavEd script that gates availability
- Run Once — only trigger once

**Participant:**
- Participant Name — usually "default"

**Line (NPC Dialogue):**
- Text — dialogue text (use `%CharName%` for the NPC name)
- Sound — voice file path
- Text (Brotherhood) / Sound (Brotherhood) — faction variant
- Tag Index — for conversation hub/loop targets

**Response (Player Choice):**
- Response Text — what the player sees
- Chosen Script — script to run when chosen
- Script File — BehavEd script path
- End Conversation — end after this choice
- Tag Jump — jump to a tagged node
- Tag Index — this node's tag
- X-Men Only / Brotherhood Only — faction gating

### Import / Export

- **Export** — generates XML and compiles to XMLB. Auto-adds to precache.
- **Import** — decompiles an existing `.engb` conversation file and loads the tree.

---

## Precache & Characters

### Precache (Map Maker > Precache)

Lists resources the game must preload for this zone. Types:
- `conversation` — dialogue file
- `dialog` — dialog file
- `fx` — visual effect
- `model` — 3D model
- `script` — BehavEd script
- `sound` — sound file

**Auto-Scan Precache** examines entity definitions and auto-discovers:
- Conversation references from `actscript` properties
- Script paths from script properties
- FX references from `loopfx`, `deatheffect`, `acteffect`
- Sound references from `actsound`, `actsoundloop`, `spawnsoundloop`
- Model references from `physent`/`doorent` model fields

### Characters (Map Maker > Characters)

The CHRB character list. Lists all characters that can appear in this zone.

**Auto-Scan Characters** examines `monsterspawnerent` entity definitions and
auto-populates the list. Characters are also auto-added when using the Character
Database **Pick** button.

---

## Navigation Mesh

Found in **Map Maker > Navigation Mesh** (collapsed by default).

### Generating the NavMesh

1. Select the mesh object(s) that represent the floor
2. Set **Nav Cell Size** (default 40 game units)
3. Click **Generate NavMesh**

The algorithm:
1. Computes the world-space bounding box of selected meshes
2. Creates a grid at cell size intervals over the XY extent
3. For each grid cell center, raycasts downward to find floor height
4. Records walkable cells as `(grid_x, grid_y, world_z)` tuples

### Visualizing

Click **Visualize NavMesh** to create a wireframe mesh preview showing the grid.

---

## Build Pipeline

Found in **Map Maker > Build**.

### Individual Steps

| Button | Action |
|--------|--------|
| **Generate XML** | Creates `.engb.xml`, `.chrb.xml`, `.navb.xml`, `.boyb.xml`, `.pkgb.xml` in the output directory |
| **Compile XMLB** | Compiles all `*.{engb,chrb,navb,boyb,pkgb}.xml` to binary XMLB format |

### Build All

Runs the full pipeline:
1. Generate XML
2. Compile XMLB
3. Copy compiled files to game data directory (if set)

The deploy path is: `{game_data_dir}/maps/{map_path}/{map_name}.{ext}`

---

## ENGB Import

Found in **Map Maker > Import** (collapsed by default).

Click **Import ENGB File** and select an existing `.engb` file. The importer:

1. Decompiles the XMLB binary to an XML tree
2. Reads the `<world>` entity to populate Map Settings
3. Creates entity definitions from `<entity>` elements
4. Creates precache entries from `<precache>` elements
5. Creates entity instances (Empties) from `<entinst>` blocks with correct positions and rotations

---

## File Format Reference

### XMLB Binary Format

- **Magic**: `0x11B1`
- **Version**: `1`
- **Structure**: Header + element tree (inline) + string table (at end)
- **Element**: `name_offset`, `next_element_offset`, `sub_element_offset`, `attr_count`
- **Attribute**: `name_offset`, `value_offset`
- **Strings**: null-terminated, CP1252 encoded

### ENGB (Entity Data)

```xml
<world>
    <entity name="world" act="1" level="1" zonescript="..." ... />
    <entity classname="monsterspawnerent" name="sp_beast01" character="Beast" ... />
    <precache filename="act1/sanctuary/..." type="conversation" />
    <entinst type="sp_beast01">
        <inst name="sp_beast01" pos="100 200 0" orient="0 0 1.57" />
    </entinst>
</world>
```

### CHRB (Character List)

```xml
<characters>
    <character name="Wolverine" />
    <character name="Beast" />
</characters>
```

### NAVB (Navigation Grid)

```xml
<nav cellsize="40">
    <c p="10 20 0" />
    <c p="11 20 0" />
</nav>
```

### BOYB (Buoy Markers)

```xml
<Buoys />
```

### PKGB (Package Manifest)

```xml
<packagedef>
    <model filename="maps/act1/sanctuary/sanctuary1" />
    <script filename="scripts/act1/sanctuary/sanctuary1" />
    <xml filename="data/npcstat" />
    <xml filename="conversations/act1/sanctuary/..." />
</packagedef>
```

### Conversation Format

```xml
<conversation>
    <startCondition conditionscriptfile="..." runonce="true">
        <participant>
            <name>default</name>
            <line text="%CharName%: Hello." soundtoplay="voice/...">
                <response text="Player choice" conversationend="true" />
                <response text="Another choice">
                    <line text="%CharName%: More dialogue.">
                        <response text="OK" conversationend="true" />
                    </line>
                </response>
            </line>
        </participant>
    </startCondition>
</conversation>
```

Key conversation attributes:
- `textb` / `soundtoplayb` — Brotherhood faction variants
- `onlyif_xman` / `onlyif_brotherhood` — faction-gated responses
- `tagjump` / `tagindex` — conversation hub/loop jumps
- `chosenscriptfile` — script executed when response is chosen
- `conversationend` — ends the conversation after this response
