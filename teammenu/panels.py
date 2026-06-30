"""UI panel for the MUA Team Menu editor."""

import bpy
from bpy.types import Panel, UIList


class IGBTEAMMENU_UL_chars(UIList):
    """Character list with editable menulocation (pad assignment)."""

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=item.name or "?")
            row.label(text=item.skin, icon='TEXTURE')
            row.prop(item, "menulocation", text="Pad")
        elif self.layout_type == 'GRID':
            layout.label(text=item.name)


class IGB_PT_TeamMenu(Panel):
    """MUA team-management lineup editor."""
    bl_label = "Team Menu"
    bl_idname = "IGB_PT_TeamMenu"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_teammenu

        box = layout.box()
        box.label(text="1. Import", icon='IMPORT')
        box.operator("igb.teammenu_import",
                     text="Import Team Menu (mlm_team_back.igb)",
                     icon='IMPORT')
        if props.igb_path:
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text=bpy.path.basename(props.igb_path), icon='FILE')
            if props.herostat_path:
                col.label(text=bpy.path.basename(props.herostat_path),
                          icon='PRESET')

        if not props.igb_path:
            layout.label(text="Import mlm_team_back.igb to begin.", icon='INFO')
            return

        box = layout.box()
        box.label(text="2. Edit positions", icon='EMPTY_ARROWS')
        col = box.column(align=True)
        col.scale_y = 0.75
        col.label(text="Move the pad/actor/camera objects in")
        col.label(text="the 'IGB Team Menu' collection.")
        col.label(text="padNN = lineup, actorNN = active team,")
        col.label(text="cameras = look-through to aim.")
        col.label(text="Backdrop = locked reference (don't edit).")

        box = layout.box()
        box.label(text="3. Character pad assignment (menulocation)",
                  icon='COMMUNITY')
        if len(props.chars):
            box.template_list("IGBTEAMMENU_UL_chars", "", props, "chars",
                              props, "chars_index", rows=8)
            box.label(text="Pad N → padNN_model. 0 = not shown.", icon='INFO')
        else:
            box.label(text="No herostat loaded.", icon='ERROR')

        box = layout.box()
        box.label(text="4. Build IGB", icon='EXPORT')
        box.scale_y = 1.2
        box.operator("igb.teammenu_export",
                     text="Build IGB", icon='EXPORT')
        box.scale_y = 1.0
        box.label(text="Writes positions + menulocation back to the",
                  icon='INFO')
        box.label(text="IGB + herostat in place (.bak saved first).")


_CLASSES = (IGBTEAMMENU_UL_chars, IGB_PT_TeamMenu)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
