# IGB Blender Addon

A Blender 4.4 addon for importing and exporting Alchemy Engine IGB/IGZ files used by **X-Men Legends II** and **Marvel Ultimate Alliance** (PC).

Includes a full map maker toolkit, actor/skeleton pipeline, and rig converter for bringing custom characters into the game.

Created by **Kaiko** with the aid of **Claude**.

---

## Installation

1. Download or clone this repo.
2. Copy the entire `igb_blender` folder into your Blender addons directory:
   ```
   <Blender Install>/scripts/addons/igb_blender/
   ```
   For portable installs this is typically:
   ```
   <Blender Portable>/scripts/addons/igb_blender/
   ```
3. Open Blender and go to **Edit > Preferences > Add-ons**.
4. Search for **"IGB Format"** and enable it.
5. The addon adds three sidebar tabs (press `N` in the 3D viewport): **IGB**, **IGB Actors**, and **Map Maker**.

### PySide6 (Optional)

The standalone Conversation Editor and Menu Editor windows require PySide6. You can install it from the **IGB > IGB Extras** panel inside Blender, then restart.

---

## Supported Games

| Game | Platform | Import | Export |
|------|----------|--------|--------|
| X-Men Legends I  | PS2, Xbox | IGB | - |
| X-Men Legends II | PC | IGB | IGB |
| X-Men Legends II | PS2, PSP, Xbox | IGB | - |
| Marvel Ultimate Alliance | PC | IGB | IGB |
| Marvel Ultimate Alliance | PS2, PSP, Xbox | IGB | - |
| Marvel Ultimate Alliance 2 | PS2, PSP | IGB | - |
| Marvel Ultimate Alliance 2 | PC | IGZ (Broken) | - |

The addon auto-detects the game format on import. You can also pick a game preset manually in the import dialog.

---

## File Menu: Import / Export

These show up in **File > Import** and **File > Export**.

### Import IGB / IGZ

Opens an Alchemy Engine file and brings geometry, materials, textures, collision, and lights into Blender.

**Options in the import dialog:**
- **Game Preset** - Auto-detect or pick a specific game/platform.
- **Import Normals** - Bring in vertex normals.
- **Import UVs** - Bring in texture coordinates.
- **Import Vertex Colors** - Bring in per-vertex color data.
- **Import Materials** - Create Blender materials with the embedded textures.
- **Import Collision** - Import collision hull as mesh in a "Colliders" collection.
- **Import Lights** - Import scene lights.
- **Texture Directory** (IGZ only) - Point to the `materials/` folder for external textures.

### Export IGB

Exports your scene to an IGB file the game can load. Uses a template-based approach (reads a reference IGB and patches in your data).

**Options in the export dialog:**
- **Texture Format** - Choose the encoding:
  - *CLUT (Universal)* - 256-color palette. Works in both XML2 and MUA. Recommended.
  - *DXT5 (XML2 Only)* - DXT5 with standard color endpoints.
  - *DXT5 (MUA Only)* - DXT5 with swapped color endpoints for MUA.
- **Collision Source** - Where to get collision geometry:
  - *Colliders Collection* - Use objects from the "Colliders" collection.
  - *Visual Mesh* - Auto-generate from the visible geometry.
  - *None* - Skip collision.
- **Surface Type** - Physics surface (0 = default, 1 = stone, 12 = wood, etc.).
- **Export Lights** - Include scene lights in the output.

### Import / Export ZAM

Opens or saves `.zam` minimap files used by the in-game automap. Available in File > Import/Export.

---

## Sidebar Panels

Press `N` in the 3D Viewport to open the sidebar. The addon adds three tabs:

---

### IGB Tab

The main tools tab. Everything for working with IGB files outside of characters and map building.

#### Import / Export
Quick-access buttons for importing IGB, IGZ, and ENGB files, plus exporting IGB. Same functionality as the File menu entries but right in the sidebar. Also has a **Models Directory** path used when importing ENGB files (so it can load the referenced entity models).

#### Collision
Tools for creating and managing collision geometry that gets baked into the IGB file on export.

- **Generate Colliders** - Three auto-generation methods:
  - *Box Colliders* - One bounding box per mesh object. Fastest, good for blocky geometry.
  - *Convex Hulls* - One convex hull per object. Tighter fit for organic shapes.
  - *Decimated Mesh* - Merges all visible geometry and reduces triangle count. Best surface coverage.
- **Manual Tools**:
  - *Create Colliders Collection* - Makes the "Colliders" collection if it doesn't exist.
  - *Add Box at Cursor* - Drops a box collider at the 3D cursor.
  - *Merge Colliders* - Joins all collider objects into one mesh and welds seam vertices.
- **Status** - Shows collider count and triangle count. Warns if you exceed the game engine's ~15,000 triangle limit.

#### Materials
- **Convert All to IGB** - Stamps IGB custom properties (lighting, blend, alpha, color) onto every material in the scene. Does this by analyzing Blender's material settings (transparency, backface culling, etc.) and setting appropriate IGB values.
- Shows a status count of how many materials are IGB-ready vs still need converting.

#### Quick Tools
Batch operators that apply a setting to every IGB material at once. Useful for quickly tweaking all materials in a scene. Has a **Selected Only** toggle to limit changes to selected objects.

Sub-panels:
- **Lighting** - Toggle scene lighting on/off. Off = fully bright (for lava, sky, energy effects).
- **Backface Culling** - Toggle single-sided vs double-sided faces.
- **UV Animation** - Enable UV scrolling/rotation (for water, conveyor belts, energy beams).
- **Alpha Test** - Toggle cutout transparency (for foliage, fences, grates).
- **Blend State** - Toggle semi-transparency (for glass, water, smoke).
- **Color Tint** - Set the color multiplier on all materials. White = no tint.
- **Shader** - Batch set shader presets (e.g. additive glow, standard opaque).
- **Material Colors** - Batch set diffuse, ambient, specular, emission, and shininess.

#### IGB Extras
- **Merge Duplicate Materials** - Cleans up `.001` / `.002` material duplicates by merging materials that share the same texture and IGB properties.
- **PySide6 Install** - One-click install of PySide6 for the external editor windows.

#### Credits & References
Lists the open-source projects this addon references.

---

### IGB Actors Tab

Everything for working with game characters: skeletons, skins, animations, and rigs.

#### Import
- **Game Directory** - Set the path to your game install (so the addon can find actor IGB files in `actors/`).
- **Import Actor** - Pick a character and import their full skeleton, skin meshes, and animations.
- **Import Options** - Toggle whether to import skins, animations, and/or materials.
- **Rig Converter** - Appears when you select a non-IGB armature (e.g. from VRChat/Unity/Mixamo). Converts the bone hierarchy, names, and transforms to match XML2's skeleton format so the character works in-game.

#### Skins
Manages the skin mesh variants attached to an actor.

- **Skin List** - Shows all skins with visibility toggles and skin codes (e.g. `0101`).
- **Solo Selected** - Hides all skins except the selected one.
- **Import / Export Skin** - Load or save individual skin IGB files.
- **Add / Remove** - Attach a Blender mesh as a new skin or remove one.

#### Animations
Manages animation clips on the actor.

- **Animation List** - Shows all loaded animations with play buttons and durations.
- **Add / Remove** - Import single animations or remove them.
- **Playback Controls** - Rest Pose / Play / Stop buttons.
- **Info Box** - Duration, track count, and frame count for the selected animation.
- **Import / Save Animations** - Batch import or export all animations.

#### Materials
Shows the IGB material properties for the selected skin's material. Lets you edit render state (blend, alpha test, lighting, culling) and material colors (diffuse, ambient, specular, emission, shininess) directly. Also has **Quick Tools** buttons for applying settings across all skins at once.

---

### Map Maker Tab

A full level editor for creating custom game maps. Handles all the game files you need: `.igb` (geometry), `.engb` (entities), `.chrb` (characters), `.navb` (navigation), `.boyb` (bounding), `.pkgb` (packages/conversations).

#### MM Map Settings
Global settings for your map.

- **Map Name / Map Path** - The internal name and path the game uses to load your map.
- **Act / Level** - Which act and level number this map belongs to.
- **Zone Script** - Lua script file for the zone.
- **Sound File** - Background music/ambient sound.
- **Party Light** - Light color and radius for the player party.
- **Combat Locked / No Save** - Gameplay flags.
- **Big Conv Map** - Enable if this map has NPC conversations.
- **Automap** - Path for the minimap data.
- **Build Paths** - Output directory and game data directory.

#### MM Entities
Define and place game entities (NPCs, doors, triggers, items, etc.).

- **Entity Definitions List** - Searchable list of entity types you've defined for this map.
- **Entity Details** - Edit the selected definition: name, classname, character reference, model path, collision toggle, and custom key-value properties.
- **Placement** - Place instances of the selected entity at the 3D cursor. Select/remove placed instances. Refresh 3D previews of placed characters and models.
- **Quick Presets** - One-click buttons for common entity types.
- **Precache** - Resources the game should preload. Auto-scan fills this from your placed entities.
- **Characters (CHRB)** - Character list for the map's `.chrb` file. Auto-scan detects characters from placed NPC entities.

#### MM Character Database
Browse the game's existing NPC and hero roster.

- **Load Character DB** - Reads `npcstat.engb` and `herostat.engb` from your game data directory.
- **Search** - Filter by name, ID, or team.
- **Character Details** - Name, ID, team, skin, anim set.
- **Quick Place at Cursor** - Instantly creates an entity definition and places it.
- **Pick for Active Entity Def** - Sets the character on your selected entity definition.
- **NPC/Hero Stat Editor** - Opens an external editor window for npcstat or herostat files.

#### MM Model Browser
Browse game model assets from the `models/` directory.

- **Scan Models** - Indexes all `.igb` files in the models directory.
- **Search / Category Filter** - Find models by name or category (props, environment, etc.).
- **Quick Place at Cursor** - Creates an entity with the model and places it.
- **Import as Asset** - Import the model geometry into Blender.
- **Asset Library** - Batch import an entire category for use as Blender assets.

#### MM Conversations
Build NPC dialogue trees that become PKGB conversation files.

- **Conversation List** - Create, remove, and select conversations.
- **Settings** - Name, file path, HUD portrait reference.
- **Edit Conversation** - Opens the external PySide6 dialogue editor (requires PySide6).
- **Dialogue Tree** - Inline tree view of all nodes. Nodes are indented to show parent/child relationships, with icons indicating type (start condition, participant, line, response) and metadata (sound, end flag, tag jump, faction gating).
- **Add Nodes** - Add start conditions, line+response pairs, individual lines, or individual responses.
- **Dialogue Preview** - Game-style preview showing how the conversation looks in-game: NPC speech boxes, player response choices, faction variants, and follow-up lines.
- **Node Properties** - Edit the selected node's text, sounds, scripts, branching tags, faction gating, and other attributes.
- **Import / Export** - Save or load conversation XML files.
- **Link to NPC** - Connect a conversation to a placed NPC entity.

#### MM Navigation
Generate navigation mesh data for AI pathfinding.

- **Cell Size** - Grid resolution for the nav mesh.
- **Advanced** - Max slope angle, multi-layer support, layer separation distance.
- **Generate Nav Mesh** - Computes walkable cells from scene geometry.
- **Visualize Nav Mesh** - Shows the generated nav grid as a wireframe overlay.

#### MM Automap
Create and manage the in-game minimap.

- **Import / Export ZAM** - Load or save `.zam` minimap files.
- **Generate Automap Mesh** - Create an automap from selected floor geometry.

#### MM Build
Compile all your map data into game-ready files.

- **Generate XML** - Creates the XML source files for all map components.
- **Compile XMLB** - Converts XML to the game's binary XMLB format.
- **Build All** - Runs the full pipeline (XML generation + XMLB compilation + IGB export) in one click.
- **Collision Preview** - Visualize what the exported collision looks like.
- **Menu Editor** - Opens an external window for editing the game's menu files.

---

## Properties Panel: Material State

In the **Properties Editor > Material tab**, a new **IGB Material State** panel appears when a material has IGB properties. This shows the raw Alchemy render state: blend function, alpha test, color tint, lighting, culling, UV animation, and full material color breakdown (diffuse, ambient, specular, emission, shininess). This is the detailed view for fine-tuning individual materials.

---

## Credits

Built on research and code from these projects:

- **IGBConverter** by nikita488 (MIT)
- **IGBDataExtractor** by ChmtTnky
- **igBlender** by ak2yny (GPLv3)
- **raven-formats** by nikita488 (MIT)
- **Crash-NST-Level-Editor** by kishimisu
- **IGB parser gist** by mateon1
- **fmt_alchemy_igz** by Nefarious/ak2yny
