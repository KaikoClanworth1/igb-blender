"""PySide6 Texture Editor -- external window for viewing/replacing IGB textures.

Launched from Blender via modal operator. Loads any IGB file (any format),
displays all textures as thumbnails, allows replacing individual textures,
and exports a modified IGB.

Two save modes:
  - Model/skin IGBs (with geometry/skeleton): round-trip via from_reader(),
    patching only the texture data while preserving everything else.
  - Texture-only IGBs (or created from image): build from scratch as CLUT.
"""

import os
import struct
import sys
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
        QApplication, QMainWindow, QWidget, QScrollArea, QVBoxLayout,
        QHBoxLayout, QLabel, QFrame, QMenuBar, QFileDialog, QMessageBox,
        QStatusBar, QSizePolicy, QComboBox, QGridLayout, QToolBar,
    )
    from PySide6.QtCore import Qt, QSize, Signal
    from PySide6.QtGui import (
        QPixmap, QFont, QIcon, QColor, QPalette, QAction, QImage,
        QPainter, QPen, QBrush,
    )
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Stylesheet -- dark X-Men Legends theme
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
QScrollArea {
    border: none;
    background-color: #1a1a2e;
}
QLabel {
    color: #d0d0d0;
}
QStatusBar {
    background-color: #0f3460;
    color: #d0d0d0;
    font-size: 12px;
}
QComboBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 12px;
    min-width: 140px;
}
QComboBox:hover {
    border: 1px solid #e0c040;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    selection-background-color: #0f3460;
    selection-color: #e0c040;
}
QToolBar {
    background-color: #16213e;
    border-bottom: 1px solid #0f3460;
    padding: 4px;
    spacing: 8px;
}
"""


# ---------------------------------------------------------------------------
# Texture info container
# ---------------------------------------------------------------------------

class TextureInfo:
    """Holds info about one texture in the loaded IGB."""
    __slots__ = (
        'index', 'obj_index', 'name', 'width', 'height',
        'pixel_format', 'image_size', 'png_path',
        'replacement_rgba', 'replacement_w', 'replacement_h',
        'pixmap', 'mipmap_obj_indices', 'is_mipmap',
    )

    def __init__(self):
        self.index = 0           # sequential texture index
        self.obj_index = 0       # index in reader.objects
        self.name = ""
        self.width = 0
        self.height = 0
        self.pixel_format = 0
        self.image_size = 0
        self.png_path = None     # cached PNG path
        self.replacement_rgba = None  # bytes if replaced
        self.replacement_w = 0
        self.replacement_h = 0
        self.mipmap_obj_indices = []  # ordered list of mipmap igImage obj indices
        self.is_mipmap = False   # True if this image is part of a mipmap chain
        self.pixmap = None       # QPixmap for display


# ---------------------------------------------------------------------------
# Thumbnail widget
# ---------------------------------------------------------------------------

THUMB_SIZE = 200

class TextureThumbnail(QFrame):
    """Clickable thumbnail widget for a single texture."""

    clicked = Signal(int)  # emits texture index

    def __init__(self, tex_info, parent=None):
        super().__init__(parent)
        self.tex_info = tex_info
        self._selected = False

        self.setFixedSize(THUMB_SIZE + 20, THUMB_SIZE + 60)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # Image label
        self._img_label = QLabel()
        self._img_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("background-color: #0a0a1e; border: 1px solid #0f3460;")
        layout.addWidget(self._img_label)

        # Name label
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setStyleSheet("color: #e0c040; font-size: 11px; font-weight: bold;")
        self._name_label.setWordWrap(True)
        layout.addWidget(self._name_label)

        # Size label
        self._size_label = QLabel()
        self._size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size_label.setStyleSheet("color: #808080; font-size: 10px;")
        layout.addWidget(self._size_label)

        self.update_display()

    def update_display(self):
        ti = self.tex_info
        # Set pixmap
        if ti.pixmap and not ti.pixmap.isNull():
            scaled = ti.pixmap.scaled(
                THUMB_SIZE, THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_label.setPixmap(scaled)

        # Name (truncate if needed)
        name = ti.name or f"texture_{ti.index}"
        if len(name) > 28:
            name = name[:25] + "..."
        self._name_label.setText(name)

        # Dimensions
        w = ti.replacement_w if ti.replacement_rgba else ti.width
        h = ti.replacement_h if ti.replacement_rgba else ti.height
        pfmt_name = _pfmt_name(ti.pixel_format)
        mip_tag = " [MIP]" if ti.is_mipmap else ""
        self._size_label.setText(f"{w} x {h}  ({pfmt_name}){mip_tag}")

        # Show modified/mipmap indicator via name color
        if ti.is_mipmap:
            self._name_label.setStyleSheet(
                "color: #808080; font-size: 11px; font-style: italic;")
        elif ti.replacement_rgba is not None:
            self._name_label.setStyleSheet(
                "color: #40e040; font-size: 11px; font-weight: bold;")
        else:
            self._name_label.setStyleSheet(
                "color: #e0c040; font-size: 11px; font-weight: bold;")

    def set_selected(self, selected):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "TextureThumbnail { background-color: #1a2a5e; "
                "border: 2px solid #e0c040; border-radius: 6px; }")
        else:
            self.setStyleSheet(
                "TextureThumbnail { background-color: #16213e; "
                "border: 1px solid #0f3460; border-radius: 6px; }"
                "TextureThumbnail:hover { border: 1px solid #e0c040; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.tex_info.index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Double-click to replace
            parent = self.window()
            if hasattr(parent, '_replace_texture'):
                parent._replace_texture(self.tex_info.index)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(STYLESHEET)

        replace_action = menu.addAction("Replace Texture...")
        export_action = menu.addAction("Export as PNG...")
        revert_action = None
        if self.tex_info.replacement_rgba is not None:
            menu.addSeparator()
            revert_action = menu.addAction("Revert to Original")

        action = menu.exec(event.globalPos())
        parent = self.window()
        if action == replace_action and hasattr(parent, '_replace_texture'):
            parent._replace_texture(self.tex_info.index)
        elif action == export_action and hasattr(parent, '_export_texture_png'):
            parent._export_texture_png(self.tex_info.index)
        elif revert_action and action == revert_action:
            if hasattr(parent, '_revert_texture'):
                parent._revert_texture(self.tex_info.index)


# ---------------------------------------------------------------------------
# Flow layout for thumbnails
# ---------------------------------------------------------------------------

class FlowLayout(QVBoxLayout):
    """Simple flow layout that wraps widgets into rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._widgets = []

    def add_widget(self, widget):
        self._widgets.append(widget)

    def reflow(self, container_width):
        # Clear existing rows
        for row_layout in self._rows:
            while row_layout.count():
                item = row_layout.takeAt(0)
                # Don't delete widget, just remove from layout
            self.removeItem(row_layout)
            row_layout.deleteLater()
        self._rows.clear()

        if not self._widgets:
            return

        item_width = THUMB_SIZE + 30
        cols = max(1, container_width // item_width)

        row_layout = None
        for i, w in enumerate(self._widgets):
            if i % cols == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(10)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
                self.addLayout(row_layout)
                self._rows.append(row_layout)
            row_layout.addWidget(w)

    def clear_widgets(self):
        for w in self._widgets:
            w.setParent(None)
            w.deleteLater()
        for row_layout in self._rows:
            while row_layout.count():
                row_layout.takeAt(0)
            self.removeItem(row_layout)
            row_layout.deleteLater()
        self._rows.clear()
        self._widgets.clear()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class TextureEditorWindow(QMainWindow):
    """PySide6 window for viewing and editing IGB textures."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IGB Texture Editor")
        self.setMinimumSize(800, 600)
        self.resize(1100, 700)
        self.setStyleSheet(STYLESHEET)

        # State
        self._igb_path = None
        self._reader = None
        self._textures = []        # list of TextureInfo
        self._selected_index = -1
        self._modified = False
        self._is_model_igb = False  # True if loaded IGB has geometry/skeleton
        self._cache_dir = Path(__file__).parent / "texture_cache"

        self._build_ui()

    def _build_ui(self):
        # Menu bar
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        open_action = file_menu.addAction("Open IGB...")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open)

        create_action = file_menu.addAction("Create from Image...")
        create_action.setShortcut("Ctrl+I")
        create_action.triggered.connect(self._on_create_from_image)

        self._save_action = file_menu.addAction("Save")
        self._save_action.setShortcut("Ctrl+S")
        self._save_action.setEnabled(False)
        self._save_action.triggered.connect(self._on_save)

        save_as_action = file_menu.addAction("Save As...")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._on_save_as)

        file_menu.addSeparator()

        close_action = file_menu.addAction("Close")
        close_action.triggered.connect(self.close)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Replace button in toolbar
        replace_action = QAction("Replace Selected", self)
        replace_action.triggered.connect(lambda: self._replace_texture(self._selected_index))
        toolbar.addAction(replace_action)

        # Swap R/B button
        swap_rb_action = QAction("Swap R/B", self)
        swap_rb_action.triggered.connect(self._on_swap_rb)
        toolbar.addAction(swap_rb_action)

        toolbar.addSeparator()

        # Show Mipmaps toggle
        from PySide6.QtWidgets import QCheckBox
        self._mip_checkbox = QCheckBox("Show Mipmaps")
        self._mip_checkbox.setStyleSheet(
            "QCheckBox { color: #d0d0d0; font-size: 12px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { background-color: #16213e; "
            "border: 1px solid #0f3460; border-radius: 2px; }"
            "QCheckBox::indicator:checked { background-color: #e0c040; "
            "border: 1px solid #e0c040; border-radius: 2px; }")
        self._mip_checkbox.setChecked(False)
        self._mip_checkbox.toggled.connect(self._on_mip_toggle)
        toolbar.addWidget(self._mip_checkbox)

        toolbar.addSeparator()

        # Export resolution dropdown
        res_label = QLabel("Export Res:")
        res_label.setStyleSheet("color: #d0d0d0; font-size: 12px; margin-left: 4px;")
        toolbar.addWidget(res_label)
        self._res_combo = QComboBox()
        self._res_combo.addItems(["Original", "2048", "1024", "512"])
        self._res_combo.setToolTip("Output resolution (square). 'Original' keeps each texture's size.")
        toolbar.addWidget(self._res_combo)

        # Scroll area for thumbnails
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background-color: #1a1a2e;")
        self._flow = FlowLayout(self._grid_widget)
        self._flow.setContentsMargins(15, 15, 15, 15)
        self._flow.setSpacing(10)

        self._scroll.setWidget(self._grid_widget)
        self.setCentralWidget(self._scroll)

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("No file loaded. Use File > Open to load an IGB file.")

        # Welcome label (shown when no file loaded)
        self._welcome = QLabel(
            "IGB Texture Editor\n\n"
            "File > Open IGB (Ctrl+O) — load textures from an existing IGB\n"
            "File > Create from Image (Ctrl+I) — build a new IGB from PNG/TGA/BMP/JPG\n\n"
            "Double-click or right-click a texture to replace it.\n"
            "Use File > Save As to export the IGB."
        )
        self._welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._welcome.setStyleSheet(
            "color: #808080; font-size: 16px; padding: 60px;")
        self._flow.addWidget(self._welcome)

    # -- File operations --

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open IGB File", "",
            "IGB Files (*.igb);;All Files (*)")
        if not path:
            return
        self._load_igb(path)

    def _on_create_from_image(self):
        """Create a new texture IGB from one or more image files."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Image(s)", "",
            "Images (*.png *.tga *.bmp *.jpg *.jpeg);;All Files (*)")
        if not paths:
            return
        self._load_images(paths)

    def _load_images(self, image_paths):
        """Load image files directly as textures (no IGB source needed)."""
        from .hud_extract import write_png

        self._reader = None
        self._igb_path = None
        self._modified = False
        self._is_model_igb = False
        self._selected_index = -1
        self._textures.clear()

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Clear old cache
        for old_png in self._cache_dir.glob("img_*.png"):
            try:
                old_png.unlink()
            except OSError:
                pass

        tex_index = 0
        for img_path in image_paths:
            qimg = QImage(img_path)
            if qimg.isNull():
                continue
            qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)

            # Flip vertically (IGB stores textures upside down)
            qimg = qimg.mirrored(False, True)

            w, h = qimg.width(), qimg.height()

            # Extract RGBA bytes
            bpl = qimg.bytesPerLine()
            row_bytes = w * 4
            ptr = qimg.constBits()
            raw = bytes(ptr)
            if bpl == row_bytes:
                rgba = raw[:w * h * 4]
            else:
                rgba = bytearray(w * h * 4)
                for row in range(h):
                    s = row * bpl
                    d = row * row_bytes
                    rgba[d:d + row_bytes] = raw[s:s + row_bytes]
                rgba = bytes(rgba)

            # Cache as PNG for display
            name = Path(img_path).stem
            png_path = self._cache_dir / f"img_{tex_index}_{name}.png"
            write_png(rgba, w, h, str(png_path))

            ti = TextureInfo()
            ti.index = tex_index
            ti.obj_index = -1
            ti.name = name
            ti.width = w
            ti.height = h
            ti.pixel_format = 0
            ti.image_size = len(rgba)
            ti.png_path = str(png_path)
            ti.pixmap = QPixmap(str(png_path))
            ti.is_mipmap = False
            ti.mipmap_obj_indices = []

            self._textures.append(ti)
            tex_index += 1

        self._rebuild_grid()
        self._save_action.setEnabled(False)  # no source IGB to overwrite
        n = len(self._textures)
        names = ", ".join(Path(p).name for p in image_paths[:3])
        if len(image_paths) > 3:
            names += f" (+{len(image_paths) - 3} more)"
        self._statusbar.showMessage(
            f"Created from {n} image(s): {names}  —  Use Save As to export IGB")
        self.setWindowTitle("IGB Texture Editor — New from Image")

    def _on_save(self):
        if not self._igb_path or not self._textures:
            return
        self._save_igb(self._igb_path)

    def _on_save_as(self):
        if not self._textures:
            QMessageBox.warning(self, "No Textures",
                                "No textures loaded. Open an IGB or create from image first.")
            return
        default_name = ""
        if self._igb_path:
            p = Path(self._igb_path)
            default_name = str(p.parent / (p.stem + "_edited.igb"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save IGB File", default_name,
            "IGB Files (*.igb);;All Files (*)")
        if not path:
            return
        self._save_igb(path)

    def _on_swap_rb(self):
        """Swap R and B channels on all loaded textures."""
        if not self._textures:
            return

        from .hud_extract import write_png

        for ti in self._textures:
            if not ti.png_path or not os.path.exists(ti.png_path):
                continue

            # Load the cached PNG via QImage, swap R/B, re-save
            qimg = QImage(ti.png_path)
            if qimg.isNull():
                continue
            qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = qimg.width(), qimg.height()
            bpl = qimg.bytesPerLine()
            row_bytes = w * 4
            ptr = qimg.bits()
            raw = bytes(ptr)

            if bpl == row_bytes:
                rgba = bytearray(raw[:w * h * 4])
            else:
                rgba = bytearray(w * h * 4)
                for row in range(h):
                    rgba[row * row_bytes:(row + 1) * row_bytes] = \
                        raw[row * bpl:row * bpl + row_bytes]

            # Swap R and B
            for px in range(w * h):
                off = px * 4
                rgba[off], rgba[off + 2] = rgba[off + 2], rgba[off]

            write_png(rgba, w, h, ti.png_path)
            ti.pixmap = QPixmap(ti.png_path)

        self._rebuild_grid()
        self._statusbar.showMessage("Swapped R/B channels on all textures.")

    def _on_mip_toggle(self, checked):
        self._rebuild_grid()

    # -- Load IGB --

    def _load_igb(self, path):
        """Load an IGB file and extract base textures for display.

        Mipmap images are identified and excluded from the thumbnail grid.
        They are stored per-texture so they can be auto-regenerated on save.
        """
        import struct as _struct
        from ..igb_format.igb_reader import IGBReader
        from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock
        from ..scene_graph.sg_materials import extract_image
        from ..utils.image_convert import convert_image_to_rgba

        try:
            reader = IGBReader(path)
            reader.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load IGB:\n{e}")
            return

        self._reader = reader
        self._igb_path = path
        self._modified = False
        self._selected_index = -1
        self._textures.clear()

        endian = reader.header.endian

        # Ensure cache dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(path).stem

        # --- Collect mipmap obj indices from igImageMipMapList objects ---
        # Also build a map: base_image_obj_index -> [mipmap_obj_indices]
        mipmap_set = set()         # all igImage indices that are mipmaps
        base_to_mips = {}          # base_obj_idx -> ordered list of mip obj indices

        for obj in reader.objects:
            if not isinstance(obj, IGBObject):
                continue
            if not obj.is_type(b"igTextureAttr"):
                continue

            base_ref = None
            mml_ref = None
            for slot, val, fi in obj._raw_fields:
                if slot == 12 and fi.short_name == b"ObjectRef" and val != -1:
                    base_ref = val
                if slot == 16 and fi.short_name == b"ObjectRef" and val != -1:
                    mml_ref = val

            if base_ref is None or mml_ref is None:
                continue

            # Resolve base image
            base_obj = reader.resolve_ref(base_ref)
            if not isinstance(base_obj, IGBObject) or \
                    not base_obj.is_type(b"igImage"):
                continue

            # Resolve igImageMipMapList
            mml_obj = reader.resolve_ref(mml_ref)
            if not isinstance(mml_obj, IGBObject) or \
                    not mml_obj.is_type(b"igImageMipMapList"):
                continue

            # Read mipmap image refs from the MML's MemoryRef block
            mml_memref = None
            for slot, val, fi in mml_obj._raw_fields:
                if slot == 4 and fi.short_name == b"MemoryRef" and val != -1:
                    mml_memref = val

            if mml_memref is None:
                continue

            block = reader.resolve_ref(mml_memref)
            if not isinstance(block, IGBMemoryBlock) or not block.data:
                continue

            mip_indices = []
            count = len(block.data) // 4
            for k in range(count):
                ref_val = _struct.unpack_from(endian + "I", block.data, k * 4)[0]
                mip_indices.append(ref_val)
                mipmap_set.add(ref_val)

            base_to_mips[base_obj.index] = mip_indices

        # --- Clear old PNG cache for this file ---
        if self._cache_dir.exists():
            for old_png in self._cache_dir.glob(f"{stem}_*.png"):
                try:
                    old_png.unlink()
                except OSError:
                    pass

        # --- Build texture list (ALL images, with is_mipmap flag) ---
        tex_index = 0
        base_count = 0
        from .hud_extract import write_png

        for i, obj in enumerate(reader.objects):
            if not isinstance(obj, IGBObject):
                continue
            if not obj.is_type(b"igImage"):
                continue

            parsed = extract_image(reader, obj)
            if parsed is None or parsed.width == 0 or parsed.height == 0:
                continue

            ti = TextureInfo()
            ti.index = tex_index
            ti.obj_index = i
            ti.name = parsed.name or f"{stem}_tex{tex_index}"
            ti.width = parsed.width
            ti.height = parsed.height
            ti.pixel_format = parsed.pixel_format
            ti.image_size = parsed.image_size
            ti.is_mipmap = (i in mipmap_set)
            ti.mipmap_obj_indices = base_to_mips.get(i, [])

            # Convert to RGBA and cache as PNG
            if parsed.pixel_data is not None:
                rgba = convert_image_to_rgba(parsed)
                if rgba is not None:
                    png_path = self._cache_dir / f"{stem}_{tex_index}.png"
                    write_png(rgba, parsed.width, parsed.height, str(png_path))
                    ti.png_path = str(png_path)

                    # Create QPixmap
                    ti.pixmap = QPixmap(str(png_path))

            if not ti.is_mipmap:
                base_count += 1
            self._textures.append(ti)
            tex_index += 1

        # Detect model/skin IGB (has geometry or skeleton → round-trip save)
        self._is_model_igb = False
        _MODEL_TYPES = (
            b"igGeometryAttr", b"igGeometryAttr1_5", b"igGeometryAttr2",
            b"igSkin", b"igSkeleton",
        )
        for obj in reader.objects:
            if not isinstance(obj, IGBObject):
                continue
            if any(obj.is_type(t) for t in _MODEL_TYPES):
                self._is_model_igb = True
                break

        mip_count = len(mipmap_set)
        self._rebuild_grid()
        self._save_action.setEnabled(True)
        info = f"{base_count} texture(s)"
        if mip_count:
            info += f", {mip_count} mipmaps (auto-generated on save)"
        mode = " [Model — round-trip save]" if self._is_model_igb else ""
        self._statusbar.showMessage(f"Loaded {path}  —  {info}{mode}")
        self.setWindowTitle(f"IGB Texture Editor — {Path(path).name}")

    # -- Grid management --

    def _rebuild_grid(self):
        """Rebuild the thumbnail grid from self._textures.

        Filters out mipmap images unless the 'Show Mipmaps' checkbox is checked.
        """
        self._flow.clear_widgets()

        show_mips = self._mip_checkbox.isChecked()
        visible = [ti for ti in self._textures
                   if show_mips or not ti.is_mipmap]

        if not visible:
            lbl = QLabel("No textures found in this IGB file.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #808080; font-size: 14px; padding: 40px;")
            self._flow.addWidget(lbl)
            return

        for ti in visible:
            thumb = TextureThumbnail(ti)
            thumb.clicked.connect(self._on_thumb_clicked)
            if ti.index == self._selected_index:
                thumb.set_selected(True)
            self._flow.add_widget(thumb)

        # Trigger reflow
        w = self._scroll.viewport().width()
        self._flow.reflow(w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self._scroll.viewport().width()
        self._flow.reflow(w)

    def _on_thumb_clicked(self, index):
        old_sel = self._selected_index
        self._selected_index = index

        # Update selection visuals
        for w in self._flow._widgets:
            if isinstance(w, TextureThumbnail):
                w.set_selected(w.tex_info.index == index)

    # -- Texture replacement --

    def _replace_texture(self, index):
        """Replace a texture with a new image file."""
        if index < 0 or index >= len(self._textures):
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Replacement Image", "",
            "Image Files (*.png *.tga *.bmp *.jpg *.jpeg *.tif *.tiff);;"
            "PNG Files (*.png);;All Files (*)")
        if not path:
            return

        # Load image via QImage
        qimg = QImage(path)
        if qimg.isNull():
            QMessageBox.critical(self, "Error",
                                 f"Failed to load image:\n{path}")
            return

        # Convert to RGBA8888
        qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)

        # Auto-flip vertically (IGB stores loading screens upside down)
        qimg = qimg.mirrored(False, True)

        # Auto-scale to match original texture dimensions
        ti = self._textures[index]
        orig_w, orig_h = ti.width, ti.height
        if qimg.width() != orig_w or qimg.height() != orig_h:
            qimg = qimg.scaled(
                orig_w, orig_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        w = qimg.width()
        h = qimg.height()

        # Extract pixel bytes (always handle row padding)
        bpl = qimg.bytesPerLine()
        expected = w * h * 4
        row_bytes = w * 4
        ptr = qimg.constBits()
        raw = bytes(ptr)

        if bpl == row_bytes:
            rgba_data = raw[:expected]
        else:
            # QImage has row padding — copy row by row to strip it
            rgba_data = bytearray(expected)
            for row in range(h):
                src_start = row * bpl
                dst_start = row * row_bytes
                rgba_data[dst_start:dst_start + row_bytes] = \
                    raw[src_start:src_start + row_bytes]

        ti.replacement_rgba = bytes(rgba_data)
        ti.replacement_w = w
        ti.replacement_h = h

        # Update pixmap from replacement
        ti.pixmap = QPixmap.fromImage(qimg)

        # Update thumbnail widget
        for widget in self._flow._widgets:
            if isinstance(widget, TextureThumbnail) and widget.tex_info.index == index:
                widget.update_display()
                break

        self._modified = True
        self._statusbar.showMessage(
            f"Replaced texture '{ti.name}' with {Path(path).name} ({w}x{h})")

    def _revert_texture(self, index):
        """Revert a texture replacement to the original."""
        if index < 0 or index >= len(self._textures):
            return

        ti = self._textures[index]
        if ti.replacement_rgba is None:
            return

        ti.replacement_rgba = None
        ti.replacement_w = 0
        ti.replacement_h = 0

        # Restore original pixmap
        if ti.png_path:
            ti.pixmap = QPixmap(ti.png_path)

        # Update thumbnail
        for widget in self._flow._widgets:
            if isinstance(widget, TextureThumbnail) and widget.tex_info.index == index:
                widget.update_display()
                break

        # Check if any modifications remain
        self._modified = any(t.replacement_rgba is not None for t in self._textures)
        self._statusbar.showMessage(f"Reverted texture '{ti.name}' to original.")

    def _export_texture_png(self, index):
        """Export a single texture as PNG."""
        if index < 0 or index >= len(self._textures):
            return

        ti = self._textures[index]
        default_name = (ti.name or f"texture_{index}") + ".png"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Texture as PNG", default_name,
            "PNG Files (*.png);;All Files (*)")
        if not path:
            return

        if ti.replacement_rgba is not None:
            # Export the replacement
            from .hud_extract import write_png
            write_png(ti.replacement_rgba, ti.replacement_w,
                      ti.replacement_h, path)
        elif ti.png_path and os.path.exists(ti.png_path):
            # Copy the cached PNG
            import shutil
            shutil.copy2(ti.png_path, path)
        else:
            QMessageBox.warning(self, "No Data",
                                "No pixel data available for this texture.")
            return

        self._statusbar.showMessage(f"Exported texture to {path}")

    # -- Save IGB --

    def _save_igb(self, output_path):
        """Save IGB (round-trip for models, from-scratch for texture-only)."""
        if not self._textures:
            return

        try:
            self._do_save(output_path)
            self._statusbar.showMessage(f"Saved to {output_path}")
            self._modified = False

            # If we saved to the same path, reload
            if output_path == self._igb_path:
                self._load_igb(output_path)
        except Exception as e:
            QMessageBox.critical(self, "Save Error",
                                 f"Failed to save IGB:\n{e}")

    def _do_save(self, output_path):
        """Save IGB — model round-trip or from-scratch depending on source."""
        if self._is_model_igb and self._reader is not None:
            self._do_save_model(output_path)
        else:
            self._do_save_scratch(output_path)

    def _do_save_scratch(self, output_path):
        """Build a brand-new PS2 CLUT IGB from scratch with all textures."""
        from ..utils.clut_compress import quantize_rgba_to_clut
        from .texture_igb_builder import build_texture_igb

        # Get target resolution from dropdown
        res_text = self._res_combo.currentText()
        target_res = int(res_text) if res_text != "Original" else None

        # Collect RGBA data for each base texture (skip mipmaps)
        tex_entries = []  # list of (palette, indices, w, h, name)

        for ti in self._textures:
            if ti.is_mipmap:
                continue

            # Get RGBA pixel data
            if ti.replacement_rgba is not None:
                rgba = ti.replacement_rgba
                w, h = ti.replacement_w, ti.replacement_h
            elif ti.png_path and os.path.exists(ti.png_path):
                rgba, w, h = self._read_png_rgba(ti.png_path)
                if rgba is None:
                    continue
            else:
                continue

            # Scale to target resolution if set
            if target_res is not None and (w != target_res or h != target_res):
                rgba, w, h = self._scale_rgba(rgba, w, h, target_res, target_res)

            name = ti.name or f"texture_{ti.index}"

            # Quantize to CLUT
            palette, indices = quantize_rgba_to_clut(rgba, w, h)
            tex_entries.append((palette, indices, w, h, name))

        if not tex_entries:
            raise RuntimeError("No texture data available to save.")

        build_texture_igb(tex_entries, output_path)

    # -- Model round-trip save --

    def _do_save_model(self, output_path):
        """Save model IGB via from_reader round-trip, patching only replaced textures.

        Preserves all geometry, skeleton, materials, and scene graph data.
        Only the texture pixel/palette memory blocks are replaced.
        """
        from ..igb_format.igb_writer import from_reader, MemoryBlockDef
        from ..igb_format.igb_objects import IGBObject
        from ..utils.clut_compress import quantize_rgba_to_clut, map_rgba_to_palette
        from ..utils.dxt_compress import compress_with_mipmaps

        writer = from_reader(self._reader)

        res_text = self._res_combo.currentText()
        target_res = int(res_text) if res_text != "Original" else None

        for ti in self._textures:
            if ti.is_mipmap or ti.replacement_rgba is None or ti.obj_index < 0:
                continue

            rgba = ti.replacement_rgba
            w, h = ti.replacement_w, ti.replacement_h

            if target_res is not None and (w != target_res or h != target_res):
                rgba, w, h = self._scale_rgba(rgba, w, h, target_res, target_res)

            img_odef = writer.objects[ti.obj_index]
            pfmt, pimage_idx, clut_ref = self._get_odef_image_info(img_odef)

            if pfmt == 65536:
                # CLUT/PSMT8 — quantize and replace palette + index data
                palette_data, index_data = quantize_rgba_to_clut(rgba, w, h)
                self._patch_memblock(writer, pimage_idx, index_data)
                self._patch_image_fields(img_odef, w, h, len(index_data), w)

                # Replace igClut palette
                if clut_ref >= 0:
                    clut_odef = writer.objects[clut_ref]
                    pal_mem_idx = self._get_odef_field(clut_odef, 5)  # _pData
                    if pal_mem_idx is not None and pal_mem_idx >= 0:
                        self._patch_memblock(writer, pal_mem_idx, palette_data)

                # Handle CLUT mipmaps — map to same palette
                if ti.mipmap_obj_indices:
                    mw, mh = w, h
                    for mip_obj_idx in ti.mipmap_obj_indices:
                        mw = max(1, mw // 2)
                        mh = max(1, mh // 2)
                        mip_rgba, mw2, mh2 = self._scale_rgba(rgba, w, h, mw, mh)
                        mip_indices = map_rgba_to_palette(mip_rgba, mw2, mh2, palette_data)
                        mip_odef = writer.objects[mip_obj_idx]
                        _, mip_pimg_idx, _ = self._get_odef_image_info(mip_odef)
                        self._patch_memblock(writer, mip_pimg_idx, mip_indices)
                        self._patch_image_fields(mip_odef, mw2, mh2, len(mip_indices), mw2)

            elif pfmt in (15, 16):
                # DXT3/DXT5 — recompress as DXT5
                mip_levels = compress_with_mipmaps(rgba, w, h)
                base_data, _, _ = mip_levels[0]
                bpr = max(1, w // 4) * 16
                self._patch_memblock(writer, pimage_idx, base_data)
                self._patch_image_fields(img_odef, w, h, len(base_data), bpr)
                # Update pfmt to DXT5 if was DXT3
                if pfmt == 15:
                    self._set_odef_field(img_odef, 11, 16)

                # Handle DXT mipmaps
                if ti.mipmap_obj_indices:
                    for m_idx, mip_obj_idx in enumerate(ti.mipmap_obj_indices):
                        level = m_idx + 1
                        if level < len(mip_levels):
                            mip_data, mip_w, mip_h = mip_levels[level]
                        else:
                            mip_w = max(1, w >> level)
                            mip_h = max(1, h >> level)
                            mip_rgba, mip_w, mip_h = self._scale_rgba(
                                rgba, w, h, mip_w, mip_h)
                            extra = compress_with_mipmaps(mip_rgba, mip_w, mip_h)
                            mip_data, _, _ = extra[0]

                        mip_bpr = max(1, mip_w // 4) * 16
                        mip_odef = writer.objects[mip_obj_idx]
                        _, mip_pimg_idx, _ = self._get_odef_image_info(mip_odef)
                        self._patch_memblock(writer, mip_pimg_idx, mip_data)
                        self._patch_image_fields(
                            mip_odef, mip_w, mip_h, len(mip_data), mip_bpr)
                        if pfmt == 15:
                            self._set_odef_field(mip_odef, 11, 16)

            else:
                # Raw RGBA or unknown — write raw pixels
                self._patch_memblock(writer, pimage_idx, rgba)
                self._patch_image_fields(img_odef, w, h, len(rgba), w * 4)

        writer.write(output_path)

    # -- ObjectDef patching helpers --

    @staticmethod
    def _get_odef_image_info(odef):
        """Extract (pfmt, pimage_memblock_idx, clut_obj_idx) from an igImage ObjectDef."""
        pfmt = 0
        pimage_idx = -1
        clut_ref = -1
        for slot, val, fd in odef.raw_fields:
            if slot == 11:    # _pfmt
                pfmt = val
            elif slot == 13:  # _pImage (MemoryRef)
                pimage_idx = val
            elif slot == 17:  # _clut (ObjectRef)
                clut_ref = val
        return pfmt, pimage_idx, clut_ref

    @staticmethod
    def _get_odef_field(odef, target_slot):
        """Get a field value from an ObjectDef by slot number."""
        for slot, val, fd in odef.raw_fields:
            if slot == target_slot:
                return val
        return None

    @staticmethod
    def _set_odef_field(odef, target_slot, new_val):
        """Set a field value in an ObjectDef by slot number. Clears raw_bytes."""
        odef.raw_bytes = None
        for j, (slot, val, fd) in enumerate(odef.raw_fields):
            if slot == target_slot:
                odef.raw_fields[j] = (slot, new_val, fd)
                return

    @staticmethod
    def _patch_image_fields(img_odef, w, h, image_size, bytes_per_row):
        """Update igImage dimension/size fields in an ObjectDef."""
        img_odef.raw_bytes = None
        for j, (slot, val, fd) in enumerate(img_odef.raw_fields):
            if slot == 2:     # _px (width)
                img_odef.raw_fields[j] = (slot, w, fd)
            elif slot == 3:   # _py (height)
                img_odef.raw_fields[j] = (slot, h, fd)
            elif slot == 12:  # _imageSize
                img_odef.raw_fields[j] = (slot, image_size, fd)
            elif slot == 19:  # _bytesPerRow
                img_odef.raw_fields[j] = (slot, bytes_per_row, fd)

    @staticmethod
    def _patch_memblock(writer, mem_idx, new_data):
        """Replace a memory block's data and update ref_info/entries."""
        from ..igb_format.igb_writer import MemoryBlockDef
        if mem_idx < 0 or mem_idx >= len(writer.objects):
            return
        writer.objects[mem_idx] = MemoryBlockDef(new_data)
        writer.ref_info[mem_idx]['mem_size'] = len(new_data)
        entry_idx = writer.index_map[mem_idx]
        writer.entries[entry_idx].field_values[1] = len(new_data)

    @staticmethod
    def _scale_rgba(rgba, src_w, src_h, dst_w, dst_h):
        """Scale RGBA pixel data to a new resolution using QImage (smooth)."""
        qimg = QImage(rgba, src_w, src_h, src_w * 4,
                      QImage.Format.Format_RGBA8888)
        scaled = qimg.scaled(dst_w, dst_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        scaled = scaled.convertToFormat(QImage.Format.Format_RGBA8888)
        bpl = scaled.bytesPerLine()
        row_bytes = dst_w * 4
        ptr = scaled.constBits()
        raw = bytes(ptr)
        if bpl == row_bytes:
            out = raw[:dst_w * dst_h * 4]
        else:
            out = bytearray(dst_w * dst_h * 4)
            for row in range(dst_h):
                s = row * bpl
                d = row * row_bytes
                out[d:d + row_bytes] = raw[s:s + row_bytes]
            out = bytes(out)
        return out, dst_w, dst_h

    def _read_png_rgba(self, png_path):
        """Read a cached PNG back to RGBA bytes via QImage.

        Returns (rgba_bytes, width, height) or (None, 0, 0) on failure.
        """
        qimg = QImage(png_path)
        if qimg.isNull():
            return None, 0, 0

        qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()

        bpl = qimg.bytesPerLine()
        row_bytes = w * 4
        ptr = qimg.constBits()
        raw = bytes(ptr)

        if bpl == row_bytes:
            rgba = raw[:w * h * 4]
        else:
            rgba = bytearray(w * h * 4)
            for row in range(h):
                src_start = row * bpl
                dst_start = row * row_bytes
                rgba[dst_start:dst_start + row_bytes] = \
                    raw[src_start:src_start + row_bytes]
            rgba = bytes(rgba)

        return rgba, w, h


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pfmt_name(pfmt):
    """Human-readable pixel format name."""
    from ..scene_graph.sg_materials import (
        PFMT_RGB_DXT1, PFMT_RGBA_DXT1, PFMT_RGBA_DXT3, PFMT_RGBA_DXT5,
        PFMT_RGBA_8888_32, PFMT_RGB_888_24, PFMT_PS2_PSMT8, PFMT_PS2_PSMT4,
        PFMT_L_8, PFMT_A_8, PFMT_LA_88_16,
    )
    names = {
        PFMT_RGB_DXT1: "DXT1",
        PFMT_RGBA_DXT1: "DXT1a",
        PFMT_RGBA_DXT3: "DXT3",
        PFMT_RGBA_DXT5: "DXT5",
        PFMT_RGBA_8888_32: "RGBA32",
        PFMT_RGB_888_24: "RGB24",
        PFMT_PS2_PSMT8: "CLUT8",
        PFMT_PS2_PSMT4: "CLUT4",
        PFMT_L_8: "L8",
        PFMT_A_8: "A8",
        PFMT_LA_88_16: "LA16",
    }
    return names.get(pfmt, f"fmt{pfmt}")
