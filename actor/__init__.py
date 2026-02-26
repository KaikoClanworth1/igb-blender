"""IGB Actor module — skeleton, skinning, and animation import/export.

Provides a separate "IGB Actors" N-panel for importing characters with
their skeletons, skins, and animations from Alchemy Engine IGB files.
"""


def register():
    from . import properties
    from . import operators
    from . import panels
    properties.register()
    operators.register()
    panels.register()


def unregister():
    from . import panels
    from . import operators
    from . import properties
    panels.unregister()
    operators.unregister()
    properties.unregister()
