from __future__ import annotations


THEMES = {
    "dark": {
        "window": "#1b1d21",
        "panel": "#202329",
        "surface": "#25282e",
        "surface_alt": "#2d3138",
        "border": "#3d424a",
        "text": "#e3e5e8",
        "muted": "#a4a9b1",
        "accent": "#597590",
        "accent_hover": "#6986a2",
        "accent_pressed": "#48627a",
        "selected": "#354553",
        "disabled_bg": "#30343a",
        "disabled_text": "#747a83",
        "success": "#67a081",
        "error": "#c77b79",
        "warning": "#c2a45f",
        "button_text": "#f5f6f7",
    },
    "light": {
        "window": "#eef0f2",
        "panel": "#f7f8f9",
        "surface": "#ffffff",
        "surface_alt": "#e6e9ec",
        "border": "#c9ced4",
        "text": "#25282c",
        "muted": "#656b73",
        "accent": "#4c6e8b",
        "accent_hover": "#3f5e78",
        "accent_pressed": "#334f66",
        "selected": "#d8e2ea",
        "disabled_bg": "#e0e3e6",
        "disabled_text": "#979da5",
        "success": "#39785b",
        "error": "#a9504d",
        "warning": "#8a6a27",
        "button_text": "#ffffff",
    },
}


def normalize_theme(name: str) -> str:
    return name if name in THEMES else "dark"


def build_stylesheet(name: str) -> str:
    colors = THEMES[normalize_theme(name)]
    return f"""
QMainWindow, QDialog {{
    background: {colors['window']};
    color: {colors['text']};
}}
QWidget {{
    color: {colors['text']};
    font-family: "Segoe UI";
    font-size: 13px;
    selection-background-color: {colors['accent']};
    selection-color: {colors['button_text']};
}}
QStatusBar {{
    background: {colors['panel']};
    border-top: 1px solid {colors['border']};
    color: {colors['muted']};
}}
QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox {{
    background: {colors['surface']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 4px;
    padding: 6px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {colors['accent']};
}}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button {{
    border: 0;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background: {colors['surface']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    selection-background-color: {colors['selected']};
    selection-color: {colors['text']};
    outline: 0;
}}
QListWidget::item {{
    border-radius: 3px;
    padding: 5px;
}}
QListWidget::item:hover {{ background: {colors['surface_alt']}; }}
QListWidget::item:selected {{
    background: {colors['selected']};
    color: {colors['text']};
}}
QPushButton {{
    background: {colors['surface_alt']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 4px;
    padding: 7px 12px;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {colors['selected']};
    border-color: {colors['accent']};
}}
QPushButton:pressed {{
    background: {colors['accent_pressed']};
    color: {colors['button_text']};
}}
QPushButton:default {{
    background: {colors['accent']};
    color: {colors['button_text']};
    border-color: {colors['accent']};
}}
QPushButton:default:hover {{ background: {colors['accent_hover']}; }}
QPushButton:disabled {{
    background: {colors['disabled_bg']};
    color: {colors['disabled_text']};
    border-color: {colors['border']};
}}
QGroupBox {{
    background: transparent;
    border: 1px solid {colors['border']};
    border-radius: 5px;
    margin-top: 9px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {colors['muted']};
}}
QTabWidget::pane {{
    background: {colors['panel']};
    border: 1px solid {colors['border']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {colors['muted']};
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 9px 15px;
}}
QTabBar::tab:hover {{
    background: {colors['surface_alt']};
    color: {colors['text']};
}}
QTabBar::tab:selected {{
    background: {colors['panel']};
    color: {colors['text']};
    border-bottom-color: {colors['accent']};
}}
QSplitter::handle {{ background: {colors['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {colors['border']};
    border-radius: 3px;
    background: {colors['surface']};
}}
QCheckBox::indicator:checked {{
    background: {colors['accent']};
    border-color: {colors['accent']};
}}
QLabel#title {{
    color: {colors['text']};
    font-size: 24px;
    font-weight: 600;
}}
QLabel#guidance {{ color: {colors['warning']}; }}
QLabel#previewFrame {{
    background: {colors['surface']};
    border: 1px solid {colors['border']};
    border-radius: 5px;
    color: {colors['muted']};
}}
QLabel[envState="checking"] {{ color: {colors['muted']}; }}
QLabel[envState="ok"] {{ color: {colors['success']}; }}
QLabel[envState="error"] {{ color: {colors['error']}; }}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {colors['panel']};
    border: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {colors['border']};
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{
    background: {colors['surface']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    padding: 5px;
}}
"""
