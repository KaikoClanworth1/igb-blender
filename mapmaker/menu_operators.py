"""Blender operators for the Menu Editor."""

import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty
from bpy.types import Operator
from pathlib import Path


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

class MENU_OT_load(Operator):
    """Load a menu ENGB file and build the Blender scene"""
    bl_idname = "menu.load"
    bl_label = "Load Menu ENGB"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.engb;*.xmlb", options={'HIDDEN'})

    def invoke(self, context, event):
        settings = context.scene.menu_settings
        if settings.game_dir:
            menus_dir = Path(settings.game_dir) / 'ui' / 'menus'
            if menus_dir.exists():
                self.filepath = str(menus_dir) + "/"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}

        settings = context.scene.menu_settings
        game_dir = settings.game_dir
        if not game_dir:
            self.report({'ERROR'}, "Set Game Data Directory first")
            return {'CANCELLED'}

        from .menu_import import import_menu
        ok, msg = import_menu(context, self.filepath, game_dir)
        if ok:
            self.report({'INFO'}, msg)
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}


class MENU_OT_save(Operator):
    """Save the menu ENGB to its original path"""
    bl_idname = "menu.save"
    bl_label = "Save Menu"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.menu_settings
        if not settings.is_loaded or not settings.engb_path:
            self.report({'ERROR'}, "No menu loaded")
            return {'CANCELLED'}

        from .menu_export import export_engb
        ok, msg = export_engb(context, settings.engb_path)
        self.report({'INFO'} if ok else {'ERROR'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


class MENU_OT_save_as(Operator):
    """Save the menu ENGB to a new path"""
    bl_idname = "menu.save_as"
    bl_label = "Save Menu As"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.engb;*.xmlb", options={'HIDDEN'})

    def invoke(self, context, event):
        settings = context.scene.menu_settings
        if settings.engb_path:
            self.filepath = settings.engb_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            return {'CANCELLED'}

        from .menu_export import export_engb
        ok, msg = export_engb(context, self.filepath)
        if ok:
            context.scene.menu_settings.engb_path = self.filepath
        self.report({'INFO'} if ok else {'ERROR'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


class MENU_OT_deploy(Operator):
    """Export ENGB + patch IGB and deploy to game directory"""
    bl_idname = "menu.deploy"
    bl_label = "Deploy Menu"
    bl_description = "Save ENGB + patch IGB transforms and copy to game directory"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .menu_export import deploy_menu
        ok, msg = deploy_menu(context)
        self.report({'INFO'} if ok else {'ERROR'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


class MENU_OT_close(Operator):
    """Close the current menu and clear the scene"""
    bl_idname = "menu.close"
    bl_label = "Close Menu"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .menu_import import clear_menu_scene
        scene = context.scene

        clear_menu_scene()
        scene.menu_items.clear()
        scene.menu_animtexts.clear()
        scene.menu_precaches.clear()

        settings = scene.menu_settings
        settings.is_loaded = False
        settings.engb_path = ""
        settings.igb_ref = ""
        settings.menu_type = 'NONE'
        settings.engb_json = ""

        self.report({'INFO'}, "Menu closed")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Item CRUD
# ---------------------------------------------------------------------------

class MENU_OT_add_item(Operator):
    """Add a new menu item at the 3D cursor"""
    bl_idname = "menu.add_item"
    bl_label = "Add Item"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .menu_import import COLL_ITEMS, _get_or_create_collection, COLL_ROOT
        scene = context.scene

        if not scene.menu_settings.is_loaded:
            self.report({'ERROR'}, "No menu loaded")
            return {'CANCELLED'}

        # Generate unique name
        existing = {pg.item_name for pg in scene.menu_items}
        base = "new_item"
        name = base
        i = 1
        while name in existing:
            name = f"{base}_{i:02d}"
            i += 1

        # Create Empty at cursor
        root_coll = _get_or_create_collection(COLL_ROOT)
        items_coll = _get_or_create_collection(COLL_ITEMS, root_coll)
        empty = bpy.data.objects.new(name, None)
        items_coll.objects.link(empty)
        empty.location = context.scene.cursor.location.copy()
        empty.empty_display_type = 'PLAIN_AXES'
        empty.empty_display_size = 8.0
        empty.color = (0.9, 0.75, 0.25, 1.0)
        empty["menu_item_name"] = name

        # Add PropertyGroup entry
        pg_item = scene.menu_items.add()
        pg_item.item_name = name
        pg_item.object_name = empty.name
        pg_item.has_transform = False
        empty["menu_item_index"] = len(scene.menu_items) - 1

        scene.menu_items_index = len(scene.menu_items) - 1

        # Select it
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        self.report({'INFO'}, f"Added item: {name}")
        return {'FINISHED'}


class MENU_OT_remove_item(Operator):
    """Remove the selected menu item"""
    bl_idname = "menu.remove_item"
    bl_label = "Remove Item"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            self.report({'ERROR'}, "No item selected")
            return {'CANCELLED'}

        pg_item = scene.menu_items[idx]
        name = pg_item.item_name

        # Remove Blender object and its children
        obj = bpy.data.objects.get(pg_item.object_name)
        if obj is not None:
            # Remove children first
            for child in list(obj.children):
                bpy.data.objects.remove(child, do_unlink=True)
            bpy.data.objects.remove(obj, do_unlink=True)

        # Remove PropertyGroup entry
        scene.menu_items.remove(idx)

        # Fix indices on remaining items
        for i, pg in enumerate(scene.menu_items):
            obj2 = bpy.data.objects.get(pg.object_name)
            if obj2 is not None:
                obj2["menu_item_index"] = i

        if scene.menu_items_index >= len(scene.menu_items):
            scene.menu_items_index = max(0, len(scene.menu_items) - 1)

        self.report({'INFO'}, f"Removed item: {name}")
        return {'FINISHED'}


class MENU_OT_duplicate_item(Operator):
    """Duplicate the selected menu item"""
    bl_idname = "menu.duplicate_item"
    bl_label = "Duplicate Item"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .menu_import import COLL_ITEMS, _get_or_create_collection, COLL_ROOT
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            self.report({'ERROR'}, "No item selected")
            return {'CANCELLED'}

        src = scene.menu_items[idx]

        # Generate unique name
        existing = {pg.item_name for pg in scene.menu_items}
        base = src.item_name + "_copy"
        name = base
        i = 1
        while name in existing:
            name = f"{src.item_name}_copy{i}"
            i += 1

        # Create Empty offset from source
        src_obj = bpy.data.objects.get(src.object_name)
        root_coll = _get_or_create_collection(COLL_ROOT)
        items_coll = _get_or_create_collection(COLL_ITEMS, root_coll)
        empty = bpy.data.objects.new(name, None)
        items_coll.objects.link(empty)
        if src_obj:
            empty.location = src_obj.location.copy()
            empty.location.x += 20.0  # offset
        empty.empty_display_type = src_obj.empty_display_type if src_obj else 'PLAIN_AXES'
        empty.empty_display_size = src_obj.empty_display_size if src_obj else 8.0
        if src_obj:
            empty.color = src_obj.color[:]
        empty["menu_item_name"] = name

        # Copy PropertyGroup
        pg = scene.menu_items.add()
        pg.item_name = name
        pg.item_type = src.item_type
        pg.model_ref = src.model_ref
        pg.text = src.text
        pg.style = src.style
        pg.usecmd = src.usecmd
        pg.neverfocus = src.neverfocus
        pg.startactive = False  # don't copy startactive
        pg.animate = src.animate
        pg.debug_only = src.debug_only
        pg.animtext_scene = src.animtext_scene
        pg.mode = src.mode
        pg.object_name = empty.name
        pg.has_transform = False
        empty["menu_item_index"] = len(scene.menu_items) - 1

        # Copy extra attrs
        for ea in src.extra_attrs:
            nea = pg.extra_attrs.add()
            nea.key = ea.key
            nea.value = ea.value

        # Copy onfocus entries
        for of_src in src.onfocus_entries:
            of_dst = pg.onfocus_entries.add()
            of_dst.focus_type = of_src.focus_type
            of_dst.target_item = of_src.target_item
            of_dst.model_ref = of_src.model_ref
            of_dst.loop_value = of_src.loop_value

        scene.menu_items_index = len(scene.menu_items) - 1

        self.report({'INFO'}, f"Duplicated → {name}")
        return {'FINISHED'}


class MENU_OT_focus_item(Operator):
    """Select and zoom to the item's Empty in the viewport"""
    bl_idname = "menu.focus_item"
    bl_label = "Focus Item"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            return {'CANCELLED'}

        pg_item = scene.menu_items[idx]
        obj = bpy.data.objects.get(pg_item.object_name)
        if obj is None:
            self.report({'WARNING'}, f"Object not found: {pg_item.object_name}")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # Zoom to selected
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        override = context.copy()
                        override['area'] = area
                        override['region'] = region
                        with context.temp_override(**override):
                            bpy.ops.view3d.view_selected()
                        break
                break

        return {'FINISHED'}


class MENU_OT_move_item(Operator):
    """Move item up or down in the list"""
    bl_idname = "menu.move_item"
    bl_label = "Move Item"
    bl_options = {'REGISTER', 'UNDO'}

    direction: StringProperty(default='UP')

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        count = len(scene.menu_items)

        if self.direction == 'UP' and idx > 0:
            scene.menu_items.move(idx, idx - 1)
            scene.menu_items_index = idx - 1
            _reindex_items(scene)
        elif self.direction == 'DOWN' and idx < count - 1:
            scene.menu_items.move(idx, idx + 1)
            scene.menu_items_index = idx + 1
            _reindex_items(scene)

        return {'FINISHED'}


def _reindex_items(scene):
    """Update menu_item_index on all objects after a list reorder."""
    for i, pg in enumerate(scene.menu_items):
        obj = bpy.data.objects.get(pg.object_name)
        if obj is not None:
            obj["menu_item_index"] = i


# ---------------------------------------------------------------------------
# OnFocus CRUD
# ---------------------------------------------------------------------------

class MENU_OT_add_onfocus(Operator):
    """Add a focus behavior entry to the selected item"""
    bl_idname = "menu.add_onfocus"
    bl_label = "Add OnFocus"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            return {'CANCELLED'}
        pg = scene.menu_items[idx]
        entry = pg.onfocus_entries.add()
        entry.focus_type = 'focus'
        pg.onfocus_index = len(pg.onfocus_entries) - 1
        return {'FINISHED'}


class MENU_OT_remove_onfocus(Operator):
    """Remove the selected focus behavior entry"""
    bl_idname = "menu.remove_onfocus"
    bl_label = "Remove OnFocus"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            return {'CANCELLED'}
        pg = scene.menu_items[idx]
        of_idx = pg.onfocus_index
        if of_idx < 0 or of_idx >= len(pg.onfocus_entries):
            return {'CANCELLED'}
        pg.onfocus_entries.remove(of_idx)
        if pg.onfocus_index >= len(pg.onfocus_entries):
            pg.onfocus_index = max(0, len(pg.onfocus_entries) - 1)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Extra attribute CRUD
# ---------------------------------------------------------------------------

class MENU_OT_add_extra_attr(Operator):
    """Add a custom attribute to the selected item"""
    bl_idname = "menu.add_extra_attr"
    bl_label = "Add Attribute"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            return {'CANCELLED'}
        pg = scene.menu_items[idx]
        ea = pg.extra_attrs.add()
        ea.key = "new_attr"
        ea.value = ""
        pg.extra_attrs_index = len(pg.extra_attrs) - 1
        return {'FINISHED'}


class MENU_OT_remove_extra_attr(Operator):
    """Remove the selected custom attribute"""
    bl_idname = "menu.remove_extra_attr"
    bl_label = "Remove Attribute"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_items_index
        if idx < 0 or idx >= len(scene.menu_items):
            return {'CANCELLED'}
        pg = scene.menu_items[idx]
        ea_idx = pg.extra_attrs_index
        if ea_idx < 0 or ea_idx >= len(pg.extra_attrs):
            return {'CANCELLED'}
        pg.extra_attrs.remove(ea_idx)
        if pg.extra_attrs_index >= len(pg.extra_attrs):
            pg.extra_attrs_index = max(0, len(pg.extra_attrs) - 1)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Precache CRUD
# ---------------------------------------------------------------------------

class MENU_OT_add_precache(Operator):
    """Add a precache entry"""
    bl_idname = "menu.add_precache"
    bl_label = "Add Precache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        pc = scene.menu_precaches.add()
        pc.filename = ""
        pc.precache_type = 'model'
        scene.menu_precaches_index = len(scene.menu_precaches) - 1
        return {'FINISHED'}


class MENU_OT_remove_precache(Operator):
    """Remove the selected precache entry"""
    bl_idname = "menu.remove_precache"
    bl_label = "Remove Precache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        idx = scene.menu_precaches_index
        if idx < 0 or idx >= len(scene.menu_precaches):
            return {'CANCELLED'}
        scene.menu_precaches.remove(idx)
        if scene.menu_precaches_index >= len(scene.menu_precaches):
            scene.menu_precaches_index = max(0, len(scene.menu_precaches) - 1)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Validation and auto-fix
# ---------------------------------------------------------------------------

class MENU_OT_validate(Operator):
    """Run validation checks on the menu"""
    bl_idname = "menu.validate"
    bl_label = "Validate Menu"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .menu_validate import validate_menu, ValidationResult
        results = validate_menu(context.scene)

        errors = sum(1 for r in results if r.level == ValidationResult.ERROR)
        warns = sum(1 for r in results if r.level == ValidationResult.WARN)
        passes = sum(1 for r in results if r.level == ValidationResult.PASS)

        for r in results:
            if r.level == ValidationResult.ERROR:
                self.report({'ERROR'}, f"[{r.rule}] {r.message}")
            elif r.level == ValidationResult.WARN:
                self.report({'WARNING'}, f"[{r.rule}] {r.message}")

        if errors == 0 and warns == 0:
            self.report({'INFO'}, f"All {passes} checks passed")
        else:
            self.report({'INFO'},
                        f"Validation: {passes} pass, {warns} warn, {errors} error")

        return {'FINISHED'}


class MENU_OT_fix_navigation(Operator):
    """Auto-generate navigation links from spatial positions"""
    bl_idname = "menu.fix_navigation"
    bl_label = "Auto-Fix Navigation"
    bl_description = "Generate up/down/left/right from item positions"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .menu_validate import auto_fix_navigation
        count, msg = auto_fix_navigation(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# View toggles
# ---------------------------------------------------------------------------

class MENU_OT_toggle_background(Operator):
    """Toggle background geometry visibility"""
    bl_idname = "menu.toggle_background"
    bl_label = "Toggle Background"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .menu_import import COLL_BG
        settings = context.scene.menu_settings
        settings.show_background = not settings.show_background
        if COLL_BG in bpy.data.collections:
            coll = bpy.data.collections[COLL_BG]
            coll.hide_viewport = not settings.show_background
        return {'FINISHED'}


class MENU_OT_select_from_viewport(Operator):
    """Sync UIList selection from the active viewport object"""
    bl_idname = "menu.select_from_viewport"
    bl_label = "Select From Viewport"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            return {'CANCELLED'}
        item_name = obj.get("menu_item_name", "")
        if not item_name:
            return {'CANCELLED'}
        for i, pg in enumerate(context.scene.menu_items):
            if pg.item_name == item_name:
                context.scene.menu_items_index = i
                return {'FINISHED'}
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    MENU_OT_load,
    MENU_OT_save,
    MENU_OT_save_as,
    MENU_OT_deploy,
    MENU_OT_close,
    MENU_OT_add_item,
    MENU_OT_remove_item,
    MENU_OT_duplicate_item,
    MENU_OT_focus_item,
    MENU_OT_move_item,
    MENU_OT_add_onfocus,
    MENU_OT_remove_onfocus,
    MENU_OT_add_extra_attr,
    MENU_OT_remove_extra_attr,
    MENU_OT_add_precache,
    MENU_OT_remove_precache,
    MENU_OT_validate,
    MENU_OT_fix_navigation,
    MENU_OT_toggle_background,
    MENU_OT_select_from_viewport,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
