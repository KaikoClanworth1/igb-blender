"""PySide6 Menu Editor -- external window for editing game menu ENGB files.

Launched from Blender via modal operator. Reads/writes ENGB files using
xmlb.py round-trip parser.  Extracts textures from referenced IGB files
for visual preview.  Qt event loop pumped by Blender modal timer.

Center pane renders the IGB scene geometry onto a 16:9 canvas using QPainter,
showing the menu as it appears in-game with all its visual assets.  Items are
selectable and draggable; position changes write back to the ENGB element tree
as _editor_x / _editor_z attributes.
"""

import copy
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Ensure bundled deps directory is on sys.path for PySide6
def _find_deps():
    local = Path(__file__).parent / "deps"
    if local.exists():
        return str(local)
    source = Path(r"F:\Projects\XMenLegends\Alchemy Engine IGB Format"
                  r"\igb_blender\mapmaker\deps")
    if source.exists():
        return str(source)
    return str(local)

_deps_dir = _find_deps()
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QSplitter, QTreeWidget,
        QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QFormLayout,
        QLabel, QLineEdit, QCheckBox, QFrame, QScrollArea, QMenu,
        QSizePolicy, QPushButton, QComboBox, QAbstractItemView,
        QMenuBar, QFileDialog, QMessageBox, QGridLayout, QToolBar,
        QDoubleSpinBox,
    )
    from PySide6.QtCore import Qt, QSize, QRectF, QPointF, Signal
    from PySide6.QtGui import (
        QPixmap, QFont, QIcon, QColor, QPalette, QAction,
        QShortcut, QKeySequence, QPainter, QPen, QBrush, QImage,
        QPolygonF, QTransform, QFontMetricsF,
    )
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Stylesheet -- dark X-Men Legends theme (shared base with convo_editor)
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
}
QMenuBar {
    background-color: #0f3460;
    color: #e0c040;
    font-weight: bold;
    padding: 2px;
}
QMenuBar::item:selected {
    background-color: #1a4a80;
}
QMenu {
    background-color: #16213e;
    color: #d0d0d0;
    border: 1px solid #0f3460;
}
QMenu::item:selected {
    background-color: #0f3460;
    color: #e0c040;
}
QSplitter {
    background-color: #1a1a2e;
}
QSplitter::handle {
    background-color: #0f3460;
    width: 3px;
}
QTreeWidget {
    background-color: #16213e;
    color: #d0d0d0;
    border: 1px solid #0f3460;
    font-size: 13px;
    outline: none;
}
QTreeWidget::item {
    padding: 3px 4px;
    border-bottom: 1px solid #1a1a3e;
}
QTreeWidget::item:selected {
    background-color: #0f3460;
    color: #ffffff;
}
QTreeWidget::item:hover {
    background-color: #1a2a5e;
}
QTreeWidget::branch {
    background-color: #16213e;
}
QTreeWidget QHeaderView::section {
    background-color: #0f3460;
    color: #e0c040;
    font-weight: bold;
    padding: 4px;
    border: none;
}
QScrollArea {
    border: none;
    background-color: #1a1a2e;
}
QLabel {
    color: #d0d0d0;
}
QLineEdit {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #e0c040;
}
QCheckBox {
    color: #d0d0d0;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QPushButton {
    background-color: #0f3460;
    color: #e0c040;
    border: 1px solid #1a4a80;
    border-radius: 4px;
    padding: 4px 12px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #1a4a80;
}
QPushButton:pressed {
    background-color: #0a2a50;
}
QPushButton:disabled {
    background-color: #1a1a2e;
    color: #555555;
    border: 1px solid #333333;
}
QComboBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
}
QComboBox QAbstractItemView {
    background-color: #16213e;
    color: #e8e8e8;
    selection-background-color: #0f3460;
}
QDoubleSpinBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
}
QDoubleSpinBox:focus {
    border: 1px solid #e0c040;
}
QFrame#preview_frame {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px;
}
QFrame#texture_frame {
    background-color: #121228;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 6px;
}
QFrame#props_frame {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 8px;
}
"""


# ---------------------------------------------------------------------------
# Element tag icons and colors for tree display
# ---------------------------------------------------------------------------

TAG_ICONS = {
    'MENU':       '\u2630',   # trigram
    'item':       '\u25A0',   # black square
    'animtext':   '\u266B',   # music note
    'anim':       '\u25B6',   # play
    'precache':   '\u21E9',   # down arrow
    'onfocus':    '\u25C9',   # fisheye
    'items':      '\u229E',   # squared plus
    'listitem':   '\u2022',   # bullet
    'childitem':  '\u2022',   # bullet
    'mark':       '\u25CF',   # circle
}

TAG_COLORS = {
    'MENU':       '#e0c040',  # gold
    'item':       '#4090e0',  # blue
    'animtext':   '#40c080',  # green
    'anim':       '#40c0c0',  # cyan
    'precache':   '#909090',  # gray
    'onfocus':    '#e09040',  # orange
    'items':      '#a060c0',  # purple
    'listitem':   '#80b0d0',  # light blue
    'childitem':  '#80b0d0',  # light blue
    'mark':       '#c0c040',  # yellow
}


# ---------------------------------------------------------------------------
# Known attribute enums for auto-complete dropdowns
# ---------------------------------------------------------------------------

ITEM_TYPES = [
    '', 'MENU_ITEM_MODEL', 'MENU_ITEM_TEXT', 'MENU_ITEM_TEXTBOX',
    'MENU_ITEM_BAR', 'MENU_ITEM_BINARY', 'MENU_ITEM_LISTBOX',
    'MENU_ITEM_LIST_CYCLE', 'MENU_ITEM_LIST_ITEMS',
    'MENU_ITEM_LISTCHARS', 'MENU_ITEM_LISTCODEX',
    'MENU_ITEM_CHAR_SUMMARY', 'MENU_ITEM_SKILLS',
]

MENU_TYPES = [
    'MAIN_MENU', 'PAUSE_MENU', 'OPTIONS_MENU', 'CODEX_MENU',
    'AUTOMAP_MENU', 'PDA_MENU', 'TEAM_MENU', 'SHOP_MENU',
    'DANGER_ROOM_MENU', 'REVIEW_MENU', 'TRIVIA_MENU',
    'LOADING_MENU', 'MOVIE_MENU', 'CREDITS_MENU',
    'CAMPAIGN_LOBBY_MENU', 'SVS_LIST_MENU',
]

PRECACHE_TYPES = [
    'model', 'texture', 'script', 'sound', 'fx', 'xml', 'xml_resident',
]

ONFOCUS_TYPES = [
    'focus', 'nofocus', 'list_focus', 'list_up_arrow', 'list_down_arrow',
]

STYLES = [
    '', 'STYLE_MENU_WHITE_MED', 'STYLE_MENU_YELLOW',
    'STYLE_BODY_MED', 'STYLE_BODY_SMALL', 'STYLE_BODY_LARGE',
    'STYLE_HEADING_MED', 'STYLE_HEADING_LARGE',
    'STYLE_MENU_SMALL_WHITE', 'STYLE_MENU_LARGE_WHITE',
]

TEXT_ALIGNS = [
    '', 'TEXT_ALIGN_LEFT', 'TEXT_ALIGN_RIGHT', 'TEXT_ALIGN_CENTER',
    'TEXT_ALIGN_TOP', 'TEXT_ALIGN_BOTTOM',
    'TEXT_ALIGN_CENTER_X', 'TEXT_ALIGN_CENTER_Y',
]

BOOL_ATTRS = {
    'fadein', 'fadeout', 'fullscreen', 'animonopen', 'animonclose',
    'lighting', 'updownonly', 'resetcontroller', 'gamepause',
    'animate', 'neverfocus', 'startactive', 'enabled',
    'conversation_end', 'loop', 'no_stack', 'debug',
}

# Map attr_name -> list of enum choices (for context-aware dropdowns)
ENUM_MAP = {
    'style': STYLES,
    'textalignx': TEXT_ALIGNS,
    'textaligny': TEXT_ALIGNS,
}

MAX_UNDO = 50


# ---------------------------------------------------------------------------
# MenuCanvasItem -- Interactive item on the visual canvas
# ---------------------------------------------------------------------------

class MenuCanvasItem:
    """Represents a single interactive ENGB item on the canvas.

    Each canvas item tracks an ENGB <item> element, its world-space
    position (X horizontal, Z vertical), bounding size computed from
    model geometry, and placed scene instances for rendering.
    """
    __slots__ = (
        'name', 'element', 'world_x', 'world_z',
        'half_w', 'half_h', 'instances', 'thumbnail',
        'text', 'item_type', 'selected', 'hovered',
    )

    def __init__(self):
        self.name = ''
        self.element = None
        self.world_x = 0.0
        self.world_z = 0.0
        self.half_w = 20.0
        self.half_h = 10.0
        self.instances = []
        self.thumbnail = None
        self.text = ''
        self.item_type = ''
        self.selected = False
        self.hovered = False


# ---------------------------------------------------------------------------
# MenuCanvasWidget -- 16:9 interactive viewport
# ---------------------------------------------------------------------------

class MenuCanvasWidget(QWidget):
    """Interactive 16:9 canvas that renders menu items as textured geometry.

    Supports:
    - Orthographic XZ projection (X horizontal, Z vertical, Y depth)
    - Triangle-based rendering with texture sampling and vertex colors
    - Click-to-select, drag-to-move canvas items
    - Pan (middle/right drag) and zoom (scroll wheel)
    - Grid overlay and item name labels
    - Selection synchronizes with tree widget via signal
    """

    ASPECT_RATIO = 16.0 / 9.0

    # Signal emitted when a canvas item is selected (passes item name)
    item_selected = Signal(str)
    # Signal emitted when an item is moved (passes item name, new x, new z)
    item_moved = Signal(str, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas_items = []
        self._bg_instances = []
        self._qimages = {}
        self._world_bounds = None
        self._bg_color = QColor(0x0a, 0x0a, 0x1a)
        self._info_text = "No IGB loaded"
        self._show_wireframe = False
        self._show_grid = True
        self._show_labels = True
        self._show_nav_arrows = False
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._last_mouse = None
        self._panning = False
        self._dragging_item = None
        self._drag_start_wx = 0.0
        self._drag_start_wz = 0.0
        self._selected_item = None
        self._nav_links = {}
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- Public API --

    def set_canvas_items(self, items, bg_instances=None):
        """Set the interactive canvas items and optional background geometry."""
        self._canvas_items = items or []
        self._bg_instances = bg_instances or []
        self._selected_item = None
        self._dragging_item = None
        self._compute_bounds()
        self._info_text = (f"{len(self._canvas_items)} items, "
                           f"{len(self._bg_instances)} bg meshes")
        self.update()

    def set_nav_links(self, nav_links):
        self._nav_links = nav_links or {}
        self.update()

    def select_item_by_name(self, name):
        for item in self._canvas_items:
            item.selected = (item.name == name)
            if item.selected:
                self._selected_item = item
        self.update()

    def clear_selection(self):
        for item in self._canvas_items:
            item.selected = False
        self._selected_item = None
        self.update()

    def set_wireframe(self, enabled):
        self._show_wireframe = enabled
        self.update()

    def set_grid(self, enabled):
        self._show_grid = enabled
        self.update()

    def set_labels(self, enabled):
        self._show_labels = enabled
        self.update()

    def set_nav_arrows(self, enabled):
        self._show_nav_arrows = enabled
        self.update()

    def clear(self):
        self._canvas_items = []
        self._bg_instances = []
        self._qimages.clear()
        self._world_bounds = None
        self._info_text = "No IGB loaded"
        self._selected_item = None
        self._dragging_item = None
        self._nav_links = {}
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    # -- Bounds computation --

    def _compute_bounds(self):
        from .menu_igb_loader import _transform_point

        min_x = float('inf')
        min_z = float('inf')
        max_x = float('-inf')
        max_z = float('-inf')
        found = False

        for ci in self._canvas_items:
            found = True
            min_x = min(min_x, ci.world_x - ci.half_w)
            max_x = max(max_x, ci.world_x + ci.half_w)
            min_z = min(min_z, ci.world_z - ci.half_h)
            max_z = max(max_z, ci.world_z + ci.half_h)

        for inst in self._bg_instances:
            mat = inst.transform
            for pos in inst.positions:
                wp = _transform_point(pos, mat)
                found = True
                min_x = min(min_x, wp[0])
                max_x = max(max_x, wp[0])
                min_z = min(min_z, wp[2])
                max_z = max(max_z, wp[2])

        if not found:
            self._world_bounds = None
            return

        dx = max_x - min_x
        dz = max_z - min_z
        pad = max(dx, dz) * 0.08 + 1.0
        self._world_bounds = (min_x - pad, min_z - pad,
                              max_x + pad, max_z + pad)

    # -- Coordinate mapping --

    def _canvas_rect(self):
        w = self.width()
        h = self.height()
        canvas_w = w
        canvas_h = int(canvas_w / self.ASPECT_RATIO)
        if canvas_h > h:
            canvas_h = h
            canvas_w = int(canvas_h * self.ASPECT_RATIO)
        x0 = (w - canvas_w) // 2
        y0 = (h - canvas_h) // 2
        return x0, y0, canvas_w, canvas_h

    def _get_scale(self):
        if self._world_bounds is None:
            return 1.0
        bx0, bz0, bx1, bz1 = self._world_bounds
        bw = max(bx1 - bx0, 0.001)
        bh = max(bz1 - bz0, 0.001)
        _, _, cw, ch = self._canvas_rect()
        canvas_aspect = cw / ch if ch > 0 else 1.0
        if (bw / bh) > canvas_aspect:
            scale = cw / bw
        else:
            scale = ch / bh
        return scale * self._zoom

    def _world_to_screen(self, wx, wz):
        if self._world_bounds is None:
            return 0.0, 0.0
        bx0, bz0, bx1, bz1 = self._world_bounds
        cx, cy, cw, ch = self._canvas_rect()
        scale = self._get_scale()
        wcx = (bx0 + bx1) * 0.5
        wcz = (bz0 + bz1) * 0.5
        sx = cx + cw * 0.5 + (wx - wcx) * scale + self._pan_x
        sy = cy + ch * 0.5 - (wz - wcz) * scale + self._pan_y
        return sx, sy

    def _screen_to_world(self, sx, sy):
        if self._world_bounds is None:
            return 0.0, 0.0
        bx0, bz0, bx1, bz1 = self._world_bounds
        cx, cy, cw, ch = self._canvas_rect()
        scale = self._get_scale()
        if scale == 0:
            return 0.0, 0.0
        wcx = (bx0 + bx1) * 0.5
        wcz = (bz0 + bz1) * 0.5
        wx = wcx + (sx - cx - cw * 0.5 - self._pan_x) / scale
        wz = wcz - (sy - cy - ch * 0.5 - self._pan_y) / scale
        return wx, wz

    # -- QImage loading --

    def _get_qimage(self, png_path):
        if png_path is None:
            return None
        if png_path in self._qimages:
            return self._qimages[png_path]
        if not os.path.exists(png_path):
            return None
        img = QImage(png_path)
        if img.isNull():
            return None
        self._qimages[png_path] = img
        return img

    # -- Hit testing --

    def _item_at(self, sx, sy):
        wx, wz = self._screen_to_world(sx, sy)
        for item in reversed(self._canvas_items):
            if (abs(wx - item.world_x) <= item.half_w and
                    abs(wz - item.world_z) <= item.half_h):
                return item
        return None

    # -- Paint event --

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), self._bg_color)

        cx, cy, cw, ch = self._canvas_rect()
        painter.setPen(QPen(QColor(0x0f, 0x34, 0x60), 2))
        painter.drawRect(cx, cy, cw, ch)
        painter.setClipRect(cx, cy, cw, ch)

        if not self._canvas_items and not self._bg_instances:
            painter.setPen(QColor(0x60, 0x60, 0x80))
            painter.setFont(QFont("", 14))
            painter.drawText(cx, cy, cw, ch, Qt.AlignCenter, self._info_text)
            painter.end()
            return

        # 1. Grid
        if self._show_grid:
            self._render_grid(painter)

        # 2. Background geometry
        if self._bg_instances:
            self._render_instances(painter, self._bg_instances)

        # 3. Canvas item geometry (brighter)
        for ci in self._canvas_items:
            if ci.instances:
                self._render_instances(painter, ci.instances, brightness=1.8)

        # 4. Wireframe
        if self._show_wireframe:
            all_inst = list(self._bg_instances)
            for ci in self._canvas_items:
                all_inst.extend(ci.instances)
            self._render_wireframe(painter, all_inst)

        # 5. Nav arrows
        if self._show_nav_arrows and self._nav_links:
            self._render_nav_arrows(painter)

        # 6. Labels
        if self._show_labels:
            self._render_labels(painter)

        # 7. Highlights
        self._render_item_highlights(painter)

        # Info bar
        painter.setClipping(False)
        painter.setPen(QColor(0x80, 0x80, 0x80))
        painter.setFont(QFont("", 9))
        info = self._info_text
        if self._selected_item:
            si = self._selected_item
            info += f"  |  {si.name} ({si.world_x:.1f}, {si.world_z:.1f})"
        painter.drawText(cx + 4, cy + ch - 6, info)

        painter.end()

    def _render_grid(self, painter):
        if self._world_bounds is None:
            return
        bx0, bz0, bx1, bz1 = self._world_bounds
        scale = self._get_scale()
        if scale <= 0:
            return
        target_px = 60.0
        raw_spacing = target_px / scale
        nice = [5, 10, 20, 50, 100, 200, 500, 1000]
        spacing = nice[0]
        for n in nice:
            if n >= raw_spacing * 0.5:
                spacing = n
                break
        painter.setPen(QPen(QColor(0x18, 0x18, 0x30), 1))
        x = math.floor(bx0 / spacing) * spacing
        while x <= bx1:
            s0 = self._world_to_screen(x, bz0)
            s1 = self._world_to_screen(x, bz1)
            painter.drawLine(QPointF(s0[0], s0[1]), QPointF(s1[0], s1[1]))
            x += spacing
        z = math.floor(bz0 / spacing) * spacing
        while z <= bz1:
            s0 = self._world_to_screen(bx0, z)
            s1 = self._world_to_screen(bx1, z)
            painter.drawLine(QPointF(s0[0], s0[1]), QPointF(s1[0], s1[1]))
            z += spacing

    def _render_instances(self, painter, instances, brightness=1.0):
        """Render MenuSceneInstance objects as textured triangles (XZ projection)."""
        from .menu_igb_loader import _transform_point

        tri_list = []
        for inst in instances:
            mat = inst.transform
            positions = inst.positions
            uvs = inst.uvs
            colors = inst.colors
            indices = inst.indices
            tex_img = self._get_qimage(inst.texture_png)
            diffuse = inst.diffuse

            if not positions or not indices:
                continue

            world_pts = [_transform_point(p, mat) for p in positions]

            num_tris = len(indices) // 3
            for t in range(num_tris):
                i0 = indices[t * 3]
                i1 = indices[t * 3 + 1]
                i2 = indices[t * 3 + 2]
                if i0 >= len(world_pts) or i1 >= len(world_pts) or i2 >= len(world_pts):
                    continue
                # Skip degenerate triangles (two or more identical indices)
                if i0 == i1 or i1 == i2 or i0 == i2:
                    continue

                p0, p1, p2 = world_pts[i0], world_pts[i1], world_pts[i2]
                avg_y = (p0[1] + p1[1] + p2[1]) / 3.0

                sx0, sy0 = self._world_to_screen(p0[0], p0[2])
                sx1, sy1 = self._world_to_screen(p1[0], p1[2])
                sx2, sy2 = self._world_to_screen(p2[0], p2[2])

                br = brightness
                if colors and i0 < len(colors):
                    c0 = colors[min(i0, len(colors)-1)]
                    c1 = colors[min(i1, len(colors)-1)]
                    c2 = colors[min(i2, len(colors)-1)]
                    r = int(((c0[0]+c1[0]+c2[0])/3.0) * diffuse[0] * 255 * br)
                    g = int(((c0[1]+c1[1]+c2[1])/3.0) * diffuse[1] * 255 * br)
                    b = int(((c0[2]+c1[2]+c2[2])/3.0) * diffuse[2] * 255 * br)
                    a = int(((c0[3]+c1[3]+c2[3])/3.0) * diffuse[3] * 255)
                else:
                    r = int(diffuse[0] * 255 * br)
                    g = int(diffuse[1] * 255 * br)
                    b = int(diffuse[2] * 255 * br)
                    a = int(diffuse[3] * 255)
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                a = max(0, min(255, a))

                uv0 = uvs[i0] if uvs and i0 < len(uvs) else None
                uv1 = uvs[i1] if uvs and i1 < len(uvs) else None
                uv2 = uvs[i2] if uvs and i2 < len(uvs) else None

                tri_list.append((avg_y, sx0, sy0, sx1, sy1, sx2, sy2,
                                 r, g, b, a, tex_img, uv0, uv1, uv2,
                                 inst.tex_width, inst.tex_height, br))

        tri_list.sort(key=lambda t: t[0])

        for tri_data in tri_list:
            (_d, sx0, sy0, sx1, sy1, sx2, sy2,
             r, g, b, a, tex_img, uv0, uv1, uv2, tw, th, br) = tri_data

            polygon = QPolygonF([
                QPointF(sx0, sy0), QPointF(sx1, sy1), QPointF(sx2, sy2)])

            if tex_img is not None and uv0 is not None:
                cu = (uv0[0] + (uv1[0] if uv1 else uv0[0]) +
                      (uv2[0] if uv2 else uv0[0])) / 3.0
                cv = (uv0[1] + (uv1[1] if uv1 else uv0[1]) +
                      (uv2[1] if uv2 else uv0[1])) / 3.0
                cu = cu % 1.0 if cu >= 0 else 1.0 + (cu % 1.0)
                cv = cv % 1.0 if cv >= 0 else 1.0 + (cv % 1.0)
                tx = max(0, min(tw-1, int(cu*(tw-1)))) if tw > 0 else 0
                ty = max(0, min(th-1, int(cv*(th-1)))) if th > 0 else 0
                texel = tex_img.pixelColor(tx, ty)
                fr = min(255, int(texel.red() * r / 255))
                fg = min(255, int(texel.green() * g / 255))
                fb = min(255, int(texel.blue() * b / 255))
                fa = min(255, (texel.alpha() * a) // 255)
                fill_color = QColor(fr, fg, fb, max(fa, 4))
            else:
                fill_color = QColor(r, g, b, max(a, 4))

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawPolygon(polygon)

    def _render_wireframe(self, painter, instances):
        from .menu_igb_loader import _transform_point
        painter.setPen(QPen(QColor(0x40, 0x60, 0x90, 60), 1))
        painter.setBrush(Qt.NoBrush)
        for inst in instances:
            mat = inst.transform
            positions = inst.positions
            indices = inst.indices
            if not positions or not indices:
                continue
            num_tris = len(indices) // 3
            for t in range(num_tris):
                i0, i1, i2 = indices[t*3], indices[t*3+1], indices[t*3+2]
                if i0 >= len(positions) or i1 >= len(positions) or i2 >= len(positions):
                    continue
                p0 = _transform_point(positions[i0], mat)
                p1 = _transform_point(positions[i1], mat)
                p2 = _transform_point(positions[i2], mat)
                s0 = self._world_to_screen(p0[0], p0[2])
                s1 = self._world_to_screen(p1[0], p1[2])
                s2 = self._world_to_screen(p2[0], p2[2])
                painter.drawPolygon(QPolygonF([
                    QPointF(s0[0], s0[1]),
                    QPointF(s1[0], s1[1]),
                    QPointF(s2[0], s2[1])]))

    def _render_labels(self, painter):
        painter.setFont(QFont("", 8))
        for ci in self._canvas_items:
            sx, sy = self._world_to_screen(ci.world_x, ci.world_z - ci.half_h)
            label = ci.name
            if ci.text:
                label += f': "{ci.text}"'
            if ci.selected:
                painter.setPen(QColor(0xe0, 0xc0, 0x40))
            elif ci.hovered:
                painter.setPen(QColor(0x80, 0xb0, 0xe0))
            else:
                painter.setPen(QColor(0x60, 0x70, 0x90))
            painter.drawText(int(sx - 80), int(sy + 12), 160, 20,
                             Qt.AlignHCenter | Qt.AlignTop, label)

    def _render_item_highlights(self, painter):
        for ci in self._canvas_items:
            if not ci.selected and not ci.hovered:
                continue
            sx0, sy0 = self._world_to_screen(
                ci.world_x - ci.half_w, ci.world_z + ci.half_h)
            sx1, sy1 = self._world_to_screen(
                ci.world_x + ci.half_w, ci.world_z - ci.half_h)
            rect = QRectF(min(sx0, sx1), min(sy0, sy1),
                          abs(sx1 - sx0), abs(sy1 - sy0))
            if ci.selected:
                painter.setPen(QPen(QColor(0xe0, 0xc0, 0x40, 200), 2))
            else:
                painter.setPen(QPen(QColor(0x60, 0x80, 0xb0, 140), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

    def _render_nav_arrows(self, painter):
        item_map = {ci.name: ci for ci in self._canvas_items}
        painter.setPen(QPen(QColor(0x40, 0x90, 0x40, 100), 1))
        for name, links in self._nav_links.items():
            if name not in item_map:
                continue
            src = item_map[name]
            for direction, target in links.items():
                if not target or target not in item_map:
                    continue
                dst = item_map[target]
                s0 = self._world_to_screen(src.world_x, src.world_z)
                s1 = self._world_to_screen(dst.world_x, dst.world_z)
                painter.drawLine(QPointF(s0[0], s0[1]), QPointF(s1[0], s1[1]))

    # -- Mouse interaction --

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom *= 1.15
        elif delta < 0:
            self._zoom /= 1.15
        self._zoom = max(0.1, min(20.0, self._zoom))
        self.update()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = True
            self._last_mouse = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton:
            hit = self._item_at(event.pos().x(), event.pos().y())
            if hit:
                for ci in self._canvas_items:
                    ci.selected = (ci is hit)
                self._selected_item = hit
                self._dragging_item = hit
                self._drag_start_wx = hit.world_x
                self._drag_start_wz = hit.world_z
                self._last_mouse = event.pos()
                self.setCursor(Qt.SizeAllCursor)
                self.item_selected.emit(hit.name)
            else:
                for ci in self._canvas_items:
                    ci.selected = False
                self._selected_item = None
                self._dragging_item = None
                self.item_selected.emit('')
            self.update()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._panning and self._last_mouse is not None:
            self._pan_x += pos.x() - self._last_mouse.x()
            self._pan_y += pos.y() - self._last_mouse.y()
            self._last_mouse = pos
            self.update()
            return

        if self._dragging_item and self._last_mouse is not None:
            scale = self._get_scale()
            if scale > 0:
                dx = pos.x() - self._last_mouse.x()
                dy = pos.y() - self._last_mouse.y()
                self._dragging_item.world_x += dx / scale
                self._dragging_item.world_z -= dy / scale
                self._last_mouse = pos
                self.update()
            return

        # Hover
        hit = self._item_at(pos.x(), pos.y())
        changed = False
        for ci in self._canvas_items:
            new_hover = (ci is hit)
            if ci.hovered != new_hover:
                ci.hovered = new_hover
                changed = True
        if changed:
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = False
            self._last_mouse = None
            self.setCursor(Qt.ArrowCursor)
            return

        if event.button() == Qt.LeftButton and self._dragging_item:
            ci = self._dragging_item
            if (abs(ci.world_x - self._drag_start_wx) > 0.01 or
                    abs(ci.world_z - self._drag_start_wz) > 0.01):
                self.item_moved.emit(ci.name, ci.world_x, ci.world_z)
            self._dragging_item = None
            self._last_mouse = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self._item_at(event.pos().x(), event.pos().y()):
                self._zoom = 1.0
                self._pan_x = 0.0
                self._pan_y = 0.0
                self.update()

    def sizeHint(self):
        return QSize(640, 360)

    def minimumSizeHint(self):
        return QSize(320, 180)


# ---------------------------------------------------------------------------
# MenuEditorWindow
# ---------------------------------------------------------------------------

class MenuEditorWindow(QMainWindow):
    """PySide6 window for editing X-Men Legends II menu ENGB files."""

    def __init__(self, game_dir=None):
        super().__init__()
        self._game_dir = game_dir or ''
        self._root = None
        self._file_path = None
        self._dirty = False
        self._updating = False
        self._undo_stack = []
        self._redo_stack = []
        self._textures = {}
        self._pixmap_cache = {}
        self._layout_transforms = {}

        self.setWindowTitle("Menu Editor")
        self.setMinimumSize(1200, 700)
        self.resize(1500, 850)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)

        self._build_menus()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menus(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        act_open = QAction("&Open ENGB...", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self._on_open)
        file_menu.addAction(act_open)

        self._act_save = QAction("&Save", self)
        self._act_save.setShortcut(QKeySequence("Ctrl+S"))
        self._act_save.triggered.connect(self._on_save)
        self._act_save.setEnabled(False)
        file_menu.addAction(self._act_save)

        act_save_as = QAction("Save &As...", self)
        act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_save_as.triggered.connect(self._on_save_as)
        file_menu.addAction(act_save_as)
        file_menu.addSeparator()
        act_close = QAction("&Close", self)
        act_close.triggered.connect(self.close)
        file_menu.addAction(act_close)

        edit_menu = mb.addMenu("&Edit")
        self._act_undo = QAction("&Undo", self)
        self._act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self._act_undo.triggered.connect(self._on_undo)
        self._act_undo.setEnabled(False)
        edit_menu.addAction(self._act_undo)

        self._act_redo = QAction("&Redo", self)
        self._act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self._act_redo.triggered.connect(self._on_redo)
        self._act_redo.setEnabled(False)
        edit_menu.addAction(self._act_redo)
        edit_menu.addSeparator()

        for label, shortcut, handler in [
            ("Add &Child Element", "Insert", self._on_add_child),
            ("Add &Sibling Element", "Shift+Insert", self._on_add_sibling),
            ("D&uplicate", "Ctrl+D", self._on_duplicate),
            ("&Delete", "Delete", self._on_delete),
        ]:
            act = QAction(label, self)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(handler)
            edit_menu.addAction(act)

        edit_menu.addSeparator()
        act_up = QAction("Move &Up", self)
        act_up.setShortcut(QKeySequence("Ctrl+Up"))
        act_up.triggered.connect(self._on_move_up)
        edit_menu.addAction(act_up)
        act_down = QAction("Move D&own", self)
        act_down.setShortcut(QKeySequence("Ctrl+Down"))
        act_down.triggered.connect(self._on_move_down)
        edit_menu.addAction(act_down)

        view_menu = mb.addMenu("&View")
        act_expand = QAction("&Expand All", self)
        act_expand.triggered.connect(lambda: self._tree.expandAll())
        view_menu.addAction(act_expand)
        act_collapse = QAction("&Collapse All", self)
        act_collapse.triggered.connect(lambda: self._tree.collapseAll())
        view_menu.addAction(act_collapse)
        view_menu.addSeparator()

        self._act_wireframe = QAction("&Wireframe Overlay", self)
        self._act_wireframe.setCheckable(True)
        self._act_wireframe.triggered.connect(
            lambda checked: self._canvas.set_wireframe(checked))
        view_menu.addAction(self._act_wireframe)

        self._act_grid = QAction("Show &Grid", self)
        self._act_grid.setCheckable(True)
        self._act_grid.setChecked(True)
        self._act_grid.triggered.connect(
            lambda checked: self._canvas.set_grid(checked))
        view_menu.addAction(self._act_grid)

        self._act_labels = QAction("Show &Labels", self)
        self._act_labels.setCheckable(True)
        self._act_labels.setChecked(True)
        self._act_labels.triggered.connect(
            lambda checked: self._canvas.set_labels(checked))
        view_menu.addAction(self._act_labels)

        self._act_nav = QAction("Show &Navigation Arrows", self)
        self._act_nav.setCheckable(True)
        self._act_nav.triggered.connect(
            lambda checked: self._canvas.set_nav_arrows(checked))
        view_menu.addAction(self._act_nav)

        view_menu.addSeparator()
        act_reset_view = QAction("&Reset View", self)
        act_reset_view.setShortcut(QKeySequence("Home"))
        act_reset_view.triggered.connect(self._reset_canvas_view)
        view_menu.addAction(act_reset_view)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Element", "Info"])
        self._tree.setColumnWidth(0, 220)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        splitter.addWidget(self._tree)

        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._canvas = MenuCanvasWidget()
        self._canvas.item_selected.connect(self._on_canvas_item_selected)
        self._canvas.item_moved.connect(self._on_canvas_item_moved)
        center_layout.addWidget(self._canvas, 3)

        tex_container = QWidget()
        tex_container_layout = QVBoxLayout(tex_container)
        tex_container_layout.setContentsMargins(0, 0, 0, 0)
        tex_container_layout.setSpacing(2)
        self._tex_label = QLabel("IGB Textures")
        self._tex_label.setFont(QFont("", 10, QFont.Bold))
        self._tex_label.setStyleSheet("color: #e0c040;")
        tex_container_layout.addWidget(self._tex_label)

        tex_scroll = QScrollArea()
        tex_scroll.setWidgetResizable(True)
        tex_scroll.setMaximumHeight(140)
        tex_frame = QFrame()
        tex_frame.setObjectName("texture_frame")
        self._tex_layout = QGridLayout(tex_frame)
        self._tex_layout.setSpacing(4)
        tex_scroll.setWidget(tex_frame)
        tex_container_layout.addWidget(tex_scroll)
        center_layout.addWidget(tex_container, 0)

        self._nav_label = QLabel("")
        self._nav_label.setStyleSheet(
            "color: #808080; font-size: 10px; padding: 2px;")
        self._nav_label.setWordWrap(True)
        center_layout.addWidget(self._nav_label)
        splitter.addWidget(center_widget)

        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        self._props_widget = QWidget()
        self._props_layout = QVBoxLayout(self._props_widget)
        self._props_layout.setAlignment(Qt.AlignTop)
        props_scroll.setWidget(self._props_widget)
        splitter.addWidget(props_scroll)

        splitter.setSizes([300, 560, 320])
        main_layout.addWidget(splitter, 1)

        self._status = QLabel("No file loaded")
        self._status.setStyleSheet("color: #808080; padding: 4px;")
        main_layout.addWidget(self._status)

    def _reset_canvas_view(self):
        self._canvas._zoom = 1.0
        self._canvas._pan_x = 0.0
        self._canvas._pan_y = 0.0
        self._canvas.update()

    # ------------------------------------------------------------------
    # Canvas <-> Tree sync
    # ------------------------------------------------------------------

    def _on_canvas_item_selected(self, name):
        if self._updating or not name:
            return
        self._updating = True
        for elem in (self._root.iter('item') if self._root is not None else []):
            if elem.attrib.get('name', '') == name:
                self._select_element(elem)
                self._update_properties(elem)
                self._update_texture_preview(elem)
                break
        self._updating = False

    def _on_canvas_item_moved(self, name, new_x, new_z):
        if self._root is None:
            return
        for elem in self._root.iter('item'):
            if elem.attrib.get('name', '') == name:
                self._push_undo()
                elem.set('_editor_x', f'{new_x:.2f}')
                elem.set('_editor_z', f'{new_z:.2f}')
                selected = self._get_selected_element()
                if selected is elem:
                    self._update_properties(elem)
                break

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _on_open(self):
        if self._dirty and not self._confirm_discard():
            return
        start_dir = ''
        if self._game_dir:
            menus_dir = os.path.join(self._game_dir, 'ui', 'menus')
            if os.path.isdir(menus_dir):
                start_dir = menus_dir
        if not start_dir and self._file_path:
            start_dir = str(Path(self._file_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Menu File", start_dir,
            "ENGB/XMLB Files (*.engb *.xmlb);;All Files (*.*)")
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path):
        try:
            from .xmlb import read_xmlb
            root = read_xmlb(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{e}")
            return

        self._root = root
        self._file_path = path
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._act_undo.setEnabled(False)
        self._act_redo.setEnabled(False)
        self._act_save.setEnabled(True)
        self._update_title()

        self._load_textures()
        self._load_scene()
        self._populate_tree()
        self._update_nav_summary()
        self._update_canvas_nav_links()
        self._status.setText(f"Loaded: {path}")

    def _load_textures(self):
        self._textures.clear()
        self._pixmap_cache.clear()
        if self._root is None or not self._game_dir:
            return
        try:
            from .menu_igb_loader import load_menu_textures, get_menu_cache_dir
            cache_dir = get_menu_cache_dir()
            self._textures = load_menu_textures(
                self._root, self._game_dir, cache_dir)
        except Exception:
            pass

    def _load_scene(self):
        """Build interactive canvas items from ENGB + layout IGB + model IGBs."""
        self._canvas.clear()
        self._layout_transforms = {}

        if self._root is None or not self._game_dir:
            return

        try:
            from .menu_igb_loader import (
                extract_menu_scene, extract_layout_transforms,
                resolve_igb_path, get_menu_cache_dir, _multiply_matrices,
                _transform_point, MenuSceneInstance,
            )
            cache_dir = get_menu_cache_dir()
            bg_instances = []

            # 1. Extract layout transforms and background geometry
            igb_ref = self._root.attrib.get('igb', '')
            if igb_ref:
                igb_path = resolve_igb_path(igb_ref, self._game_dir)
                if igb_path is not None:
                    self._layout_transforms = extract_layout_transforms(
                        igb_path)
                    bg = extract_menu_scene(igb_path, cache_dir)
                    if bg:
                        bg_instances.extend(bg)

            # 2. Detect template transforms (duplicated positions)
            def _pos_key(mat):
                return (round(mat[12], 1), round(mat[13], 1),
                        round(mat[14], 1))

            pos_counts = {}
            for name, mat in self._layout_transforms.items():
                pk = _pos_key(mat)
                pos_counts[pk] = pos_counts.get(pk, 0) + 1

            def _get_placement_transform(item_name):
                if item_name in self._layout_transforms:
                    mat = self._layout_transforms[item_name]
                    pk = _pos_key(mat)
                    if pos_counts.get(pk, 0) <= 2:
                        return mat
                label_name = f"label_{item_name}"
                if label_name in self._layout_transforms:
                    return self._layout_transforms[label_name]
                if item_name in self._layout_transforms:
                    return self._layout_transforms[item_name]
                return None

            # 3. Extract geometry from each unique model IGB
            seen_models = {}
            for item in self._root.iter('item'):
                model_ref = item.attrib.get('model', '')
                if model_ref and model_ref not in seen_models:
                    igb_path = resolve_igb_path(model_ref, self._game_dir)
                    if igb_path is not None:
                        seen_models[model_ref] = (
                            extract_menu_scene(igb_path, cache_dir) or [])
                    else:
                        seen_models[model_ref] = []

            # 4. Build canvas items
            canvas_items = []
            grid_z = 0.0

            for item_elem in self._root.iter('item'):
                item_name = item_elem.attrib.get('name', '')
                if not item_name:
                    continue

                ci = MenuCanvasItem()
                ci.name = item_name
                ci.element = item_elem
                ci.item_type = item_elem.attrib.get('type', '')
                ci.text = item_elem.attrib.get('text', '')

                # Position: _editor overrides > layout transform > fallback
                if ('_editor_x' in item_elem.attrib and
                        '_editor_z' in item_elem.attrib):
                    try:
                        ci.world_x = float(item_elem.attrib['_editor_x'])
                        ci.world_z = float(item_elem.attrib['_editor_z'])
                    except ValueError:
                        ci.world_x = 200.0
                        ci.world_z = grid_z
                        grid_z -= 30.0
                else:
                    placement = _get_placement_transform(item_name)
                    if placement is not None:
                        ci.world_x = placement[12]
                        ci.world_z = placement[14]
                        # Write initial position to ENGB for future edits
                        item_elem.set('_editor_x', f'{ci.world_x:.2f}')
                        item_elem.set('_editor_z', f'{ci.world_z:.2f}')
                    else:
                        ci.world_x = 200.0
                        ci.world_z = grid_z
                        grid_z -= 30.0

                # Load model geometry
                model_ref = item_elem.attrib.get('model', '')
                if model_ref and model_ref in seen_models:
                    template_instances = seen_models[model_ref]
                    placement_mat = (
                        1, 0, 0, 0,
                        0, 1, 0, 0,
                        0, 0, 1, 0,
                        ci.world_x, 0, ci.world_z, 1)
                    placed = []
                    for tmpl in template_instances:
                        inst = MenuSceneInstance()
                        inst.positions = tmpl.positions
                        inst.uvs = tmpl.uvs
                        inst.colors = tmpl.colors
                        inst.indices = tmpl.indices
                        inst.texture_png = tmpl.texture_png
                        inst.tex_width = tmpl.tex_width
                        inst.tex_height = tmpl.tex_height
                        inst.diffuse = tmpl.diffuse
                        if tmpl.transform is not None:
                            inst.transform = _multiply_matrices(
                                tmpl.transform, placement_mat)
                        else:
                            inst.transform = placement_mat
                        placed.append(inst)
                    ci.instances = placed

                    # Compute bounds from geometry
                    min_x = min_z = float('inf')
                    max_x = max_z = float('-inf')
                    for inst in placed:
                        for pos in inst.positions:
                            wp = _transform_point(pos, inst.transform)
                            min_x = min(min_x, wp[0])
                            max_x = max(max_x, wp[0])
                            min_z = min(min_z, wp[2])
                            max_z = max(max_z, wp[2])
                    if min_x < float('inf'):
                        ci.half_w = max((max_x - min_x) * 0.5, 5.0)
                        ci.half_h = max((max_z - min_z) * 0.5, 3.0)
                else:
                    ci.half_w = 40.0
                    ci.half_h = 10.0

                canvas_items.append(ci)

            self._canvas.set_canvas_items(canvas_items, bg_instances)

        except Exception:
            import traceback
            traceback.print_exc()

    def _update_canvas_nav_links(self):
        if self._root is None:
            self._canvas.set_nav_links({})
            return
        nav_links = {}
        for elem in self._root.iter('item'):
            name = elem.attrib.get('name', '')
            if not name:
                continue
            links = {}
            for d in ('up', 'down', 'left', 'right'):
                target = elem.attrib.get(d, '')
                if target and target.strip():
                    links[d] = target
            if links:
                nav_links[name] = links
        self._canvas.set_nav_links(nav_links)

    def _on_save(self):
        if self._root is None:
            return
        if not self._file_path:
            self._on_save_as()
            return
        self._save_to(self._file_path)

    def _on_save_as(self):
        if self._root is None:
            return
        start_dir = str(Path(self._file_path).parent) if self._file_path else ''
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Menu File", start_dir,
            "ENGB Files (*.engb);;XMLB Files (*.xmlb);;All Files (*.*)")
        if not path:
            return
        self._save_to(path)

    def _save_to(self, path):
        try:
            from .xmlb import write_xmlb
            write_xmlb(self._root, Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")
            return
        self._file_path = path
        self._dirty = False
        self._update_title()
        self._status.setText(f"Saved: {path}")

    def _confirm_discard(self):
        return QMessageBox.question(
            self, "Unsaved Changes",
            "You have unsaved changes. Discard them?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _update_title(self):
        if self._file_path:
            name = Path(self._file_path).name
            dirty = " *" if self._dirty else ""
            self.setWindowTitle(f"Menu Editor \u2014 {name}{dirty}")
        else:
            self.setWindowTitle("Menu Editor")

    def closeEvent(self, event):
        if self._dirty:
            result = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if result == QMessageBox.Save:
                self._on_save()
                event.accept()
            elif result == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _push_undo(self):
        if self._root is None:
            return
        self._undo_stack.append(copy.deepcopy(self._root))
        if len(self._undo_stack) > MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._act_undo.setEnabled(True)
        self._act_redo.setEnabled(False)
        self._dirty = True
        self._update_title()

    def _on_undo(self):
        if not self._undo_stack or self._root is None:
            return
        self._redo_stack.append(copy.deepcopy(self._root))
        self._root = self._undo_stack.pop()
        self._act_undo.setEnabled(len(self._undo_stack) > 0)
        self._act_redo.setEnabled(True)
        self._refresh_after_undo_redo()

    def _on_redo(self):
        if not self._redo_stack or self._root is None:
            return
        self._undo_stack.append(copy.deepcopy(self._root))
        self._root = self._redo_stack.pop()
        self._act_undo.setEnabled(True)
        self._act_redo.setEnabled(len(self._redo_stack) > 0)
        self._refresh_after_undo_redo()

    def _refresh_after_undo_redo(self):
        self._dirty = True
        self._update_title()
        self._populate_tree()
        self._update_nav_summary()
        self._load_scene()
        self._clear_properties()

    # ------------------------------------------------------------------
    # Element tree
    # ------------------------------------------------------------------

    def _populate_tree(self):
        self._updating = True
        self._tree.clear()
        if self._root is not None:
            root_item = self._build_tree_item(self._root)
            self._tree.addTopLevelItem(root_item)
            self._tree.expandToDepth(1)
        self._updating = False

    def _build_tree_item(self, elem):
        tag = elem.tag
        name = elem.attrib.get('name', '')
        item_type = elem.attrib.get('type', '')
        icon = TAG_ICONS.get(tag, '\u25AB')
        color = TAG_COLORS.get(tag, '#a0a0a0')
        info_parts = []
        if name:
            info_parts.append(name)
        if item_type:
            info_parts.append(item_type)
        tree_item = QTreeWidgetItem([f"{icon} {tag}", ' | '.join(info_parts)])
        tree_item.setData(0, Qt.UserRole, id(elem))
        tree_item.setForeground(0, QColor(color))
        tree_item._element = elem
        for child in elem:
            tree_item.addChild(self._build_tree_item(child))
        return tree_item

    def _get_selected_element(self):
        item = self._tree.currentItem()
        return getattr(item, '_element', None) if item else None

    def _find_parent_and_index(self, target_elem):
        if self._root is None or target_elem is self._root:
            return None, -1
        def _search(parent):
            for i, child in enumerate(parent):
                if child is target_elem:
                    return parent, i
                r = _search(child)
                if r[0] is not None:
                    return r
            return None, -1
        return _search(self._root)

    def _on_tree_selection_changed(self, current, previous):
        if self._updating or current is None:
            return
        elem = getattr(current, '_element', None)
        if elem is None:
            return
        self._update_properties(elem)
        self._update_texture_preview(elem)
        name = elem.attrib.get('name', '')
        if name:
            self._canvas.select_item_by_name(name)
        else:
            self._canvas.clear_selection()

    def _on_tree_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        elem = getattr(item, '_element', None)
        if elem is None:
            return
        menu = QMenu(self)
        for label, handler in [
            ("Add Child Element", self._on_add_child),
            ("Add Sibling Element", self._on_add_sibling),
            ("Duplicate", self._on_duplicate),
        ]:
            act = menu.addAction(label)
            act.triggered.connect(handler)
        menu.addSeparator()
        for label, handler in [
            ("Move Up", self._on_move_up),
            ("Move Down", self._on_move_down),
        ]:
            act = menu.addAction(label)
            act.triggered.connect(handler)
        menu.addSeparator()
        act_del = menu.addAction("Delete")
        act_del.triggered.connect(self._on_delete)
        if elem is self._root:
            act_del.setEnabled(False)
        menu.exec_(self._tree.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Edit operations
    # ------------------------------------------------------------------

    def _on_add_child(self):
        elem = self._get_selected_element()
        if elem is None:
            return
        self._push_undo()
        new = ET.SubElement(elem, 'item')
        new.set('name', 'new_item')
        self._populate_tree()
        self._select_element(new)

    def _on_add_sibling(self):
        elem = self._get_selected_element()
        if elem is None or elem is self._root:
            return
        parent, idx = self._find_parent_and_index(elem)
        if parent is None:
            return
        self._push_undo()
        new = ET.Element('item')
        new.set('name', 'new_item')
        parent.insert(idx + 1, new)
        self._populate_tree()
        self._select_element(new)

    def _on_duplicate(self):
        elem = self._get_selected_element()
        if elem is None or elem is self._root:
            return
        parent, idx = self._find_parent_and_index(elem)
        if parent is None:
            return
        self._push_undo()
        dup = copy.deepcopy(elem)
        name = dup.attrib.get('name', '')
        if name:
            dup.set('name', name + '_copy')
        parent.insert(idx + 1, dup)
        self._populate_tree()
        self._select_element(dup)

    def _on_delete(self):
        elem = self._get_selected_element()
        if elem is None or elem is self._root:
            return
        parent, idx = self._find_parent_and_index(elem)
        if parent is None:
            return
        self._push_undo()
        parent.remove(elem)
        self._populate_tree()
        self._clear_properties()

    def _on_move_up(self):
        elem = self._get_selected_element()
        if elem is None or elem is self._root:
            return
        parent, idx = self._find_parent_and_index(elem)
        if parent is None or idx <= 0:
            return
        self._push_undo()
        parent.remove(elem)
        parent.insert(idx - 1, elem)
        self._populate_tree()
        self._select_element(elem)

    def _on_move_down(self):
        elem = self._get_selected_element()
        if elem is None or elem is self._root:
            return
        parent, idx = self._find_parent_and_index(elem)
        if parent is None or idx >= len(parent) - 1:
            return
        self._push_undo()
        parent.remove(elem)
        parent.insert(idx + 1, elem)
        self._populate_tree()
        self._select_element(elem)

    def _select_element(self, target_elem):
        def _find(item):
            if getattr(item, '_element', None) is target_elem:
                return item
            for i in range(item.childCount()):
                r = _find(item.child(i))
                if r:
                    return r
            return None
        for i in range(self._tree.topLevelItemCount()):
            r = _find(self._tree.topLevelItem(i))
            if r:
                self._tree.setCurrentItem(r)
                return

    # ------------------------------------------------------------------
    # Properties panel
    # ------------------------------------------------------------------

    def _clear_properties(self):
        while self._props_layout.count():
            child = self._props_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()

    def _update_properties(self, elem):
        self._clear_properties()
        if elem is None:
            return

        tag_label = QLabel(f"<{elem.tag}>")
        tag_label.setFont(QFont("", 14, QFont.Bold))
        tag_label.setStyleSheet("color: #e0c040; padding: 4px;")
        self._props_layout.addWidget(tag_label)

        # Tag editor
        tag_frame = QFrame()
        tag_frame.setObjectName("props_frame")
        tag_form = QFormLayout(tag_frame)
        tag_form.setContentsMargins(8, 8, 8, 8)
        tag_edit = QLineEdit(elem.tag)
        tag_edit.editingFinished.connect(
            lambda e=elem, le=tag_edit: self._on_tag_changed(e, le.text()))
        tag_form.addRow("Tag:", tag_edit)
        self._props_layout.addWidget(tag_frame)

        # Position section (for items)
        if elem.tag == 'item' and elem.attrib.get('name', ''):
            pos_frame = QFrame()
            pos_frame.setObjectName("props_frame")
            pos_form = QFormLayout(pos_frame)
            pos_form.setContentsMargins(8, 8, 8, 8)
            hdr = QLabel("Position")
            hdr.setFont(QFont("", 11, QFont.Bold))
            hdr.setStyleSheet("color: #40c080;")
            pos_form.addRow(hdr)

            for axis, attr in [("X:", '_editor_x'), ("Z:", '_editor_z')]:
                spin = QDoubleSpinBox()
                spin.setRange(-10000, 10000)
                spin.setDecimals(2)
                spin.setSingleStep(1.0)
                val = 0.0
                if attr in elem.attrib:
                    try:
                        val = float(elem.attrib[attr])
                    except ValueError:
                        pass
                spin.setValue(val)
                if attr == '_editor_x':
                    spin.valueChanged.connect(
                        lambda v, e=elem: self._on_position_changed(e, v, None))
                else:
                    spin.valueChanged.connect(
                        lambda v, e=elem: self._on_position_changed(e, None, v))
                pos_form.addRow(axis, spin)
            self._props_layout.addWidget(pos_frame)

        # Attributes
        attrs_frame = QFrame()
        attrs_frame.setObjectName("props_frame")
        attrs_form = QFormLayout(attrs_frame)
        attrs_form.setContentsMargins(8, 8, 8, 8)

        for attr_name, attr_value in elem.attrib.items():
            if attr_name.startswith('_editor_'):
                continue
            attrs_form.addRow(self._create_attr_row(elem, attr_name, attr_value))

        add_btn = QPushButton("+ Add Attribute")
        add_btn.clicked.connect(lambda _, e=elem: self._on_add_attribute(e))
        attrs_form.addRow(add_btn)
        self._props_layout.addWidget(attrs_frame)
        self._props_layout.addStretch()

    def _on_position_changed(self, elem, new_x, new_z):
        name = elem.attrib.get('name', '')
        if not name:
            return
        changed = False
        if new_x is not None:
            s = f'{new_x:.2f}'
            if elem.attrib.get('_editor_x', '') != s:
                elem.set('_editor_x', s)
                changed = True
        if new_z is not None:
            s = f'{new_z:.2f}'
            if elem.attrib.get('_editor_z', '') != s:
                elem.set('_editor_z', s)
                changed = True
        if changed:
            if not self._dirty:
                self._push_undo()
            for ci in self._canvas._canvas_items:
                if ci.name == name:
                    if new_x is not None:
                        ci.world_x = new_x
                    if new_z is not None:
                        ci.world_z = new_z
                    self._rebuild_item_instances(ci)
                    break
            self._canvas.update()

    def _rebuild_item_instances(self, ci):
        """Rebuild a canvas item's geometry at its current position."""
        try:
            from .menu_igb_loader import (
                extract_menu_scene, resolve_igb_path,
                get_menu_cache_dir, _multiply_matrices, MenuSceneInstance,
            )
        except Exception:
            return
        if ci.element is None:
            return
        model_ref = ci.element.attrib.get('model', '')
        if not model_ref:
            return
        igb_path = resolve_igb_path(model_ref, self._game_dir)
        if igb_path is None:
            return
        cache_dir = get_menu_cache_dir()
        templates = extract_menu_scene(igb_path, cache_dir)
        if not templates:
            return
        placement_mat = (
            1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
            ci.world_x, 0, ci.world_z, 1)
        placed = []
        for tmpl in templates:
            inst = MenuSceneInstance()
            inst.positions = tmpl.positions
            inst.uvs = tmpl.uvs
            inst.colors = tmpl.colors
            inst.indices = tmpl.indices
            inst.texture_png = tmpl.texture_png
            inst.tex_width = tmpl.tex_width
            inst.tex_height = tmpl.tex_height
            inst.diffuse = tmpl.diffuse
            if tmpl.transform is not None:
                inst.transform = _multiply_matrices(
                    tmpl.transform, placement_mat)
            else:
                inst.transform = placement_mat
            placed.append(inst)
        ci.instances = placed

    def _create_attr_row(self, elem, attr_name, attr_value):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)

        name_label = QLabel(attr_name)
        name_label.setMinimumWidth(100)
        name_label.setStyleSheet("color: #80a0c0; font-weight: bold;")
        row_layout.addWidget(name_label)

        if attr_name in BOOL_ATTRS:
            widget = QCheckBox()
            widget.setChecked(attr_value.lower() == 'true')
            widget.stateChanged.connect(
                lambda state, e=elem, n=attr_name:
                    self._on_attr_changed(
                        e, n, 'true' if state == Qt.Checked else 'false'))
        elif attr_name in ENUM_MAP:
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(ENUM_MAP[attr_name])
            widget.setCurrentText(attr_value)
            widget.currentTextChanged.connect(
                lambda text, e=elem, n=attr_name:
                    self._on_attr_changed(e, n, text))
        elif attr_name == 'type' and elem.tag == 'item':
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(ITEM_TYPES)
            widget.setCurrentText(attr_value)
            widget.currentTextChanged.connect(
                lambda text, e=elem, n=attr_name:
                    self._on_attr_changed(e, n, text))
        elif attr_name == 'type' and elem.tag == 'MENU':
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(MENU_TYPES)
            widget.setCurrentText(attr_value)
            widget.currentTextChanged.connect(
                lambda text, e=elem, n=attr_name:
                    self._on_attr_changed(e, n, text))
        elif attr_name == 'type' and elem.tag == 'precache':
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(PRECACHE_TYPES)
            widget.setCurrentText(attr_value)
            widget.currentTextChanged.connect(
                lambda text, e=elem, n=attr_name:
                    self._on_attr_changed(e, n, text))
        elif attr_name == 'type' and elem.tag == 'onfocus':
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(ONFOCUS_TYPES)
            widget.setCurrentText(attr_value)
            widget.currentTextChanged.connect(
                lambda text, e=elem, n=attr_name:
                    self._on_attr_changed(e, n, text))
        else:
            widget = QLineEdit(attr_value)
            widget.editingFinished.connect(
                lambda e=elem, n=attr_name, le=widget:
                    self._on_attr_changed(e, n, le.text()))

        widget.setMinimumWidth(150)
        row_layout.addWidget(widget, 1)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedWidth(28)
        del_btn.setToolTip(f"Remove '{attr_name}'")
        del_btn.clicked.connect(
            lambda _, e=elem, n=attr_name: self._on_delete_attribute(e, n))
        row_layout.addWidget(del_btn)
        return row

    def _on_tag_changed(self, elem, new_tag):
        new_tag = new_tag.strip()
        if not new_tag or new_tag == elem.tag:
            return
        self._push_undo()
        elem.tag = new_tag
        self._populate_tree()
        self._select_element(elem)

    def _on_attr_changed(self, elem, attr_name, new_value):
        if new_value == elem.attrib.get(attr_name, ''):
            return
        self._push_undo()
        elem.set(attr_name, new_value)
        self._refresh_tree_item_for(elem)
        if attr_name in ('name', 'up', 'down', 'left', 'right'):
            self._update_nav_summary()
            self._update_canvas_nav_links()

    def _on_add_attribute(self, elem):
        self._push_undo()
        base = 'new_attr'
        name = base
        i = 1
        while name in elem.attrib:
            name = f"{base}_{i}"
            i += 1
        elem.set(name, '')
        self._update_properties(elem)

    def _on_delete_attribute(self, elem, attr_name):
        if attr_name not in elem.attrib:
            return
        self._push_undo()
        del elem.attrib[attr_name]
        self._update_properties(elem)
        self._refresh_tree_item_for(elem)

    def _refresh_tree_item_for(self, elem):
        def _find(item):
            if getattr(item, '_element', None) is elem:
                return item
            for i in range(item.childCount()):
                r = _find(item.child(i))
                if r:
                    return r
            return None
        for i in range(self._tree.topLevelItemCount()):
            ti = _find(self._tree.topLevelItem(i))
            if ti:
                tag = elem.tag
                name = elem.attrib.get('name', '')
                item_type = elem.attrib.get('type', '')
                icon = TAG_ICONS.get(tag, '\u25AB')
                ti.setText(0, f"{icon} {tag}")
                parts = []
                if name:
                    parts.append(name)
                if item_type:
                    parts.append(item_type)
                ti.setText(1, ' | '.join(parts))
                ti.setForeground(0, QColor(TAG_COLORS.get(tag, '#a0a0a0')))
                return

    # ------------------------------------------------------------------
    # Navigation summary
    # ------------------------------------------------------------------

    def _update_nav_summary(self):
        if self._root is None:
            self._nav_label.setText("")
            return
        name_map = {}
        item_count = 0
        for ie in self._root.iter('item'):
            n = ie.attrib.get('name', '')
            if n:
                name_map[n] = ie
            item_count += 1
        broken = []
        for ie in self._root.iter('item'):
            n = ie.attrib.get('name', '')
            for d in ('up', 'down', 'left', 'right'):
                t = ie.attrib.get(d, '')
                if t and t.strip() and t not in name_map:
                    broken.append(f"{n} \u2192 {d}={t}")
        parts = [f"{item_count} items"]
        if broken:
            parts.append(
                f"<span style='color:#c04040;'>"
                f"\u26A0 {len(broken)} broken nav link"
                f"{'s' if len(broken)!=1 else ''}: "
                f"{', '.join(broken[:5])}"
                f"{'...' if len(broken)>5 else ''}</span>")
        self._nav_label.setText(" | ".join(parts))

    # ------------------------------------------------------------------
    # Texture thumbnails
    # ------------------------------------------------------------------

    def _update_texture_preview(self, elem):
        while self._tex_layout.count():
            child = self._tex_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()
        if not self._textures:
            self._tex_label.setText("IGB Textures (none loaded)")
            return
        igb_stem = None
        if elem is not None:
            model = elem.attrib.get('model', '')
            if model:
                igb_stem = model.split('/')[-1] if '/' in model else model
        if not igb_stem and self._root is not None:
            bg = self._root.attrib.get('igb', '')
            if bg:
                igb_stem = bg
        if not igb_stem or igb_stem not in self._textures:
            all_tex = []
            for stem, texs in self._textures.items():
                for t in texs:
                    all_tex.append((stem, t))
            if not all_tex:
                self._tex_label.setText("IGB Textures (none found)")
                return
            self._tex_label.setText(f"IGB Textures ({len(all_tex)} total)")
            col = row = 0
            for stem, (tn, pp, tw, th) in all_tex[:32]:
                self._add_texture_thumbnail(pp, f"{stem}\n{tw}x{th}", row, col)
                col += 1
                if col >= 8:
                    col = 0
                    row += 1
            return
        textures = self._textures[igb_stem]
        self._tex_label.setText(
            f"IGB Textures \u2014 {igb_stem} ({len(textures)})")
        col = row = 0
        for tn, pp, tw, th in textures:
            self._add_texture_thumbnail(pp, f"{tw}x{th}", row, col)
            col += 1
            if col >= 8:
                col = 0
                row += 1

    def _add_texture_thumbnail(self, png_path, label_text, row, col):
        c = QWidget()
        cl = QVBoxLayout(c)
        cl.setContentsMargins(2, 2, 2, 2)
        cl.setSpacing(1)
        px = self._get_pixmap(png_path)
        if px is None:
            return
        il = QLabel()
        il.setPixmap(px.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        il.setAlignment(Qt.AlignCenter)
        il.setToolTip(f"{png_path}\n{label_text}")
        il.setCursor(Qt.PointingHandCursor)
        il.mousePressEvent = (
            lambda ev, p=png_path, t=label_text: self._show_texture_large(p, t))
        cl.addWidget(il)
        sl = QLabel(label_text)
        sl.setAlignment(Qt.AlignCenter)
        sl.setStyleSheet("color: #808080; font-size: 9px;")
        cl.addWidget(sl)
        self._tex_layout.addWidget(c, row, col)

    def _get_pixmap(self, png_path):
        if png_path in self._pixmap_cache:
            return self._pixmap_cache[png_path]
        if not os.path.exists(png_path):
            return None
        px = QPixmap(png_path)
        if px.isNull():
            return None
        self._pixmap_cache[png_path] = px
        return px

    def _show_texture_large(self, png_path, label_text):
        px = self._get_pixmap(png_path)
        if px is None:
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle(f"Texture: {label_text}")
        dlg.setStyleSheet(STYLESHEET)
        ms = 512
        s = (px.scaled(ms, ms, Qt.KeepAspectRatio, Qt.SmoothTransformation)
             if px.width() > ms or px.height() > ms else px)
        dlg.setIconPixmap(s)
        dlg.setText(f"Size: {px.width()}x{px.height()}\nPath: {png_path}")
        dlg.exec_()
