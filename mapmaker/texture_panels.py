"""N-panel UI for the IGB Texture Editor."""

import bpy
from bpy.types import Panel


class TEX_PT_main(Panel):
    """IGB Texture Editor"""
    bl_label = "Texture Editor"
    bl_idname = "TEX_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB"

    def draw(self, context):
        layout = self.layout

        # Description
        box = layout.box()
        col = box.column(align=True)
        col.label(text="View & replace textures in IGB files", icon='IMAGE_DATA')
        col.label(text="Loading screens, HUD, menus, etc.")

        layout.separator()

        # Open button
        row = layout.row()
        row.scale_y = 1.5
        row.operator("tex.open_texture_editor",
                      text="Open Texture Editor",
                      icon='IMAGE_DATA')


_classes = (
    TEX_PT_main,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
