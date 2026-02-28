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
            'game_preset': props.game_preset,
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

        # Ensure all imported skin meshes have IGB material properties
        # so the IGB Materials panel can always edit render state.
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

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
        from ..game_profiles import detect_profile, get_profile

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

        game_preset = props.game_preset
        if game_preset == 'auto':
            profile = detect_profile(reader)
        else:
            profile = get_profile(game_preset)

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
                # Ensure IGB material properties exist for the panel
                _ensure_igb_material_properties(mesh_obj)

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
    """Import animations from an IGB file (select which ones)"""
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
        """Read the IGB file, cache animations, then open the multi-select dialog."""
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

        # Populate the cache for the multi-select dialog
        _anim_import_cache['parsed_anims'] = parsed_anims
        _anim_import_cache['bind_pose'] = bind_pose
        _anim_import_cache['anim_names'] = [
            (pa.name, pa.name, f"{pa.duration_ms / 1000.0:.2f}s, {len(pa.tracks)} tracks")
            for pa in parsed_anims
        ]

        # Open the multi-select pick dialog
        bpy.ops.actor.pick_animation('INVOKE_DEFAULT')
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

        # Collect all mesh objects for this skin using collect_skin_segments
        # This only uses meshes registered in the skins list (no armature
        # children scan — export only what's explicitly in the panel).
        from .actor_import import collect_skin_segments
        segments = collect_skin_segments(props, props.skins_index)
        mesh_objs = segments if segments else [
            (mesh_obj, bool(mesh_obj.get("igb_is_outline", False)))
        ]

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
            if source_path and os.path.exists(source_path):
                dirname = os.path.dirname(source_path)
                basename = os.path.splitext(os.path.basename(source_path))[0]
                self.filepath = os.path.join(dirname, f"{basename}_export.igb")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_export_animations(Operator, ExportHelper):
    """Export animations to a NEW IGB file (uses original as read-only template)"""
    bl_idname = "actor.export_animations"
    bl_label = "Export Animations"
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
        # Default to a NEW file alongside the original (never overwrite template)
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj:
            template = armature_obj.get("igb_anim_file", "")
            if template and os.path.exists(template):
                dirname = os.path.dirname(template)
                stem = os.path.splitext(os.path.basename(template))[0]
                self.filepath = os.path.join(dirname, f"{stem}_export.igb")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def _round_trip_skin(context, armature_obj, props, operator=None):
    """Full round-trip: export to game-scale IGB, delete original, reimport fresh.

    Exports the rig + child meshes at full game scale (with igb_export_scale
    and axis rotation applied), then deletes the original armature and all
    child meshes, and reimports the exported IGB as a completely fresh actor.

    This gives a clean IGB-compatible model usable for XML2 animation testing.
    """
    import tempfile

    def _report(level, msg):
        if operator:
            operator.report(level, msg)

    # Collect child meshes
    mesh_children = [c for c in armature_obj.children if c.type == 'MESH']
    if not mesh_children:
        _report({'INFO'}, "No child meshes to round-trip")
        return

    # Check that skeleton data exists
    if "igb_skin_bone_translations" not in armature_obj:
        _report({'WARNING'},
                "No skeleton data on armature, skipping round-trip")
        return

    try:
        from ..exporter.skin_export import export_skin
        from .actor_import import import_actor

        # 1. Export to temp IGB at FULL game scale.
        tmp_path = os.path.join(tempfile.gettempdir(),
                                f"{armature_obj.name}_roundtrip.igb")

        mesh_objs = [(c, False) for c in mesh_children]
        success = export_skin(
            filepath=tmp_path,
            mesh_objs=mesh_objs,
            armature_obj=armature_obj,
            operator=operator,
            swap_rb=False,
            texture_mode='clut',
        )

        if not success:
            _report({'WARNING'},
                    "Round-trip export failed, keeping original meshes")
            return

        # 2. Find old collection
        old_collection = None
        for coll in bpy.data.collections:
            if armature_obj.name in coll.objects:
                old_collection = coll
                break

        # 3. Delete original armature and ALL child meshes
        children_to_remove = list(armature_obj.children)
        for child in children_to_remove:
            bpy.data.objects.remove(child, do_unlink=True)
        bpy.data.objects.remove(armature_obj, do_unlink=True)
        armature_obj = None

        # Remove old empty collection
        if old_collection and len(old_collection.objects) == 0:
            bpy.data.collections.remove(old_collection)

        # 4. Reimport the temp IGB as a completely fresh actor.
        result = import_actor(
            context, tmp_path,
            skin_filepaths=[("default", tmp_path)],
            operator=operator,
            options={
                'import_skins': True,
                'import_animations': False,
                'import_materials': True,
                'game_preset': 'xml2_pc',
            },
        )

        if result is None:
            _report({'ERROR'}, "Round-trip reimport failed")
            return

        new_armature, skin_objects, actions = result

        # 5. Set game-scale properties
        new_armature["igb_export_scale"] = 1.0
        new_armature["igb_converted_rig"] = False

        # 5b. Fix non-deforming bone orientations
        _fix_nondeforming_bone_orientations(new_armature)

        # 5c. Add default IGB material properties
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

        # 6. Update panel state
        _populate_import_results(context, props, new_armature,
                                 skin_objects, actions)

        _report({'INFO'},
                f"Round-trip complete: game-scale actor with "
                f"{len(skin_objects)} mesh(es)")

        # 7. Clean up temp file
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    except Exception as e:
        _report({'WARNING'}, f"Round-trip failed: {e}")
        import traceback
        traceback.print_exc()


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

    target_pose: EnumProperty(
        name="Target Pose",
        items=[
            ('T_POSE', "T-Pose (Recommended)",
             "Repose arms to T-pose for XML2 animation compatibility"),
            ('A_POSE', "A-Pose",
             "Keep A-pose arm orientation (for custom animations or other games)"),
        ],
        default='T_POSE',
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
                             target_height=self.target_height,
                             target_pose=self.target_pose)

        if result['success']:
            pose_info = ""
            src = result.get('source_pose', 'UNKNOWN')
            if src != 'UNKNOWN':
                pose_info = f" (detected {src} → {self.target_pose})"
            self.report(
                {'INFO'},
                f"Converted rig: {result['mapped']} mapped, "
                f"{result['added']} added, {result['removed']} removed"
                f"{pose_info}")
            # Set as active armature in the IGB Actors panel
            props = context.scene.igb_actor
            props.active_armature = armature_obj.name

            # Auto-populate skins list with child meshes
            _populate_skins_from_children(props, armature_obj)

            # Ensure child meshes have IGB material properties before
            # round-trip export.  VRChat/Unity materials won't have igb_*
            # custom props, so fill in sensible defaults.
            for child in armature_obj.children:
                if child.type == 'MESH':
                    _ensure_igb_material_properties(child)

            # Round-trip: export skin to temp IGB then reimport for a clean
            # IGB-compatible mesh that responds correctly to XML2 animations.
            _round_trip_skin(context, armature_obj, props, operator=self)

            return {'FINISHED'}
        else:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class ACTOR_OT_setup_skin(Operator):
    """Auto-setup an armature for IGB skin export.

    Detects whether the rig is already an XML2 Bip01 skeleton or needs
    conversion. For Bip01 rigs: computes skeleton data and configures
    properties. For non-Bip01 rigs: runs full conversion pipeline.
    Either way, performs a round-trip export/reimport for a clean
    IGB-compatible model ready for in-game use.
    """
    bl_idname = "actor.setup_skin"
    bl_label = "Setup Skin"
    bl_options = {'REGISTER', 'UNDO'}

    # --- Conversion options (only used for non-Bip01 rigs) ---
    rig_profile: EnumProperty(
        name="Source Rig",
        items=[
            ('AUTO', "Auto-Detect", "Detect Unity or Mixamo automatically"),
            ('UNITY', "Unity Humanoid", "Unity Humanoid bone naming"),
            ('MIXAMO', "Mixamo", "Mixamo bone naming (mixamorig: prefix)"),
        ],
        default='AUTO',
    )

    target_pose: EnumProperty(
        name="Target Pose",
        items=[
            ('T_POSE', "T-Pose (Recommended)",
             "Repose arms to T-pose for XML2 animation compatibility"),
            ('A_POSE', "A-Pose",
             "Keep A-pose arm orientation (for custom animations)"),
        ],
        default='T_POSE',
    )

    # --- Shared options ---
    auto_scale: BoolProperty(
        name="Auto Scale",
        description="Automatically scale the rig to match XML2 character proportions",
        default=True,
    )

    target_height: FloatProperty(
        name="Target Height",
        description="Target character height in game units "
                    "(XML2 characters are ~68 units tall)",
        default=68.0,
        min=10.0,
        max=500.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        armature_obj = context.active_object
        has_bip01 = ("Bip01" in armature_obj.data.bones
                     if armature_obj else False)

        if has_bip01:
            layout.label(text="Bip01 rig detected", icon='CHECKMARK')
            layout.label(text="Will compute skeleton data and round-trip")
        else:
            layout.label(text="Non-XML2 rig detected", icon='INFO')
            layout.label(text="Will convert to XML2 and round-trip")
            layout.separator()
            layout.prop(self, "rig_profile")
            layout.prop(self, "target_pose")

        layout.separator()
        layout.prop(self, "auto_scale")
        if self.auto_scale:
            layout.prop(self, "target_height")

    def execute(self, context):
        armature_obj = context.active_object
        has_bip01 = "Bip01" in armature_obj.data.bones

        if has_bip01:
            return self._setup_bip01(context, armature_obj)
        else:
            return self._convert_and_setup(context, armature_obj)

    def _convert_and_setup(self, context, armature_obj):
        """Full conversion path for non-XML2 rigs."""
        from .rig_converter import convert_rig

        result = convert_rig(armature_obj, profile=self.rig_profile,
                             auto_scale=self.auto_scale,
                             target_height=self.target_height,
                             target_pose=self.target_pose)

        if not result['success']:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}

        pose_info = ""
        src = result.get('source_pose', 'UNKNOWN')
        if src != 'UNKNOWN':
            pose_info = f" (detected {src} → {self.target_pose})"
        self.report(
            {'INFO'},
            f"Converted rig: {result['mapped']} mapped, "
            f"{result['added']} added, {result['removed']} removed"
            f"{pose_info}")

        props = context.scene.igb_actor
        props.active_armature = armature_obj.name

        _populate_skins_from_children(props, armature_obj)

        for child in armature_obj.children:
            if child.type == 'MESH':
                _ensure_igb_material_properties(child)

        _round_trip_skin(context, armature_obj, props, operator=self)
        return {'FINISHED'}

    def _setup_bip01(self, context, armature_obj):
        """Setup path for existing Bip01 rigs.

        Only computes and stores skeleton metadata needed for export.
        Does NOT modify bone orientations or round-trip the mesh — the
        model is already in the correct state.
        """
        from .rig_converter import setup_bip01_rig

        result = setup_bip01_rig(armature_obj,
                                 auto_scale=self.auto_scale,
                                 target_height=self.target_height)

        if not result['success']:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}

        props = context.scene.igb_actor
        props.active_armature = armature_obj.name

        _populate_skins_from_children(props, armature_obj)

        for child in armature_obj.children:
            if child.type == 'MESH':
                _ensure_igb_material_properties(child)

        added = result.get('added', 0)
        msg = "Skin setup complete — skeleton data stored"
        if added > 0:
            msg += f", created {added} missing bone(s)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


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
    """Import animations from an IGB file (select which ones)"""
    bl_idname = "actor.import_single_animation"
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

        # Open the multi-select pick dialog
        bpy.ops.actor.pick_animation('INVOKE_DEFAULT')
        return {'FINISHED'}


class ACTOR_OT_pick_animation(Operator):
    """Select which animations to import"""
    bl_idname = "actor.pick_animation"
    bl_label = "Select Animations to Import"
    bl_options = {'REGISTER', 'UNDO'}

    source_game: EnumProperty(
        name="Source Game",
        description=(
            "Game the animation file comes from. "
            "MUA and XML2 share bone names, so cross-game import works automatically. "
            "Extra MUA bones (Nub endpoints, fx/Gun bones) are skipped on XML2 rigs"
        ),
        items=[
            ('AUTO', "Auto", "Same game as current rig (no bone remapping)"),
            ('XML2', "XML2", "X-Men Legends II animations"),
            ('MUA', "MUA", "Marvel Ultimate Alliance animations"),
        ],
        default='AUTO',
    )

    # Up to 30 animation slots with toggles (enough for most actors)
    anim_00: BoolProperty(name="Anim 0", default=True)
    anim_01: BoolProperty(name="Anim 1", default=True)
    anim_02: BoolProperty(name="Anim 2", default=True)
    anim_03: BoolProperty(name="Anim 3", default=True)
    anim_04: BoolProperty(name="Anim 4", default=True)
    anim_05: BoolProperty(name="Anim 5", default=True)
    anim_06: BoolProperty(name="Anim 6", default=True)
    anim_07: BoolProperty(name="Anim 7", default=True)
    anim_08: BoolProperty(name="Anim 8", default=True)
    anim_09: BoolProperty(name="Anim 9", default=True)
    anim_10: BoolProperty(name="Anim 10", default=True)
    anim_11: BoolProperty(name="Anim 11", default=True)
    anim_12: BoolProperty(name="Anim 12", default=True)
    anim_13: BoolProperty(name="Anim 13", default=True)
    anim_14: BoolProperty(name="Anim 14", default=True)
    anim_15: BoolProperty(name="Anim 15", default=True)
    anim_16: BoolProperty(name="Anim 16", default=True)
    anim_17: BoolProperty(name="Anim 17", default=True)
    anim_18: BoolProperty(name="Anim 18", default=True)
    anim_19: BoolProperty(name="Anim 19", default=True)
    anim_20: BoolProperty(name="Anim 20", default=True)
    anim_21: BoolProperty(name="Anim 21", default=True)
    anim_22: BoolProperty(name="Anim 22", default=True)
    anim_23: BoolProperty(name="Anim 23", default=True)
    anim_24: BoolProperty(name="Anim 24", default=True)
    anim_25: BoolProperty(name="Anim 25", default=True)
    anim_26: BoolProperty(name="Anim 26", default=True)
    anim_27: BoolProperty(name="Anim 27", default=True)
    anim_28: BoolProperty(name="Anim 28", default=True)
    anim_29: BoolProperty(name="Anim 29", default=True)

    @classmethod
    def poll(cls, context):
        return len(_anim_import_cache.get('parsed_anims', [])) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout

        # Source game selector at the top
        layout.prop(self, "source_game")
        layout.separator()

        anims = _anim_import_cache.get('parsed_anims', [])
        if not anims:
            layout.label(text="No animations found.", icon='INFO')
            return
        layout.label(text=f"Found {len(anims)} animation(s):", icon='ACTION')
        col = layout.column(align=True)
        for i, pa in enumerate(anims):
            if i >= 30:
                col.label(text=f"... and {len(anims) - 30} more (not shown)")
                break
            prop_name = f"anim_{i:02d}"
            secs = pa.duration_ms / 1000.0
            label = f"{pa.name}  ({secs:.1f}s, {len(pa.tracks)} tracks)"
            col.prop(self, prop_name, text=label)

    def execute(self, context):
        from .animation_builder import build_action

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        parsed_anims = _anim_import_cache.get('parsed_anims', [])
        bind_pose = _anim_import_cache.get('bind_pose')

        # Determine bone remapping based on source game selection.
        # MUA and XML2 use identical bone names for shared bones, so
        # cross-game import works automatically — extra MUA bones
        # (HeadNub, Finger0Nub, Toe0Nub, fx01, Gun1, etc.) are
        # silently skipped when not found in the target armature.
        # The bone_remap dict is reserved for future use if any
        # name mismatches are discovered.
        bone_remap = None  # No remapping needed — names are identical

        # For cross-character animation: extract the TARGET armature's
        # bind_pose from its original animation file.  This ensures bone
        # translations are computed relative to the target character's
        # skeleton, not the source's.
        target_bind_pose = None
        target_anim_file = armature_obj.get("igb_anim_file", "")
        if target_anim_file and os.path.exists(target_anim_file):
            try:
                from ..igb_format.igb_reader import IGBReader
                from .sg_skeleton import extract_skeleton
                from .sg_animation import extract_animations
                from .actor_import import _extract_bind_pose

                target_reader = IGBReader(target_anim_file)
                target_reader.read()
                target_skel = extract_skeleton(target_reader)
                if target_skel:
                    target_anims = extract_animations(target_reader, target_skel)
                    target_bind_pose = _extract_bind_pose(target_anims)
            except Exception:
                pass  # Fall back to source bind_pose

        # Collect selected animations based on checkboxes
        selected = []
        for i, pa in enumerate(parsed_anims):
            if i >= 30:
                break
            prop_name = f"anim_{i:02d}"
            if getattr(self, prop_name, True):
                selected.append(pa)

        if not selected:
            self.report({'WARNING'}, "No animations selected")
            return {'CANCELLED'}

        fps = context.scene.render.fps
        imported_count = 0
        for pa in selected:
            action = build_action(armature_obj, pa, bind_pose=bind_pose,
                                  bone_remap=bone_remap,
                                  target_bind_pose=target_bind_pose)
            if action is None:
                continue

            item = props.animations.add()
            item.name = action.name
            item.action_name = action.name
            item.duration_ms = action.get("igb_duration_ms", 0.0)
            item.track_count = action.get("igb_track_count", 0)
            item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))
            imported_count += 1

        if imported_count > 0:
            props.animations_index = len(props.animations) - 1

        self.report({'INFO'}, f"Imported {imported_count} animation(s)")

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

        # Ensure all imported skin meshes have IGB material properties
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

        # Update panel state (same as original import_actor execute)
        _populate_import_results(context, props, armature_obj,
                                 skin_objects, actions)

        return {'FINISHED'}


def _populate_import_results(context, props, armature_obj, skin_objects, actions):
    """Populate the panel lists after a successful import.

    Saves the previous armature's state before switching, then populates
    the new armature's skins and animations from the import result.
    """
    from . import properties as actor_props

    # Save the outgoing armature's state before we overwrite the lists
    old_armature = props.active_armature
    if old_armature and old_armature != armature_obj.name:
        actor_props._save_armature_state(props, old_armature)

    # Set the new armature (bypass update callback since we're populating directly)
    props["active_armature"] = armature_obj.name
    actor_props._prev_armature = armature_obj.name
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

    # Save the newly populated state so switching away and back restores it
    actor_props._save_armature_state(props, armature_obj.name)


def _fix_nondeforming_bone_orientations(armature_obj):
    """Fix Root/Bip01/Motion bone orientations after round-trip reimport.

    In native XML2 imports, these non-deforming bones get their orientation
    from the afakeanim bind_pose (identity quaternion → bone Y-axis along
    world Y, Z-axis along world Z).  The round-trip has no bind_pose, so
    build_armature defaults to Z-up tails, which changes rest_q and causes
    a 90° animation rotation.

    Fix: enter edit mode and set these bones to identity orientation
    (tail along Y-forward, roll aligned to Z-up).
    """
    from mathutils import Vector

    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    nondeforming = {"Bone_000", "", "Bip01", "Motion"}
    for eb in armature_obj.data.edit_bones:
        if eb.name in nondeforming:
            bone_len = max(eb.length, 0.5)
            # Identity orientation: tail along Y (forward in game space)
            eb.tail = eb.head + Vector((0, bone_len, 0))
            eb.align_roll(Vector((0, 0, 1)))

    bpy.ops.object.mode_set(mode='OBJECT')


def _ensure_igb_material_properties(mesh_obj):
    """Ensure a mesh's materials have IGB custom properties for the panel.

    If a material already has igb_diffuse (set by build_material from IGB
    data), it's left as-is.  Otherwise, sensible defaults are added so the
    IGB Materials panel can edit blend, alpha, lighting, and cull settings.
    """
    if not mesh_obj or mesh_obj.type != 'MESH':
        return

    for mat in mesh_obj.data.materials:
        if mat is None:
            continue

        # Derive diffuse from Principled BSDF if no igb_diffuse already set
        diffuse = [0.8, 0.8, 0.8, 1.0]
        if "igb_diffuse" not in mat and mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    bc = node.inputs.get('Base Color')
                    if bc:
                        c = bc.default_value
                        diffuse = [c[0], c[1], c[2], c[3]]
                    break

        # Set each property only if not already present.
        # build_material() sets these from IGB data during normal imports;
        # this fills in defaults for non-IGB materials (VRChat, Unity) or
        # IGB files with missing state attributes.
        def _set(key, val):
            if key not in mat:
                mat[key] = val

        # Core material properties
        _set("igb_diffuse", diffuse)
        _set("igb_ambient", diffuse[:])
        _set("igb_specular", [0.0, 0.0, 0.0, 1.0])
        _set("igb_emission", [0.0, 0.0, 0.0, 1.0])
        _set("igb_shininess", 0.0)
        _set("igb_flags", 31)

        # Render state defaults (standard opaque character material)
        _set("igb_blend_enabled", True)
        _set("igb_blend_src", 4)   # SRC_ALPHA
        _set("igb_blend_dst", 5)   # ONE_MINUS_SRC_ALPHA
        _set("igb_alpha_test_enabled", True)
        _set("igb_alpha_func", 6)  # GREATER
        _set("igb_alpha_ref", 0.5)
        _set("igb_lighting_enabled", True)
        _set("igb_cull_face_enabled", True)
        _set("igb_cull_face_mode", 0)

        # Color attribute (white tint = no tint)
        _set("igb_color_r", 1.0)
        _set("igb_color_g", 1.0)
        _set("igb_color_b", 1.0)
        _set("igb_color_a", 1.0)


class ACTOR_OT_refresh_segments(Operator):
    """Recompute segment diagnostics (blend weight counts) for the active skin"""
    bl_idname = "actor.refresh_segments"
    bl_label = "Refresh Segment Info"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import _count_weighted_vertices, collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not segments:
            self.report({'WARNING'}, "No segments found for active skin")
            return {'CANCELLED'}

        count = 0
        for seg_mesh, _ in segments:
            seg_mesh["igb_weighted_vert_count"] = _count_weighted_vertices(seg_mesh)
            count += 1

        self.report({'INFO'}, f"Updated diagnostics for {count} segment(s)")
        return {'FINISHED'}


class ACTOR_OT_add_segment(Operator):
    """Add the selected mesh as a segment to the active skin"""
    bl_idname = "actor.add_segment"
    bl_label = "Add Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Name for this segment (e.g. 'gun_left', 'cape'). "
                    "Leave empty to add as part of the body (no segment wrapping)",
        default="",
    )

    segment_visible: BoolProperty(
        name="Visible",
        description="If checked, segment starts visible. "
                    "If unchecked, segment starts hidden (flag bit 1 set)",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        if not (len(props.skins) > 0 and props.skins_index < len(props.skins)):
            return False
        # Need a mesh selected
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                return True
        return False

    def invoke(self, context, event):
        # Show dialog so user can set segment name and visibility
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature")
            return {'CANCELLED'}

        seg_flags = 0 if self.segment_visible else 2

        existing_names = {item.object_name for item in props.skins}
        added = 0

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

            # Auto-detect outline from name
            is_outline = "_outline" in obj.name.lower()
            obj["igb_is_outline"] = is_outline

            # Set segment properties
            if self.segment_name:
                if is_outline:
                    obj["igb_segment_name"] = self.segment_name + "_outline"
                else:
                    obj["igb_segment_name"] = self.segment_name
            else:
                obj["igb_segment_name"] = ""

            obj["igb_segment_flags"] = seg_flags

            # Apply Blender visibility to match segment flags
            if seg_flags & 2:
                obj.hide_viewport = True
                obj.hide_render = True
            else:
                obj.hide_viewport = False
                obj.hide_render = False

            item = props.skins.add()
            item.name = f"{obj.name} (outline)" if is_outline else obj.name
            item.object_name = obj.name
            item.is_visible = not obj.hide_viewport
            added += 1

        if added > 0:
            vis_text = "visible" if self.segment_visible else "hidden"
            seg_text = f"'{self.segment_name}'" if self.segment_name else "body"
            self.report({'INFO'},
                        f"Added {added} mesh(es) as {seg_text} segment ({vis_text})")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No new meshes to add as segments")
            return {'CANCELLED'}


class ACTOR_OT_remove_segment(Operator):
    """Remove all meshes in a segment group from the skins list"""
    bl_idname = "actor.remove_segment"
    bl_label = "Remove Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Base segment name (e.g. 'gun_left'). "
                    "Removes both main and outline meshes for this segment",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        props = context.scene.igb_actor

        if not self.segment_name:
            self.report({'WARNING'}, "No segment specified")
            return {'CANCELLED'}

        # Collect indices of skins list entries that belong to this segment
        # A mesh belongs to segment "gun_left" if its igb_segment_name is
        # "gun_left" or "gun_left_outline"
        matching_names = {self.segment_name, self.segment_name + "_outline"}
        indices_to_remove = []

        for i, item in enumerate(props.skins):
            mesh_obj = bpy.data.objects.get(item.object_name)
            if mesh_obj is None:
                continue
            seg = mesh_obj.get("igb_segment_name", "")
            if seg in matching_names:
                indices_to_remove.append(i)

        if not indices_to_remove:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.segment_name}'")
            return {'CANCELLED'}

        # Remove in reverse order so indices stay valid
        for i in reversed(indices_to_remove):
            props.skins.remove(i)

        # Adjust skins_index if needed
        if props.skins_index >= len(props.skins) and len(props.skins) > 0:
            props.skins_index = len(props.skins) - 1

        self.report({'INFO'},
                    f"Removed {len(indices_to_remove)} mesh(es) "
                    f"from segment '{self.segment_name}'")
        return {'FINISHED'}


class ACTOR_OT_rename_segment(Operator):
    """Rename a segment group (updates all meshes in the group)"""
    bl_idname = "actor.rename_segment"
    bl_label = "Rename Segment"
    bl_options = {'REGISTER', 'UNDO'}

    old_name: StringProperty(
        name="Current Name",
        description="Current base segment name",
        default="",
    )

    new_name: StringProperty(
        name="New Name",
        description="New segment name (e.g. 'gun_left', 'cape'). "
                    "Leave empty to convert segment meshes to body meshes",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def invoke(self, context, event):
        self.new_name = self.old_name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "new_name")

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not self.old_name:
            self.report({'WARNING'}, "Cannot rename the body group")
            return {'CANCELLED'}

        new_name = self.new_name.strip()
        if new_name == self.old_name:
            return {'CANCELLED'}

        # Find all meshes matching the old base segment name
        count = 0
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            base_seg = mesh_seg
            is_outline_seg = base_seg.endswith("_outline")
            if is_outline_seg:
                base_seg = base_seg[:-8]
            if base_seg != self.old_name:
                continue

            # Set new segment name
            if new_name:
                if is_outline_seg:
                    seg_mesh["igb_segment_name"] = new_name + "_outline"
                else:
                    seg_mesh["igb_segment_name"] = new_name
            else:
                # Converting to body — clear segment name
                seg_mesh["igb_segment_name"] = ""
            count += 1

        if count == 0:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.old_name}'")
            return {'CANCELLED'}

        target = f"'{new_name}'" if new_name else "body"
        self.report({'INFO'},
                    f"Renamed segment '{self.old_name}' → {target} "
                    f"({count} mesh(es))")
        return {'FINISHED'}


class ACTOR_OT_select_segment(Operator):
    """Select all meshes belonging to a segment group"""
    bl_idname = "actor.select_segment"
    bl_label = "Select Segment Meshes"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Base segment name to select (empty = body)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        # Deselect all first
        bpy.ops.object.select_all(action='DESELECT')

        count = 0
        last_obj = None
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            base_seg = mesh_seg
            if base_seg.endswith("_outline"):
                base_seg = base_seg[:-8]
            if base_seg == self.segment_name:
                seg_mesh.select_set(True)
                last_obj = seg_mesh
                count += 1

        if last_obj:
            context.view_layer.objects.active = last_obj

        name = f"'{self.segment_name}'" if self.segment_name else "body"
        self.report({'INFO'}, f"Selected {count} mesh(es) in {name}")
        return {'FINISHED'}


class ACTOR_OT_toggle_segment(Operator):
    """Toggle segment visibility (visible/hidden) for export"""
    bl_idname = "actor.toggle_segment"
    bl_label = "Toggle Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not segments or not self.segment_name:
            self.report({'WARNING'}, "No segment specified")
            return {'CANCELLED'}

        # Find all meshes matching this base segment name (main + outline pair)
        # Main segments: igb_segment_name = "gun_left"
        # Outline segments: igb_segment_name = "gun_left_outline"
        matched = []
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            # Strip _outline suffix for comparison
            base_seg = mesh_seg
            if base_seg.endswith("_outline"):
                base_seg = base_seg[:-8]
            if base_seg == self.segment_name:
                matched.append(seg_mesh)

        if not matched:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.segment_name}'")
            return {'CANCELLED'}

        # Toggle: if currently hidden (flags & 2), make visible, else hide
        current_flags = matched[0].get("igb_segment_flags", 0)
        new_hidden = not bool(current_flags & 2)
        new_flags = 2 if new_hidden else 0

        for mesh_obj in matched:
            mesh_obj["igb_segment_flags"] = new_flags
            mesh_obj.hide_viewport = new_hidden
            mesh_obj.hide_render = new_hidden

        state = "Hidden" if new_hidden else "Visible"
        self.report({'INFO'},
                    f"Segment '{self.segment_name}': {state} "
                    f"({len(matched)} mesh(es))")
        return {'FINISHED'}


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
    ACTOR_OT_setup_skin,
    ACTOR_OT_add_mesh_as_skin,
    ACTOR_OT_remove_skin,
    ACTOR_OT_refresh_segments,
    ACTOR_OT_add_segment,
    ACTOR_OT_remove_segment,
    ACTOR_OT_rename_segment,
    ACTOR_OT_select_segment,
    ACTOR_OT_toggle_segment,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
