"""Map Maker Tool panels — self-contained top-level panels in the Map Maker N-panel tab.

Panel structure:
  MM Map Settings    — world entity, build paths
  MM Entities        — entity defs, placement, presets, previews, precache, characters
  MM Character DB    — npcstat/herostat browser, quick place
  MM Model Browser   — models/ directory browser, quick place, asset library
  MM Conversations   — dialogue tree editor, import/export
  MM Navigation      — navmesh generation and visualization
  MM Build           — XML generation, XMLB compilation, ENGB import
"""

import bpy
from bpy.types import Panel, UIList


# ===========================================================================
# UIList renderers
# ===========================================================================

class MM_UL_entity_defs(UIList):
    """UIList for entity definitions with search/filter support."""
    bl_idname = "MM_UL_entity_defs"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "entity_name", text="", emboss=False)
            row.label(text=item.classname, icon='NONE')
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.entity_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt_flags = [0] * len(items)
        flt_neworder = list(range(len(items)))

        # Search filter — match against entity_name and classname
        if self.filter_name:
            query = self.filter_name.lower()
            for i, item in enumerate(items):
                if (query in item.entity_name.lower()
                        or query in item.classname.lower()):
                    flt_flags[i] = self.bitflag_filter_item
        else:
            flt_flags = [self.bitflag_filter_item] * len(items)

        return flt_flags, flt_neworder


class MM_UL_entity_properties(UIList):
    """UIList for custom key-value properties on an entity def."""
    bl_idname = "MM_UL_entity_properties"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            split = layout.split(factor=0.4)
            split.prop(item, "key", text="", emboss=False)
            split.prop(item, "value", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.label(text=f"{item.key}={item.value}")


class MM_UL_precache(UIList):
    """UIList for precache entries."""
    bl_idname = "MM_UL_precache"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "filename", text="", emboss=False)
            row.prop(item, "entry_type", text="")
        elif self.layout_type == 'GRID':
            layout.label(text=item.filename)


class MM_UL_characters(UIList):
    """UIList for character entries."""
    bl_idname = "MM_UL_characters"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "char_name", text="", emboss=False, icon='ARMATURE_DATA')
        elif self.layout_type == 'GRID':
            layout.label(text=item.char_name)


class MM_UL_char_db(UIList):
    """UIList for character database entries with search filtering."""
    bl_idname = "MM_UL_char_db"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Team icon
            if item.team == 'hero':
                team_icon = 'COMMUNITY'
            elif item.team in ('enemy', 'boss'):
                team_icon = 'GHOST_ENABLED'
            else:
                team_icon = 'OUTLINER_OB_EMPTY'
            row.label(text="", icon=team_icon)
            row.label(text=item.display_name)
            row.label(text=item.skin)
        elif self.layout_type == 'GRID':
            layout.label(text=item.display_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        search = context.scene.mm_char_search.lower()

        # Filter
        flt_flags = [self.bitflag_filter_item] * len(items)
        if search:
            for i, item in enumerate(items):
                if (search not in item.display_name.lower() and
                        search not in item.name_id.lower() and
                        search not in item.team.lower()):
                    flt_flags[i] = 0

        # No re-ordering needed (already sorted)
        flt_neworder = list(range(len(items)))
        return flt_flags, flt_neworder


class MM_UL_model_db(UIList):
    """UIList for model database entries with search/category filtering."""
    bl_idname = "MM_UL_model_db"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=item.display_name, icon='MESH_CUBE')
            row.label(text=item.category)
        elif self.layout_type == 'GRID':
            layout.label(text=item.display_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        search = context.scene.mm_model_search.lower()
        category = context.scene.mm_model_filter_category

        # Filter
        flt_flags = [self.bitflag_filter_item] * len(items)
        for i, item in enumerate(items):
            if search and search not in item.display_name.lower():
                flt_flags[i] = 0
            elif category and item.category != category:
                flt_flags[i] = 0

        flt_neworder = list(range(len(items)))
        return flt_flags, flt_neworder


class MM_UL_conversations(UIList):
    """UIList for conversations."""
    bl_idname = "MM_UL_conversations"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "conv_name", text="", emboss=False, icon='OUTLINER')
        elif self.layout_type == 'GRID':
            layout.label(text=item.conv_name)


class MM_UL_conv_nodes(UIList):
    """UIList for conversation nodes with tree indentation."""
    bl_idname = "MM_UL_conv_nodes"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            from .conversation import (
                get_flat_display_order, get_node_indicators, parse_speaker_text,
            )

            display = get_flat_display_order(data)
            depth = 0
            display_text = ""
            node_icon = 'BLANK1'
            indicators = None

            if index < len(display):
                node, depth = display[index]
                indicators = get_node_indicators(node)

                if node.node_type == 'START_CONDITION':
                    node_icon = 'PLAY'
                    if node.condition_script:
                        display_text = f"[Start] {node.condition_script}"
                    else:
                        display_text = "[Start Condition]"
                    if node.run_once:
                        display_text += " (once)"

                elif node.node_type == 'PARTICIPANT':
                    node_icon = 'USER'
                    display_text = f"[{node.participant_name}]"

                elif node.node_type == 'LINE':
                    node_icon = 'OUTLINER_OB_FONT'
                    speaker, dialogue = parse_speaker_text(node.text)
                    if speaker:
                        full = f"{speaker}: {dialogue}"
                    else:
                        full = dialogue if dialogue else "(empty line)"
                    display_text = full[:50] + "..." if len(full) > 50 else full

                elif node.node_type == 'RESPONSE':
                    if indicators['is_blank']:
                        node_icon = 'FORWARD'
                        display_text = "(auto-advance)"
                    else:
                        node_icon = 'TRIA_RIGHT'
                        _, cleaned = parse_speaker_text(node.response_text)
                        if not cleaned:
                            cleaned = "(empty response)"
                        display_text = (cleaned[:50] + "..."
                                        if len(cleaned) > 50 else cleaned)

            # Build the row
            row = layout.row(align=True)

            # Icon-based indentation (consistent width)
            for _ in range(depth):
                row.label(text="", icon='BLANK1')

            # Main content
            row.label(text=display_text, icon=node_icon)

            # Trailing indicator icons
            if indicators:
                if indicators['has_sound']:
                    row.label(text="", icon='SOUND')
                if indicators['has_end']:
                    row.label(text="", icon='PANEL_CLOSE')
                if indicators['has_tag_jump']:
                    row.label(text="", icon='LINKED')
                if indicators['has_tag_index']:
                    row.label(text="", icon='BOOKMARKS')
                if indicators['has_faction'] == 'xmen':
                    row.label(text="", icon='COMMUNITY')
                elif indicators['has_faction'] == 'brotherhood':
                    row.label(text="", icon='GHOST_ENABLED')
                if indicators['has_brotherhood_variant']:
                    row.label(text="", icon='FILE_TEXT')

        elif self.layout_type == 'GRID':
            layout.label(text="node")

    def filter_items(self, context, data, propname):
        """Override to show nodes in DFS tree order."""
        from .conversation import get_flat_display_order

        items = getattr(data, propname)
        display = get_flat_display_order(data)

        flt_flags = [0] * len(items)
        flt_neworder = list(range(len(items)))

        # Build the mapping: display_index -> collection_index
        # First, build a node_id -> collection_index map
        id_to_idx = {}
        for i, node in enumerate(items):
            id_to_idx[node.node_id] = i

        # Mark visible items and build sort order
        new_order = [0] * len(items)
        for display_idx, (node, depth) in enumerate(display):
            coll_idx = id_to_idx.get(node.node_id, -1)
            if coll_idx >= 0:
                flt_flags[coll_idx] = self.bitflag_filter_item
                new_order[coll_idx] = display_idx

        return flt_flags, new_order


# ===========================================================================
# Panel 1: MM Map Settings
# ===========================================================================

class MM_PT_MapSettings(Panel):
    """Map Settings — world entity properties, build paths, and global options"""
    bl_label = "MM Map Settings"
    bl_idname = "MM_PT_MapSettings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        col = layout.column(align=True)
        col.prop(settings, "map_name")
        col.prop(settings, "map_path")
        col.separator()
        row = col.row(align=True)
        row.prop(settings, "act")
        row.prop(settings, "level")
        col.prop(settings, "zone_script")
        col.prop(settings, "soundfile")
        col.separator()
        col.prop(settings, "partylight")
        col.prop(settings, "partylightradius")
        row = col.row(align=True)
        row.prop(settings, "combatlocked")
        row.prop(settings, "nosave")
        col.prop(settings, "bigconvmap")

        layout.separator()
        box = layout.box()
        box.label(text="Automap", icon='IMAGE_DATA')
        col = box.column(align=True)
        col.prop(settings, "automap_path")
        col.label(text="Leave blank for auto: automaps/{map_path}/{map_name}", icon='INFO')

        layout.separator()
        box = layout.box()
        box.label(text="Build Paths", icon='FILE_FOLDER')
        col = box.column(align=True)
        col.prop(settings, "output_dir")
        col.prop(settings, "game_data_dir")

        # Global options
        layout.separator()
        box = layout.box()
        box.label(text="Options", icon='PREFERENCES')
        box.prop(settings, "show_previews", toggle=True)


# ===========================================================================
# Panel 2: MM Entities
# ===========================================================================

class MM_PT_Entities(Panel):
    """Entities — definitions, placement, presets, precache, and characters"""
    bl_label = "MM Entities"
    bl_idname = "MM_PT_Entities"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # --- Entity Definitions ---
        row = layout.row()
        row.template_list(
            "MM_UL_entity_defs", "",
            scene, "mm_entity_defs",
            scene, "mm_entity_defs_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("mm.add_entity_def", icon='ADD', text="")
        col.operator("mm.remove_entity_def", icon='REMOVE', text="")

        # Selected entity details
        if 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs):
            edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

            box = layout.box()
            col = box.column(align=True)
            col.prop(edef, "entity_name")
            col.prop(edef, "classname")

            # Show character field for spawners
            if edef.classname == 'monsterspawnerent':
                col.prop(edef, "character")
                col.prop(edef, "monster_name")

            # Show model field for any entity that can reference a model
            if edef.classname in ('physent', 'doorent', 'gameent', 'actionent') or edef.model:
                col.prop(edef, "model")

            col.prop(edef, "nocollide")

            # Apply defaults button
            col.separator()
            col.operator("mm.apply_defaults", icon='FILE_REFRESH')

            # Custom properties sub-list
            box2 = box.box()
            box2.label(text="Custom Properties", icon='PROPERTIES')
            row = box2.row()
            row.template_list(
                "MM_UL_entity_properties", "",
                edef, "properties",
                edef, "properties_index",
                rows=3,
            )
            col2 = row.column(align=True)
            col2.operator("mm.add_entity_property", icon='ADD', text="")
            col2.operator("mm.remove_entity_property", icon='REMOVE', text="")


class MM_PT_Entities_Placement(Panel):
    """Placement controls and quick presets"""
    bl_label = "Placement"
    bl_idname = "MM_PT_Entities_Placement"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Entities"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("mm.place_entity", icon='EMPTY_SINGLE_ARROW')

        row = layout.row(align=True)
        row.operator("mm.select_by_type", icon='RESTRICT_SELECT_OFF')
        row.operator("mm.remove_entity_instance", icon='X')

        # Instance count
        count = 0
        if "[MapMaker] Entities" in bpy.data.collections:
            count = len(bpy.data.collections["[MapMaker] Entities"].objects)
        layout.label(text=f"Placed instances: {count}", icon='OBJECT_DATA')

        # Actor preview controls
        layout.separator()
        row = layout.row(align=True)
        row.operator("mm.refresh_previews", icon='FILE_REFRESH', text="Refresh Previews")
        row.operator("mm.strip_previews", icon='TRASH', text="Strip All")


class MM_PT_Entities_Presets(Panel):
    """Quick entity presets"""
    bl_label = "Quick Presets"
    bl_idname = "MM_PT_Entities_Presets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Entities"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        from .entity_defs import ENTITY_PRESETS
        col = layout.column(align=True)
        for i in range(0, len(ENTITY_PRESETS), 2):
            row = col.row(align=True)
            pid, label, desc, *_ = ENTITY_PRESETS[i]
            op = row.operator("mm.place_preset", text=label)
            op.preset_id = pid
            if i + 1 < len(ENTITY_PRESETS):
                pid2, label2, desc2, *_ = ENTITY_PRESETS[i + 1]
                op2 = row.operator("mm.place_preset", text=label2)
                op2.preset_id = pid2


class MM_PT_Entities_Precache(Panel):
    """Precache entries — resources to preload"""
    bl_label = "Precache"
    bl_idname = "MM_PT_Entities_Precache"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Entities"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.template_list(
            "MM_UL_precache", "",
            scene, "mm_precache",
            scene, "mm_precache_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("mm.add_precache", icon='ADD', text="")
        col.operator("mm.remove_precache", icon='REMOVE', text="")

        layout.operator("mm.scan_precache", icon='VIEWZOOM', text="Auto-Scan Precache")
        layout.label(text=f"{len(scene.mm_precache)} entries", icon='INFO')


class MM_PT_Entities_Characters(Panel):
    """CHRB character list for this map"""
    bl_label = "Characters (CHRB)"
    bl_idname = "MM_PT_Entities_Characters"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Entities"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.template_list(
            "MM_UL_characters", "",
            scene, "mm_characters",
            scene, "mm_characters_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("mm.add_character", icon='ADD', text="")
        col.operator("mm.remove_character", icon='REMOVE', text="")

        layout.operator("mm.scan_characters", icon='VIEWZOOM', text="Auto-Scan Characters")

        # Rename / Replace buttons (active when a character is selected)
        if 0 <= scene.mm_characters_index < len(scene.mm_characters):
            ch = scene.mm_characters[scene.mm_characters_index]
            box = layout.box()
            box.label(text=f"Selected: {ch.char_name}", icon='ARMATURE_DATA')
            row = box.row(align=True)
            row.operator("mm.rename_character", icon='SORTALPHA', text="Rename")
            row.operator("mm.replace_character", icon='FILE_REFRESH', text="Replace from DB")

        layout.label(text=f"{len(scene.mm_characters)} characters", icon='ARMATURE_DATA')


# ===========================================================================
# Panel 3: MM Character Database
# ===========================================================================

class MM_PT_CharacterDB(Panel):
    """Character Database — browse NPCs and heroes from game data"""
    bl_label = "MM Character Database"
    bl_idname = "MM_PT_CharacterDB"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Load button
        row = layout.row(align=True)
        row.operator("mm.load_char_db", icon='FILE_REFRESH', text="Load Character DB")
        row.label(text=f"({len(scene.mm_char_db)} chars)")

        if len(scene.mm_char_db) > 0:
            # Search field
            layout.prop(scene, "mm_char_search", icon='VIEWZOOM')

            # Character list
            row = layout.row()
            row.template_list(
                "MM_UL_char_db", "",
                scene, "mm_char_db",
                scene, "mm_char_db_index",
                rows=6,
            )

            # Selected character details
            if 0 <= scene.mm_char_db_index < len(scene.mm_char_db):
                entry = scene.mm_char_db[scene.mm_char_db_index]
                box = layout.box()
                col = box.column(align=True)
                col.label(text=f"Name: {entry.display_name}", icon='ARMATURE_DATA')
                col.label(text=f"ID: {entry.name_id}")
                col.label(text=f"Team: {entry.team}")
                col.label(text=f"Skin: {entry.skin}")
                col.label(text=f"Source: {entry.source}")
                if entry.characteranims:
                    col.label(text=f"Anims: {entry.characteranims}")

                # Action buttons
                col.separator()
                col.scale_y = 1.3
                col.operator("mm.quick_place_character", icon='EMPTY_SINGLE_ARROW',
                             text="Quick Place at Cursor")
                col.scale_y = 1.0
                col.operator("mm.pick_character", icon='EYEDROPPER',
                             text="Pick for Active Entity Def")

        # NPC Stat Editor
        layout.separator()
        box = layout.box()
        box.label(text="NPC Stat Editor", icon='PREFERENCES')
        row = box.row(align=True)
        op = row.operator("mm.open_npcstat_editor", text="NPC Stats",
                          icon='WINDOW')
        op.stat_file = 'npcstat'
        op = row.operator("mm.open_npcstat_editor", text="Hero Stats",
                          icon='COMMUNITY')
        op.stat_file = 'herostat'


# ===========================================================================
# Panel 4: MM Model Browser
# ===========================================================================

class MM_PT_ModelBrowser(Panel):
    """Model Browser — browse game model assets"""
    bl_label = "MM Model Browser"
    bl_idname = "MM_PT_ModelBrowser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Scan button
        row = layout.row(align=True)
        row.operator("mm.scan_models", icon='FILE_REFRESH', text="Scan Models")
        row.label(text=f"({len(scene.mm_model_db)} models)")

        if len(scene.mm_model_db) > 0:
            # Search and category filter
            col = layout.column(align=True)
            col.prop(scene, "mm_model_search", icon='VIEWZOOM')
            col.prop(scene, "mm_model_filter_category", icon='FILE_FOLDER', text="Category")

            # Model list
            row = layout.row()
            row.template_list(
                "MM_UL_model_db", "",
                scene, "mm_model_db",
                scene, "mm_model_db_index",
                rows=6,
            )

            # Selected model details
            if 0 <= scene.mm_model_db_index < len(scene.mm_model_db):
                entry = scene.mm_model_db[scene.mm_model_db_index]
                box = layout.box()
                col = box.column(align=True)
                col.label(text=f"Name: {entry.display_name}", icon='MESH_CUBE')
                col.label(text=f"Path: {entry.rel_path}")
                col.label(text=f"Category: {entry.category}")

                # Action buttons
                col.separator()
                col.scale_y = 1.3
                col.operator("mm.quick_place_model", icon='EMPTY_SINGLE_ARROW',
                             text="Quick Place at Cursor")
                col.scale_y = 1.0
                col.operator("mm.pick_model", icon='EYEDROPPER',
                             text="Pick for Active Entity Def")
                col.operator("mm.import_model_asset", icon='ASSET_MANAGER',
                             text="Import as Asset")

            # Batch asset import
            layout.separator()
            box = layout.box()
            box.label(text="Asset Library", icon='ASSET_MANAGER')
            col = box.column(align=True)
            col.operator("mm.import_category_assets", icon='IMPORT',
                         text="Import Category as Assets")
            col.operator("mm.detect_placed_assets", icon='VIEWZOOM',
                         text="Detect Placed Assets")


# ===========================================================================
# Panel 5: MM Conversations
# ===========================================================================

class MM_PT_Conversations(Panel):
    """Conversations — dialogue tree editor"""
    bl_label = "MM Conversations"
    bl_idname = "MM_PT_Conversations"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Conversation list
        row = layout.row()
        row.template_list(
            "MM_UL_conversations", "",
            scene, "mm_conversations",
            scene, "mm_conversations_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("mm.add_conversation", icon='ADD', text="")
        col.operator("mm.remove_conversation", icon='REMOVE', text="")

        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return

        conv = scene.mm_conversations[scene.mm_conversations_index]

        # Conversation settings
        box = layout.box()
        col = box.column(align=True)
        col.prop(conv, "conv_name")
        col.prop(conv, "conv_path")
        col.separator()
        col.prop(conv, "hud_head", icon='USER')
        col.label(text="e.g. hud_head_1101 (skin number)", icon='INFO')

        # Edit Conversation button (external PySide6 editor)
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("mm.open_convo_editor", text="Edit Conversation", icon='WINDOW')
        row.operator("mm.extract_hud_heads", text="", icon='IMAGE_DATA')

        # Node tree
        layout.separator()
        box = layout.box()
        box.label(text="Dialogue Tree", icon='OUTLINER')

        # Node list (using custom draw for tree indentation)
        row = box.row()
        row.template_list(
            "MM_UL_conv_nodes", "",
            conv, "nodes",
            conv, "nodes_index",
            rows=10,
        )

        # Add node buttons
        col = box.column(align=True)
        col.operator("mm.add_start_condition", icon='PLAY', text="Add Start Condition")
        row = col.row(align=True)
        row.operator("mm.add_dialogue_exchange", icon='PLUS', text="Add Line+Response")
        op_line = row.operator("mm.add_conv_node", icon='OUTLINER_OB_FONT', text="Line")
        op_line.node_type = 'LINE'
        op_resp = row.operator("mm.add_conv_node", icon='TRIA_RIGHT', text="Response")
        op_resp.node_type = 'RESPONSE'
        col.operator("mm.remove_conv_node", icon='REMOVE', text="Remove Node")

        # Import/Export/Link
        layout.separator()
        row = layout.row(align=True)
        row.operator("mm.export_conversation", icon='EXPORT', text="Export")
        row.operator("mm.import_conversation", icon='IMPORT', text="Import")
        layout.operator("mm.link_conversation_to_npc", icon='LINKED',
                         text="Link to Active Entity Def")


# ===========================================================================
# Panel 5b: Dialogue Preview (sub-panel of Conversations)
# ===========================================================================

def _wrap_text(text, width=60):
    """Word-wrap text for Blender labels (which don't support wrapping).

    Returns list of strings, each <= width characters, broken at word boundaries.
    """
    if not text:
        return [""]

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        if current_line and len(current_line) + 1 + len(word) > width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = f"{current_line} {word}" if current_line else word

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]


class MM_PT_Conversations_Preview(Panel):
    """Game-style dialogue preview — shows how the conversation looks in-game"""
    bl_label = "Dialogue Preview"
    bl_idname = "MM_PT_Conversations_Preview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Conversations"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0

    def draw(self, context):
        from .conversation import (
            get_flat_display_order, get_conversation_context,
        )

        layout = self.layout
        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        if not (0 <= conv.nodes_index < len(display)):
            layout.label(text="Select a dialogue node", icon='INFO')
            return

        node, depth = display[conv.nodes_index]
        ctx = get_conversation_context(conv, node)

        # --- START_CONDITION / PARTICIPANT: metadata, not game preview ---
        if ctx['mode'] in ('start_condition', 'participant'):
            box = layout.box()
            if ctx['mode'] == 'start_condition':
                box.label(text="Start Condition", icon='PLAY')
                if node.condition_script:
                    box.label(text=f"Script: {node.condition_script}")
                else:
                    box.label(text="(no condition script)")
                if node.run_once:
                    box.label(text="Runs once", icon='CHECKBOX_HLT')
            else:
                box.label(text=f"Participant: {node.participant_name}",
                          icon='USER')
            return

        # === GAME-STYLE DIALOGUE BOX ===
        if ctx['mode'] == 'line':
            self._draw_npc_speech(layout, conv, ctx)
        elif ctx['mode'] == 'response':
            self._draw_response_view(layout, conv, ctx)

    def _draw_npc_speech(self, layout, conv, ctx):
        """Draw the NPC dialogue box when a LINE node is selected."""
        # NPC name header
        speech_box = layout.box()
        header = speech_box.row()
        header.scale_y = 1.4
        speaker = ctx['speaker'] or "NPC"
        if conv.hud_head:
            header.label(text="", icon='USER')
        header.label(text=speaker)
        if ctx['has_sound']:
            header.label(text="", icon='SOUND')

        # Dialogue text (word-wrapped)
        speech_box.separator()
        dialogue = ctx['dialogue']
        if dialogue:
            for line in _wrap_text(dialogue, 55):
                speech_box.label(text=f"  {line}")
        else:
            speech_box.label(text="  (no dialogue text)")

        # Brotherhood variant
        if ctx['dialogue_b']:
            speech_box.separator()
            var_box = speech_box.box()
            var_box.label(text="Brotherhood variant:", icon='GHOST_ENABLED')
            for line in _wrap_text(ctx['dialogue_b'], 50):
                var_box.label(text=f"  {line}")

        # Player responses
        if ctx['responses']:
            layout.separator()
            self._draw_responses(layout, ctx['responses'])

    def _draw_response_view(self, layout, conv, ctx):
        """Draw the view when a RESPONSE node is selected."""
        # What the NPC said (parent LINE)
        if ctx['parent_speaker'] or ctx['parent_dialogue']:
            npc_box = layout.box()
            header = npc_box.row()
            header.scale_y = 1.2
            speaker = ctx['parent_speaker'] or "NPC"
            header.label(text=speaker, icon='USER')

            if ctx['parent_dialogue']:
                for line in _wrap_text(ctx['parent_dialogue'], 55):
                    npc_box.label(text=f"  {line}")

        # Response choices with the selected one highlighted
        if ctx['responses']:
            layout.separator()
            self._draw_responses(layout, ctx['responses'])

        # Follow-up NPC line
        if ctx['follow_up_speaker'] or ctx['follow_up_dialogue']:
            layout.separator()
            follow_box = layout.box()
            header = follow_box.row()
            header.scale_y = 1.2
            fu_speaker = ctx['follow_up_speaker'] or "NPC"
            header.label(text=f"{fu_speaker} responds:", icon='USER')
            if ctx['has_sound']:
                header.label(text="", icon='SOUND')

            if ctx['follow_up_dialogue']:
                for line in _wrap_text(ctx['follow_up_dialogue'], 55):
                    follow_box.label(text=f"  {line}")

    def _draw_responses(self, layout, responses):
        """Draw the player response list."""
        resp_box = layout.box()
        resp_box.label(text="Player Choices:", icon='TRIA_RIGHT')
        resp_box.separator()

        for i, resp in enumerate(responses):
            row = resp_box.row(align=True)

            if resp.get('is_selected'):
                row.alert = True
                icon = 'RADIOBUT_ON'
            else:
                icon = 'RADIOBUT_OFF'

            if resp.get('is_blank'):
                row.label(text=f"  {i + 1}. (auto-advance)", icon='FORWARD')
            else:
                row.label(text=f"  {i + 1}. {resp['text']}", icon=icon)

            # Trailing indicators
            faction = resp.get('faction')
            if faction == 'xmen':
                row.label(text="X", icon='COMMUNITY')
            elif faction == 'brotherhood':
                row.label(text="B", icon='GHOST_ENABLED')
            if resp.get('is_end'):
                row.label(text="", icon='PANEL_CLOSE')
            if resp.get('tag_jump'):
                row.label(text="", icon='LINKED')


# ===========================================================================
# Panel 5c: Node Properties (sub-panel of Conversations)
# ===========================================================================

class MM_PT_Conversations_NodeProps(Panel):
    """Selected node properties editor"""
    bl_label = "Node Properties"
    bl_idname = "MM_PT_Conversations_NodeProps"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_parent_id = "MM_PT_Conversations"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        from .conversation import get_flat_display_order
        display = get_flat_display_order(conv)
        return 0 <= conv.nodes_index < len(display)

    def draw(self, context):
        from .conversation import get_flat_display_order

        layout = self.layout
        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        node, depth = display[conv.nodes_index]

        box = layout.box()
        box.label(text=f"Selected: {node.node_type}", icon='PREFERENCES')

        col = box.column(align=True)

        if node.node_type == 'START_CONDITION':
            col.prop(node, "condition_script")
            col.prop(node, "run_once")

        elif node.node_type == 'PARTICIPANT':
            col.prop(node, "participant_name")

        elif node.node_type == 'LINE':
            col.prop(node, "text")
            col.prop(node, "sound_to_play")
            col.separator()
            col.label(text="Brotherhood Variants:")
            col.prop(node, "text_b")
            col.prop(node, "sound_to_play_b")
            col.separator()
            col.prop(node, "tag_index")

        elif node.node_type == 'RESPONSE':
            col.prop(node, "response_text")
            col.prop(node, "chosen_script")
            col.prop(node, "script_file")
            col.prop(node, "conversation_end")
            col.separator()
            col.label(text="Branching:")
            col.prop(node, "tag_jump")
            col.prop(node, "tag_index")
            col.separator()
            col.label(text="Faction Gating:")
            row = col.row(align=True)
            row.prop(node, "only_if_xman", toggle=True)
            row.prop(node, "only_if_brotherhood", toggle=True)


# ===========================================================================
# Panel 6: MM Navigation
# ===========================================================================

class MM_PT_Navigation(Panel):
    """Navigation Mesh — generate NAVB grid data"""
    bl_label = "MM Navigation"
    bl_idname = "MM_PT_Navigation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.mm_settings

        layout.prop(settings, "nav_cellsize")

        # Advanced settings
        box = layout.box()
        box.label(text="Advanced", icon='PREFERENCES')
        col = box.column(align=True)
        col.prop(settings, "nav_max_slope", text="Max Slope")
        col.prop(settings, "nav_multi_layer")
        if settings.nav_multi_layer:
            col.prop(settings, "nav_layer_separation", text="Layer Gap")

        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mm.generate_navmesh", icon='MESH_GRID')
        col.operator("mm.visualize_navmesh", icon='SHADING_WIRE')

        # Show stored cell count
        if "mm_nav_cells" in scene:
            import ast
            try:
                cells = ast.literal_eval(scene["mm_nav_cells"])
                layout.label(text=f"Stored: {len(cells)} nav cells", icon='INFO')
            except Exception:
                layout.label(text="Nav cell data invalid", icon='ERROR')
        else:
            layout.label(text="No nav cells generated yet", icon='INFO')


# ===========================================================================
# Panel 7: MM Automap (ZAM)
# ===========================================================================

class MM_PT_Automap(Panel):
    """Automap — import/export .zam minimap files"""
    bl_label = "MM Automap"
    bl_idname = "MM_PT_Automap"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.operator("mm.import_zam", icon='IMPORT', text="Import ZAM")
        col.operator("mm.export_zam", icon='EXPORT', text="Export ZAM")

        layout.separator()
        box = layout.box()
        box.label(text="Make Automap", icon='IMAGE_DATA')
        col = box.column(align=True)
        col.label(text="Create from floor geometry:")
        col.operator("mm.make_automap", icon='MESH_GRID',
                     text="Generate Automap Mesh")
        col.label(text="Uses selected mesh as floor reference", icon='INFO')


# ===========================================================================
# Panel 8: MM Build
# ===========================================================================

class MM_PT_Build(Panel):
    """Build & Compile — generate game files, import existing maps"""
    bl_label = "MM Build"
    bl_idname = "MM_PT_Build"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Map Maker"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Import the operator class to check build state
        from .operators import MM_OT_build_all

        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mm.generate_xml", icon='FILE_TEXT', text="Generate XML")
        col.operator("mm.compile_xmlb", icon='MODIFIER', text="Compile XMLB")
        col.separator()
        col.scale_y = 1.4
        if MM_OT_build_all._building:
            col.enabled = False
            col.operator("mm.build_all", icon='SORTTIME',
                         text="Building... (async)")
        else:
            col.operator("mm.build_all", icon='PLAY', text="Build All")

        if not scene.mm_settings.output_dir:
            layout.label(text="Set Output Directory in MM Map Settings", icon='ERROR')

        # Collision preview
        layout.separator()
        box = layout.box()
        box.label(text="Collision Preview", icon='PHYSICS')
        col = box.column(align=True)
        op = col.operator("mm.visualize_colliders", icon='SHADING_WIRE',
                          text="Preview Colliders")
        op.source = 'COLLIDERS'
        op = col.operator("mm.visualize_colliders", icon='MESH_DATA',
                          text="Preview Visual Mesh Collision")
        op.source = 'VISUAL'

        # Collider stats
        coll = bpy.data.collections.get("Colliders")
        if coll:
            mesh_count = sum(1 for o in coll.objects if o.type == 'MESH')
            col.label(text=f"Colliders collection: {mesh_count} mesh(es)", icon='INFO')
        else:
            col.label(text="No 'Colliders' collection found", icon='INFO')

        # Menu Editor
        layout.separator()
        box = layout.box()
        box.label(text="Menu Editor", icon='WINDOW')
        row = box.row()
        row.scale_y = 1.3
        row.operator("mm.open_menu_editor", text="Open Menu Editor", icon='WINDOW')


# ===========================================================================
# Registration
# ===========================================================================

_classes = (
    # UILists
    MM_UL_entity_defs,
    MM_UL_entity_properties,
    MM_UL_precache,
    MM_UL_characters,
    MM_UL_char_db,
    MM_UL_model_db,
    MM_UL_conversations,
    MM_UL_conv_nodes,
    # Top-level panels (order = display order in N-panel)
    MM_PT_MapSettings,
    MM_PT_Entities,
    MM_PT_Entities_Placement,
    MM_PT_Entities_Presets,
    MM_PT_Entities_Precache,
    MM_PT_Entities_Characters,
    MM_PT_CharacterDB,
    MM_PT_ModelBrowser,
    MM_PT_Conversations,
    MM_PT_Conversations_Preview,
    MM_PT_Conversations_NodeProps,
    MM_PT_Navigation,
    MM_PT_Automap,
    MM_PT_Build,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
