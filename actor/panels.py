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

        # Rig Converter — show when an armature is selected but not yet
        # set up for IGB export (no igb_skin_bone_info_list property)
        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            has_igb_data = "igb_skin_bone_info_list" in obj
            layout.separator()
            box = layout.box()
            box.label(text="Rig Converter", icon='ARMATURE_DATA')
            if has_igb_data:
                box.label(text="Armature has XML2 skeleton data", icon='CHECKMARK')
            else:
                box.label(text="Convert Unity/Mixamo rig for IGB export")
            box.operator("actor.convert_rig", icon='FILE_REFRESH')

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
    ACTOR_PT_Animations,
    ACTOR_PT_Materials,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
