"""Blender operators for the IGB Actors panel."""

import os

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper


def _discover_skin_files(anim_filepath, game_dir=""):
    """Discover skin files for an actor animation file.

    Returns list of (variant_name, filepath) tuples.
    """
    from .actor_import import resolve_skin_files

    skin_filepaths = []

    # Try herostat-based resolution first
    if game_dir:
        anim_name = os.path.splitext(os.path.basename(anim_filepath))[0]
        skin_filepaths = resolve_skin_files(anim_name, game_dir)

    if not skin_filepaths:
        # Scan same directory for matching skin files
        actors_dir = os.path.dirname(anim_filepath)
        anim_name = os.path.splitext(os.path.basename(anim_filepath))[0]
        parts = anim_name.split('_', 1)
        if parts[0].isdigit():
            char_id = parts[0]
            if os.path.isdir(actors_dir):
                for f in sorted(os.listdir(actors_dir)):
                    if f.lower().endswith('.igb') and f[:len(char_id)] == char_id:
                        stem = f[:-4]
                        if stem.isdigit() and len(stem) == 4:
                            variant = stem[len(char_id):]
                            path = os.path.join(actors_dir, f)
                            name = f"skin_{variant}" if variant != "01" else "default"
                            skin_filepaths.append((name, path))

    return skin_filepaths


class ACTOR_OT_import_actor(Operator, ImportHelper):
    """Import an actor with skeleton, skins, and animations"""
    bl_idname = "actor.import_actor"
    bl_label = "Import Actor"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    def execute(self, context):
        from .actor_import import import_actor, resolve_skin_files

        props = context.scene.igb_actor
        anim_filepath = self.filepath

        # Try to resolve skin files
        skin_filepaths = _discover_skin_files(anim_filepath, props.game_dir)

        options = {
            'import_skins': props.import_skins,
            'import_animations': props.import_animations,
            'import_materials': props.import_materials,
            'game_preset': 'auto',
        }

        # If skins found and import_skins enabled, show selection dialog
        if skin_filepaths and props.import_skins:
            _skin_import_cache['anim_filepath'] = anim_filepath
            _skin_import_cache['skins'] = skin_filepaths
            _skin_import_cache['options'] = options
            bpy.ops.actor.select_skins('INVOKE_DEFAULT')
            return {'FINISHED'}

        # No skins or skins disabled — import directly
        result = import_actor(
            context, anim_filepath,
            skin_filepaths=skin_filepaths if props.import_skins else [],
            game_dir=props.game_dir if props.game_dir else None,
            operator=self,
            options=options,
        )

        if result is None:
            return {'CANCELLED'}

        armature_obj, skin_objects, actions = result
        _populate_import_results(context, props, armature_obj,
                                 skin_objects, actions)
        return {'FINISHED'}

    def invoke(self, context, event):
        # Pre-fill with game_dir actors folder if set
        props = context.scene.igb_actor
        if props.game_dir:
            actors_dir = os.path.join(props.game_dir, "actors")
            if os.path.isdir(actors_dir):
                self.filepath = actors_dir + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_import_skin(Operator, ImportHelper):
    """Import an additional skin file for the current actor"""
    bl_idname = "actor.import_skin"
    bl_label = "Import Skin"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    variant_name: StringProperty(
        name="Variant Name",
        default="custom",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return props.active_armature and props.active_armature in bpy.data.objects

    def execute(self, context):
        from .actor_import import _import_skin_file
        from .sg_skeleton import extract_skeleton
        from ..igb_format.igb_reader import IGBReader
        from ..game_profiles import detect_profile

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Re-extract skeleton from the animation file
        anim_file = props.anim_file
        if not anim_file or not os.path.exists(anim_file):
            self.report({'ERROR'}, "Animation file not found")
            return {'CANCELLED'}

        reader = IGBReader(anim_file)
        reader.read()
        skeleton = extract_skeleton(reader)
        if skeleton is None:
            self.report({'ERROR'}, "Failed to extract skeleton")
            return {'CANCELLED'}

        profile = detect_profile(reader)

        # Find the actor's collection
        collection = None
        for coll in bpy.data.collections:
            if armature_obj.name in coll.objects:
                collection = coll
                break
        if collection is None:
            collection = context.collection

        options = {
            'import_normals': True,
            'import_uvs': True,
            'import_vertex_colors': True,
            'import_materials': props.import_materials,
        }

        mesh_objs = _import_skin_file(
            context, self.filepath, skeleton, armature_obj,
            collection, self.variant_name, profile, options
        )

        if mesh_objs:
            for mesh_obj in mesh_objs:
                item = props.skins.add()
                item.name = self.variant_name
                item.object_name = mesh_obj.name
                item.filepath = self.filepath
                item.is_visible = True
            self.report({'INFO'}, f"Imported skin '{self.variant_name}' ({len(mesh_objs)} parts)")
            return {'FINISHED'}

        self.report({'WARNING'}, "No geometry found in skin file")
        return {'CANCELLED'}


class ACTOR_OT_toggle_skin(Operator):
    """Toggle visibility of a skin variant"""
    bl_idname = "actor.toggle_skin"
    bl_label = "Toggle Skin"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return len(props.skins) > 0

    def execute(self, context):
        props = context.scene.igb_actor
        if self.index >= len(props.skins):
            return {'CANCELLED'}

        item = props.skins[self.index]
        obj = bpy.data.objects.get(item.object_name)
        if obj:
            obj.hide_viewport = not obj.hide_viewport
            obj.hide_render = obj.hide_viewport
            item.is_visible = not obj.hide_viewport

        return {'FINISHED'}


class ACTOR_OT_solo_skin(Operator):
    """Show only the selected skin, hide all others"""
    bl_idname = "actor.solo_skin"
    bl_label = "Solo Skin"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return len(props.skins) > 0

    def execute(self, context):
        props = context.scene.igb_actor
        for i, item in enumerate(props.skins):
            obj = bpy.data.objects.get(item.object_name)
            if obj:
                visible = (i == self.index)
                obj.hide_viewport = not visible
                obj.hide_render = not visible
                item.is_visible = visible

        return {'FINISHED'}


class ACTOR_OT_set_animation(Operator):
    """Set the selected animation as active on the armature"""
    bl_idname = "actor.set_animation"
    bl_label = "Set Animation"

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                len(props.animations) > 0)

    def execute(self, context):
        props = context.scene.igb_actor
        if self.index >= len(props.animations):
            return {'CANCELLED'}

        item = props.animations[self.index]
        action = bpy.data.actions.get(item.action_name)
        armature_obj = bpy.data.objects.get(props.active_armature)

        if action and armature_obj:
            from .animation_builder import set_active_action
            set_active_action(armature_obj, action)
            props.animations_index = self.index

            # Set frame range
            fps = context.scene.render.fps
            context.scene.frame_start = 0
            context.scene.frame_end = max(1, int(item.duration_ms / 1000.0 * fps))

        return {'FINISHED'}


class ACTOR_OT_rest_pose(Operator):
    """Clear animation and show the armature in its rest pose (T-pose)"""
    bl_idname = "actor.rest_pose"
    bl_label = "Rest Pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects)

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj and armature_obj.animation_data:
            armature_obj.animation_data.action = None
        return {'FINISHED'}


class ACTOR_OT_play_animation(Operator):
    """Play/stop the current animation in the viewport"""
    bl_idname = "actor.play_animation"
    bl_label = "Play Animation"

    def execute(self, context):
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel()
        else:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class ACTOR_OT_import_animations(Operator, ImportHelper):
    """Import animations from an IGB file onto the current armature"""
    bl_idname = "actor.import_animations"
    bl_label = "Import Animations"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects)

    def execute(self, context):
        from .sg_skeleton import extract_skeleton
        from .sg_animation import extract_animations
        from .animation_builder import build_all_actions
        from .actor_import import _extract_bind_pose
        from ..igb_format.igb_reader import IGBReader

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Read the animation IGB file
        reader = IGBReader(self.filepath)
        reader.read()

        skeleton = extract_skeleton(reader)
        if skeleton is None:
            self.report({'ERROR'}, "No skeleton found in animation file")
            return {'CANCELLED'}

        # Extract animations
        parsed_anims = extract_animations(reader, skeleton)
        if not parsed_anims:
            self.report({'WARNING'}, "No animations found in file")
            return {'CANCELLED'}

        # Extract bind pose for delta conversion
        bind_pose = _extract_bind_pose(parsed_anims)

        # Build Blender Actions (fps=None → auto-detect from scene)
        actions = build_all_actions(
            armature_obj, parsed_anims, bind_pose=bind_pose
        )

        if not actions:
            self.report({'WARNING'}, "Failed to build any animations")
            return {'CANCELLED'}

        # Add to the animations list in the panel
        fps = context.scene.render.fps
        for action in actions:
            item = props.animations.add()
            item.name = action.name
            item.action_name = action.name
            item.duration_ms = action.get("igb_duration_ms", 0.0)
            item.track_count = action.get("igb_track_count", 0)
            item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))

        basename = os.path.splitext(os.path.basename(self.filepath))[0]
        self.report({'INFO'},
                    f"Imported {len(actions)} animation(s) from '{basename}'")
        return {'FINISHED'}


def _skin_export_texture_items(self, context):
    """Enum items for skin export texture format."""
    return [
        ('clut', "CLUT (Universal)",
         "256-color palette texture. Works in both XML2 and MUA (recommended)"),
        ('dxt5_xml2', "DXT5 (XML2 Only)",
         "DXT5 compressed for X-Men Legends 2 (standard RGB565)"),
        ('dxt5_mua', "DXT5 (MUA Only)",
         "DXT5 compressed for Marvel Ultimate Alliance (BGR565)"),
    ]


class ACTOR_OT_export_skin(Operator, ExportHelper):
    """Export the active skin to an IGB file (from-scratch builder, no template)"""
    bl_idname = "actor.export_skin"
    bl_label = "Export Skin"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    texture_format: EnumProperty(
        name="Texture Format",
        description="Texture encoding format",
        items=_skin_export_texture_items,
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not (len(props.skins) > 0 and
                props.skins_index < len(props.skins)):
            return False
        # Check that the skin mesh object exists
        item = props.skins[props.skins_index]
        return item.object_name in bpy.data.objects

    def execute(self, context):
        from ..exporter.skin_export import export_skin

        props = context.scene.igb_actor
        item = props.skins[props.skins_index]

        # Get the main mesh object
        mesh_obj = bpy.data.objects.get(item.object_name)
        if mesh_obj is None or mesh_obj.type != 'MESH':
            self.report({'ERROR'}, f"Skin mesh not found: {item.object_name}")
            return {'CANCELLED'}

        # Get the armature
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Check that skeleton data is stored on the armature
        if "igb_skin_bone_translations" not in armature_obj:
            self.report({'ERROR'},
                        "No skeleton data found on armature. "
                        "Re-import the actor with a skin file to store "
                        "skeleton data for from-scratch export.")
            return {'CANCELLED'}

        # Collect all mesh objects for this skin (main + outline)
        # Identify siblings by matching the source skin file path
        source_file = mesh_obj.get("igb_skin_template", "")
        mesh_objs = [(mesh_obj, bool(mesh_obj.get("igb_is_outline", False)))]

        # Find outline/sibling meshes from the skins list
        for skin_item in props.skins:
            if skin_item.object_name == item.object_name:
                continue
            sibling = bpy.data.objects.get(skin_item.object_name)
            if sibling is None or sibling.type != 'MESH':
                continue
            # Check if it shares the same source file
            sibling_source = sibling.get("igb_skin_template", "")
            if source_file and sibling_source == source_file:
                is_outline = bool(sibling.get("igb_is_outline", False))
                mesh_objs.append((sibling, is_outline))

        # Also search child meshes of the armature not in skins list
        for child in armature_obj.children:
            if child.type != 'MESH':
                continue
            # Skip if already collected
            if any(m.name == child.name for m, _ in mesh_objs):
                continue
            child_source = child.get("igb_skin_template", "")
            if source_file and child_source == source_file:
                is_outline = bool(child.get("igb_is_outline", False))
                mesh_objs.append((child, is_outline))

        self.report({'INFO'},
                    f"Exporting {len(mesh_objs)} mesh part(s) "
                    f"({sum(1 for _, o in mesh_objs if not o)} main, "
                    f"{sum(1 for _, o in mesh_objs if o)} outline)")

        # Do the export (from-scratch, no template needed)
        if self.texture_format == 'clut':
            swap_rb = False
            texture_mode = 'clut'
        elif self.texture_format == 'dxt5_mua':
            swap_rb = True
            texture_mode = 'dxt5'
        else:  # dxt5_xml2
            swap_rb = False
            texture_mode = 'dxt5'

        success = export_skin(
            filepath=self.filepath,
            mesh_objs=mesh_objs,
            armature_obj=armature_obj,
            operator=self,
            swap_rb=swap_rb,
            texture_mode=texture_mode,
        )

        return {'FINISHED'} if success else {'CANCELLED'}

    def invoke(self, context, event):
        # Pre-fill filename from skin source path or object name
        props = context.scene.igb_actor
        if props.skins_index < len(props.skins):
            item = props.skins[props.skins_index]
            mesh_obj = bpy.data.objects.get(item.object_name)
            source_path = ""
            if item.filepath and os.path.exists(item.filepath):
                source_path = item.filepath
            elif mesh_obj:
                source_path = mesh_obj.get("igb_skin_template", "")
            if source_path and os.path.exists(source_path):
                dirname = os.path.dirname(source_path)
                basename = os.path.splitext(os.path.basename(source_path))[0]
                self.filepath = os.path.join(dirname, f"{basename}_export.igb")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_export_animations(Operator, ExportHelper):
    """Save animations back to the actor IGB file"""
    bl_idname = "actor.export_animations"
    bl_label = "Save Animations"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                len(props.animations) > 0)

    def execute(self, context):
        from .animation_export import export_animations
        return export_animations(context, self.filepath, operator=self)

    def invoke(self, context, event):
        # Default to the original animation file path
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj:
            template = armature_obj.get("igb_anim_file", "")
            if template and os.path.exists(template):
                self.filepath = template
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_convert_rig(Operator):
    """Convert a Unity Humanoid or Mixamo armature to the XML2 Bip01 skeleton"""
    bl_idname = "actor.convert_rig"
    bl_label = "Convert Rig to XML2"
    bl_options = {'REGISTER', 'UNDO'}

    rig_profile: EnumProperty(
        name="Source Rig",
        items=[
            ('AUTO', "Auto-Detect", "Detect Unity or Mixamo automatically"),
            ('UNITY', "Unity Humanoid", "Unity Humanoid bone naming"),
            ('MIXAMO', "Mixamo", "Mixamo bone naming (mixamorig: prefix)"),
        ],
        default='AUTO',
    )

    auto_scale: BoolProperty(
        name="Auto Scale",
        description="Automatically scale the rig to match XML2 character proportions",
        default=True,
    )

    target_height: FloatProperty(
        name="Target Height",
        description="Target character height in game units (XML2 characters are ~68 units tall)",
        default=68.0,
        min=10.0,
        max=500.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        from .rig_converter import convert_rig

        armature_obj = context.active_object
        result = convert_rig(armature_obj, profile=self.rig_profile,
                             auto_scale=self.auto_scale,
                             target_height=self.target_height)

        if result['success']:
            self.report(
                {'INFO'},
                f"Converted rig: {result['mapped']} mapped, "
                f"{result['added']} added, {result['removed']} removed")
            # Set as active armature in the IGB Actors panel
            props = context.scene.igb_actor
            props.active_armature = armature_obj.name

            # Auto-populate skins list with child meshes
            _populate_skins_from_children(props, armature_obj)

            return {'FINISHED'}
        else:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class ACTOR_OT_add_mesh_as_skin(Operator):
    """Add the selected mesh as a skin in the IGB Actors panel"""
    bl_idname = "actor.add_mesh_as_skin"
    bl_label = "Add Mesh as Skin"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Need an active armature set AND a mesh selected
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        arm_obj = bpy.data.objects.get(props.active_armature)
        if arm_obj is None:
            return False
        # Check for mesh selection
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                return True
        return False

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature")
            return {'CANCELLED'}

        added = 0
        existing_names = {item.object_name for item in props.skins}

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            if obj.name in existing_names:
                continue

            # Parent to armature if not already
            if obj.parent != armature_obj:
                obj.parent = armature_obj
                obj.parent_type = 'OBJECT'

            # Add Armature modifier if not present
            has_armature_mod = any(
                m.type == 'ARMATURE' and m.object == armature_obj
                for m in obj.modifiers
            )
            if not has_armature_mod:
                mod = obj.modifiers.new(name="Armature", type='ARMATURE')
                mod.object = armature_obj

            item = props.skins.add()
            item.name = obj.name
            item.object_name = obj.name
            item.is_visible = not obj.hide_viewport
            added += 1

        if added > 0:
            self.report({'INFO'}, f"Added {added} mesh(es) to skins list")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No new meshes to add")
            return {'CANCELLED'}


class ACTOR_OT_remove_skin(Operator):
    """Remove the selected skin from the skins list"""
    bl_idname = "actor.remove_skin"
    bl_label = "Remove Skin"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        props = context.scene.igb_actor
        idx = props.skins_index
        if idx >= len(props.skins):
            return {'CANCELLED'}

        props.skins.remove(idx)
        # Adjust index
        if props.skins_index >= len(props.skins) and len(props.skins) > 0:
            props.skins_index = len(props.skins) - 1

        return {'FINISHED'}


def _populate_skins_from_children(props, armature_obj):
    """Populate the skins list with all mesh children of the armature."""
    existing_names = {item.object_name for item in props.skins}

    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        if child.name in existing_names:
            continue

        # Add Armature modifier if not present
        has_armature_mod = any(
            m.type == 'ARMATURE' and m.object == armature_obj
            for m in child.modifiers
        )
        if not has_armature_mod:
            mod = child.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = armature_obj

        item = props.skins.add()
        item.name = child.name
        item.object_name = child.name
        item.is_visible = not child.hide_viewport


# ---------------------------------------------------------------------------
# Animation management operators (Part 2: single import, Part 3: remove)
# ---------------------------------------------------------------------------

# Module-level cache for the two-step animation import dialog
_anim_import_cache = {
    'parsed_anims': [],  # list of ParsedAnimation
    'bind_pose': None,
    'anim_names': [],    # [(identifier, display_name, description), ...]
}


def _anim_name_items(self, context):
    """Dynamic EnumProperty items from cached parsed animations."""
    items = _anim_import_cache.get('anim_names', [])
    if not items:
        return [('NONE', "No animations found", "")]
    return items


class ACTOR_OT_import_single_animation(Operator, ImportHelper):
    """Import a single animation from an IGB file"""
    bl_idname = "actor.import_single_animation"
    bl_label = "Import Animation"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects)

    def execute(self, context):
        """Read the IGB file, cache animations, then open the pick dialog."""
        from .sg_skeleton import extract_skeleton
        from .sg_animation import extract_animations
        from .actor_import import _extract_bind_pose
        from ..igb_format.igb_reader import IGBReader

        reader = IGBReader(self.filepath)
        reader.read()

        skeleton = extract_skeleton(reader)
        if skeleton is None:
            self.report({'ERROR'}, "No skeleton found in animation file")
            return {'CANCELLED'}

        parsed_anims = extract_animations(reader, skeleton)
        if not parsed_anims:
            self.report({'WARNING'}, "No animations found in file")
            return {'CANCELLED'}

        bind_pose = _extract_bind_pose(parsed_anims)

        # Populate the cache for the pick dialog
        _anim_import_cache['parsed_anims'] = parsed_anims
        _anim_import_cache['bind_pose'] = bind_pose
        _anim_import_cache['anim_names'] = [
            (pa.name, pa.name, f"{pa.duration_ms / 1000.0:.2f}s, {len(pa.tracks)} tracks")
            for pa in parsed_anims
        ]

        # Open the pick dialog
        bpy.ops.actor.pick_animation('INVOKE_DEFAULT')
        return {'FINISHED'}


class ACTOR_OT_pick_animation(Operator):
    """Pick which animation to import and how"""
    bl_idname = "actor.pick_animation"
    bl_label = "Select Animation"
    bl_options = {'REGISTER', 'UNDO'}

    anim_name: EnumProperty(
        name="Animation",
        description="Select the animation to import",
        items=_anim_name_items,
    )

    mode: EnumProperty(
        name="Mode",
        items=[
            ('ADD', "Add New", "Add as a new animation to the list"),
            ('REPLACE', "Replace Selected",
             "Replace the currently selected animation"),
        ],
        default='ADD',
    )

    @classmethod
    def poll(cls, context):
        return len(_anim_import_cache.get('parsed_anims', [])) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "anim_name")
        layout.prop(self, "mode", expand=True)

    def execute(self, context):
        from .animation_builder import build_action

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Find the selected parsed animation
        parsed_anims = _anim_import_cache.get('parsed_anims', [])
        bind_pose = _anim_import_cache.get('bind_pose')
        target_anim = None
        for pa in parsed_anims:
            if pa.name == self.anim_name:
                target_anim = pa
                break

        if target_anim is None:
            self.report({'ERROR'}, f"Animation '{self.anim_name}' not found")
            return {'CANCELLED'}

        # Build the Blender Action
        action = build_action(armature_obj, target_anim, bind_pose=bind_pose)
        if action is None:
            self.report({'ERROR'}, "Failed to build animation")
            return {'CANCELLED'}

        fps = context.scene.render.fps

        if self.mode == 'REPLACE' and props.animations_index < len(props.animations):
            # Replace the currently selected animation
            item = props.animations[props.animations_index]
            old_action = bpy.data.actions.get(item.action_name)

            # Clear from armature if it was active
            if (armature_obj.animation_data and
                    armature_obj.animation_data.action == old_action):
                armature_obj.animation_data.action = None

            # Remove old action
            if old_action is not None:
                bpy.data.actions.remove(old_action)

            # Update the list item
            item.name = action.name
            item.action_name = action.name
            item.duration_ms = action.get("igb_duration_ms", 0.0)
            item.track_count = action.get("igb_track_count", 0)
            item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))

            self.report({'INFO'},
                        f"Replaced animation with '{action.name}'")
        else:
            # Add as new
            item = props.animations.add()
            item.name = action.name
            item.action_name = action.name
            item.duration_ms = action.get("igb_duration_ms", 0.0)
            item.track_count = action.get("igb_track_count", 0)
            item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))
            props.animations_index = len(props.animations) - 1

            self.report({'INFO'},
                        f"Added animation '{action.name}'")

        # Clear cache
        _anim_import_cache['parsed_anims'] = []
        _anim_import_cache['bind_pose'] = None
        _anim_import_cache['anim_names'] = []

        return {'FINISHED'}


class ACTOR_OT_remove_animation(Operator):
    """Remove the selected animation from the list"""
    bl_idname = "actor.remove_animation"
    bl_label = "Remove Animation"
    bl_options = {'REGISTER', 'UNDO'}

    delete_action: BoolProperty(
        name="Delete Action",
        description="Also delete the Blender Action data",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.animations) > 0 and
                props.animations_index < len(props.animations))

    def execute(self, context):
        props = context.scene.igb_actor
        idx = props.animations_index
        if idx >= len(props.animations):
            return {'CANCELLED'}

        item = props.animations[idx]
        action = bpy.data.actions.get(item.action_name)

        # Clear from armature if it was the active action
        armature_obj = bpy.data.objects.get(props.active_armature)
        if (armature_obj and armature_obj.animation_data and
                armature_obj.animation_data.action == action):
            armature_obj.animation_data.action = None

        # Delete the Blender Action
        if self.delete_action and action is not None:
            bpy.data.actions.remove(action)

        # Remove from list
        props.animations.remove(idx)
        if props.animations_index >= len(props.animations) and len(props.animations) > 0:
            props.animations_index = len(props.animations) - 1

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Skin selection dialog (Part 5)
# ---------------------------------------------------------------------------

# Module-level cache for the two-step skin selection
_skin_import_cache = {
    'anim_filepath': '',
    'skins': [],      # [(name, path), ...]
    'options': {},
}


class ACTOR_OT_select_skins(Operator):
    """Select which skins to import with the actor"""
    bl_idname = "actor.select_skins"
    bl_label = "Select Skins to Import"
    bl_options = {'REGISTER', 'UNDO'}

    # Up to 10 skin slots with toggles
    skin_00: BoolProperty(name="Skin 0", default=True)
    skin_01: BoolProperty(name="Skin 1", default=True)
    skin_02: BoolProperty(name="Skin 2", default=True)
    skin_03: BoolProperty(name="Skin 3", default=True)
    skin_04: BoolProperty(name="Skin 4", default=True)
    skin_05: BoolProperty(name="Skin 5", default=True)
    skin_06: BoolProperty(name="Skin 6", default=True)
    skin_07: BoolProperty(name="Skin 7", default=True)
    skin_08: BoolProperty(name="Skin 8", default=True)
    skin_09: BoolProperty(name="Skin 9", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        skins = _skin_import_cache.get('skins', [])
        if not skins:
            layout.label(text="No skins discovered.", icon='INFO')
            return
        layout.label(text=f"Found {len(skins)} skin(s):", icon='MESH_DATA')
        col = layout.column(align=True)
        for i, (name, path) in enumerate(skins):
            if i >= 10:
                break
            prop_name = f"skin_{i:02d}"
            basename = os.path.basename(path)
            col.prop(self, prop_name, text=f"{name} ({basename})")

    def execute(self, context):
        from .actor_import import import_actor

        skins = _skin_import_cache.get('skins', [])
        anim_filepath = _skin_import_cache.get('anim_filepath', '')
        options = _skin_import_cache.get('options', {})

        if not anim_filepath:
            self.report({'ERROR'}, "No animation file cached")
            return {'CANCELLED'}

        # Filter skins based on checkboxes
        selected_skins = []
        for i, (name, path) in enumerate(skins):
            if i >= 10:
                break
            prop_name = f"skin_{i:02d}"
            if getattr(self, prop_name, True):
                selected_skins.append((name, path))

        # Clear the import_skins option if no skins selected
        # but always pass the list (even empty) to import_actor
        props = context.scene.igb_actor
        game_dir = props.game_dir if props.game_dir else None

        result = import_actor(
            context, anim_filepath,
            skin_filepaths=selected_skins,
            game_dir=game_dir,
            operator=self,
            options=options,
        )

        # Clear cache
        _skin_import_cache['anim_filepath'] = ''
        _skin_import_cache['skins'] = []
        _skin_import_cache['options'] = {}

        if result is None:
            return {'CANCELLED'}

        armature_obj, skin_objects, actions = result

        # Update panel state (same as original import_actor execute)
        _populate_import_results(context, props, armature_obj,
                                 skin_objects, actions)

        return {'FINISHED'}


def _populate_import_results(context, props, armature_obj, skin_objects, actions):
    """Populate the panel lists after a successful import."""
    props.active_armature = armature_obj.name
    props.anim_file = armature_obj.get("igb_anim_file", "")

    # Populate skins list
    props.skins.clear()
    for variant_name, mesh_obj in skin_objects:
        item = props.skins.add()
        is_outline = mesh_obj.get("igb_is_outline", False)
        if is_outline:
            item.name = f"{variant_name} (outline)"
        else:
            item.name = variant_name
        item.object_name = mesh_obj.name
        item.is_visible = not mesh_obj.hide_viewport
        template_path = mesh_obj.get("igb_skin_template", "")
        if template_path:
            item.filepath = template_path

    # Populate animations list
    props.animations.clear()
    fps = context.scene.render.fps
    for action in actions:
        item = props.animations.add()
        item.name = action.name
        item.action_name = action.name
        item.duration_ms = action.get("igb_duration_ms", 0.0)
        item.track_count = action.get("igb_track_count", 0)
        item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))


_classes = (
    ACTOR_OT_import_actor,
    ACTOR_OT_import_skin,
    ACTOR_OT_import_animations,
    ACTOR_OT_import_single_animation,
    ACTOR_OT_pick_animation,
    ACTOR_OT_remove_animation,
    ACTOR_OT_select_skins,
    ACTOR_OT_toggle_skin,
    ACTOR_OT_solo_skin,
    ACTOR_OT_set_animation,
    ACTOR_OT_rest_pose,
    ACTOR_OT_play_animation,
    ACTOR_OT_export_skin,
    ACTOR_OT_export_animations,
    ACTOR_OT_convert_rig,
    ACTOR_OT_add_mesh_as_skin,
    ACTOR_OT_remove_skin,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
