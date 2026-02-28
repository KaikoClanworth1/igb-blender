"""IGB Actors N-panel for the 3D Viewport sidebar.

Provides UI for importing and managing actor characters:
- Import actor (skeleton + skins + animations)
- Skin list with visibility toggles
- Animation list with playback controls
- Export buttons for skins and animations
"""

import os

import bpy
from bpy.types import Panel, UIList


# ---------------------------------------------------------------------------
# UIList classes
# ---------------------------------------------------------------------------

class ACTOR_UL_skins(UIList):
    """UIList for actor skin variants."""
    bl_idname = "ACTOR_UL_skins"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Visibility icon
            vis_icon = 'HIDE_OFF' if item.is_visible else 'HIDE_ON'
            op = row.operator("actor.toggle_skin", text="", icon=vis_icon, emboss=False)
            op.index = list(data.skins).index(item)
            # Name
            row.prop(item, "name", text="", emboss=False)
            # Skin code
            if item.skin_code:
                row.label(text=item.skin_code)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name)


class ACTOR_UL_animations(UIList):
    """UIList for actor animations."""
    bl_idname = "ACTOR_UL_animations"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Play button
            op = row.operator("actor.set_animation", text="", icon='PLAY', emboss=False)
            op.index = list(data.animations).index(item)
            # Name
            row.prop(item, "name", text="", emboss=False)
            # Duration
            if item.duration_ms > 0:
                secs = item.duration_ms / 1000.0
                row.label(text=f"{secs:.1f}s")
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name)


# ---------------------------------------------------------------------------
# Panel classes
# ---------------------------------------------------------------------------

class ACTOR_PT_Main(Panel):
    """IGB Actors main panel"""
    bl_label = "IGB Actors"
    bl_idname = "ACTOR_PT_Main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"

    def draw(self, context):
        pass  # Children handle the drawing


class ACTOR_PT_Import(Panel):
    """Import section"""
    bl_label = "Import"
    bl_idname = "ACTOR_PT_Import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"
    bl_parent_id = "ACTOR_PT_Main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Game directory
        layout.prop(props, "game_dir")
        layout.separator()

        # Import button
        layout.operator("actor.import_actor", icon='ARMATURE_DATA')
        layout.separator()

        # Import options
        box = layout.box()
        box.label(text="Import Options", icon='PREFERENCES')
        col = box.column(align=True)
        col.prop(props, "import_skins")
        col.prop(props, "import_animations")
        col.prop(props, "import_materials")
        col.separator()
        col.prop(props, "game_preset")

        # Skin Setup — show when an armature is selected.
        # Detects whether it's a Bip01 rig or needs conversion.
        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            has_igb_data = "igb_skin_bone_info_list" in obj
            layout.separator()
            box = layout.box()
            box.label(text="Skin Setup", icon='ARMATURE_DATA')
            if has_igb_data:
                box.label(text="Armature has XML2 skeleton data",
                          icon='CHECKMARK')
            else:
                has_bip01 = "Bip01" in obj.data.bones
                if has_bip01:
                    box.label(text="Bip01 rig — will configure for export")
                else:
                    box.label(text="Non-XML2 rig — will convert and setup")
            box.operator("actor.setup_skin", icon='FILE_REFRESH')

        # Active armature info
        if props.active_armature:
            arm_obj = bpy.data.objects.get(props.active_armature)
            if arm_obj:
                layout.separator()
                box = layout.box()
                box.label(text="Active Actor", icon='ARMATURE_DATA')
                box.label(text=f"Name: {arm_obj.name}")
                bone_count = arm_obj.get("igb_bone_count", 0)
                box.label(text=f"Bones: {bone_count}")


class ACTOR_PT_Skins(Panel):
    """Skin management section"""
    bl_label = "Skins"
    bl_idname = "ACTOR_PT_Skins"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.igb_actor.active_armature != ""

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Skin list
        row = layout.row()
        row.template_list(
            "ACTOR_UL_skins", "",
            props, "skins",
            props, "skins_index",
            rows=3,
        )

        # Solo button for selected skin
        if props.skins_index < len(props.skins):
            row = layout.row(align=True)
            op = row.operator("actor.solo_skin", text="Solo Selected", icon='RESTRICT_VIEW_OFF')
            op.index = props.skins_index

        layout.separator()

        # Import/Export buttons
        row = layout.row(align=True)
        row.operator("actor.import_skin", icon='IMPORT')
        row.operator("actor.export_skin", icon='EXPORT')

        # Add/Remove buttons
        row = layout.row(align=True)
        row.operator("actor.add_mesh_as_skin", icon='ADD')
        row.operator("actor.remove_skin", icon='REMOVE')


class ACTOR_PT_Segments(Panel):
    """Segment management and diagnostics for the selected skin"""
    bl_label = "Segments"
    bl_idname = "ACTOR_PT_Segments"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"
    bl_parent_id = "ACTOR_PT_Skins"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature != "" and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        from ..actor.actor_import import collect_skin_segments

        segments = collect_skin_segments(props, props.skins_index)

        if not segments:
            layout.label(text="No segments found.", icon='INFO')
            return

        # Group meshes by base segment name (strip _outline suffix to pair)
        groups = {}  # base_name -> {'main': [], 'outline': [], 'flags': int}
        for seg_mesh, is_outline in segments:
            seg_name = seg_mesh.get("igb_segment_name", "")
            seg_flags = seg_mesh.get("igb_segment_flags", 0)
            # Base name: strip _outline suffix for grouping
            # Outline segments are named "gun_left_outline" — strip suffix
            base = seg_name
            if base.endswith("_outline"):
                base = base[:-8]  # strip "_outline"
            if not base:
                base = ""  # body group
            if base not in groups:
                groups[base] = {'main': [], 'outline': [], 'flags': seg_flags}
            if is_outline:
                groups[base]['outline'].append(seg_mesh)
            else:
                groups[base]['main'].append(seg_mesh)

        # Draw body group first, then named segments
        sorted_names = sorted(groups.keys(), key=lambda n: (n != "", n))

        for base_name in sorted_names:
            grp = groups[base_name]
            is_body = (base_name == "")
            display_name = "Body" if is_body else base_name
            seg_flags = grp['flags']
            is_hidden = bool(seg_flags & 2)

            box = layout.box()

            # Header row: name + action buttons
            header = box.row(align=True)
            header.label(text=display_name,
                         icon='MESH_DATA' if is_body else 'OBJECT_DATA')

            # Select button — works for both body and segments
            op_sel = header.operator("actor.select_segment",
                                     text="", icon='RESTRICT_SELECT_OFF')
            op_sel.segment_name = base_name

            if not is_body:
                # Rename button
                op_ren = header.operator("actor.rename_segment",
                                         text="", icon='GREASEPENCIL')
                op_ren.old_name = base_name

                # Visibility toggle
                vis_icon = 'HIDE_ON' if is_hidden else 'HIDE_OFF'
                op = header.operator("actor.toggle_segment",
                                     text="", icon=vis_icon,
                                     depress=not is_hidden)
                op.segment_name = base_name

                # Remove button
                all_seg_meshes = grp['main'] + grp['outline']
                if all_seg_meshes:
                    op_rem = header.operator("actor.remove_segment",
                                            text="", icon='X')
                    op_rem.segment_name = base_name

            # Sub-meshes
            col = box.column(align=True)
            all_meshes = grp['main'] + grp['outline']
            for mesh_obj in all_meshes:
                mesh_outline = bool(mesh_obj.get("igb_is_outline", False))
                vert_count = len(mesh_obj.data.vertices) if mesh_obj.data else 0
                vgroup_count = len(mesh_obj.vertex_groups)
                weighted = mesh_obj.get("igb_weighted_vert_count", -1)

                mesh_icon = 'MOD_WIREFRAME' if mesh_outline else 'MESH_DATA'
                mesh_type = "Outline" if mesh_outline else "Main"

                row = col.row(align=True)
                row.label(text=f"{mesh_type}: {mesh_obj.name}", icon=mesh_icon)

                sub = col.row(align=True)
                sub.label(text=f"  {vert_count}v, {vgroup_count} groups")

                # Weight diagnostic
                if weighted >= 0 and vert_count > 0:
                    ratio = weighted / vert_count
                    w_icon = 'CHECKMARK' if ratio > 0.99 else 'ERROR'
                    sub.label(text=f"{weighted}/{vert_count}", icon=w_icon)

        # Buttons
        layout.separator()
        row = layout.row(align=True)
        row.operator("actor.add_segment", icon='ADD')
        row.operator("actor.refresh_segments", text="Refresh", icon='FILE_REFRESH')


class ACTOR_PT_Animations(Panel):
    """Animation management section"""
    bl_label = "Animations"
    bl_idname = "ACTOR_PT_Animations"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.igb_actor.active_armature != ""

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Animation list with +/- buttons on the side
        row = layout.row()
        row.template_list(
            "ACTOR_UL_animations", "",
            props, "animations",
            props, "animations_index",
            rows=5,
        )

        # Side buttons for add/remove
        col = row.column(align=True)
        col.operator("actor.import_single_animation", icon='IMPORT', text="")
        col.operator("actor.remove_animation", icon='REMOVE', text="")

        # Playback controls
        row = layout.row(align=True)
        row.operator("actor.rest_pose", text="Rest Pose", icon='ARMATURE_DATA')
        is_playing = context.screen.is_animation_playing
        play_icon = 'PAUSE' if is_playing else 'PLAY'
        play_text = "Stop" if is_playing else "Play"
        row.operator("actor.play_animation", text=play_text, icon=play_icon)

        # Info for selected animation
        if props.animations_index < len(props.animations):
            anim = props.animations[props.animations_index]
            box = layout.box()
            col = box.column(align=True)
            col.label(text=f"Duration: {anim.duration_ms / 1000.0:.2f}s")
            col.label(text=f"Tracks: {anim.track_count}")
            col.label(text=f"Frames: {anim.frame_count}")

        layout.separator()
        row = layout.row(align=True)
        row.operator("actor.import_animations", text="Import Animations",
                      icon='IMPORT')
        row.operator("actor.export_animations", text="Save Animations",
                      icon='EXPORT')


class ACTOR_PT_Materials(Panel):
    """Material properties for actor skins"""
    bl_label = "Materials"
    bl_idname = "ACTOR_PT_Materials"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Actors"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.igb_actor.active_armature != ""

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Find the selected skin's mesh object to show its material
        mat = None
        mesh_obj = None
        if props.skins_index < len(props.skins):
            skin = props.skins[props.skins_index]
            mesh_obj = bpy.data.objects.get(skin.name)
            if mesh_obj and mesh_obj.data.materials:
                mat = mesh_obj.data.materials[0]

        if mat is None:
            layout.label(text="Select a skin to see materials.", icon='INFO')
            return

        # Skin name + material name
        box = layout.box()
        box.label(text=f"Skin: {mesh_obj.name}", icon='MESH_DATA')
        box.label(text=f"Material: {mat.name}", icon='MATERIAL')

        # Draw material colors using the shared helper from panels.py
        from ..panels import _draw_material_colors
        _draw_material_colors(layout, mat)

        # --- Render State Summary ---
        box = layout.box()
        box.label(text="Render State", icon='SHADING_RENDERED')

        # Blend
        blend_en = mat.get("igb_blend_enabled")
        if blend_en is not None:
            box.prop(mat, '["igb_blend_enabled"]', text="Blend Enabled")

        # Alpha test
        alpha_en = mat.get("igb_alpha_test_enabled")
        if alpha_en is not None:
            box.prop(mat, '["igb_alpha_test_enabled"]', text="Alpha Test")

        # Lighting
        lighting = mat.get("igb_lighting_enabled")
        if lighting is not None:
            box.prop(mat, '["igb_lighting_enabled"]', text="Lighting")

        # Backface culling
        cull = mat.get("igb_cull_face_enabled")
        if cull is not None:
            box.prop(mat, '["igb_cull_face_enabled"]', text="Backface Culling")

        has_igb = any(key.startswith("igb_") for key in mat.keys())
        if not has_igb:
            layout.separator()
            layout.label(text="No IGB properties. Import an IGB skin first.")

        # --- Quick Tools for actor materials ---
        layout.separator()
        box = layout.box()
        box.label(text="Quick Tools (All Skins)", icon='TOOL_SETTINGS')
        box.label(text="Apply to all materials on all skin meshes.")

        # Emission quick set
        row = box.row(align=True)
        op = row.operator("igb.set_all_emission", text="No Emission",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.selected_only = False
        op = row.operator("igb.set_all_emission", text="Set Emission",
                          icon='LIGHT_SUN')
        op.selected_only = False

        # Specular quick set
        row = box.row(align=True)
        op = row.operator("igb.set_all_specular", text="No Specular",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.selected_only = False
        op = row.operator("igb.set_all_specular", text="Set Specular",
                          icon='LIGHT_POINT')
        op.selected_only = False

        # Diffuse quick set
        row = box.row(align=True)
        op = row.operator("igb.set_all_diffuse", text="Set Diffuse",
                          icon='COLORSET_03_VEC')
        op.selected_only = False

        # Shininess
        row = box.row(align=True)
        op = row.operator("igb.set_all_shininess", text="Matte (0)",
                          icon='MATPLANE')
        op.shininess = 0.0
        op.selected_only = False
        op = row.operator("igb.set_all_shininess", text="Shiny (64)",
                          icon='SHADING_RENDERED')
        op.shininess = 64.0
        op.selected_only = False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    ACTOR_UL_skins,
    ACTOR_UL_animations,
    ACTOR_PT_Main,
    ACTOR_PT_Import,
    ACTOR_PT_Skins,
    ACTOR_PT_Segments,
    ACTOR_PT_Animations,
    ACTOR_PT_Materials,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
