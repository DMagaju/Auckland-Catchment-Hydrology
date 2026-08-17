# -*- coding: utf-8 -*-
"""
Dock tab — Stream and Catchment (two panels).

Panel 1 — Generate: runs Stream and Catchment Generator (r.watershed).
Panel 2 — Outlet Catchment: runs Outlet Catchment Generator (r.water.outlet)
           using the drainage raster produced by Panel 1. Outlet points can be
           selected from an existing layer OR drawn interactively on the map.

Author: Dipendra Magaju
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"

import os
import traceback

from qgis.PyQt.QtCore import Qt, QObject, pyqtSignal, QVariant
from qgis.PyQt.QtGui import QIcon, QColor, QCursor, QFont
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar,
    QDoubleSpinBox, QCheckBox, QLineEdit, QFrame, QScrollArea, QDialog, QApplication,
    QSizePolicy, QStackedWidget, QListWidget, QMessageBox,
)

from qgis.core import (
    QgsMapLayerProxyModel,
    QgsProject,
    QgsMapLayer,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsProcessingFeedback,
    QgsLineSymbol,
    QgsFillSymbol,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateTransform,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapTool, QgsVertexMarker

import processing


# ── Color constants ───────────────────────────────────────────────────────────
_NAV  = '#1A237E'
_BLUE = '#1565C0'

# ── Qt6 / Qt5 compat ─────────────────────────────────────────────────────────
try:
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _AlignCenter = Qt.AlignCenter                           # type: ignore

try:
    _AlignL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
except AttributeError:
    _AlignL = Qt.AlignLeft | Qt.AlignVCenter                # type: ignore

try:
    _NoFrame = QFrame.Shape.NoFrame
except AttributeError:
    _NoFrame = QFrame.NoFrame                               # type: ignore

try:
    _PointingHand = Qt.CursorShape.PointingHandCursor
except AttributeError:
    _PointingHand = Qt.PointingHandCursor                   # type: ignore

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
    _POINT_FILTER  = QgsMapLayerProxyModel.Filter.PointLayer
    _LINE_FILTER   = QgsMapLayerProxyModel.Filter.LineLayer
    _POLY_FILTER   = QgsMapLayerProxyModel.Filter.PolygonLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer      # type: ignore
    _POINT_FILTER  = QgsMapLayerProxyModel.PointLayer       # type: ignore
    _LINE_FILTER   = QgsMapLayerProxyModel.LineLayer        # type: ignore
    _POLY_FILTER   = QgsMapLayerProxyModel.PolygonLayer     # type: ignore

try:
    _sp_ignored = QSizePolicy.Policy.Ignored
    _sp_pref    = QSizePolicy.Policy.Preferred
    _sp_exp     = QSizePolicy.Policy.Expanding
except AttributeError:
    _sp_ignored = QSizePolicy.Ignored   # type: ignore
    _sp_pref    = QSizePolicy.Preferred # type: ignore
    _sp_exp     = QSizePolicy.Expanding # type: ignore

try:
    _Qt_StaysOnTop  = Qt.WindowType.WindowStaysOnTopHint
    _Qt_CrossCursor = Qt.CursorShape.CrossCursor
    _Qt_LeftButton  = Qt.MouseButton.LeftButton
    _Qt_RightButton = Qt.MouseButton.RightButton
except AttributeError:
    _Qt_StaysOnTop  = Qt.WindowStaysOnTopHint   # type: ignore
    _Qt_CrossCursor = Qt.CrossCursor            # type: ignore
    _Qt_LeftButton  = Qt.LeftButton             # type: ignore
    _Qt_RightButton = Qt.RightButton            # type: ignore

# ── Widget stylesheet ─────────────────────────────────────────────────────────
_WIDGET_STYLE = """
    QgsMapLayerComboBox, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {
        background-color: white;
        color: black;
        border: 1px solid #B0BEC5;
        border-radius: 3px;
        padding: 2px 4px;
        font-size: 11px;
    }
    QComboBox::drop-down { border: none; }
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 16px; }
"""

_SWITCH_ACTIVE = (
    f'QPushButton {{ background: {_BLUE}; color: white; border: none;'
    f'  border-radius: 3px; padding: 4px 8px; font-size: 11px; font-weight: bold; }}'
    f'QPushButton:hover {{ background: #1976D2; }}'
)
_SWITCH_INACTIVE = (
    'QPushButton { background: #ECEFF1; color: #546E7A; border: none;'
    '  border-radius: 3px; padding: 4px 8px; font-size: 11px; }'
    'QPushButton:hover { background: #CFD8DC; }'
)

_DRAW_BTN_STYLE = (
    'QPushButton { background: #2E7D32; color: white; border: none;'
    '  border-radius: 3px; padding: 2px 8px; font-size: 11px; font-weight: bold; }'
    'QPushButton:hover   { background: #388E3C; }'
    'QPushButton:pressed { background: #1B5E20; }'
)

# ── Help text ─────────────────────────────────────────────────────────────────
_HELP = {
    'gen_tool': (
        "Delineates a stream network and sub-catchment polygons from a LiDAR DEM "
        "in a single processing pass using GRASS r.watershed.\n\n"
        "Both outputs share the same flow-accumulation grid, so catchment boundaries "
        "are perfectly consistent with the stream network.\n\n"
        "A drainage direction raster is also saved — use it in the 'Outlet Catchment' "
        "panel to delineate catchments for specific outlet locations without re-running "
        "r.watershed.\n\n"
        "Prerequisites:\n"
        "• GRASS GIS must be installed and enabled under\n"
        "  Processing → Options → Providers → GRASS GIS\n"
        "• DEM must use a projected CRS in metres (e.g. EPSG:2193 NZTM)"
    ),
    'gen_params': (
        "Min Contributing Area (ha)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Minimum upstream drainage area required to initiate a stream. "
        "Controls stream density and the lower bound of sub-catchment size.\n"
        "• Smaller value → denser network, more sub-catchments\n"
        "• Larger value  → fewer, coarser catchments\n"
        "Typical range for LiDAR: 0.5 – 5 ha.\n\n"
        "Min Stream Length (m)\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Segments shorter than this are removed to clean up artefact streams "
        "in flat or poorly-conditioned DEM areas. Set to 0 to keep all segments."
    ),
    'out_tool': (
        "Delineates the catchment area draining to each outlet point, using the "
        "drainage direction raster produced by the 'Generate' panel.\n\n"
        "r.watershed does NOT re-run — only the fast r.water.outlet step executes "
        "once per outlet point.\n\n"
        "Workflow:\n"
        "1. Run 'Generate' to create the drainage direction raster.\n"
        "2. Define your outlet locations:\n"
        "   • Click '✏ Draw' to place outlets interactively on the map, OR\n"
        "   • Select an existing point layer from the combo.\n"
        "3. Select the drainage raster and click Run.\n\n"
        "Each outlet produces one catchment polygon. Nested outlets are supported."
    ),
    'out_params': (
        "Drainage Direction Raster\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The 'Drainage Direction' layer produced by the Generate panel. "
        "It encodes the D8 flow direction at every raster cell.\n\n"
        "Outlet Points\n"
        "━━━━━━━━━━━━━\n"
        "Point layer with one feature per outlet. Each point produces one "
        "catchment polygon. Use '✏ Draw' to digitise outlets interactively, "
        "or select an existing layer.\n\n"
        "Stream Network (optional)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The stream lines layer from the Generate panel. When provided with a "
        "snap tolerance > 0, outlet points are automatically snapped to the "
        "nearest stream centreline before delineation.\n\n"
        "Snap Tolerance (m)\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Maximum distance to move an outlet to the nearest stream. "
        "Set to 0 to disable snapping.\n\n"
        "Sub-catchments (optional)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The 'Sub-Catchments' polygon layer from the Generate panel. "
        "When provided, a second output layer ('Outlet Sub-Catchments') is "
        "produced containing the individual sub-catchment polygons that fall "
        "within each outlet's contributing area, each tagged with outlet_id."
    ),
}


# ── Module-level state ────────────────────────────────────────────────────────
# Keeps the draw session alive (prevents garbage collection) between signals.
_active_draw_session = None


# ─────────────────────────────────────────────────────────────────────────────
# Interactive draw session (ported from SCH_SubBasin_Generator_V01.py)
# ─────────────────────────────────────────────────────────────────────────────

class OutletDrawSession(QObject):
    """Manages an interactive outlet-drawing session on the QGIS map canvas."""

    layer_ready = pyqtSignal(object)   # emits QgsVectorLayer when session completes

    def __init__(self, canvas):
        super().__init__()
        self.canvas     = canvas
        self._points    = []   # list of QgsPointXY
        self._markers   = []   # list of QgsVertexMarker
        self._prev_tool = canvas.mapTool()
        self._panel     = None
        self._map_tool  = None
        self._mode_bar  = None

    def start(self):
        self._panel = OutletDrawPanel(self)
        self._panel.add_requested.connect(self._activate_place_tool)
        self._panel.undo_requested.connect(self._undo_last)
        self._panel.done_requested.connect(self._on_done)
        self._panel.cancel_requested.connect(self._cancel)
        self._panel.show()
        self._activate_place_tool()

    def _activate_place_tool(self):
        self._hide_mode_bar()
        self._map_tool = PlaceOutletTool(self.canvas)
        self._map_tool.point_placed.connect(self._on_point_placed)
        self._map_tool.cancelled.connect(self._on_place_cancelled)
        self.canvas.setMapTool(self._map_tool)
        self._mode_bar = PlaceModeBar(self.canvas)
        self._mode_bar.cancel_requested.connect(self._on_place_cancelled)

    def _hide_mode_bar(self):
        if self._mode_bar:
            self._mode_bar.hide()
            self._mode_bar.deleteLater()
            self._mode_bar = None

    def _on_point_placed(self, map_pt):
        self._hide_mode_bar()
        self.canvas.unsetMapTool(self._map_tool)
        self._points.append(map_pt)

        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(map_pt)
        marker.setColor(QColor(220, 50, 50))
        marker.setIconSize(14)
        marker.setIconType(QgsVertexMarker.ICON_CROSS)
        marker.setPenWidth(3)
        self._markers.append(marker)

        self._refresh_panel_list()
        self.canvas.refresh()

    def _on_place_cancelled(self):
        self._hide_mode_bar()
        if self._map_tool:
            self.canvas.unsetMapTool(self._map_tool)

    def _undo_last(self):
        if not self._points:
            return
        self._points.pop()
        marker = self._markers.pop()
        self.canvas.scene().removeItem(marker)
        try:
            import sip
            sip.delete(marker)
        except Exception:
            pass
        self._refresh_panel_list()
        self.canvas.refresh()

    def _refresh_panel_list(self):
        self._panel.update_list([
            f"Outlet {i + 1}  ({pt.x():.1f}, {pt.y():.1f})"
            for i, pt in enumerate(self._points)
        ])

    def _on_done(self):
        if not self._points:
            QMessageBox.warning(
                self._panel, "No Outlets",
                "Place at least one outlet point before completing."
            )
            return

        map_crs = self.canvas.mapSettings().destinationCrs()
        feats = []
        for i, pt in enumerate(self._points):
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPointXY(pt))
            f.setAttributes([i + 1])
            feats.append(f)

        # Replace any existing "Drawn Outlets" layer
        for old in QgsProject.instance().mapLayersByName("Drawn Outlets"):
            QgsProject.instance().removeMapLayer(old.id())

        mem_lyr = QgsVectorLayer(
            f"Point?crs={map_crs.authid()}&field=outlet_id:integer",
            "Drawn Outlets", "memory"
        )
        mem_lyr.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(mem_lyr)

        self._cleanup(restore_tool=True)
        self.layer_ready.emit(mem_lyr)

    def _cancel(self):
        self._cleanup(restore_tool=True)

    def _cleanup(self, restore_tool=False):
        for marker in self._markers:
            try:
                self.canvas.scene().removeItem(marker)
                import sip
                sip.delete(marker)
            except Exception:
                pass
        self._markers.clear()
        self._points.clear()
        self._hide_mode_bar()
        if restore_tool and self._prev_tool:
            self.canvas.setMapTool(self._prev_tool)
        if self._panel:
            self._panel.close()
            self._panel = None
        global _active_draw_session
        _active_draw_session = None


class OutletDrawPanel(QDialog):
    add_requested    = pyqtSignal()
    undo_requested   = pyqtSignal()
    done_requested   = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, session):
        super().__init__()
        self.setWindowTitle('Draw Outlets')
        self.setWindowFlags(self.windowFlags() | _Qt_StaysOnTop)
        self.setFixedWidth(300)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("<b>Draw Outlet Points</b>")
        title.setFont(QFont("Segoe UI", 11))
        layout.addWidget(title)

        instr = QLabel(
            "Click <b>Add Outlet</b>, then click on the stream\n"
            "network to place each outlet point.\n\n"
            "Click <b>Done</b> when finished."
        )
        instr.setWordWrap(True)
        instr.setStyleSheet("color: #444;")
        layout.addWidget(instr)

        layout.addWidget(QLabel("<b>Placed outlets:</b>"))
        self._list = QListWidget()
        self._list.setFixedHeight(110)
        self._list.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("➕  Add Outlet")
        self._btn_add.setStyleSheet("padding: 5px;")
        self._btn_add.clicked.connect(self.add_requested.emit)
        self._btn_undo = QPushButton("↩  Undo Last")
        self._btn_undo.setStyleSheet("padding: 5px;")
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_undo)
        layout.addLayout(btn_row)

        layout.addSpacing(2)
        btn_done = QPushButton("✔  Done")
        btn_done.setStyleSheet(
            "QPushButton { background: #2E7D32; color: white; padding: 7px;"
            "  border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #388E3C; }"
        )
        btn_done.clicked.connect(self.done_requested.emit)
        layout.addWidget(btn_done)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("padding: 5px;")
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(btn_cancel)

    def update_list(self, items):
        self._list.clear()
        for item in items:
            self._list.addItem(item)

    def closeEvent(self, event):
        self.cancel_requested.emit()
        event.accept()


class PlaceOutletTool(QgsMapTool):
    point_placed = pyqtSignal(object)
    cancelled    = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(QCursor(_Qt_CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == _Qt_LeftButton:
            self.point_placed.emit(self.toMapCoordinates(event.pos()))
        elif event.button() == _Qt_RightButton:
            self.cancelled.emit()


class PlaceModeBar(QFrame):
    cancel_requested = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        try:
            self.setFrameShape(QFrame.Shape.StyledPanel)
        except AttributeError:
            self.setFrameShape(QFrame.StyledPanel)    # type: ignore
        self.setStyleSheet(
            "QFrame { background: rgba(30,30,30,210); border-radius: 6px; }"
            "QLabel { color: white; font-weight: bold; }"
            "QPushButton { color: white; background: #444; border: 1px solid #666;"
            "  border-radius: 3px; padding: 3px 10px; }"
            "QPushButton:hover { background: #0078d4; border-color: #0078d4; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(12)
        row.addWidget(QLabel("Click on the stream to place an outlet  |  Right-click to cancel"))
        btn = QPushButton("Cancel")
        btn.clicked.connect(self.cancel_requested.emit)
        row.addWidget(btn)
        self.adjustSize()
        x = (canvas.width()  - self.width())  // 2
        y =  canvas.height() - self.height() - 14
        self.move(x, y)
        self.show()
        self.raise_()


# ── Shared feedback class ─────────────────────────────────────────────────────
class _AlgoFeedback(QgsProcessingFeedback):
    def __init__(self, bar, lbl):
        super().__init__()
        self._bar = bar
        self._lbl = lbl

    def setProgress(self, v):
        self._bar.setValue(int(v))
        QApplication.processEvents()

    def pushInfo(self, msg):
        if msg.strip():
            self._lbl.setText(msg.strip()[-120:])
            QApplication.processEvents()

    def pushWarning(self, msg):
        self._lbl.setText(f'⚠ {msg.strip()[-115:]}')
        QApplication.processEvents()

    def reportError(self, error, fatal=False):
        if fatal:
            self._lbl.setText(f'✗ {error.strip()[-115:]}')
            QApplication.processEvents()


# ── Shared UI helpers ─────────────────────────────────────────────────────────
class _HelpDialog:
    def __init__(self, title, text, parent=None):
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(title.strip())
        self._dlg.setMinimumWidth(440)
        self._dlg.setMaximumWidth(560)
        self._dlg.setSizeGripEnabled(True)
        self._dlg.setStyleSheet("""
            QDialog, QWidget { background-color: #FAFAFA; color: #212121; }
            QScrollArea      { background: #FAFAFA; border: none; }
            QLabel           { background: transparent; color: #212121; font-size: 11px; }
            QPushButton {
                background: #1565C0; color: white; border: none;
                border-radius: 4px; padding: 5px 20px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #1976D2; }
        """)
        root = QVBoxLayout(self._dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f'background: {_NAV};')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 14, 0)
        lbl = QLabel(title.strip())
        lbl.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        bl.addWidget(lbl)
        root.addWidget(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        body = QWidget()
        body.setStyleSheet('background: #FAFAFA;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(16, 14, 16, 10)
        txt = QLabel(text)
        txt.setWordWrap(True)
        txt.setStyleSheet('font-size: 11px; color: #212121; background: transparent;')
        bv.addWidget(txt)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        foot = QWidget()
        foot.setStyleSheet('background: #F0F0F0; border-top: 1px solid #DCDCDC;')
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 8, 14, 10)
        fl.addStretch()
        ok = QPushButton('OK')
        ok.setFixedWidth(80)
        ok.clicked.connect(self._dlg.accept)
        fl.addWidget(ok)
        root.addWidget(foot)

        txt.adjustSize()
        preferred = txt.sizeHint().height() + 36 + 56 + 28
        self._dlg.resize(460, min(preferred, 500))

    def exec(self):
        self._dlg.exec()


def _help_btn(title, text, anchor, size=18):
    btn = QToolButton()
    btn.setText('?')
    btn.setFixedSize(size, size)
    btn.setCursor(_PointingHand)
    btn.setStyleSheet(
        f'QToolButton {{'
        f'  background: rgba(255,255,255,0.22); color: white;'
        f'  border: 1px solid rgba(255,255,255,0.50);'
        f'  border-radius: {size // 2}px;'
        f'  font-size: {size - 8}px; font-weight: bold; padding: 0;'
        f'}}'
        f'QToolButton:hover {{ background: rgba(255,255,255,0.45); }}'
    )
    btn.clicked.connect(lambda: _HelpDialog(title, text, anchor).exec())
    return btn


def _section(title, color, body_color, help_text=''):
    outer = QWidget()
    outer.setObjectName('section')
    outer.setStyleSheet(
        f'QWidget#section {{ background: {body_color};'
        f'  border: 1px solid {color}44; border-radius: 6px; }}'
    )
    v = QVBoxLayout(outer)
    v.setContentsMargins(0, 0, 0, 8)
    v.setSpacing(0)
    bar = QWidget()
    bar.setFixedHeight(28)
    bar.setStyleSheet(f'background: {color}; border-radius: 5px 5px 0 0;')
    bl = QHBoxLayout(bar)
    bl.setContentsMargins(10, 0, 10, 0)
    lbl = QLabel(title)
    lbl.setStyleSheet('color: white; font-weight: bold; font-size: 11px; background: transparent;')
    bl.addWidget(lbl)
    bl.addStretch()
    if help_text:
        bl.addWidget(_help_btn(title.strip(), help_text, bar))
        bl.addSpacing(2)
    v.addWidget(bar)
    inner = QWidget()
    inner.setStyleSheet('background: transparent;')
    grid = QGridLayout(inner)
    grid.setContentsMargins(12, 8, 12, 4)
    grid.setVerticalSpacing(6)
    grid.setHorizontalSpacing(8)
    grid.setColumnStretch(1, 1)
    v.addWidget(inner)
    return outer, grid


def _row_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet('font-size: 11px; background: transparent;')
    lbl.setAlignment(_AlignL)
    return lbl


def _make_footer(run_label, run_cb, parent):
    """Build progress + status + run button footer; returns (widget, progress, status, btn)."""
    footer = QWidget(parent)
    footer.setStyleSheet('background: #ECEFF1; border-top: 1px solid #CFD8DC;')
    fv = QVBoxLayout(footer)
    fv.setContentsMargins(14, 8, 14, 12)
    fv.setSpacing(6)

    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setValue(0)
    progress.setFixedHeight(6)
    progress.setTextVisible(False)
    progress.setStyleSheet(
        f'QProgressBar {{ background: #CFD8DC; border-radius: 3px; border: none; }}'
        f'QProgressBar::chunk {{ background: {_BLUE}; border-radius: 3px; }}'
    )
    fv.addWidget(progress)

    btn_row = QHBoxLayout()
    status = QLabel('Ready.')
    status.setStyleSheet('font-size: 10px; color: #546E7A;')
    status.setSizePolicy(_sp_ignored, _sp_pref)
    btn_row.addWidget(status, 1)

    btn = QPushButton(run_label)
    btn.setFixedWidth(100)
    btn.setStyleSheet(
        f'QPushButton {{ background: {_BLUE}; color: white; border-radius: 4px;'
        f'  padding: 6px 14px; font-size: 12px; font-weight: bold; }}'
        f'QPushButton:hover   {{ background: #1976D2; }}'
        f'QPushButton:pressed {{ background: #0D47A1; }}'
        f'QPushButton:disabled {{ background: #90A4AE; }}'
    )
    btn.clicked.connect(run_cb)
    btn_row.addWidget(btn)
    fv.addLayout(btn_row)

    return footer, progress, status, btn


# ─────────────────────────────────────────────────────────────────────────────
class StreamCatchmentWidget(QWidget):

    _ALGO_GENERATE = 'subcatchmentassessment:stream_catchment_generator'
    _ALGO_OUTLET   = 'subcatchmentassessment:outlet_catchment_generator'

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet(_WIDGET_STYLE)
        self._build()

    # ── Top-level construction ────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(40)
        header.setStyleSheet(f'background: {_NAV};')
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 0, 10, 0)
        icon_dir  = os.path.join(os.path.dirname(__file__), '..')
        icon_path = os.path.join(icon_dir, 'icon.svg')
        if not os.path.exists(icon_path):
            icon_path = os.path.join(icon_dir, 'icon.png')
        if os.path.exists(icon_path):
            ic = QLabel()
            ic.setPixmap(QIcon(icon_path).pixmap(24, 24))
            ic.setFixedSize(26, 26)
            ic.setStyleSheet('background: transparent;')
            hl.addWidget(ic)
            hl.addSpacing(6)
        t = QLabel('Stream and Catchment')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        root.addWidget(header)

        # Panel switcher
        switcher = QWidget()
        switcher.setFixedHeight(34)
        switcher.setStyleSheet(f'background: {_NAV};')
        sw = QHBoxLayout(switcher)
        sw.setContentsMargins(8, 4, 8, 4)
        sw.setSpacing(4)
        self._btn_gen = QPushButton('Generate')
        self._btn_out = QPushButton('Outlet Catchment')
        for b in (self._btn_gen, self._btn_out):
            b.setFixedHeight(24)
            sw.addWidget(b, 1)
        root.addWidget(switcher)

        # Stacked panels
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_generate_panel())
        self._stack.addWidget(self._build_outlet_panel())
        root.addWidget(self._stack, 1)

        self._btn_gen.clicked.connect(lambda: self._switch(0))
        self._btn_out.clicked.connect(lambda: self._switch(1))
        self._switch(0)

    def _switch(self, idx):
        self._stack.setCurrentIndex(idx)
        self._btn_gen.setStyleSheet(_SWITCH_ACTIVE   if idx == 0 else _SWITCH_INACTIVE)
        self._btn_out.setStyleSheet(_SWITCH_ACTIVE   if idx == 1 else _SWITCH_INACTIVE)
        if idx == 1:
            self._refresh_outlet_inputs()

    # ── Generate panel ────────────────────────────────────────────────────────

    def _build_generate_panel(self):
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        scroll.setStyleSheet('background: #F5F5F5;')

        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_lbl = QLabel('Stream and Catchment Generator')
        hdr_lbl.setStyleSheet(f'color: {_NAV}; font-size: 11px; font-weight: bold; background: transparent;')
        hdr_row.addWidget(hdr_lbl, 1)
        hdr_row.addWidget(_help_btn('Stream and Catchment Generator', _HELP['gen_tool'], body))
        bv.addLayout(hdr_row)

        sec, g = _section('  Parameters', _BLUE, '#EEF4FF', _HELP['gen_params'])
        row = 0

        g.addWidget(_row_label('LiDAR DEM'), row, 0)
        self._gen_dem = QgsMapLayerComboBox()
        self._gen_dem.setFilters(_RASTER_FILTER)
        self._gen_dem.setAllowEmptyLayer(True)
        self._gen_dem.setCurrentIndex(0)
        g.addWidget(self._gen_dem, row, 1); row += 1

        g.addWidget(_row_label('Min contributing area'), row, 0)
        self._gen_threshold = QDoubleSpinBox()
        self._gen_threshold.setRange(0.01, 10000.0)
        self._gen_threshold.setValue(1.0)
        self._gen_threshold.setDecimals(2)
        self._gen_threshold.setSuffix('  ha')
        g.addWidget(self._gen_threshold, row, 1); row += 1

        g.addWidget(_row_label('Min stream length'), row, 0)
        self._gen_min_length = QDoubleSpinBox()
        self._gen_min_length.setRange(0.0, 100000.0)
        self._gen_min_length.setValue(0.0)
        self._gen_min_length.setDecimals(1)
        self._gen_min_length.setSuffix('  m  (0 = keep all)')
        g.addWidget(self._gen_min_length, row, 1); row += 1

        g.addWidget(_row_label('Assign SubcatchID'), row, 0)
        self._gen_assign_ids = QCheckBox('Add unique ID to each subcatchment')
        self._gen_assign_ids.setChecked(True)
        self._gen_assign_ids.setStyleSheet('font-size: 11px; background: transparent;')
        g.addWidget(self._gen_assign_ids, row, 1); row += 1

        self._lbl_prefix = _row_label('ID Prefix')
        g.addWidget(self._lbl_prefix, row, 0)
        self._gen_prefix = QLineEdit('SC')
        self._gen_prefix.setMaxLength(20)
        self._gen_prefix.setPlaceholderText('e.g. SC → SC_0001')
        self._gen_prefix.setStyleSheet(
            'background-color: white; color: black; border: 1px solid #B0BEC5;'
            'border-radius: 3px; padding: 2px 4px; font-size: 11px;'
        )
        g.addWidget(self._gen_prefix, row, 1)

        self._gen_assign_ids.toggled.connect(self._on_assign_ids_toggled)
        self._on_assign_ids_toggled(self._gen_assign_ids.isChecked())

        bv.addWidget(sec)
        bv.addStretch(1)
        scroll.setWidget(body)
        pv.addWidget(scroll, 1)

        footer, self._gen_progress, self._gen_status, self._gen_run = _make_footer(
            '  ▶  Run', self._run_generate, panel
        )

        try:
            from ..algorithms.Subcatch_StreamCatchment import __version__ as _ver
        except Exception:
            _ver = __version__
        try:
            from qgis.core import Qgis
            _qv = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qv = '-'
        ver_lbl = QLabel(f'Developer: D Magaju   ·   Version: {_ver}   ·   QGIS: {_qv}')
        ver_lbl.setAlignment(_AlignCenter)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        footer.layout().addWidget(ver_lbl)

        pv.addWidget(footer)
        return panel

    # ── Assign IDs toggle ─────────────────────────────────────────────────────

    def _on_assign_ids_toggled(self, checked):
        self._lbl_prefix.setVisible(checked)
        self._gen_prefix.setVisible(checked)

    # ── Outlet Catchment panel ────────────────────────────────────────────────

    def _build_outlet_panel(self):
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        scroll.setStyleSheet('background: #F5F5F5;')

        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_lbl = QLabel('Outlet Catchment Generator')
        hdr_lbl.setStyleSheet(f'color: {_NAV}; font-size: 11px; font-weight: bold; background: transparent;')
        hdr_row.addWidget(hdr_lbl, 1)
        hdr_row.addWidget(_help_btn('Outlet Catchment Generator', _HELP['out_tool'], body))
        bv.addLayout(hdr_row)

        sec, g = _section('  Parameters', _BLUE, '#EEF4FF', _HELP['out_params'])
        row = 0

        g.addWidget(_row_label('Drainage direction raster'), row, 0)
        self._out_drainage = QgsMapLayerComboBox()
        self._out_drainage.setFilters(_RASTER_FILTER)
        self._out_drainage.setAllowEmptyLayer(True)
        self._out_drainage.setCurrentIndex(0)
        g.addWidget(self._out_drainage, row, 1); row += 1

        # Outlet points row — combo + Draw button side by side
        g.addWidget(_row_label('Outlet points'), row, 0)
        outlets_cell = QWidget()
        outlets_cell.setStyleSheet('background: transparent;')
        oc = QHBoxLayout(outlets_cell)
        oc.setContentsMargins(0, 0, 0, 0)
        oc.setSpacing(4)
        self._out_outlets = QgsMapLayerComboBox()
        self._out_outlets.setFilters(_POINT_FILTER)
        self._out_outlets.setAllowEmptyLayer(True)
        self._out_outlets.setCurrentIndex(0)
        oc.addWidget(self._out_outlets, 1)
        self._draw_btn = QPushButton('✏ Draw')
        self._draw_btn.setFixedWidth(62)
        self._draw_btn.setFixedHeight(26)
        self._draw_btn.setStyleSheet(_DRAW_BTN_STYLE)
        self._draw_btn.setToolTip(
            'Draw outlet points interactively on the map.\n'
            'A "Drawn Outlets" layer will be created and auto-selected.'
        )
        self._draw_btn.clicked.connect(self._start_draw_session)
        oc.addWidget(self._draw_btn)
        g.addWidget(outlets_cell, row, 1); row += 1

        g.addWidget(_row_label('Stream network (optional)'), row, 0)
        self._out_streams = QgsMapLayerComboBox()
        self._out_streams.setFilters(_LINE_FILTER)
        self._out_streams.setAllowEmptyLayer(True)
        self._out_streams.setCurrentIndex(0)
        g.addWidget(self._out_streams, row, 1); row += 1

        g.addWidget(_row_label('Snap tolerance'), row, 0)
        self._out_snap = QDoubleSpinBox()
        self._out_snap.setRange(0.0, 10000.0)
        self._out_snap.setValue(0.0)
        self._out_snap.setDecimals(1)
        self._out_snap.setSuffix('  m  (0 = no snapping)')
        self._out_snap.setToolTip(
            'Snap outlets to nearest stream line within this distance.\n'
            'Set to 0 to use outlet coordinates exactly.'
        )
        g.addWidget(self._out_snap, row, 1); row += 1

        g.addWidget(_row_label('Sub-catchments (optional)'), row, 0)
        self._out_subcatchments = QgsMapLayerComboBox()
        self._out_subcatchments.setFilters(_POLY_FILTER)
        self._out_subcatchments.setAllowEmptyLayer(True)
        self._out_subcatchments.setCurrentIndex(0)
        self._out_subcatchments.setToolTip(
            'Sub-catchment polygons from the Generate panel.\n'
            'When provided, produces a second output with the individual\n'
            'sub-catchments within each outlet\'s contributing area.'
        )
        g.addWidget(self._out_subcatchments, row, 1)

        bv.addWidget(sec)
        bv.addStretch(1)
        scroll.setWidget(body)
        pv.addWidget(scroll, 1)

        footer, self._out_progress, self._out_status, self._out_run = _make_footer(
            '  ▶  Run', self._run_outlet, panel
        )
        pv.addWidget(footer)
        return panel

    # ── Draw session ──────────────────────────────────────────────────────────

    def _start_draw_session(self):
        global _active_draw_session
        if _active_draw_session is not None:
            self._out_status.setText('Drawing already active — use the panel on screen.')
            return

        canvas = self.iface.mapCanvas()
        session = OutletDrawSession(canvas)
        session.layer_ready.connect(self._on_draw_complete)
        _active_draw_session = session
        session.start()
        self._out_status.setText('Drawing mode active — click on the stream to place outlets.')

    def _on_draw_complete(self, layer):
        """Auto-select the newly drawn layer in the outlet combo."""
        self._out_outlets.setLayer(layer)
        n = layer.featureCount()
        self._out_status.setText(
            f'{n} outlet{"s" if n != 1 else ""} drawn and selected — click Run to delineate.'
        )

    # ── Run: Generate ─────────────────────────────────────────────────────────

    def _refresh_outlet_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self._out_drainage,      'drainage')
            set_combo_to_layer(self._out_streams,       'streams')
            set_combo_to_layer(self._out_subcatchments, 'subcatchments')
        except Exception:
            pass

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self._gen_dem, 'burned_dem', 'dem')
        except Exception:
            pass
        self._refresh_outlet_inputs()

    def _run_generate(self):
        dem = self._gen_dem.currentLayer()
        if dem is None or not dem.isValid():
            self._gen_status.setText('No DEM selected — please choose a raster layer.')
            return

        self._gen_run.setEnabled(False)
        self._gen_status.setText('Running r.watershed — this may take several minutes…')
        self._gen_progress.setValue(0)
        QApplication.processEvents()

        fb = _AlgoFeedback(self._gen_progress, self._gen_status)

        try:
            result = processing.run(
                self._ALGO_GENERATE,
                {
                    'INPUT_DEM'          : dem,
                    'THRESHOLD_AREA'     : self._gen_threshold.value(),
                    'MIN_STREAM_LENGTH'  : self._gen_min_length.value(),
                    'ASSIGN_SUBCATCH_ID' : self._gen_assign_ids.isChecked(),
                    'SUBCATCH_PREFIX'    : self._gen_prefix.text().strip() or 'SC',
                    'OUTPUT_STREAMS'     : 'TEMPORARY_OUTPUT',
                    'OUTPUT_SUBBASINS'   : 'TEMPORARY_OUTPUT',
                    'OUTPUT_DRAINAGE'    : 'TEMPORARY_OUTPUT',
                    'OUTPUT_ACCUMULATION': 'TEMPORARY_OUTPUT',
                },
                feedback=fb,
            )
            self._load_generate_outputs(result)
            self._save_generate_state(dem.name())
            self._gen_status.setText('Done — streams, sub-catchments and drainage raster loaded.')
            self._gen_progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Stream and Catchment generation complete.\nStreams, sub-catchments and drainage raster loaded into the project.')

        except Exception:
            err = traceback.format_exc()
            self._gen_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self._gen_run.setEnabled(True)

    # ── Run: Outlet Catchment ─────────────────────────────────────────────────

    def _run_outlet(self):
        drainage = self._out_drainage.currentLayer()
        if drainage is None or not drainage.isValid():
            self._out_status.setText('No drainage raster selected.')
            return
        outlets = self._out_outlets.currentLayer()
        if outlets is None or not outlets.isValid():
            self._out_status.setText('No outlet points layer selected.')
            return

        streams       = self._out_streams.currentLayer()
        subcatchments = self._out_subcatchments.currentLayer()
        snap_tol      = self._out_snap.value()

        self._out_run.setEnabled(False)
        self._out_status.setText('Running r.water.outlet per outlet…')
        self._out_progress.setValue(0)
        QApplication.processEvents()

        fb = _AlgoFeedback(self._out_progress, self._out_status)

        params = {
            'INPUT_DRAINAGE'             : drainage,
            'INPUT_OUTLETS'              : outlets,
            'SNAP_TOLERANCE'             : snap_tol,
            'OUTPUT_CATCHMENTS'          : 'TEMPORARY_OUTPUT',
            'OUTPUT_CATCHMENTS_UNMERGED' : 'TEMPORARY_OUTPUT',
            'OUTPUT_STREAMS'             : 'TEMPORARY_OUTPUT',
        }
        if streams is not None and streams.isValid():
            params['INPUT_STREAMS'] = streams
        if subcatchments is not None and subcatchments.isValid():
            params['INPUT_SUBCATCHMENTS'] = subcatchments

        try:
            result = processing.run(self._ALGO_OUTLET, params, feedback=fb)
            self._load_outlet_outputs(result)
            try:
                from ..pipeline_state import save_pipeline_state
                save_pipeline_state('catchments',         'Outlet Catchments')
                save_pipeline_state('catchment_id_field', 'SubcatchID')
            except Exception:
                pass
            if subcatchments is not None and subcatchments.isValid():
                self._out_status.setText('Done — outlet catchments and sub-catchments loaded.')
            else:
                self._out_status.setText('Done — outlet catchments loaded.')
            self._out_progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Outlet Catchment delineation complete.\nCatchment layers have been loaded into the project.')

        except Exception:
            err = traceback.format_exc()
            self._out_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self._out_run.setEnabled(True)

    # ── Load outputs: Generate ────────────────────────────────────────────────

    def _save_generate_state(self, dem_name):
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('dem',              dem_name)
            save_pipeline_state('drainage',         'Drainage Direction')
            save_pipeline_state('accumulation',     'Flow Accumulation')
            save_pipeline_state('streams',          'Streams')
            save_pipeline_state('subcatchments',    'Sub-Catchments')
            prefix = self._gen_prefix.text().strip() or 'SC'
            save_pipeline_state('catchment_prefix', prefix)
            cat_id_field = 'SubcatchID' if self._gen_assign_ids.isChecked() else 'basin_id'
            save_pipeline_state('catchment_id_field', cat_id_field)
        except Exception:
            pass

    def _load_generate_outputs(self, result):
        def _resolve_vector(val, name):
            if isinstance(val, QgsMapLayer):
                val.setName(name)
                return val if val.isValid() else None
            lyr = QgsVectorLayer(str(val), name, 'ogr')
            return lyr if lyr.isValid() else None

        # Sub-Catchments first — Streams added after will sit on top
        val = result.get('OUTPUT_SUBBASINS')
        if val:
            lyr = _resolve_vector(val, 'Sub-Catchments')
            if lyr:
                sym = QgsFillSymbol.createSimple({
                    'color'        : '0,0,0,0',
                    'outline_color': '#546E7A',
                    'outline_width': '0.26',
                    'outline_style': 'solid',
                })
                lyr.renderer().setSymbol(sym)
                QgsProject.instance().addMapLayer(lyr)

        val = result.get('OUTPUT_STREAMS')
        if val:
            lyr = _resolve_vector(val, 'Streams')
            if lyr:
                sym = QgsLineSymbol.createSimple({
                    'color': '#1A237E',
                    'width': '0.35',
                })
                lyr.renderer().setSymbol(sym)
                QgsProject.instance().addMapLayer(lyr)

        # Drainage and accumulation rasters — added to project, visibility off
        for key, name in (('OUTPUT_DRAINAGE', 'Drainage Direction'),
                          ('OUTPUT_ACCUMULATION', 'Flow Accumulation')):
            val = result.get(key)
            if val:
                lyr = QgsRasterLayer(str(val), name, 'gdal')
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    node = QgsProject.instance().layerTreeRoot().findLayer(lyr.id())
                    if node:
                        node.setItemVisibilityChecked(False)

        self.iface.mapCanvas().refresh()

    # ── Load outputs: Outlet Catchment ────────────────────────────────────────

    def _load_outlet_outputs(self, result):
        # Load sub-catchments first so merged catchments appear on top
        val_sub = result.get('OUTPUT_CATCHMENTS_UNMERGED')
        if val_sub:
            if isinstance(val_sub, QgsMapLayer):
                sub_lyr = val_sub
                sub_lyr.setName('Outlet Sub-Catchments')
            else:
                sub_lyr = QgsVectorLayer(str(val_sub), 'Outlet Sub-Catchments', 'ogr')
            if sub_lyr.isValid():
                sym = QgsFillSymbol.createSimple({
                    'color'        : '0,0,0,0',
                    'outline_color': '#546E7A',
                    'outline_width': '0.26',
                    'outline_style': 'solid',
                })
                sub_lyr.renderer().setSymbol(sym)
                QgsProject.instance().addMapLayer(sub_lyr)

        val = result.get('OUTPUT_CATCHMENTS')
        if not val:
            self.iface.mapCanvas().refresh()
            return
        if isinstance(val, QgsMapLayer):
            lyr = val
            lyr.setName('Outlet Catchments')
        else:
            lyr = QgsVectorLayer(str(val), 'Outlet Catchments', 'ogr')
        if lyr.isValid():
            try:
                from ..pipeline_state import get_pipeline_state
                dp = lyr.dataProvider()
                # Strip internal fields that the algorithm may not have removed
                _internal = {'dn', 'layer', 'path'}
                drop_idxs = [
                    lyr.fields().indexOf(f.name())
                    for f in lyr.fields()
                    if f.name().lower() in _internal
                ]
                if drop_idxs:
                    dp.deleteAttributes(drop_idxs)
                    lyr.updateFields()
                # Add SubcatchID so downstream tools use the same ID field as sub-catchments.
                # Value: {prefix}_Outlet (e.g. "SC_Outlet"), or numbered if multiple outlets.
                prefix = get_pipeline_state('catchment_prefix') or 'SC'
                dp.addAttributes([QgsField('SubcatchID', QVariant.String, '', 50)])
                lyr.updateFields()
                fid_idx = lyr.fields().indexOf('SubcatchID')
                features = list(lyr.getFeatures())
                n = len(features)
                attr_map = {
                    feat.id(): {fid_idx: f'{prefix}_Outlet_{i + 1}' if n > 1
                                         else f'{prefix}_Outlet'}
                    for i, feat in enumerate(features)
                }
                dp.changeAttributeValues(attr_map)
            except Exception:
                pass
            sym = QgsFillSymbol.createSimple({
                'color'        : '255,152,0,60',
                'outline_color': '#E65100',
                'outline_width': '0.4',
                'outline_style': 'solid',
            })
            lyr.renderer().setSymbol(sym)
            QgsProject.instance().addMapLayer(lyr)

        # Clipped streams loaded last so they appear on top
        val_str = result.get('OUTPUT_STREAMS')
        if val_str:
            if isinstance(val_str, QgsMapLayer):
                str_lyr = val_str
                str_lyr.setName('Outlet Streams')
            else:
                str_lyr = QgsVectorLayer(str(val_str), 'Outlet Streams', 'ogr')
            if str_lyr.isValid():
                sym = QgsLineSymbol.createSimple({
                    'color': '#1A237E',
                    'width': '0.35',
                })
                str_lyr.renderer().setSymbol(sym)
                QgsProject.instance().addMapLayer(str_lyr)

        self.iface.mapCanvas().refresh()
