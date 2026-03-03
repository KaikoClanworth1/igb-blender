"""N-panel UI for the Menu Editor."""

import bpy
from bpy.types import Panel, UIList


# ---------------------------------------------------------------------------
# UIList for menu items
# ---------------------------------------------------------------------------

class MENU_UL_items(UIList):
    """UIList for menu items with icons and info."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            # Icon based on item properties
            if item.startactive:
                row.label(text="", icon='PLAY')
            elif item.usecmd or item.nav_up or item.nav_down:
                row.label(text="", icon='FORWARD')
            elif item.item_type == 'MENU_ITEM_MODEL':
                row.label(text="", icon='MESH_CUBE')
            else:
                row.label(text="", icon='FONT_DATA')

            # Name
            row.prop(item, "item_name", text="", emboss=False)

            # Info column
            sub = row.row()
            sub.alignment = 'RIGHT'
            sub.scale_x = 0.5
            if item.text:
                # Truncate text display
                display = item.text[:16] + ("..." if len(item.text) > 16 else "")
                sub.label(text=display)
            elif item.item_type:
                short_type = item.item_type.replace('MENU_ITEM_', '')
                sub.label(text=short_type)

            # Warning for items without transform
            if not item.has_transform:
                row.label(text="", icon='ERROR')

        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.item_name, icon='DOT')

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        search = context.scene.menu_settings.item_search.lower()
        show_debug = context.scene.menu_settings.show_debug_items

        flt_flags = []
        flt_neworder = []

        for item in items:
            flag = self.bitflag_filter_item
            # Search filter
            if search and search not in item.item_name.lower():
                if not item.text or search not in item.text.lower():
                    flag = 0
            # Debug filter
            if not show_debug and item.debug_only:
                flag = 0
            flt_flags.append(flag)

        return flt_flags, flt_neworder


# ---------------------------------------------------------------------------
# UIList for precache entries
# ---------------------------------------------------------------------------

class MENU_UL_precache(UIList):
    """UIList for precache entries."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "precache_type", text="", emboss=False)
            row.prop(item, "filename", text="", emboss=False)


# ---------------------------------------------------------------------------
# Main panel: File operations
# ---------------------------------------------------------------------------

class MENU_PT_file(Panel):
    """Menu Editor file operations"""
    bl_label = "Menu Editor"
    bl_idname = "MENU_PT_file"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.menu_settings

        # Game directory
        layout.prop(settings, "game_dir")

        # Load button
        row = layout.row()
        row.scale_y = 1.3
        row.operator("menu.load", icon='FILE_FOLDER')

        if settings.is_loaded:
            # File info
            box = layout.box()
            col = box.column(align=True)
            engb_name = Path(settings.engb_path).name if settings.engb_path else "?"
            col.label(text=f"File: {engb_name}", icon='FILE')
            if settings.igb_ref:
                col.label(text=f"IGB: {settings.igb_ref}.igb", icon='MESH_ICOSPHERE')
            if settings.menu_type and settings.menu_type != 'NONE':
                col.label(text=f"Type: {settings.menu_type}", icon='MENU_PANEL')

            # Save / Deploy
            row = layout.row(align=True)
            row.operator("menu.save", icon='FILE_TICK')
            row.operator("menu.save_as", text="Save As", icon='EXPORT')

            col = layout.column(align=True)
            col.operator("menu.deploy", icon='PLAY')
            col.prop(settings, "patch_igb_transforms")

            layout.separator()
            layout.operator("menu.close", icon='X', text="Close Menu")


# ---------------------------------------------------------------------------
# Items list panel
# ---------------------------------------------------------------------------

class MENU_PT_items(Panel):
    """Menu item list"""
    bl_label = "Items"
    bl_idname = "MENU_PT_items"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_file"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.menu_settings.is_loaded

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.menu_settings

        # Search
        layout.prop(settings, "item_search", icon='VIEWZOOM', text="")

        # Item list
        row = layout.row()
        row.template_list(
            "MENU_UL_items", "",
            scene, "menu_items",
            scene, "menu_items_index",
            rows=8,
        )

        # Side buttons
        col = row.column(align=True)
        col.operator("menu.add_item", icon='ADD', text="")
        col.operator("menu.remove_item", icon='REMOVE', text="")
        col.separator()
        col.operator("menu.duplicate_item", icon='DUPLICATE', text="")
        col.separator()
        op = col.operator("menu.move_item", icon='TRIA_UP', text="")
        op.direction = 'UP'
        op = col.operator("menu.move_item", icon='TRIA_DOWN', text="")
        op.direction = 'DOWN'

        # Focus button
        layout.operator("menu.focus_item", icon='ZOOM_SELECTED')

        # Item count
        total = len(scene.menu_items)
        selectable = sum(1 for pg in scene.menu_items
                         if not pg.neverfocus and (pg.usecmd or pg.nav_up))
        layout.label(text=f"{total} items, {selectable} selectable")


# ---------------------------------------------------------------------------
# Selected item properties
# ---------------------------------------------------------------------------

class MENU_PT_item_props(Panel):
    """Properties of the selected menu item"""
    bl_label = "Selected Item"
    bl_idname = "MENU_PT_item_props"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_file"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (scene.menu_settings.is_loaded
                and 0 <= scene.menu_items_index < len(scene.menu_items))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        pg = scene.menu_items[scene.menu_items_index]

        # Basic properties
        layout.prop(pg, "item_name")
        layout.prop(pg, "item_type")
        layout.prop(pg, "model_ref")
        layout.prop(pg, "text")
        layout.prop(pg, "style")
        layout.prop(pg, "usecmd")

        # Navigation
        box = layout.box()
        box.label(text="Navigation", icon='ORIENTATION_CURSOR')
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(pg, "nav_up", text="Up")
        row.prop(pg, "nav_down", text="Down")
        row = col.row(align=True)
        row.prop(pg, "nav_left", text="Left")
        row.prop(pg, "nav_right", text="Right")

        # Flags
        box = layout.box()
        box.label(text="Flags", icon='PROPERTIES')
        flow = box.grid_flow(columns=2, align=True)
        flow.prop(pg, "neverfocus")
        flow.prop(pg, "startactive")
        flow.prop(pg, "animate")
        flow.prop(pg, "debug_only")

        # Other
        if pg.animtext_scene or pg.mode:
            box = layout.box()
            box.label(text="Other", icon='MODIFIER')
            if pg.animtext_scene:
                box.prop(pg, "animtext_scene")
            if pg.mode:
                box.prop(pg, "mode")


# ---------------------------------------------------------------------------
# OnFocus entries panel
# ---------------------------------------------------------------------------

class MENU_PT_onfocus(Panel):
    """Focus behavior entries for the selected item"""
    bl_label = "OnFocus"
    bl_idname = "MENU_PT_onfocus"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_item_props"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (scene.menu_settings.is_loaded
                and 0 <= scene.menu_items_index < len(scene.menu_items))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        pg = scene.menu_items[scene.menu_items_index]

        for i, entry in enumerate(pg.onfocus_entries):
            box = layout.box()
            row = box.row(align=True)
            row.prop(entry, "focus_type", text="")
            row.prop(entry, "target_item", text="Item")
            box.prop(entry, "model_ref", text="Model")
            if entry.loop_value:
                box.prop(entry, "loop_value", text="Loop")

        row = layout.row(align=True)
        row.operator("menu.add_onfocus", icon='ADD')
        if pg.onfocus_entries:
            row.operator("menu.remove_onfocus", icon='REMOVE')


# ---------------------------------------------------------------------------
# Extra attributes panel
# ---------------------------------------------------------------------------

class MENU_PT_extra_attrs(Panel):
    """Custom attributes on the selected item"""
    bl_label = "Extra Attributes"
    bl_idname = "MENU_PT_extra_attrs"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_item_props"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (scene.menu_settings.is_loaded
                and 0 <= scene.menu_items_index < len(scene.menu_items))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        pg = scene.menu_items[scene.menu_items_index]

        for i, ea in enumerate(pg.extra_attrs):
            row = layout.row(align=True)
            row.prop(ea, "key", text="")
            row.prop(ea, "value", text="")

        row = layout.row(align=True)
        row.operator("menu.add_extra_attr", icon='ADD')
        if pg.extra_attrs:
            row.operator("menu.remove_extra_attr", icon='REMOVE')


# ---------------------------------------------------------------------------
# Precache panel
# ---------------------------------------------------------------------------

class MENU_PT_precache(Panel):
    """Precache resource entries"""
    bl_label = "Precache"
    bl_idname = "MENU_PT_precache"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_file"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.menu_settings.is_loaded

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.template_list(
            "MENU_UL_precache", "",
            scene, "menu_precaches",
            scene, "menu_precaches_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("menu.add_precache", icon='ADD', text="")
        col.operator("menu.remove_precache", icon='REMOVE', text="")


# ---------------------------------------------------------------------------
# View options panel
# ---------------------------------------------------------------------------

class MENU_PT_view(Panel):
    """View options"""
    bl_label = "View"
    bl_idname = "MENU_PT_view"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_file"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.menu_settings.is_loaded

    def draw(self, context):
        layout = self.layout
        settings = context.scene.menu_settings

        col = layout.column(align=True)
        col.prop(settings, "show_background")
        col.prop(settings, "show_models")
        col.prop(settings, "show_debug_items")


# ---------------------------------------------------------------------------
# Validation panel
# ---------------------------------------------------------------------------

class MENU_PT_validation(Panel):
    """Validation and auto-fix tools"""
    bl_label = "Validation"
    bl_idname = "MENU_PT_validation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Menu Editor"
    bl_parent_id = "MENU_PT_file"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.menu_settings.is_loaded

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator("menu.validate", icon='CHECKMARK')
        row.operator("menu.fix_navigation", icon='CON_TRACKTO')


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# Need Path for file panel
from pathlib import Path

_classes = (
    MENU_UL_items,
    MENU_UL_precache,
    MENU_PT_file,
    MENU_PT_items,
    MENU_PT_item_props,
    MENU_PT_onfocus,
    MENU_PT_extra_attrs,
    MENU_PT_precache,
    MENU_PT_view,
    MENU_PT_validation,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
