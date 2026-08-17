# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

# Resolve junction so icon paths point to the real clone directory
_PLUGIN_DIR = os.path.realpath(os.path.dirname(__file__))

try:
    _RIGHT_DOCK = Qt.DockWidgetArea.RightDockWidgetArea
except AttributeError:
    _RIGHT_DOCK = Qt.RightDockWidgetArea


class SubCatchmentAssessmentPlugin:

    def __init__(self, iface):
        self.iface    = iface
        self.provider = None
        self.toolbar  = None
        self.dock     = None
        self._actions = []

    def initGui(self):
        from .provider import SubCatchmentProvider
        self.provider = SubCatchmentProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        self.toolbar = self.iface.addToolBar('Auckland Catchment & Hydrology')
        self.toolbar.setObjectName('AucklandCatchmentHydrologyToolbar')

        svg_path = os.path.join(_PLUGIN_DIR, 'icon.svg')
        png_path = os.path.join(_PLUGIN_DIR, 'icon.png')
        icon_path = svg_path if os.path.exists(svg_path) else png_path
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action = QAction(icon, 'Auckland Catchment & Hydrology', self.iface.mainWindow())
        action.setToolTip('Open / close the Auckland Catchment & Hydrology panel')
        action.setCheckable(True)
        action.triggered.connect(self._toggle_dock)
        self.toolbar.addAction(action)
        self.iface.addPluginToMenu('&Auckland Catchment & Hydrology', action)
        self._actions.append(action)

        from .dock import SubCatchmentDock
        self.dock = SubCatchmentDock(self.iface, self.iface.mainWindow())
        self.iface.addDockWidget(_RIGHT_DOCK, self.dock)

        self.dock.visibilityChanged.connect(action.setChecked)
        action.setChecked(True)

    def unload(self):
        for action in self._actions:
            self.iface.removePluginMenu('&Auckland Catchment & Hydrology', action)
            action.deleteLater()
        self._actions.clear()

        if self.toolbar:
            self.toolbar.deleteLater()
            self.toolbar = None

        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def _toggle_dock(self, checked):
        if self.dock:
            self.dock.setVisible(checked)
