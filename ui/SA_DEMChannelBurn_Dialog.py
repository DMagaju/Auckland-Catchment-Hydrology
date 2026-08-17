# -*- coding: utf-8 -*-
"""Dock tab — DEM Channel Burn."""

__author__  = "Dipendra Magaju"
__version__ = "V01"

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar,
    QComboBox, QDoubleSpinBox, QMessageBox, QFrame, QScrollArea, QDialog, QApplication,
)
from qgis.core import (
    QgsMapLayerProxyModel, QgsProject, QgsProcessingFeedback,
    QgsRasterLayer, QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox
import processing

_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_SLATE = '#37474F'

_BURN_LAYER_NAME = 'Burn Line'

try:
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _AlignCenter = Qt.AlignCenter

try:
    _AlignL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
except AttributeError:
    _AlignL = Qt.AlignLeft | Qt.AlignVCenter

try:
    _NoFrame = QFrame.Shape.NoFrame
except AttributeError:
    _NoFrame = QFrame.NoFrame

try:
    _PointingHand = Qt.CursorShape.PointingHandCursor
except AttributeError:
    _PointingHand = Qt.PointingHandCursor

try:
    _LINE_FILTER = QgsMapLayerProxyModel.Filter.LineLayer
except AttributeError:
    _LINE_FILTER = QgsMapLayerProxyModel.LineLayer

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer

_WIDGET_STYLE = """
    QgsMapLayerComboBox, QComboBox, QDoubleSpinBox {
        background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px;
    }
    QComboBox::drop-down { border: none; }
    QDoubleSpinBox:disabled { background-color: #ECEFF1; color: #90A4AE; }
"""

_HELP = {
    'tool': (
        "Burns one or more channel corridors into a LiDAR DEM.\n\n"
        "Baseline DEM\n"
        "  LiDAR raster in a projected metric CRS (metres). The 'Burn Line' "
        "layer is created in the DEM's CRS automatically.\n\n"
        "Centreline / Draw Burn Lines\n"
        "  Select an existing line layer, or click 'Draw Burn Lines' to create "
        "a 'Burn Line' layer and digitise directly on the map using QGIS's "
        "standard tools. Each line feature = one channel corridor.\n\n"
        "Burn Width (m)\n"
        "  Total corridor width — half-width each side of the centreline."
    ),
    'burning': (
        "Replace\n"
        "  Overwrites all corridor pixels to (centreline DEM elevation - burn depth). "
        "Can raise or lower terrain.\n\n"
        "Lower-Only\n"
        "  Same target as Replace, but never raises pixels already below the target. "
        "Safe default for most workflows.\n\n"
        "Lower-Only with Tolerance\n"
        "  Same as Lower-Only but only applies the cut where depth > tolerance. "
        "Suppresses sub-millimetre edits near feathered edges.\n\n"
        "Longitudinal Gradient\n"
        "  The DEM is sampled automatically at the start and end vertices of each "
        "burn line to obtain the upstream and downstream reduce levels (RL). A "
        "straight-line invert gradient is then burned between those two sampled "
        "levels along the full centreline length. Only cuts terrain (Lower-Only).\n"
        "  Perfect for smoothing an irregular channel bed while preserving the "
        "existing head and tail elevations."
    ),
    'edge': (
        "None\n"
        "  Hard cutoff at the corridor edge.\n\n"
        "Edge Buffer\n"
        "  Extends the full burn depth beyond the corridor edge by Blend Distance "
        "before the hard cutoff.\n\n"
        "Feather Blending\n"
        "  Cosine taper (depth 100% → 0%) over Blend Distance beyond the corridor "
        "edge. Produces a smooth, natural transition — recommended."
    ),
}


# ── UI helpers ────────────────────────────────────────────────────────────────

class _HelpDialog:
    def __init__(self, title, text, parent=None):
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(title.strip())
        self._dlg.setMinimumWidth(440)
        self._dlg.setMaximumWidth(560)
        self._dlg.setSizeGripEnabled(True)
        self._dlg.setStyleSheet("""
            QDialog, QWidget { background-color: #FAFAFA; color: #212121; }
            QScrollArea { background: #FAFAFA; border: none; }
            QLabel { background: transparent; color: #212121; font-size: 11px; }
            QPushButton { background: #1565C0; color: white; border: none;
                border-radius: 4px; padding: 5px 20px; font-size: 11px; font-weight: bold; }
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
        self._dlg.resize(460, min(preferred, 520))

    def exec(self):
        self._dlg.exec()


def _help_btn(title, text, anchor, size=18):
    btn = QToolButton()
    btn.setText('?')
    btn.setFixedSize(size, size)
    btn.setCursor(_PointingHand)
    btn.setStyleSheet(
        f'QToolButton {{ background: rgba(255,255,255,0.22); color: white;'
        f'  border: 1px solid rgba(255,255,255,0.50); border-radius: {size // 2}px;'
        f'  font-size: {size - 8}px; font-weight: bold; padding: 0; }}'
        f'QToolButton:hover {{ background: rgba(255,255,255,0.45); }}'
    )
    btn.clicked.connect(lambda: _HelpDialog(title, text, anchor).exec())
    return btn


def _section(title, color, body_color, help_key=''):
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
    if help_key and help_key in _HELP:
        bl.addWidget(_help_btn(title.strip(), _HELP[help_key], bar))
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


def _spinbox(lo, hi, default, decimals, suffix=''):
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setValue(default)
    sb.setDecimals(decimals)
    if suffix:
        sb.setSuffix(f'  {suffix}')
    return sb


# ── Main widget ───────────────────────────────────────────────────────────────

class DEMChannelBurnWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sa_dem_channel_burn_v01'

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet(_WIDGET_STYLE)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
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
        t = QLabel('DEM Channel Burn')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('DEM Channel Burn', _HELP['tool'], header, size=20))
        hl.addSpacing(2)
        root.addWidget(header)

        # ── Scroll body ──────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        scroll.setStyleSheet('background: #F5F5F5;')
        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        # ── Section 1: Inputs ────────────────────────────────────────────────
        sec1, g1 = _section('  Inputs', _BLUE, '#EEF4FF', 'tool')
        row = 0

        g1.addWidget(_row_label('Baseline DEM'), row, 0)
        self.combo_dem = QgsMapLayerComboBox()
        self.combo_dem.setFilters(_RASTER_FILTER)
        self.combo_dem.setAllowEmptyLayer(True)
        self.combo_dem.setCurrentIndex(0)
        g1.addWidget(self.combo_dem, row, 1); row += 1

        g1.addWidget(_row_label('Centreline Layer'), row, 0)
        self.combo_centreline = QgsMapLayerComboBox()
        self.combo_centreline.setFilters(_LINE_FILTER)
        self.combo_centreline.setAllowEmptyLayer(True)
        self.combo_centreline.setCurrentIndex(0)
        g1.addWidget(self.combo_centreline, row, 1); row += 1

        # Draw / Clear buttons
        btn_row_w = QWidget()
        btn_row_w.setStyleSheet('background: transparent;')
        brl = QHBoxLayout(btn_row_w)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.setSpacing(4)
        self.btn_draw = QPushButton('Draw Burn Lines')
        self.btn_draw.setFixedHeight(22)
        self.btn_draw.setStyleSheet(
            f'QPushButton {{ background: {_SLATE}; color: white; border-radius: 3px;'
            f'  padding: 2px 8px; font-size: 10px; }}'
            f'QPushButton:hover {{ background: #546E7A; }}'
        )
        self.btn_draw.clicked.connect(self._start_draw)
        brl.addWidget(self.btn_draw)
        self.btn_clear = QPushButton('Clear Layer')
        self.btn_clear.setFixedHeight(22)
        self.btn_clear.setStyleSheet(
            'QPushButton { background: #CFD8DC; color: #37474F; border-radius: 3px;'
            '  padding: 2px 8px; font-size: 10px; }'
            'QPushButton:hover { background: #B0BEC5; }'
        )
        self.btn_clear.clicked.connect(self._clear_burn_layer)
        brl.addWidget(self.btn_clear)
        brl.addStretch()
        g1.addWidget(btn_row_w, row, 1); row += 1

        self.lbl_draw_status = QLabel(
            'Or click Draw to create a "Burn Line" layer and digitise on the map.'
        )
        self.lbl_draw_status.setWordWrap(True)
        self.lbl_draw_status.setStyleSheet('font-size: 10px; color: #78909C; background: transparent;')
        g1.addWidget(self.lbl_draw_status, row, 1); row += 1

        g1.addWidget(_row_label('Burn Width (m)'), row, 0)
        self.spin_width = _spinbox(0.1, 9999.0, 10.0, 2, 'm')
        g1.addWidget(self.spin_width, row, 1)

        bv.addWidget(sec1)

        # ── Section 2: Channel Burning ───────────────────────────────────────
        sec2, g2 = _section('  Channel Burning', _BLUE, '#EEF4FF', 'burning')
        row = 0

        g2.addWidget(_row_label('Burn Mode'), row, 0)
        self.combo_burn = QComboBox()
        self.combo_burn.addItems([
            'Replace (overwrite DEM inside corridor)',
            'Lower-Only (never raises terrain)',
            'Lower-Only with Tolerance (cut only if > threshold)',
            'Longitudinal Gradient (linear invert — upstream to downstream RL)',
        ])
        self.combo_burn.setCurrentIndex(1)
        self.combo_burn.currentIndexChanged.connect(self._on_burn_mode_changed)
        g2.addWidget(self.combo_burn, row, 1); row += 1

        # Burn depth row (hidden in gradient mode)
        self.lbl_burn_depth = _row_label('Burn Depth (m)')
        self.spin_depth = _spinbox(0.0, 999.0, 0.5, 3, 'm')
        g2.addWidget(self.lbl_burn_depth, row, 0)
        g2.addWidget(self.spin_depth, row, 1); row += 1

        # Tolerance row (visible only for mode 2)
        self.lbl_tolerance = _row_label('Tolerance Depth (m)')
        self.spin_tolerance = _spinbox(0.0, 99.0, 0.05, 3, 'm')
        g2.addWidget(self.lbl_tolerance, row, 0)
        g2.addWidget(self.spin_tolerance, row, 1); row += 1

        # ── Gradient info label (visible only for mode 3) ───────────────────
        self.lbl_gradient_info = QLabel(
            'The DEM elevation will be sampled automatically at the start and end '
            'of each burn line. A straight-line gradient is burned between those '
            'two levels — no manual input required.'
        )
        self.lbl_gradient_info.setWordWrap(True)
        self.lbl_gradient_info.setStyleSheet(
            'font-size: 10px; color: #1B5E20; background: #E8F5E9; '
            'border: 1px solid #A5D6A7; border-radius: 3px; padding: 5px 7px;'
        )
        self.lbl_gradient_info.setVisible(False)
        g2.addWidget(self.lbl_gradient_info, row, 0, 1, 2); row += 1

        bv.addWidget(sec2)

        # ── Section 3: Edge Treatment ────────────────────────────────────────
        sec3, g3 = _section('  Edge Treatment', _SLATE, '#ECEFF1', 'edge')
        row = 0

        g3.addWidget(_row_label('Edge Mode'), row, 0)
        self.combo_edge = QComboBox()
        self.combo_edge.addItems([
            'None — sharp cutoff at corridor boundary',
            'Edge Buffer — flat extension beyond corridor edge',
            'Feather Blending — cosine taper over blend distance',
        ])
        self.combo_edge.setCurrentIndex(2)
        self.combo_edge.currentIndexChanged.connect(self._toggle_blend)
        g3.addWidget(self.combo_edge, row, 1); row += 1

        g3.addWidget(_row_label('Blend / Buffer Distance (m)'), row, 0)
        self.spin_blend = _spinbox(0.0, 9999.0, 2.0, 2, 'm')
        g3.addWidget(self.spin_blend, row, 1)

        bv.addWidget(sec3)
        bv.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ───────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet('background: #ECEFF1; border-top: 1px solid #CFD8DC;')
        fv = QVBoxLayout(footer)
        fv.setContentsMargins(14, 8, 14, 12)
        fv.setSpacing(6)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            f'QProgressBar {{ background: #CFD8DC; border-radius: 3px; border: none; }}'
            f'QProgressBar::chunk {{ background: {_BLUE}; border-radius: 3px; }}'
        )
        fv.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.lbl_status = QLabel('Ready.')
        self.lbl_status.setStyleSheet('font-size: 10px; color: #546E7A;')
        btn_row.addWidget(self.lbl_status, 1)
        self.btn_run = QPushButton('  Run')
        self.btn_run.setFixedWidth(100)
        self.btn_run.setStyleSheet(
            f'QPushButton {{ background: {_BLUE}; color: white; border-radius: 4px;'
            f'  padding: 6px 14px; font-size: 12px; font-weight: bold; }}'
            f'QPushButton:hover   {{ background: #1976D2; }}'
            f'QPushButton:pressed {{ background: #0D47A1; }}'
            f'QPushButton:disabled {{ background: #90A4AE; }}'
        )
        self.btn_run.clicked.connect(self._run)
        btn_row.addWidget(self.btn_run)
        fv.addLayout(btn_row)

        try:
            from ..algorithms.Subcatch_DEMChannelBurn import __version__ as _algo_ver
        except Exception:
            _algo_ver = __version__
        try:
            from qgis.core import Qgis
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(f'Developer: D Magaju   ·   Version: {_algo_ver}   ·   QGIS: {_qgis_ver}')
        ver_lbl.setAlignment(_AlignCenter)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        fv.addWidget(ver_lbl)

        root.addWidget(footer)

        # Apply initial state
        self._on_burn_mode_changed(self.combo_burn.currentIndex())
        self._toggle_blend(self.combo_edge.currentIndex())

    # ── Mode toggles ─────────────────────────────────────────────────────────

    def _on_burn_mode_changed(self, index):
        is_gradient = (index == 3)
        # Burn depth row visible for modes 0/1/2
        self.lbl_burn_depth.setVisible(not is_gradient)
        self.spin_depth.setVisible(not is_gradient)
        # Tolerance row visible only for mode 2
        self.lbl_tolerance.setVisible(index == 2)
        self.spin_tolerance.setVisible(index == 2)
        # Gradient info label visible only for mode 3
        self.lbl_gradient_info.setVisible(is_gradient)

    def _toggle_blend(self, index):
        self.spin_blend.setEnabled(index > 0)

    # ── Burn Line layer management ────────────────────────────────────────────

    def _get_burn_layer(self):
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == _BURN_LAYER_NAME and isinstance(lyr, QgsVectorLayer):
                return lyr
        return None

    def _start_draw(self):
        dem = self.combo_dem.currentLayer()
        if not (dem and dem.isValid()):
            self.lbl_draw_status.setText(
                'Select a Baseline DEM first — "Burn Line" is created in the DEM CRS.'
            )
            self.lbl_draw_status.setStyleSheet('font-size: 10px; color: #C62828; background: transparent;')
            return

        burn_layer = self._get_burn_layer()
        if burn_layer is None:
            burn_layer = QgsVectorLayer(
                f'LineString?crs={dem.crs().authid()}',
                _BURN_LAYER_NAME, 'memory',
            )
            QgsProject.instance().addMapLayer(burn_layer)

        self.iface.setActiveLayer(burn_layer)
        if not burn_layer.isEditable():
            burn_layer.startEditing()
        self.iface.actionAddFeature().trigger()

        n = burn_layer.featureCount()
        self.lbl_draw_status.setText(
            f'"Burn Line" active  ({n} line{"s" if n != 1 else ""} so far) — '
            'draw on the map, then click Run.'
        )
        self.lbl_draw_status.setStyleSheet('font-size: 10px; color: #2E7D32; background: transparent;')

    def _clear_burn_layer(self):
        burn_layer = self._get_burn_layer()
        if burn_layer is None:
            self.lbl_draw_status.setText('No "Burn Line" layer found.')
            self.lbl_draw_status.setStyleSheet('font-size: 10px; color: #78909C; background: transparent;')
            return
        if not burn_layer.isEditable():
            burn_layer.startEditing()
        burn_layer.selectAll()
        burn_layer.deleteSelectedFeatures()
        burn_layer.commitChanges()
        self.lbl_draw_status.setText('"Burn Line" layer cleared.')
        self.lbl_draw_status.setStyleSheet('font-size: 10px; color: #78909C; background: transparent;')

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run(self):
        dem = self.combo_dem.currentLayer()
        if not (dem and dem.isValid()):
            self.lbl_status.setText('No baseline DEM selected.'); return

        burn_layer = self._get_burn_layer()
        if burn_layer and burn_layer.featureCount() > 0:
            if burn_layer.isEditable():
                burn_layer.commitChanges()
            cl_layer = burn_layer
        else:
            cl_layer = self.combo_centreline.currentLayer()
            if not (cl_layer and cl_layer.isValid()):
                self.lbl_status.setText(
                    'No centreline — draw burn lines or select a layer.'
                )
                return

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Running — please wait…')
        self.progress.setValue(0)
        QApplication.processEvents()

        class _FB(QgsProcessingFeedback):
            def __init__(self, bar, lbl):
                super().__init__()
                self._bar = bar; self._lbl = lbl
            def setProgress(self, v):
                self._bar.setValue(int(v)); QApplication.processEvents()
            def pushInfo(self, msg):
                if msg.strip():
                    self._lbl.setText(msg.strip()[-120:]); QApplication.processEvents()
            def pushWarning(self, msg):
                self._lbl.setText(f'! {msg.strip()[-115:]}'); QApplication.processEvents()
            def reportError(self, error, fatal=False):
                if fatal:
                    self._lbl.setText(f'Error: {error.strip()[-110:]}')
                    QApplication.processEvents()

        fb = _FB(self.progress, self.lbl_status)

        try:
            params = {
                'INPUT_DEM':  dem,
                'CENTERLINE': cl_layer,
                'BURN_WIDTH': self.spin_width.value(),
                'BURN_DEPTH': self.spin_depth.value(),
                'BURN_MODE':  self.combo_burn.currentIndex(),
                'TOLERANCE':  self.spin_tolerance.value(),
                'EDGE_MODE':  self.combo_edge.currentIndex(),
                'BLEND_DIST': self.spin_blend.value(),
                'OUTPUT_DEM': 'TEMPORARY_OUTPUT',
            }
            result = processing.run(self._ALGO_ID, params, feedback=fb)
            self._load_outputs(result)
            self.lbl_status.setText('Done — burned DEM loaded into project.')
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'DEM Channel Burn complete.\nThe burned DEM has been loaded into the project.')

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()
        finally:
            self.btn_run.setEnabled(True)

    def _load_outputs(self, result):
        dem_path = result.get('OUTPUT_DEM')
        if dem_path:
            lyr = QgsRasterLayer(dem_path, 'Burned DEM')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
                try:
                    from ..pipeline_state import save_pipeline_state
                    save_pipeline_state('burned_dem', 'Burned DEM')
                except Exception:
                    pass
        diff_path = result.get('OUTPUT_DIFF')
        if diff_path:
            lyr = QgsRasterLayer(diff_path, 'Channel Burn Difference')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.combo_dem, 'dem')
        except Exception:
            pass
