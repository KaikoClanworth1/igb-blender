"""PySide6 Script Editor — per-entity Python script builder with command palette.

Launched from Blender via modal operator. Provides a 3-pane layout:
  Left:   Command palette (categorized, searchable game commands)
  Center: Python script text editor with syntax highlighting
  Right:  Command help/documentation panel

Reads/writes .py files on disk. Updates entity script property via signal.
"""

import os
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
        QApplication, QMainWindow, QWidget, QSplitter, QTreeWidget,
        QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QPlainTextEdit, QPushButton, QComboBox, QStatusBar, QFrame,
    )
    from PySide6.QtCore import Qt, Signal, QRegularExpression
    from PySide6.QtGui import (
        QFont, QSyntaxHighlighter, QTextCharFormat, QColor, QTextCursor,
        QKeySequence, QShortcut,
    )
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Stylesheet — dark theme matching other Map Maker editors
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
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
QPlainTextEdit {
    background-color: #0d1117;
    color: #e0e0e0;
    border: 1px solid #0f3460;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 13px;
    selection-background-color: #264f78;
}
QStatusBar {
    background-color: #0f3460;
    color: #d0d0d0;
    font-size: 12px;
}
QFrame#help_panel {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 8px;
}
"""


# ---------------------------------------------------------------------------
# Syntax highlighter for Python + game.* calls
# ---------------------------------------------------------------------------

class PythonHighlighter(QSyntaxHighlighter):
    """Basic Python syntax highlighting with game.* call accents."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = []

        # Keywords
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor('#c586c0'))
        kw_fmt.setFontWeight(QFont.Bold)
        keywords = [
            'def', 'class', 'if', 'elif', 'else', 'return', 'pass',
            'import', 'from', 'for', 'while', 'in', 'not', 'and', 'or',
            'True', 'False', 'None', 'try', 'except', 'finally', 'with',
            'as', 'break', 'continue', 'yield', 'lambda', 'global',
        ]
        for kw in keywords:
            self._rules.append((
                QRegularExpression(rf'\b{kw}\b'), kw_fmt
            ))

        # game.* calls — gold accent
        game_fmt = QTextCharFormat()
        game_fmt.setForeground(QColor('#e0c040'))
        game_fmt.setFontWeight(QFont.Bold)
        self._rules.append((
            QRegularExpression(r'\bgame\.\w+'), game_fmt
        ))

        # Numbers
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor('#b5cea8'))
        self._rules.append((
            QRegularExpression(r'\b\d+\.?\d*\b'), num_fmt
        ))

        # Strings (single and double quoted)
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor('#ce9178'))
        self._rules.append((
            QRegularExpression(r"'[^']*'"), str_fmt
        ))
        self._rules.append((
            QRegularExpression(r'"[^"]*"'), str_fmt
        ))

        # Comments
        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor('#6a9955'))

        # Function definitions
        func_fmt = QTextCharFormat()
        func_fmt.setForeground(QColor('#dcdcaa'))
        self._rules.append((
            QRegularExpression(r'\bdef\s+(\w+)'), func_fmt
        ))

    def highlightBlock(self, text):
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Comments (override everything after #)
        idx = text.find('#')
        if idx >= 0:
            # Make sure # isn't inside a string (simple check)
            in_single = text[:idx].count("'") % 2 == 1
            in_double = text[:idx].count('"') % 2 == 1
            if not in_single and not in_double:
                self.setFormat(idx, len(text) - idx, self._comment_fmt)


# ---------------------------------------------------------------------------
# Script templates
# ---------------------------------------------------------------------------

TEMPLATES = {
    'OnActivate': (
        'def OnActivate():\n'
        '    """Called when the entity is activated (touched/used)."""\n'
        '    '
    ),
    'OnSpawn': (
        'def OnSpawn():\n'
        '    """Called when the entity spawns into the map."""\n'
        '    '
    ),
    'OnPostInit': (
        'def OnPostInit():\n'
        '    """Called after the map finishes loading."""\n'
        '    '
    ),
    'Custom': '',
}


# ---------------------------------------------------------------------------
# Main editor window
# ---------------------------------------------------------------------------

class ScriptEditorWindow(QMainWindow):
    """Per-entity script editor with command palette."""

    saved = Signal(str)  # Emitted with relative script path after save

    def __init__(self, entity_name, script_key, script_path, game_data_dir,
                 game='xml2'):
        super().__init__()
        self._entity_name = entity_name
        self._script_key = script_key
        self._script_path = script_path
        self._game_data_dir = game_data_dir
        self._game = game

        self.setWindowTitle(f"Script Editor — {entity_name} ({script_key})")
        self.setMinimumSize(1000, 600)
        self.resize(1200, 700)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)

        self._build_ui()
        self._populate_palette()
        self._load_script()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._btn_save = QPushButton("Save")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        toolbar.addWidget(QLabel("Template:"))
        self._combo_template = QComboBox()
        self._combo_template.addItems(list(TEMPLATES.keys()))
        self._combo_template.currentTextChanged.connect(self._on_template_changed)
        toolbar.addWidget(self._combo_template)

        toolbar.addWidget(QLabel("Game:"))
        self._combo_game = QComboBox()
        self._combo_game.addItems(['XML2', 'MUA', 'Both'])
        idx = {'xml2': 0, 'mua': 1}.get(self._game, 2)
        self._combo_game.setCurrentIndex(idx)
        self._combo_game.currentTextChanged.connect(self._on_game_changed)
        toolbar.addWidget(self._combo_game)

        toolbar.addStretch()

        # File path display
        self._lbl_path = QLabel()
        self._lbl_path.setStyleSheet("color: #808080; font-size: 11px;")
        toolbar.addWidget(self._lbl_path)

        main_layout.addLayout(toolbar)

        # Splitter: palette | editor | help
        splitter = QSplitter(Qt.Horizontal)

        # Left — Command Palette
        palette_widget = QWidget()
        palette_layout = QVBoxLayout(palette_widget)
        palette_layout.setContentsMargins(0, 0, 0, 0)
        palette_layout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search commands...")
        self._search.textChanged.connect(self._on_search_changed)
        palette_layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Command", "Game"])
        self._tree.setColumnWidth(0, 180)
        self._tree.setColumnWidth(1, 40)
        self._tree.setRootIsDecorated(True)
        self._tree.itemClicked.connect(self._on_command_clicked)
        self._tree.currentItemChanged.connect(self._on_command_selected)
        palette_layout.addWidget(self._tree)

        splitter.addWidget(palette_widget)

        # Center — Script Editor
        self._editor = QPlainTextEdit()
        font = QFont('Consolas', 11)
        font.setStyleHint(QFont.Monospace)
        self._editor.setFont(font)
        self._editor.setTabStopDistance(
            self._editor.fontMetrics().horizontalAdvance(' ') * 4
        )
        self._editor.cursorPositionChanged.connect(self._update_status)
        self._highlighter = PythonHighlighter(self._editor.document())
        splitter.addWidget(self._editor)

        # Right — Command Help
        help_frame = QFrame()
        help_frame.setObjectName("help_panel")
        help_layout = QVBoxLayout(help_frame)
        help_layout.setContentsMargins(8, 8, 8, 8)

        help_title = QLabel("Command Reference")
        help_title.setStyleSheet(
            "color: #e0c040; font-size: 14px; font-weight: bold;"
        )
        help_layout.addWidget(help_title)

        self._help_label = QLabel(
            "<p style='color: #808080;'>Select a command from the palette "
            "to see its documentation.</p>"
        )
        self._help_label.setWordWrap(True)
        self._help_label.setTextFormat(Qt.RichText)
        self._help_label.setAlignment(Qt.AlignTop)
        help_layout.addWidget(self._help_label)
        help_layout.addStretch()

        splitter.addWidget(help_frame)

        # Splitter sizes: palette 250, editor 550, help 250
        splitter.setSizes([250, 550, 250])
        main_layout.addWidget(splitter)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._update_status()
        self._update_path_label()

        # Keyboard shortcuts
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save)

    # -- Palette population -------------------------------------------------

    def _populate_palette(self):
        """Fill the command tree from script_commands data."""
        from .script_commands import get_commands_by_category

        game_text = self._combo_game.currentText()
        game_filter = {'XML2': 'xml2', 'MUA': 'mua', 'Both': None}.get(
            game_text)

        self._tree.clear()
        categories = get_commands_by_category(game_filter)

        for cat_name, commands in categories.items():
            cat_item = QTreeWidgetItem(self._tree, [cat_name, ''])
            cat_item.setExpanded(False)
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsSelectable)
            # Category styling
            fmt = QFont()
            fmt.setBold(True)
            cat_item.setFont(0, fmt)
            cat_item.setForeground(0, QColor('#e0c040'))

            for cmd in commands:
                game_str = '/'.join(
                    sorted(g.upper() for g in cmd['games']))
                child = QTreeWidgetItem(cat_item, [cmd['name'], game_str])
                child.setData(0, Qt.UserRole, cmd)
                # Color MUA-only commands differently
                if cmd['games'] == {'mua'}:
                    child.setForeground(1, QColor('#ff6060'))
                elif cmd['games'] == {'xml2'}:
                    child.setForeground(1, QColor('#60a0ff'))

    # -- Event handlers -----------------------------------------------------

    def _on_command_clicked(self, item, column):
        """Insert command snippet at cursor when a command leaf is clicked."""
        cmd = item.data(0, Qt.UserRole)
        if cmd is None:
            return  # Category header clicked

        from .script_commands import get_snippet
        snippet = get_snippet(cmd)

        cursor = self._editor.textCursor()
        cursor.insertText(snippet)
        self._editor.setFocus()

    def _on_command_selected(self, current, previous):
        """Update help panel when command selection changes."""
        if current is None:
            return
        cmd = current.data(0, Qt.UserRole)
        if cmd is None:
            self._help_label.setText(
                "<p style='color: #808080;'>Select a command to see docs.</p>"
            )
            return

        from .script_commands import get_doc_html
        self._help_label.setText(get_doc_html(cmd))

    def _on_search_changed(self, text):
        """Filter palette tree by search text."""
        query = text.lower().strip()

        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            any_visible = False

            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                cmd = child.data(0, Qt.UserRole)
                if cmd is None:
                    continue

                visible = (not query
                           or query in cmd['name'].lower()
                           or query in cmd['desc'].lower()
                           or query in cmd['category'].lower())
                child.setHidden(not visible)
                if visible:
                    any_visible = True

            cat_item.setHidden(not any_visible)
            if any_visible and query:
                cat_item.setExpanded(True)

    def _on_game_changed(self, text):
        """Repopulate palette when game filter changes."""
        self._populate_palette()

    def _on_template_changed(self, template_name):
        """Replace editor content with selected template."""
        if template_name not in TEMPLATES:
            return

        current = self._editor.toPlainText().strip()
        if current:
            # Don't overwrite if there's content — only apply to empty editors
            # or if user explicitly wants it
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Replace Script",
                "Replace current script content with template?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._editor.setPlainText(TEMPLATES[template_name])
        # Move cursor to end
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._editor.setTextCursor(cursor)

    def _on_save(self):
        """Write script file to disk and emit saved signal."""
        file_path = self._get_file_path()
        if not file_path:
            self._status.showMessage("Error: No file path configured", 5000)
            return

        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        content = self._editor.toPlainText()
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        self._status.showMessage(f"Saved: {file_path}", 5000)
        self.saved.emit(self._script_path)

    # -- Script loading -----------------------------------------------------

    def _load_script(self):
        """Load existing script file or create from template."""
        file_path = self._get_file_path()

        if file_path and os.path.isfile(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._editor.setPlainText(content)
            self._combo_template.blockSignals(True)
            self._combo_template.setCurrentText('Custom')
            self._combo_template.blockSignals(False)
        else:
            # Pick template based on script key
            if self._script_key == 'spawnscript':
                template = 'OnSpawn'
            elif self._script_key == 'monster_actscript':
                template = 'OnActivate'
            else:
                template = 'OnActivate'

            self._editor.setPlainText(TEMPLATES.get(template, ''))
            self._combo_template.blockSignals(True)
            self._combo_template.setCurrentText(template)
            self._combo_template.blockSignals(False)

        # Move cursor to end
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._editor.setTextCursor(cursor)
        self._editor.setFocus()

    # -- Helpers ------------------------------------------------------------

    def _get_file_path(self):
        """Compute absolute path for the script file."""
        if not self._script_path or not self._game_data_dir:
            return None
        path = self._script_path
        if not path.endswith('.py'):
            path = path + '.py'
        if not path.startswith('scripts/') and not path.startswith('scripts\\'):
            path = os.path.join('scripts', path)
        return os.path.join(self._game_data_dir, path)

    def _update_status(self):
        """Update status bar with cursor position."""
        cursor = self._editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self._status.showMessage(f"Line {line}, Col {col}")

    def _update_path_label(self):
        """Update the file path display in toolbar."""
        fp = self._get_file_path()
        if fp:
            self._lbl_path.setText(fp)
        else:
            self._lbl_path.setText("(no file path)")

    def keyPressEvent(self, event):
        """Override Tab to insert 4 spaces in editor."""
        if (event.key() == Qt.Key_Tab
                and self._editor.hasFocus()
                and not event.modifiers()):
            self._editor.textCursor().insertText('    ')
            return
        super().keyPressEvent(event)
