"""MUA Team Menu editor.

Edit the team-management screen lineup: where each character stands (the
pad/actor/camera transforms in mlm_team_back.igb) and which pad each character
uses (the `menulocation` field in herostat.xmlb).
"""

from . import operators, panels


def register():
    operators.register()
    panels.register()


def unregister():
    panels.unregister()
    operators.unregister()
