"""Kaiko's Map Maker Tools — create custom maps for X-Men Legends II inside Blender."""

from . import conversation
from . import properties
from . import objective_properties
from . import operators
from . import objective_operators
from . import panels
from . import menu_properties
from . import menu_operators
from . import menu_panels
from . import texture_operators
from . import texture_panels


def register():
    conversation.register()
    properties.register()
    objective_properties.register()
    operators.register()
    objective_operators.register()
    panels.register()
    menu_properties.register()
    menu_operators.register()
    menu_panels.register()
    texture_operators.register()
    texture_panels.register()


def unregister():
    texture_panels.unregister()
    texture_operators.unregister()
    menu_panels.unregister()
    menu_operators.unregister()
    menu_properties.unregister()
    panels.unregister()
    objective_operators.unregister()
    operators.unregister()
    objective_properties.unregister()
    properties.unregister()
    conversation.unregister()
