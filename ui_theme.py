"""Estilos de Qt con estados explícitos para cada variante de botón.

Los selectores por nombre tienen más prioridad que los genéricos.
Por eso cada variante define sus propios estados hover, pressed y disabled.
"""

STYLE = """
QWidget {
    background:#0c1320;
    color:#e6edf7;
    font-family:"Segoe UI","DejaVu Sans";
    font-size:12px;
}
QMainWindow {background:#0c1320;}
QLabel {background:transparent;}
QLabel#title {
    font-size:25px;
    font-weight:700;
    letter-spacing:-0.5px;
}
QLabel#heading {font-size:16px;font-weight:600;}
QLabel#eyebrow {
    font-size:10px;
    font-weight:700;
    color:#78a4dc;
    letter-spacing:1.5px;
}
QLabel#muted {color:#91a3bc;}
QLabel#metric {font-size:31px;font-weight:600;}
QLabel#badge {
    color:#87baff;
    background:#142b47;
    border:1px solid #294b71;
    border-radius:12px;
    padding:6px 12px;
}
QLabel#fleet {
    color:#9fb4cf;
    background:#101d2e;
    border:1px solid #26394f;
    border-radius:7px;
    padding:10px;
}
QWidget#header {
    background:#101b2c;
    border-bottom:1px solid #24344b;
}
QWidget#sidebar {
    background:#101a2a;
    border-right:1px solid #24344b;
}
QWidget#inventory {background:#0d1726;}
QFrame#panel {
    border:1px solid #26384f;
    border-radius:10px;
    background:#111f30;
}
QPushButton {
    background:#1b2b41;
    border:1px solid #354d6c;
    border-radius:6px;
    padding:9px 12px;
    font-weight:500;
}
QPushButton:hover {background:#263e5d;border-color:#6daaff;}
QPushButton:pressed {background:#102139;border-color:#a5cdff;}
QPushButton:checked {background:#234c7b;border-color:#68a7ff;}
QPushButton:focus {border-color:#a7cdff;}
QPushButton:disabled {
    background:#152236;
    color:#61748e;
    border-color:#25374f;
}
QPushButton#primary {
    background:#2766cf;
    border-color:#4785e8;
    color:white;
}
QPushButton#primary:focus {border-color:#a7cdff;}
QPushButton#primary:hover {
    background:#347be8;
    border-color:#8abbff;
}
QPushButton#primary:pressed {
    background:#184fa8;
    border-color:#b6d5ff;
}
QPushButton#primary:checked {
    background:#2159ae;
    border-color:#89b9ff;
}
QPushButton#primary:disabled {
    background:#1c3558;
    color:#7186a5;
    border-color:#2b4769;
}
QPushButton#green {
    color:#81e6b3;
    border-color:#285d4c;
    background:#14392e;
}
QPushButton#green:focus {border-color:#a7e7c6;}
QPushButton#green:checked {
    background:#1b4c38;
    border-color:#75cfa6;
}
QPushButton#green:hover {
    background:#1d5440;
    border-color:#72d6aa;
    color:#c6ffe0;
}
QPushButton#green:pressed {
    background:#0d281e;
    border-color:#b9ffdc;
    color:white;
}
QPushButton#green:disabled {
    background:#142a27;
    color:#54786a;
    border-color:#29423b;
}
QPushButton#red {
    color:#f7a3ac;
    border-color:#6e3846;
    background:#39232f;
}
QPushButton#red:focus {border-color:#f3b5c0;}
QPushButton#red:checked {
    background:#5a2e3b;
    border-color:#d78999;
}
QPushButton#red:hover {
    background:#65313f;
    border-color:#f095a5;
    color:#ffced5;
}
QPushButton#red:pressed {
    background:#2c1622;
    border-color:#ffd0d8;
    color:white;
}
QPushButton#red:disabled {
    background:#2b222d;
    color:#7b6674;
    border-color:#45323f;
}
QPushButton#nav {
    text-align:left;
    padding:12px 15px;
    background:transparent;
    border:1px solid transparent;
    color:#a1b2cb;
}
QPushButton#nav:focus {border-color:#7eaddf;}
QPushButton#nav:hover {
    background:#1b304b;
    border-color:#34577e;
    color:white;
}
QPushButton#nav:pressed {
    background:#102640;
    border-color:#80b9ff;
    color:white;
}
QPushButton#nav:checked {
    background:#203e65;
    border-color:#37649b;
    color:#d7e9ff;
}
QPushButton#nav:checked:hover {
    background:#294e7b;
    border-color:#7ab2f8;
}
QPushButton#nav:checked:pressed {
    background:#142d4a;
    border-color:#bbdbff;
}
QPushButton#nav:disabled {
    background:transparent;
    color:#546983;
    border-color:transparent;
}
QLineEdit,QComboBox,QSpinBox,QPlainTextEdit {
    border:1px solid #304763;
    border-radius:6px;
    padding:8px;
    background:#0c1726;
    selection-background-color:#28568b;
}
QLineEdit:focus,QComboBox:focus,
QSpinBox:focus,QPlainTextEdit:focus {
    border-color:#689ee7;
}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled {
    color:#65758c;
    border-color:#23354c;
}
QComboBox::drop-down {border:0;width:22px;}
QSpinBox::up-button,QSpinBox::down-button {
    width:22px;
    border:0;
    background:#233c59;
}
QSpinBox::up-button:hover,QSpinBox::down-button:hover {
    background:#356493;
}
QTableWidget {
    background:#0d1726;
    alternate-background-color:#101d2e;
    border:1px solid #26384f;
    border-radius:7px;
    gridline-color:#1d2e44;
    selection-background-color:transparent;
}
QHeaderView::section {
    background:#17263a;
    padding:10px 7px;
    border:0;
    border-bottom:1px solid #2d415d;
    font-weight:600;
    color:#9db1cc;
}
QTableCornerButton::section {background:#17263a;border:0;}
QTabWidget::pane {border:0;}
QTabBar::tab {
    padding:12px 10px;
    border-bottom:2px solid #23354d;
    color:#91a6c2;
    background:transparent;
}
QTabBar::tab:hover {background:#172b43;color:#d2e6ff;}
QTabBar::tab:selected {
    border-bottom:2px solid #6aa6ff;
    color:#dbeaff;
    font-weight:600;
}
QProgressBar {
    border:0;
    background:#263950;
    border-radius:3px;
    max-height:6px;
    min-height:6px;
}
QProgressBar::chunk {
    background:#4bb991;
    border-radius:3px;
}
QSplitter::handle {background:#213249;width:1px;}
QScrollBar:vertical {
    background:#0e1929;
    width:9px;
    margin:0;
}
QScrollBar::handle:vertical {
    background:#344c6c;
    min-height:25px;
    border-radius:4px;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {height:0;}
QScrollBar:horizontal {background:#0e1929;height:9px;}
QScrollBar::handle:horizontal {
    background:#344c6c;
    min-width:25px;
    border-radius:4px;
}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {width:0;}
QStatusBar {
    background:#101b2c;
    border-top:1px solid #26394f;
    color:#9db2cd;
    font-size:11px;
}
QToolTip {
    background:#233a55;
    color:#eaf3ff;
    border:1px solid #5e85b4;
    padding:6px;
}
"""