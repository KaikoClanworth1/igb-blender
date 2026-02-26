"""Kaiko's Map Maker Tools — create custom maps for X-Men Legends II inside Blender."""

from . import conversation
from . import properties
from . import operators
from . import panels


def register():
    conversation.register()
    properties.register()
    operators.register()
    panels.register()


def unregister():
    panels.unregister()
    operators.unregister()
    properties.unregister()
    conversation.unregister()
