"""Dashboard de escritorio para servicios y túneles SSH.

Qt gestiona presentación y planificación. core.py gestiona SSH y comandos.
tunnels.py mantiene las conexiones persistentes de reenvío.
Las operaciones de red se ejecutan fuera del hilo gráfico.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import SSH, action_command, config_directory, validate
from tunnels import TunnelInfo, TunnelManager
from ui_theme import STYLE

DATA = config_directory()
LOG = logging.getLogger("servercontrol.app")
VERSION = "1.0.0"


def button(text, fn, kind=""):
    control = QPushButton(text)
    control.setObjectName(kind)
    control.setCursor(Qt.PointingHandCursor)
    control.clicked.connect(fn)
    return control


def label(text, name=""):
    control = QLabel(text)
    control.setObjectName(name)
    return control


def panel(title=None):
    frame = QFrame()
    frame.setObjectName("panel")

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(15, 13, 15, 13)
    layout.setSpacing(10)

    if title:
        layout.addWidget(label(title, "heading"))

    return frame, layout


class HoverDelegate(QStyledItemDelegate):
    """Conserva los colores de estado al resaltar una fila."""

    def paint(self, painter, option, index):
        clean = QStyleOptionViewItem(option)
        clean.state &= ~(
            QStyle.State_Selected
            | QStyle.State_MouseOver
            | QStyle.State_HasFocus
        )
        super().paint(painter, clean, index)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~(
            QStyle.State_Selected
            | QStyle.State_MouseOver
            | QStyle.State_HasFocus
        )

        if index.row() == self.parent().hover_row:
            option.backgroundBrush = QColor("#084eb8")


class HoverTable(QTableWidget):
    """Mantiene el hover al pasar sobre los botones de una celda."""

    def __init__(self, *args):
        super().__init__(*args)
        self.hover_row = -1
        self.setMouseTracking(True)
        self.setItemDelegate(HoverDelegate(self))
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (
            QEvent.MouseMove,
            QEvent.Enter,
            QEvent.Leave,
            QEvent.Wheel,
        ):
            position = (
                event.globalPosition().toPoint()
                if event.type() == QEvent.MouseMove
                else QCursor.pos()
            )

            point = self.viewport().mapFromGlobal(position)

            widget = (
                obj
                if event.type() == QEvent.MouseMove
                and isinstance(obj, QWidget)
                else QApplication.widgetAt(position)
            )

            inside = widget is not None and (
                widget == self.viewport()
                or self.viewport().isAncestorOf(widget)
            )

            row = self.indexAt(point).row() if inside else -1

            if row != self.hover_row:
                self.hover_row = row
                self.viewport().update()

        return super().eventFilter(obj, event)


class Signals(QObject):
    finished = Signal(object, object)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = Signals()

    def run(self):
        try:
            result = self.fn()
        except Exception as exc:
            LOG.exception(
                "La operación en segundo plano ha fallado"
            )
            self.signals.finished.emit(None, str(exc))
        else:
            self.signals.finished.emit(result, None)


class CredentialsDialog(QDialog):
    def __init__(self, server, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conectar por SSH")
        self.resize(560, 300)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            label(
                f"{server['name']} · "
                f"{server['user']}@{server['host']}",
                "heading",
            )
        )

        layout.addWidget(label("Método de autenticación"))

        self.method = QComboBox()

        for title, mode in [
            ("Contraseña", "password"),
            ("Archivo de clave privada", "key"),
            ("Agente SSH / claves predeterminadas", "auto"),
        ]:
            self.method.addItem(title, mode)

        layout.addWidget(self.method)

        self.key_row = QWidget()
        row = QHBoxLayout(self.key_row)
        row.setContentsMargins(0, 0, 0, 0)

        self.key_path = QLineEdit(server.get("key", ""))
        self.key_path.setPlaceholderText(
            "Ruta al archivo de clave privada"
        )

        row.addWidget(self.key_path)
        row.addWidget(button("Examinar…", self.browse))
        layout.addWidget(self.key_row)

        self.secret_label = label("Contraseña")
        layout.addWidget(self.secret_label)

        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.secret)

        note = label(
            "La contraseña o frase de la clave se conserva "
            "solo en memoria.\n"
            "La selección se aplica a esta sesión.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )

        buttons.button(QDialogButtonBox.Ok).setText("Conectar")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.method.currentIndexChanged.connect(
            self.update_fields
        )

        mode = server.get(
            "auth_method",
            "key" if server.get("key") else "password",
        )
        self.method.setCurrentIndex(self.method.findData(mode))
        self.update_fields()

    def update_fields(self):
        mode = self.method.currentData()
        self.key_row.setVisible(mode == "key")

        self.secret_label.setText(
            "Frase de contraseña de la clave (opcional)"
            if mode == "key"
            else "Contraseña"
        )

        self.secret_label.setVisible(mode != "auto")
        self.secret.setVisible(mode != "auto")
        self.secret.clear()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar clave privada SSH",
            str(Path.home() / ".ssh"),
            "Todos los archivos (*)",
        )

        if path:
            self.key_path.setText(path)

    def accept(self):
        if self.method.currentData() == "key":
            path = Path(
                self.key_path.text().strip()
            ).expanduser()

            if (
                not self.key_path.text().strip()
                or not path.is_file()
            ):
                QMessageBox.warning(
                    self,
                    "Clave privada",
                    "Selecciona un archivo de clave privada existente.",
                )
                return

            try:
                with path.open("rb") as stream:
                    stream.read(1)

            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "Clave privada",
                    f"No se puede leer el archivo: {exc}",
                )
                return

            self.key_path.setText(str(path.resolve()))

        super().accept()


class ConfigDialog(QDialog):
    def __init__(self, servers, parent):
        super().__init__(parent)
        self.setWindowTitle("Configuración de servidores")
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        note = label(
            "Edita la lista JSON. Linux: systemd + Python 3. "
            "Windows: OpenSSH + PowerShell.\n"
            "Claves SSH verificadas en known_hosts. "
            "Las contraseñas se introducen en Conectar.\n"
            "Campos opcionales: port, auth_method, key, "
            "known_hosts, sudo (Linux).",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.editor = QPlainTextEdit(
            json.dumps(
                servers or EXAMPLE,
                indent=2,
                ensure_ascii=False,
            )
        )
        self.editor.setStyleSheet(
            'font-family:"Consolas","DejaVu Sans Mono";'
        )
        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            self.servers = validate(
                json.loads(self.editor.toPlainText())
            )

        except (ValueError, TypeError) as exc:
            QMessageBox.warning(
                self,
                "Configuración no válida",
                str(exc),
            )
            return

        self.accept()


EXAMPLE = [
    {
        "name": "web-01",
        "host": "192.168.1.10",
        "port": 22,
        "user": "operador",
        "os": "linux",
        "services": [
            "nginx",
            "docker",
            "postgresql",
            "redis",
        ],
        "sudo": True,
    },
    {
        "name": "win-01",
        "host": "192.168.1.50",
        "port": 22,
        "user": "operador",
        "os": "windows",
        "services": ["Spooler", "W32Time"],
    },
]

DEMO = [
    {
        "name": name,
        "host": f"192.168.1.{ip}",
        "user": "demo",
        "os": "linux",
        "services": services,
    }
    for name, ip, services in [
        ("web-01", 10, ["nginx", "docker", "postgresql", "redis"]),
        ("web-02", 11, ["nginx"]),
        ("db-01", 20, ["postgresql"]),
        ("db-02", 21, ["postgresql"]),
        ("app-01", 30, ["java"]),
        ("app-02", 31, ["java"]),
        ("cache-01", 40, ["redis"]),
        ("cache-02", 41, ["redis"]),
    ]
]


class Window(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()

        self.demo = demo
        self.servers = []
        self.snapshots = {}
        self.errors = {}
        self.passwords = {}
        self.busy = set()
        self.jobs = set()
        self.generation = 0
        self.demo_states = {}
        self.history = []
        self.cancel_events = {}
        self.disconnected = set()
        self.closing = False

        self.tunnel_manager = TunnelManager()
        self.demo_tunnels = []

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(6)

        self.setWindowTitle(
            f"ServerControl v{VERSION}"
            + (" — DEMOSTRACIÓN" if demo else "")
        )
        self.resize(1440, 920)
        self.setMinimumSize(1150, 740)

        if demo:
            # Copia independiente para no modificar los datos de ejemplo.
            self.servers = json.loads(json.dumps(DEMO))

        elif (DATA / "servers.json").exists():
            try:
                self.servers = validate(
                    json.loads(
                        (DATA / "servers.json").read_text(
                            encoding="utf-8"
                        )
                    )
                )

            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "Error de configuración",
                    str(exc),
                )

        self.build_ui()
        self.load_history()
        self.populate()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(15000)

        self.tunnel_timer = QTimer(self)
        self.tunnel_timer.timeout.connect(self.update_tunnels)
        self.tunnel_timer.start(1000)

        QTimer.singleShot(100, self.refresh)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("header")

        head = QHBoxLayout(header)
        head.setContentsMargins(24, 16, 24, 16)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(
            label("OPERACIONES DE SERVIDOR", "eyebrow")
        )
        titles.addWidget(label("ServerControl", "title"))

        head.addLayout(titles)
        head.addStretch()

        self.mode = label(
            "DEMOSTRACIÓN · sin conexiones reales"
            if self.demo
            else "SSH · identidad verificada",
            "badge",
        )

        head.addWidget(self.mode)
        head.addSpacing(12)
        head.addWidget(
            button("Configuración", self.configure)
        )
        outer.addWidget(header)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(170)
        sidebar.setMaximumWidth(200)

        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 22, 12, 18)
        side.setSpacing(6)
        side.addWidget(
            label("  ESPACIO DE TRABAJO", "eyebrow")
        )
        side.addSpacing(12)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []

        for index, text in enumerate(
            [
                "Resumen",
                "Servicios",
                "Recursos",
                "Logs",
                "Historial",
                "Túneles SSH",
            ]
        ):
            control = button(
                text,
                lambda checked=False, i=index:
                    self.tabs.setCurrentIndex(i),
                "nav",
            )
            control.setCheckable(True)
            self.nav_group.addButton(control, index)
            self.nav_buttons.append(control)
            side.addWidget(control)

        self.nav_buttons[0].setChecked(True)
        side.addStretch()

        quick, quick_layout = panel()
        quick_layout.addWidget(
            label("ACCESO RÁPIDO", "eyebrow")
        )
        quick_layout.addWidget(
            button("Actualizar", self.refresh)
        )
        quick_layout.addWidget(
            button("Ver logs", self.load_logs)
        )
        quick_layout.addWidget(
            button(
                "Crear túnel",
                lambda: self.tabs.setCurrentIndex(5),
            )
        )

        side.addWidget(quick)
        side.addSpacing(14)
        side.addWidget(
            label(
                f"  v{VERSION}  ·  Windows / Linux",
                "muted",
            )
        )
        split.addWidget(sidebar)

        inventory = QWidget()
        inventory.setObjectName("inventory")
        inventory.setMinimumWidth(335)

        inv = QVBoxLayout(inventory)
        inv.setContentsMargins(16, 22, 16, 18)
        inv.setSpacing(12)
        inv.addWidget(label("Servidores", "heading"))

        self.fleet = label(
            "Sin servidores configurados",
            "fleet",
        )
        inv.addWidget(self.fleet)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Buscar por nombre o dirección…"
        )
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_rows)
        inv.addWidget(self.search)

        filters = QHBoxLayout()

        self.filter = QComboBox()
        self.filter.addItems(
            [
                "Todos los estados",
                "Conectados",
                "Desconectados",
            ]
        )
        self.filter.currentTextChanged.connect(
            self.filter_rows
        )

        filters.addWidget(self.filter, 1)
        filters.addWidget(
            button("Actualizar", self.refresh)
        )
        inv.addLayout(filters)

        self.table = HoverTable(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Nombre", "IP / Host", "Estado", "Servicios"]
        )
        self.configure_table(self.table)

        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        # Los servicios se muestran en el panel de detalle.
        self.table.setColumnHidden(3, True)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(
            self.show_selected
        )
        inv.addWidget(self.table, 1)

        hint = label(
            "Selecciona un equipo para ver sus servicios,\n"
            "recursos y conexiones seguras.",
            "muted",
        )
        hint.setWordWrap(True)
        inv.addWidget(hint)

        self.connect_button = button(
            "Conectar / credenciales",
            self.credentials,
            "primary",
        )
        inv.addWidget(self.connect_button)

        self.disconnect_button = button(
            "Desconectar SSH",
            self.disconnect_ssh,
            "red",
        )
        inv.addWidget(self.disconnect_button)

        split.addWidget(inventory)

        detail = QWidget()
        detail.setMinimumWidth(560)

        details = QVBoxLayout(detail)
        details.setContentsMargins(20, 22, 20, 18)
        details.setSpacing(16)

        hero, hero_layout = panel()
        hero_layout.addWidget(
            label("SERVIDOR SELECCIONADO", "eyebrow")
        )

        self.server_title = label(
            "Selecciona un servidor",
            "title",
        )
        self.host_title = label(
            "Configura tus equipos para empezar",
            "muted",
        )
        self.host_title.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )

        hero_layout.addWidget(self.server_title)
        hero_layout.addWidget(self.host_title)
        details.addWidget(hero)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(
            self.sync_navigation
        )
        details.addWidget(self.tabs, 1)

        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(0, 14, 0, 0)
        summary_layout.setSpacing(14)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.metrics = {}

        for key, title, color in [
            ("cpu", "CPU", "#56c49a"),
            ("memory", "MEMORIA", "#6aa6ff"),
            ("disk", "DISCO", "#ae8cf2"),
        ]:
            frame, layout = panel()
            layout.addWidget(label(title, "eyebrow"))

            value = label("—", "metric")
            layout.addWidget(value)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setStyleSheet(
                f"QProgressBar::chunk {{background:{color};}}"
            )
            layout.addWidget(bar)

            self.metrics[key] = value, bar
            cards.addWidget(frame)

        summary_layout.addLayout(cards)

        self.service_table = self.make_services()
        frame, layout = panel("Servicios del sistema")
        layout.addWidget(self.service_table)
        summary_layout.addWidget(frame, 2)

        frame, layout = panel("Actividad reciente")
        layout.addWidget(
            button(
                "Ver logs del servicio seleccionado",
                self.load_logs,
            )
        )

        self.logs = self.make_log_view()
        self.logs.setPlaceholderText(
            "Selecciona un servicio y pulsa Ver logs."
        )
        layout.addWidget(self.logs)
        summary_layout.addWidget(frame, 1)

        self.tabs.addTab(summary, "Resumen")

        services = QWidget()
        services_layout = QVBoxLayout(services)
        services_layout.setContentsMargins(0, 15, 0, 0)
        services_layout.addWidget(
            label("Control de servicios", "heading")
        )

        self.services_full = self.make_services()
        services_layout.addWidget(self.services_full)
        self.tabs.addTab(services, "Servicios")

        self.resources = self.make_log_view()
        self.tabs.addTab(self.resources, "Recursos")

        logs = QWidget()
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(0, 15, 0, 0)
        logs_layout.addWidget(
            button(
                "Actualizar logs del servicio seleccionado",
                self.load_logs,
            )
        )

        self.logs_full = self.make_log_view()
        logs_layout.addWidget(self.logs_full)
        self.tabs.addTab(logs, "Logs")

        self.history_view = self.make_log_view()
        self.tabs.addTab(self.history_view, "Historial")
        self.tabs.addTab(
            self.make_tunnels_page(),
            "Túneles SSH",
        )

        split.addWidget(detail)
        split.setSizes([185, 390, 825])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 2)

        self.statusBar().showMessage(
            "Monitorización cada 15 s · "
            "operaciones SSH en segundo plano"
        )

    @staticmethod
    def configure_table(table):
        table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.horizontalHeader().setHighlightSections(False)

    @staticmethod
    def make_log_view():
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setStyleSheet(
            'font-family:"Consolas","DejaVu Sans Mono";'
            "font-size:11px;"
        )
        view.setMaximumBlockCount(5000)
        return view

    def sync_navigation(self, index):
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)

    def make_services(self):
        table = HoverTable(0, 3)
        table.setHorizontalHeaderLabels(
            ["Servicio", "Estado", "Acciones"]
        )
        self.configure_table(table)

        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )

        return table

    def make_tunnels_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(14)

        frame, form = panel(
            "Un puerto, una conexión segura"
        )

        note = label(
            "Accede a un servicio del servidor desde tu equipo. "
            "El túnel usa las credenciales SSH "
            "del servidor seleccionado.",
            "muted",
        )
        note.setWordWrap(True)
        form.addWidget(note)

        controls = QHBoxLayout()
        controls.addWidget(label("Puerto remoto"))

        self.tunnel_port = QSpinBox()
        self.tunnel_port.setRange(1, 65535)
        self.tunnel_port.setValue(8080)
        self.tunnel_port.setMinimumWidth(110)
        self.tunnel_port.setAccessibleName(
            "Puerto remoto del túnel SSH"
        )

        controls.addWidget(self.tunnel_port)
        controls.addStretch()

        self.create_tunnel_button = button(
            "Crear túnel",
            self.create_tunnel,
            "primary",
        )
        controls.addWidget(self.create_tunnel_button)
        form.addLayout(controls)

        self.tunnel_hint = label(
            "Destino: 127.0.0.1 en el servidor. "
            "Si el puerto local está ocupado, "
            "se elige otro automáticamente.",
            "muted",
        )
        self.tunnel_hint.setWordWrap(True)
        form.addWidget(self.tunnel_hint)
        layout.addWidget(frame)

        self.tunnel_table = QTableWidget(0, 3)
        self.tunnel_table.setHorizontalHeaderLabels(
            ["Acceso local", "Puerto remoto", "Estado"]
        )
        self.configure_table(self.tunnel_table)

        self.tunnel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.tunnel_table.setStyleSheet(
            "QTableWidget::item:selected "
            "{background:#234c7b;color:#edf4ff;}"
        )
        self.tunnel_table.itemSelectionChanged.connect(
            self.update_tunnel_actions
        )
        layout.addWidget(self.tunnel_table, 1)

        actions = QHBoxLayout()

        self.copy_tunnel_button = button(
            "Copiar dirección",
            self.copy_tunnel,
        )
        self.close_tunnel_button = button(
            "Cerrar seleccionado",
            self.close_tunnel,
            "red",
        )
        self.close_tunnels_button = button(
            "Cerrar todos del servidor",
            self.close_server_tunnels,
            "red",
        )

        actions.addWidget(self.copy_tunnel_button)
        actions.addStretch()
        actions.addWidget(self.close_tunnel_button)
        actions.addWidget(self.close_tunnels_button)
        layout.addLayout(actions)

        self.tunnel_feedback = label(
            "Selecciona un puerto y pulsa Crear túnel.",
            "muted",
        )
        self.tunnel_feedback.setWordWrap(True)
        self.tunnel_feedback.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        layout.addWidget(self.tunnel_feedback)

        note = label(
            "Los túneles se cierran al desconectar "
            "el servidor o salir. «Activo» confirma "
            "el enlace SSH; el servicio remoto se comprueba "
            "al usar la dirección local.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        return page

    def load_history(self):
        try:
            with (DATA / "history.jsonl").open(
                encoding="utf-8"
            ) as stream:
                for line in deque(stream, maxlen=500):
                    try:
                        item = json.loads(line)
                        self.history.append(
                            f"{item['time']} | "
                            f"{item['server']} | "
                            f"{item['action']} {item['service']} | "
                            + ("OK" if item["ok"] else "ERROR")
                            + (
                                " [DEMO]"
                                if item.get("demo")
                                else ""
                            )
                        )
                    except (
                        ValueError,
                        KeyError,
                        TypeError,
                    ):
                        continue

            self.history_view.setPlainText(
                "\n".join(self.history)
            )

        except OSError:
            pass

    def selected(self):
        row = self.table.currentRow()
        return (
            self.servers[row]
            if 0 <= row < len(self.servers)
            else None
        )

    def service(self):
        table = (
            self.services_full
            if self.tabs.currentIndex() == 1
            else self.service_table
        )
        item = table.item(table.currentRow(), 0)
        return item.text() if item else None

    def populate(self):
        self.table.setRowCount(len(self.servers))

        for row, server in enumerate(self.servers):
            self.table.setRowHeight(row, 49)

            values = [
                server["name"],
                server["host"],
                "Sin comprobar",
                ", ".join(server["services"]),
            ]

            for column, value in enumerate(values):
                self.table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )

        if self.servers:
            self.table.selectRow(0)

        self.show_selected()
        self.update_fleet()
        self.filter_rows()

    def update_fleet(self):
        connected = sum(
            server["name"] in self.snapshots
            and server["name"] not in self.errors
            for server in self.servers
        )

        self.fleet.setText(
            f"{len(self.servers)} equipos   ·   "
            f"{connected} conectados   ·   "
            f"{len(self.errors)} sin conexión"
        )

    def filter_rows(self):
        text = self.search.text().strip().casefold()

        for row, server in enumerate(self.servers):
            connected = (
                server["name"] in self.snapshots
                and server["name"] not in self.errors
            )

            hide = text not in (
                server["name"] + " " + server["host"]
            ).casefold()

            hide |= (
                self.filter.currentIndex() == 1
                and not connected
            )
            hide |= (
                self.filter.currentIndex() == 2
                and connected
            )

            self.table.setRowHidden(row, hide)

    def launch(self, key, fn, callback):
        """Evita duplicados y descarta respuestas de sesiones canceladas."""
        if self.closing or key in self.busy:
            return

        self.busy.add(key)
        generation = self.generation
        cancel = self.cancel_events.setdefault(
            key[1], Event()
        )

        job = Job(fn)
        self.jobs.add(job)

        def done(result, error):
            self.jobs.discard(job)
            self.busy.discard(key)

            # La función puede conservar una credencial en su closure.
            job.fn = None

            if (
                not self.closing
                and generation == self.generation
                and not cancel.is_set()
            ):
                callback(result, error)

            self.update_tunnels()

        # El callback se ejecuta en el hilo de Qt.
        job.signals.finished.connect(
            done, Qt.QueuedConnection
        )
        self.pool.start(job)

    def refresh(self):
        if self.closing:
            return

        for server in self.servers:
            name = server["name"]

            if (
                name in self.disconnected
                or ("op", name) in self.busy
            ):
                continue

            cancel = self.cancel_events.setdefault(
                name, Event()
            )
            password = self.passwords.get(name, "")
            demo = self.demo

            def poll(
                server=dict(server),
                cancel=cancel,
                password=password,
                demo=demo,
            ):
                if demo:
                    if server["name"] == "app-02":
                        raise ConnectionError(
                            "Demostración: servidor no disponible"
                        )

                    return {
                        "metrics": {
                            "cpu": 12,
                            "memory": 34,
                            "disk": 28,
                        },
                        "services": {
                            service: self.demo_states.get(
                                (server["name"], service),
                                "inactive"
                                if (
                                    service == "redis"
                                    and server["name"] == "web-01"
                                )
                                else "active",
                            )
                            for service in server["services"]
                        },
                        "warnings": [],
                    }

                with SSH(server, password, cancel) as ssh:
                    return ssh.snapshot()

            self.launch(
                ("poll", name),
                poll,
                lambda result, error, server=server:
                    self.received(server, result, error),
            )

    def received(self, server, result, error):
        name = server["name"]

        if error:
            self.errors[name] = error
            self.snapshots.pop(name, None)
        else:
            self.errors.pop(name, None)
            self.snapshots[name] = result

        row = self.servers.index(server)
        item = self.table.item(row, 2)

        item.setText(
            "● Desconectado" if error else "● Conectado"
        )
        item.setForeground(
            QColor("#ff5866" if error else "#00e890")
        )
        item.setToolTip(
            "Datos simulados"
            if self.demo
            else error or "Última consulta SSH verificada"
        )

        self.update_fleet()
        self.filter_rows()

        if self.selected() == server:
            self.show_selected()

    def show_selected(self):
        server = self.selected()

        self.connect_button.setEnabled(bool(server))
        self.disconnect_button.setEnabled(
            bool(server)
            and server["name"] not in self.disconnected
        )

        self.update_tunnels()

        tunnel_host = server["name"] if server else None

        if (
            getattr(self, "_tunnel_feedback_host", None)
            != tunnel_host
        ):
            self._tunnel_feedback_host = tunnel_host
            self.tunnel_feedback.setText(
                f"Túneles de {tunnel_host}. "
                "Selecciona un puerto y pulsa Crear túnel."
                if tunnel_host
                else "Configura un servidor para crear un túnel."
            )

        if not server:
            self.server_title.setText("Sin servidores")
            self.host_title.setText(
                "Abre Configuración para añadir equipos"
            )
            self.service_table.setRowCount(0)
            self.services_full.setRowCount(0)
            self.logs.clear()
            self.logs_full.clear()
            self.resources.clear()

            for value, bar in self.metrics.values():
                value.setText("—")
                bar.setValue(0)

            return

        data = self.snapshots.get(server["name"], {})

        state = (
            "Desconectado"
            if server["name"] in self.errors
            else "Conectado"
            if data
            else "Sin comprobar"
        )

        self.server_title.setText(server["name"])
        self.host_title.setText(
            f"{server['user']}@{server['host']}:"
            f"{server.get('port', 22)}   ·   "
            f"{server['os'].upper()}   ·   {state}"
        )

        for key, (value, bar) in self.metrics.items():
            number = data.get("metrics", {}).get(key)
            value.setText(
                "—" if number is None else f"{number:.0f}%"
            )
            bar.setValue(
                0
                if number is None
                else max(0, min(100, int(number)))
            )

        for table in (
            self.service_table,
            self.services_full,
        ):
            self.update_services(table, server, data)

        self.resources.setPlainText(
            "Métricas actuales\n\n"
            + json.dumps(data.get("metrics", {}), indent=2)
            + "\n\n"
            + "\n".join(data.get("warnings", []))
            + "\n"
            + self.errors.get(server["name"], "")
        )

        if getattr(self, "log_host", None) != server["name"]:
            self.logs.clear()
            self.logs_full.clear()
            self.log_host = server["name"]

        self.statusBar().showMessage(
            self.errors.get(server["name"])
            or (
                f"{server['name']}  ·  Última consulta: "
                f"{datetime.now():%H:%M:%S}"
            )
        )

    def update_services(self, table, server, data):
        """Reutiliza botones para no perder su estado visual en cada consulta."""
        previous = table.currentRow()

        service_key = (
            server["name"],
            tuple(server["services"]),
        )
        same_host = (
            getattr(table, "_service_key", None)
            == service_key
        )

        if not same_host:
            table.setRowCount(0)
            table._service_key = service_key

        table.setRowCount(len(server["services"]))

        for row, name in enumerate(server["services"]):
            table.setRowHeight(row, 46)

            if table.item(row, 0) is None:
                table.setItem(
                    row, 0, QTableWidgetItem(name)
                )
                table.setItem(
                    row, 1, QTableWidgetItem()
                )

                actions = QWidget()
                actions.setObjectName("actions")
                actions.setStyleSheet(
                    "QWidget#actions {background:transparent;}"
                )
                actions.setMouseTracking(True)

                layout = QHBoxLayout(actions)
                layout.setContentsMargins(3, 4, 3, 4)
                layout.setSpacing(4)

                for text, action, kind in [
                    ("Ver", "logs", ""),
                    ("Iniciar", "start", "green"),
                    ("Detener", "stop", "red"),
                ]:
                    control = QPushButton(text)
                    control.setObjectName(kind)
                    control.setCursor(Qt.PointingHandCursor)
                    control.setProperty("action", action)
                    control.setStyleSheet(
                        "padding:7px 8px;"
                    )
                    control.clicked.connect(
                        lambda checked=False,
                        service=name,
                        control=control: (
                            self.load_logs(service)
                            if control.property("action") == "logs"
                            else self.action(
                                control.property("action"),
                                service,
                            )
                        )
                    )
                    layout.addWidget(control)

                table.setCellWidget(row, 2, actions)

            state = data.get(
                "services", {}
            ).get(name, "desconocido")

            active = state.lower() in (
                "active", "running"
            )

            item = table.item(row, 1)
            item.setText(
                "● "
                + {
                    "active": "Activo",
                    "running": "Activo",
                    "inactive": "Inactivo",
                    "stopped": "Detenido",
                }.get(state.lower(), state)
            )
            item.setForeground(
                QColor("#00e890" if active else "#ff6975")
            )

            controls = table.cellWidget(
                row, 2
            ).findChildren(QPushButton)

            controls[1].setText(
                "Reiniciar" if active else "Iniciar"
            )
            controls[1].setProperty(
                "action",
                "restart" if active else "start",
            )

            for control in controls:
                control.setEnabled(
                    bool(data)
                    and server["name"] not in self.disconnected
                )

        if table.rowCount():
            table.selectRow(
                previous
                if (
                    same_host
                    and 0 <= previous < table.rowCount()
                )
                else 0
            )

    def load_logs(self, name=None):
        server = self.selected()
        name = (
            name
            if isinstance(name, str)
            else self.service()
        )

        if (
            not server
            or not name
            or server["name"] in self.disconnected
        ):
            return

        if self.demo:
            self.logs.setPlainText(
                f"[DEMO] {name}: servicio inicializado\n"
                "[DEMO] Muestra de logs; sin conexión real."
            )
            self.logs_full.setPlainText(
                self.logs.toPlainText()
            )
            return

        cancel = self.cancel_events.setdefault(
            server["name"], Event()
        )
        password = self.passwords.get(
            server["name"], ""
        )

        def fetch():
            with SSH(
                dict(server), password, cancel
            ) as ssh:
                return ssh.logs(name)

        def done(result, error):
            if self.selected() != server:
                return

            text = f"{name} — {server['name']}\n" + (
                error
                or result
                or "No hay registros visibles para este servicio."
            )

            self.logs.setPlainText(text)
            self.logs_full.setPlainText(text)

        self.launch(
            ("logs", server["name"]),
            fetch,
            done,
        )

    def action(self, action, name=None):
        server = self.selected()
        name = name or self.service()

        if (
            not server
            or not name
            or server["name"] in self.disconnected
        ):
            return

        if (
            ("poll", server["name"]) in self.busy
            or ("op", server["name"]) in self.busy
        ):
            self.statusBar().showMessage(
                "Espera a que termine la operación actual."
            )
            return

        verb = {
            "start": "Iniciar",
            "stop": "Detener",
            "restart": "Reiniciar",
        }[action]

        question = (
            f"{verb} {name} en {server['name']} "
            f"({server['host']})?"
        )

        if self.demo:
            question += (
                "\nModo demostración: acción simulada."
            )

        if QMessageBox.question(
            self,
            "Confirmar acción",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        cancel = self.cancel_events.setdefault(
            server["name"], Event()
        )
        password = self.passwords.get(
            server["name"], ""
        )
        demo = self.demo

        def execute():
            if demo:
                self.demo_states[
                    (server["name"], name)
                ] = (
                    "inactive"
                    if action == "stop"
                    else "active"
                )
                return "Simulado"

            with SSH(
                dict(server), password, cancel
            ) as ssh:
                return ssh.run(
                    action_command(
                        server, name, action
                    )
                )

        def done(result, error):
            entry = (
                f"{datetime.now().isoformat(timespec='seconds')} | "
                f"{server['name']} | {verb} {name} | "
                + (
                    "ERROR: " + error
                    if error
                    else "Comando completado; comprobando estado"
                )
            )

            self.history.append(entry)
            self.history = self.history[-500:]
            self.history_view.setPlainText(
                "\n".join(self.history)
            )

            try:
                DATA.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                with (DATA / "history.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        json.dumps(
                            {
                                "time": datetime.now().isoformat(),
                                "server": server["name"],
                                "service": name,
                                "action": action,
                                "ok": not bool(error),
                                "demo": demo,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            except OSError as exc:
                QMessageBox.warning(
                    self, "Historial", str(exc)
                )

            if error:
                QMessageBox.warning(
                    self, "Acción fallida", error
                )

            self.refresh()

        self.launch(
            ("op", server["name"]),
            execute,
            done,
        )

    def credentials(self):
        server = self.selected()

        if not server:
            return

        if any(
            key[1] == server["name"]
            for key in self.busy
        ):
            self.statusBar().showMessage(
                "Espera a que finalice la operación SSH "
                "pendiente antes de conectar."
            )
            return

        dialog = CredentialsDialog(server, self)

        if dialog.exec() != QDialog.Accepted:
            dialog.secret.clear()
            dialog.deleteLater()
            return

        mode = dialog.method.currentData()
        server["auth_method"] = mode

        if mode == "key":
            server["key"] = dialog.key_path.text()
        else:
            server.pop("key", None)

        self.cancel_events[server["name"]] = Event()
        self.disconnected.discard(server["name"])

        self.passwords[server["name"]] = (
            dialog.secret.text()
            if mode != "auto"
            else ""
        )

        dialog.secret.clear()
        dialog.deleteLater()

        self.show_selected()
        self.refresh()

    def disconnect_ssh(self):
        server = self.selected()

        if not server:
            return

        name = server["name"]

        self.disconnected.add(name)
        self.cancel_events.setdefault(
            name, Event()
        ).set()

        self.tunnel_manager.close_server(name)
        self.demo_tunnels = [
            info
            for info in self.demo_tunnels
            if info.server != name
        ]

        self.passwords.pop(name, None)
        self.logs.clear()
        self.logs_full.clear()

        self.received(
            server,
            None,
            "Desconectado manualmente. Pulsa "
            "Conectar / credenciales para reanudar.",
        )

        self.statusBar().showMessage(
            "SSH y túneles desconectados; "
            "monitorización suspendida."
        )
        self.update_tunnels()

    def tunnel_infos(self):
        server = self.selected()

        if not server:
            return []

        return (
            [
                info
                for info in self.demo_tunnels
                if info.server == server["name"]
            ]
            if self.demo
            else self.tunnel_manager.list(server["name"])
        )

    def update_tunnels(self):
        if not hasattr(self, "tunnel_table"):
            return

        server = self.selected()

        busy = bool(
            server
            and ("tunnel", server["name"]) in self.busy
        )
        enabled = (
            bool(server)
            and server["name"] not in self.disconnected
            and not self.closing
        )

        self.tunnel_port.setEnabled(
            enabled and not busy
        )
        self.create_tunnel_button.setEnabled(
            enabled and not busy
        )
        self.create_tunnel_button.setText(
            "Conectando…" if busy else "Crear túnel"
        )

        selected_id = self.selected_tunnel_id()
        infos = self.tunnel_infos()

        # Evita reconstruir la tabla y perder su selección en cada tick.
        signature = tuple(
            (
                info.id,
                info.local_port,
                info.state,
                info.error,
            )
            for info in infos
        )

        if signature != getattr(
            self, "_tunnel_signature", None
        ):
            self._tunnel_signature = signature
            self.tunnel_table.setRowCount(len(infos))

            for row, info in enumerate(infos):
                self.tunnel_table.setRowHeight(row, 45)

                endpoint = (
                    info.endpoint
                    if info.local_port
                    else "—"
                )

                values = [
                    endpoint,
                    str(info.remote_port),
                    {
                        "active": (
                            "Simulado"
                            if self.demo
                            else "Activo"
                        ),
                        "closed": "Cerrado",
                        "error": "Error",
                        "connecting": "Conectando…",
                    }.get(info.state, info.state),
                ]

                if info.error and info.state == "active":
                    values[2] = "Revisar destino"

                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, info.id)

                    item.setToolTip(
                        info.error
                        or (
                            "Simulación: no se abre ningún puerto."
                            if self.demo
                            else (
                                f"{info.endpoint} → "
                                f"{info.server}:127.0.0.1:"
                                f"{info.remote_port}"
                            )
                        )
                    )

                    self.tunnel_table.setItem(
                        row, column, item
                    )

                color = (
                    "#f0bd77"
                    if info.error
                    else "#00e890"
                    if info.state == "active"
                    else "#8ea2be"
                )

                self.tunnel_table.item(
                    row, 2
                ).setForeground(QColor(color))

                if info.id == selected_id:
                    self.tunnel_table.selectRow(row)

            if infos and not self.selected_tunnel_id():
                self.tunnel_table.selectRow(
                    len(infos) - 1
                )

        self.update_tunnel_actions()

    def selected_tunnel_id(self):
        item = self.tunnel_table.item(
            self.tunnel_table.currentRow(), 0
        )
        return item.data(Qt.UserRole) if item else None

    def update_tunnel_actions(self):
        info = next(
            (
                item
                for item in self.tunnel_infos()
                if item.id == self.selected_tunnel_id()
            ),
            None,
        )

        self.copy_tunnel_button.setEnabled(
            bool(
                info
                and info.state == "active"
                and not self.demo
            )
        )
        self.close_tunnel_button.setEnabled(
            bool(
                info
                and info.state in (
                    "active", "connecting"
                )
            )
        )
        self.close_tunnels_button.setEnabled(
            any(
                item.state in (
                    "active", "connecting"
                )
                for item in self.tunnel_infos()
            )
        )

    def create_tunnel(self):
        server = self.selected()

        if (
            not server
            or server["name"] in self.disconnected
            or self.closing
        ):
            return

        port = self.tunnel_port.value()

        if self.demo:
            duplicate = next(
                (
                    info
                    for info in self.demo_tunnels
                    if (
                        info.server == server["name"]
                        and info.remote_port == port
                    )
                ),
                None,
            )

            if not duplicate:
                self.demo_tunnels.append(
                    TunnelInfo(
                        id=f"demo-{server['name']}-{port}",
                        server=server["name"],
                        remote_port=port,
                        local_port=port,
                        state="active",
                        error="",
                    )
                )

            self.tunnel_feedback.setText(
                f"[DEMO] Túnel simulado a {server['name']}:"
                f"127.0.0.1:{port}. "
                "No se ha abierto ningún puerto local."
            )
            self.update_tunnels()
            return

        cancel = self.cancel_events.setdefault(
            server["name"], Event()
        )
        password = self.passwords.get(
            server["name"], ""
        )
        settings = dict(server)

        self.tunnel_feedback.setText(
            f"Conectando con {server['name']} "
            f"para abrir el puerto {port}…"
        )

        def create():
            return self.tunnel_manager.open(
                settings,
                port,
                password=password,
                cancel_event=cancel,
            )

        def done(info, error):
            if self.selected() != server:
                self.update_tunnels()
                return

            if error:
                self.tunnel_feedback.setText(
                    "No se pudo crear el túnel: "
                    + error
                )
            else:
                suffix = (
                    " Se asignó otro puerto local "
                    "porque el solicitado estaba ocupado."
                    if info.local_port != port
                    else ""
                )

                self.tunnel_feedback.setText(
                    f"Túnel creado: {info.endpoint} → "
                    f"{info.server}:127.0.0.1:{port}."
                    f"{suffix}"
                )

            self.update_tunnels()

        self.launch(
            ("tunnel", server["name"]),
            create,
            done,
        )
        self.update_tunnels()

    def copy_tunnel(self):
        info = next(
            (
                item
                for item in self.tunnel_infos()
                if item.id == self.selected_tunnel_id()
            ),
            None,
        )

        if (
            info
            and info.state == "active"
            and not self.demo
        ):
            QApplication.clipboard().setText(
                info.endpoint
            )
            self.tunnel_feedback.setText(
                f"Dirección copiada: {info.endpoint}"
            )

    def close_tunnel(self):
        tunnel_id = self.selected_tunnel_id()

        if not tunnel_id:
            return

        if self.demo:
            self.demo_tunnels = [
                info
                for info in self.demo_tunnels
                if info.id != tunnel_id
            ]
        else:
            self.tunnel_manager.close(tunnel_id)

        self.tunnel_feedback.setText(
            "Túnel cerrado."
        )
        self.update_tunnels()

    def close_server_tunnels(self):
        server = self.selected()

        if server:
            self.tunnel_manager.close_server(
                server["name"]
            )
            self.demo_tunnels = [
                info
                for info in self.demo_tunnels
                if info.server != server["name"]
            ]

            self.tunnel_feedback.setText(
                "Todos los túneles de este servidor "
                "están cerrados."
            )
            self.update_tunnels()

    def configure(self):
        if self.jobs:
            QMessageBox.information(
                self,
                "Operación en curso",
                "Espera a que finalicen las consultas SSH.",
            )
            return

        dialog = ConfigDialog(
            [] if self.demo else self.servers,
            self,
        )

        if dialog.exec() != QDialog.Accepted:
            return

        try:
            DATA.mkdir(
                parents=True,
                exist_ok=True,
            )
            temporary = DATA / "servers.tmp"

            temporary.write_text(
                json.dumps(
                    dialog.servers,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(
                DATA / "servers.json"
            )

        except OSError as exc:
            QMessageBox.warning(
                self,
                "No se pudo guardar",
                str(exc),
            )
            return

        self.tunnel_manager.close_all()
        self.demo_tunnels.clear()
        self.cancel_events.clear()
        self.disconnected.clear()

        self.generation += 1
        self.servers = dialog.servers
        self.demo = False

        self.setWindowTitle(
            f"ServerControl v{VERSION}"
        )
        self.mode.setText(
            "SSH · identidad verificada"
        )

        self.snapshots.clear()
        self.errors.clear()
        self.passwords.clear()

        self.populate()
        self.refresh()

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True
            self.timer.stop()
            self.tunnel_timer.stop()

            # Una autenticación pendiente no debe reactivar un túnel
            # después de que el usuario haya cerrado la ventana.
            for cancel in self.cancel_events.values():
                cancel.set()

            self.tunnel_manager.close_all()
            self.demo_tunnels.clear()
            self.passwords.clear()

        if self.jobs:
            self.statusBar().showMessage(
                "Cerrando conexiones SSH pendientes…"
            )
            event.ignore()
            QTimer.singleShot(200, self.close)
            return

        event.accept()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Dashboard de servicios y túneles SSH "
            "para Windows y Linux."
        )
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Datos simulados, sin conexiones SSH",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Registrar detalles de diagnóstico",
    )
    args = parser.parse_args()

    from diagnostics import configure_logging

    configure_logging(debug=args.debug)

    application = QApplication(sys.argv)
    application.setApplicationName("ServerControl")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)

    icon_path = Path(__file__).resolve().parent / "servercontrol.ico"
    application.setWindowIcon(QIcon(str(icon_path)))

    window = Window(args.demo)
    window.show()

    sys.exit(application.exec())


if __name__ == "__main__":
    main()