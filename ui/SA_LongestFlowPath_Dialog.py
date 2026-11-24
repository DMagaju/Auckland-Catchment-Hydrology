# -*- coding: utf-8 -*-
"""
Dock tab — Longest Flow Path.
Follows the same design system as SA_StreamCatchment_Dialog.py.

Author: Dipendra Magaju
"""

__author__  = "Dipendra Magaju"
__version__ = "V02"

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar,
    QDoubleSpinBox, QMessageBox, QFrame, QScrollArea, QDialog, QApplication,
    QSizePolicy, QCheckBox,
)
from qgis.core import (
    QgsMapLayerProxyModel,
    QgsProject,
    QgsMapLayer,
    QgsVectorLayer,
    QgsLineSymbol,
    QgsProcessingFeedback,
)
from qgis.gui import QgsMapLayerComboBox

import processing


# ── Color constants ───────────────────────────────────────────────────────────
_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_SLATE = '#37474F'

# ── Qt6 / Qt5 compat ─────────────────────────────────────────────────────────
try:
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _AlignCenter = Qt.AlignCenter                          # type: ignore

try:
    _AlignL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
except AttributeError:
    _AlignL = Qt.AlignLeft | Qt.AlignVCenter               # type: ignore

try:
    _NoFrame = QFrame.Shape.NoFrame
except AttributeError:
    _NoFrame = QFrame.NoFrame                              # type: ignore

try:
    _PointingHand = Qt.CursorShape.PointingHandCursor
except AttributeError:
    _PointingHand = Qt.PointingHandCursor                  # type: ignore

try:
    _RASTER_FILTER  = QgsMapLayerProxyModel.Filter.RasterLayer
    _POLYGON_FILTER = QgsMapLayerProxyModel.Filter.PolygonLayer
    _LINE_FILTER    = QgsMapLayerProxyModel.Filter.LineLayer
except AttributeError:
    _RASTER_FILTER  = QgsMapLayerProxyModel.RasterLayer    # type: ignore
    _POLYGON_FILTER = QgsMapLayerProxyModel.PolygonLayer   # type: ignore
    _LINE_FILTER    = QgsMapLayerProxyModel.LineLayer       # type: ignore

try:
    _sp_ignored = QSizePolicy.Policy.Ignored
    _sp_pref    = QSizePolicy.Policy.Preferred
except AttributeError:
    _sp_ignored = QSizePolicy.Ignored   # type: ignore
    _sp_pref    = QSizePolicy.Preferred # type: ignore

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
    QComboBox QAbstractItemView {
        background-color: white; color: #212121;
        selection-background-color: #1565C0; selection-color: white;
        border: 1px solid #B0BEC5;
    }
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 16px; }
"""

# ── Help text ─────────────────────────────────────────────────────────────────
_HELP = {
    'tool': (
        "For each sub-catchment polygon, traces the longest D8 flow path from "
        "the most upstream headwater cell to the sub-catchment outlet, using the "
        "drainage direction raster from the Stream and Catchment Generator.\n\n"
        "The path covers both overland and channel flow — no stream vector layer "
        "is required. Each sub-catchment produces exactly one polyline.\n\n"
        "This flow path is typically used as the time-of-concentration path for "
        "hydrological pre-processing (e.g. ICM, SWMM).\n\n"
        "Prerequisites:\n"
        "• Run 'Stream and Catchment Generator' first to produce the drainage "
        "direction raster\n"
        "• DEM CRS must be projected and use metres (e.g. EPSG:2193 NZTM)\n\n"
        "Output\n"
        "The result is saved as Longest_Flow_Path.shp in the Project Folder "
        "(Download DEM tab) if one is set, otherwise as a temporary QGIS "
        "scratch layer."
    ),
    'inputs': (
        "Sub-catchment Polygons\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Polygon layer from the Stream and Catchment Generator. "
        "One flow path line is produced per feature.\n\n"
        "Drainage Direction Raster\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The 'Drainage Direction' raster from the Stream and Catchment Generator "
        "(OUTPUT_DRAINAGE). Encodes the GRASS D8 flow direction at every cell.\n\n"
        "Flow Accumulation Raster\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The 'Flow Accumulation' raster from the Stream and Catchment Generator "
        "(OUTPUT_ACCUMULATION). Required to correctly identify the pour point "
        "(outlet cell) for each sub-catchment — the cell with the highest "
        "accumulation value is selected as the true outlet.\n\n"
        "Stream Network (optional)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Line layer of the stream network. When supplied, the tool finds where each "
        "stream exits the sub-catchment boundary and uses that as the forced outlet "
        "cell, preventing the LFP from stopping at an internal high-accumulation "
        "point instead of the true pour point.\n\n"
        "DEM (optional)\n"
        "━━━━━━━━━━━━━━\n"
        "Digital Elevation Model. Used only when a stream network is also supplied, "
        "to break ties between candidate outlet cells of equal accumulation — the "
        "lowest cell is chosen as the true outlet.\n\n"
        "Extend Along Stream Network (experimental)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Only applies when a stream network is supplied. When ticked, extends the "
        "D8 endpoint along the stream line to the true catchment-boundary outlet "
        "instead of stopping where the D8 trace itself ends. Off by default."
    ),
    'advanced': (
        "Simplification Tolerance\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Douglas-Peucker tolerance applied after path extraction to smooth the "
        "D8 staircase artefact. Default 0 = auto (2 × cell size). "
        "Increase for coarser but smoother output; set to a very small value "
        "to preserve every cell-centre vertex.\n\n"
        "Minimum Sub-catchment Area\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Sub-catchments smaller than this area are skipped. Useful to exclude "
        "sliver polygons at the DEM boundary. Set to 0 to process all features."
    ),

}


# ── Help popup ────────────────────────────────────────────────────────────────
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


class LongestFlowPathWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_longest_flow_path_v02'

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet(_WIDGET_STYLE)
        self._build()

    # ── UI construction ───────────────────────────────────────────────────────

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
        t = QLabel('Longest Flow Path')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Longest Flow Path', _HELP['tool'], header, size=20))
        hl.addSpacing(2)
        root.addWidget(header)

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        scroll.setStyleSheet('background: #F5F5F5;')

        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        # ── Section: Inputs ───────────────────────────────────────────────────
        sec1, g1 = _section('  Inputs', _BLUE, '#EEF4FF', _HELP['inputs'])
        row = 0

        g1.addWidget(_row_label('Sub-catchment polygons'), row, 0)
        self.combo_subcat = QgsMapLayerComboBox()
        self.combo_subcat.setFilters(_POLYGON_FILTER)
        self.combo_subcat.setAllowEmptyLayer(True)
        self.combo_subcat.setCurrentIndex(0)
        g1.addWidget(self.combo_subcat, row, 1); row += 1

        g1.addWidget(_row_label('Drainage direction raster'), row, 0)
        self.combo_drainage = QgsMapLayerComboBox()
        self.combo_drainage.setFilters(_RASTER_FILTER)
        self.combo_drainage.setAllowEmptyLayer(True)
        self.combo_drainage.setCurrentIndex(0)
        self.combo_drainage.setToolTip(
            'The Drainage Direction raster produced by the Stream and Catchment Generator.'
        )
        g1.addWidget(self.combo_drainage, row, 1); row += 1

        g1.addWidget(_row_label('Flow accumulation raster'), row, 0)
        self.combo_accum = QgsMapLayerComboBox()
        self.combo_accum.setFilters(_RASTER_FILTER)
        self.combo_accum.setAllowEmptyLayer(True)
        self.combo_accum.setCurrentIndex(0)
        self.combo_accum.setToolTip(
            'The Flow Accumulation raster produced by the Stream and Catchment Generator.\n'
            'Used to identify the true outlet (pour point) for each sub-catchment.'
        )
        g1.addWidget(self.combo_accum, row, 1); row += 1

        g1.addWidget(_row_label('Stream network (optional)'), row, 0)
        self.combo_streams = QgsMapLayerComboBox()
        self.combo_streams.setFilters(_LINE_FILTER)
        self.combo_streams.setAllowEmptyLayer(True)
        self.combo_streams.setCurrentIndex(0)
        self.combo_streams.setToolTip(
            'Optional — stream line layer.\n'
            'When supplied, the stream-catchment boundary intersection is used to\n'
            'force the correct outlet cell for each sub-catchment.'
        )
        g1.addWidget(self.combo_streams, row, 1); row += 1

        g1.addWidget(_row_label('DEM (optional)'), row, 0)
        self.combo_dem = QgsMapLayerComboBox()
        self.combo_dem.setFilters(_RASTER_FILTER)
        self.combo_dem.setAllowEmptyLayer(True)
        self.combo_dem.setCurrentIndex(0)
        self.combo_dem.setToolTip(
            'Optional — DEM raster.\n'
            'Used together with the stream network to tiebreak outlet cell selection\n'
            'by choosing the lowest-elevation candidate.'
        )
        g1.addWidget(self.combo_dem, row, 1); row += 1

        self.cb_extend_stream = QCheckBox('Extend along stream network (experimental)')
        self.cb_extend_stream.setToolTip(
            'Only applies when a stream network is supplied above.\n'
            'Extends the D8 endpoint along the stream line to the true\n'
            'catchment-boundary outlet. Off by default — newly added and\n'
            'not yet verified on enough real catchments to trust by default.'
        )
        g1.addWidget(self.cb_extend_stream, row, 0, 1, 2)

        bv.addWidget(sec1)

        # ── Section: Advanced Options ─────────────────────────────────────────
        sec2, g2 = _section('  Advanced Options', _SLATE, '#ECEFF1', _HELP['advanced'])
        row = 0

        g2.addWidget(_row_label('Simplification tolerance'), row, 0)
        self.sb_simplify = QDoubleSpinBox()
        self.sb_simplify.setRange(0.0, 1000.0)
        self.sb_simplify.setValue(0.0)
        self.sb_simplify.setDecimals(2)
        self.sb_simplify.setSuffix('  m  (0 = auto)')
        self.sb_simplify.setToolTip(
            'Douglas-Peucker tolerance for smoothing the D8 staircase.\n'
            '0 = automatic (2 × cell size).'
        )
        g2.addWidget(self.sb_simplify, row, 1); row += 1

        g2.addWidget(_row_label('Min sub-catchment area'), row, 0)
        self.sb_min_area = QDoubleSpinBox()
        self.sb_min_area.setRange(0.0, 100000.0)
        self.sb_min_area.setValue(0.0)
        self.sb_min_area.setDecimals(2)
        self.sb_min_area.setSuffix('  ha  (0 = all)')
        self.sb_min_area.setToolTip('Skip sub-catchments smaller than this area.')
        g2.addWidget(self.sb_min_area, row, 1)

        bv.addWidget(sec2)
        bv.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ────────────────────────────────────────────────────────────
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
        self.lbl_status.setSizePolicy(_sp_ignored, _sp_pref)
        btn_row.addWidget(self.lbl_status, 1)

        self.btn_run = QPushButton('  ▶  Run')
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
            from qgis.core import Qgis
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(f'Developer: D Magaju   ·   Version: Beta-01   ·   QGIS: {_qgis_ver}')
        ver_lbl.setAlignment(_AlignCenter)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        fv.addWidget(ver_lbl)

        root.addWidget(footer)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run(self):
        subcat = self.combo_subcat.currentLayer()
        if subcat is None or not subcat.isValid():
            self.lbl_status.setText('No sub-catchment layer selected.')
            return

        drainage = self.combo_drainage.currentLayer()
        if drainage is None or not drainage.isValid():
            self.lbl_status.setText('No drainage direction raster selected.')
            return

        accum = self.combo_accum.currentLayer()
        if accum is None or not accum.isValid():
            self.lbl_status.setText('No flow accumulation raster selected.')
            return

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Tracing flow paths — please wait…')
        self.progress.setValue(0)
        QApplication.processEvents()

        class _FB(QgsProcessingFeedback):
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
                self._lbl.setText(f'! {msg.strip()[-115:]}')
                QApplication.processEvents()

            def reportError(self, error, fatal=False):
                if fatal:
                    self._lbl.setText(f'✗ {error.strip()[-115:]}')
                    QApplication.processEvents()

        fb = _FB(self.progress, self.lbl_status)

        out_folder = ''
        try:
            from ..pipeline_state import get_pipeline_state
            out_folder = get_pipeline_state('project_folder')
        except Exception:
            pass

        if out_folder:
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, 'Longest_Flow_Path.shp')
        else:
            out_path = 'TEMPORARY_OUTPUT'

        try:
            params = {
                'INPUT_SUBCATCHMENTS': subcat,
                'INPUT_DRAINAGE':      drainage,
                'INPUT_ACCUMULATION':  accum,
                'SIMPLIFY_TOLERANCE':  self.sb_simplify.value(),
                'MIN_AREA_HA':         self.sb_min_area.value(),
                'EXTEND_ALONG_STREAM': self.cb_extend_stream.isChecked(),
                'OUTPUT':              out_path,
            }

            streams_lyr = self.combo_streams.currentLayer()
            if streams_lyr is not None and streams_lyr.isValid():
                params['INPUT_STREAMS'] = streams_lyr

            dem_lyr = self.combo_dem.currentLayer()
            if dem_lyr is not None and dem_lyr.isValid():
                params['INPUT_DEM'] = dem_lyr

            result = processing.run(self._ALGO_ID, params, feedback=fb)
            self._load_output(result)
            self.lbl_status.setText('Done — Longest Flow Path loaded.')
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Longest Flow Path calculation complete.\nThe result has been loaded into the project.')

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.combo_subcat,  'catchments', 'subcatchments')
            set_combo_to_layer(self.combo_drainage, 'drainage')
            set_combo_to_layer(self.combo_accum,    'accumulation')
            set_combo_to_layer(self.combo_streams,  'streams')
            set_combo_to_layer(self.combo_dem,      'burned_dem', 'dem')
        except Exception:
            pass

    # ── Load output ───────────────────────────────────────────────────────────

    def _load_output(self, result):
        val = result.get('OUTPUT')
        if not val:
            return
        if isinstance(val, QgsMapLayer):
            lyr = val
            lyr.setName('Longest Flow Path')
        else:
            lyr = QgsVectorLayer(str(val), 'Longest Flow Path', 'ogr')
        if lyr.isValid():
            sym = QgsLineSymbol.createSimple({
                'color': '#B71C1C',
                'width': '0.5',
            })
            lyr.renderer().setSymbol(sym)
            QgsProject.instance().addMapLayer(lyr)
            try:
                from ..pipeline_state import save_pipeline_state
                save_pipeline_state('flow_paths',         'Longest Flow Path')
                save_pipeline_state('flow_path_id_field', 'FLOWPATH_ID')
            except Exception:
                pass
        self.iface.mapCanvas().refresh()
