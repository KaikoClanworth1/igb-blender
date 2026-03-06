"""Blender operators for the IGB Texture Editor."""

import os
import sys
import bpy
from bpy.types import Operator
from pathlib import Path


def _get_deps_dir():
    """Get the path to the bundled PySide6 deps directory."""
    return str(Path(__file__).parent / "deps")


def _ensure_pyside6_path():
    """Add the bundled deps directory to sys.path for PySide6 imports."""
    deps_dir = _get_deps_dir()
    if deps_dir not in sys.path:
        sys.path.insert(0, deps_dir)


def _has_pyside6():
    """Check if PySide6 is importable."""
    _ensure_pyside6_path()
    try:
        from PySide6.QtWidgets import QApplication  # noqa
        return True
    except ImportError:
        return False


class TEX_OT_open_texture_editor(Operator):
    """Open the IGB Texture Editor window (PySide6)"""
    bl_idname = "tex.open_texture_editor"
    bl_label = "Open Texture Editor"
    bl_description = ("Open a PySide6 window to view, replace, and export "
                      "textures in IGB files (loading screens, HUD, menus)")

    _app = None
    _window = None
    _timer = None

    @classmethod
    def poll(cls, context):
        if cls._window is not None:
            return False  # Already open
        return _has_pyside6()

    def execute(self, context):
        _ensure_pyside6_path()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.report({'ERROR'},
                        "PySide6 not installed. Use 'Install PySide6' in IGB Extras panel")
            return {'CANCELLED'}

        from .texture_editor import TextureEditorWindow

        # Create QApplication if needed
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        TEX_OT_open_texture_editor._app = app

        # Create editor window
        window = TextureEditorWindow()
        window.show()
        TEX_OT_open_texture_editor._window = window

        # Register modal timer to pump Qt events
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            app = TEX_OT_open_texture_editor._app
            if app is not None:
                app.processEvents()

            # Check if window was closed
            window = TEX_OT_open_texture_editor._window
            if window is None or not window.isVisible():
                self._cleanup(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        window = TEX_OT_open_texture_editor._window
        if window is not None:
            window.close()
        TEX_OT_open_texture_editor._window = None


_classes = (
    TEX_OT_open_texture_editor,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
