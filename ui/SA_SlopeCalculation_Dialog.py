# -*- coding: utf-8 -*-
"""Dock tab — Subcatchment Slope Calculation (TP108)."""

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
    QgsMapLayerProxyModel, QgsProject, QgsMapLayer,
    QgsVectorLayer, QgsProcessingFeedback,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsFileWidget
import processing

_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_SLATE = '#37474F'

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
    _POLYGON_FILTER = QgsMapLayerProxyModel.Filter.PolygonLayer
except AttributeError:
    _POLYGON_FILTER = QgsMapLayerProxyModel.PolygonLayer

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer

_WIDGET_STYLE = """
    QgsMapLayerComboBox, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {
        background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px;
    }
    QComboBox::drop-down { border: none; }
    QgsFieldComboBox { background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px; }
"""

_HELP = {
    'tool': (
        "Calculates catchment slope (Sc_TP108) and flow path length (Catch_L_m) "
        "using the Auckland TP108 equal-area method.\n\n"
        "Prerequisites:\n"
        "• All layers must share the same CRS.\n"
        "• DEM must fully cover all flow paths.\n"
        "• Subcatchment and flow path ID fields must match.\n\n"
        "Outputs:\n"
        "• Catchment polygon layer with Sc_TP108 and Catch_L_m fields.\n"
        "• Optional elevation profile PNG plots saved to an output folder.\n\n"
        "Sampling Methods:\n"
        "• User-specified interval — densifies flow path at a fixed spacing.\n"
        "• At flow path vertices only — uses the existing vertices.\n\n"
        "Output Modes:\n"
        "• New output file — creates a copy with new fields added.\n"
        "• Edit input layer in-place — adds fields directly to the source."
    ),
    'adv': (
        "Sampling Method\n"
        "  'User-specified interval' densifies the flow path at the given spacing "
        "before sampling the DEM.  A smaller interval increases accuracy but is slower.\n\n"
        "Output Plot Folder (optional)\n"
        "  When provided, a PNG elevation profile is saved for each flow path."
    ),
}


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
        self._dlg.resize(460, min(preferred, 500))

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


def _field_combo(layer_combo, field_type=None):
    cb = QgsFieldComboBox()
    cb.setAllowEmptyFieldName(True)
    layer_combo.layerChanged.connect(cb.setLayer)
    if layer_combo.currentLayer():
        cb.setLayer(layer_combo.currentLayer())
    return cb


class SlopeCalculationWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_slope_calculation_v01'

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet(_WIDGET_STYLE)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

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
        t = QLabel('Slope Calculation (TP108)')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Slope Calculation', _HELP['tool'], header, size=20))
        hl.addSpacing(2)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NoFrame)
        scroll.setStyleSheet('background: #F5F5F5;')
        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        sec1, g1 = _section('  Inputs', _BLUE, '#EEF4FF', _HELP['tool'])
        row = 0

        g1.addWidget(_row_label('Flow Path Layer'), row, 0)
        self.combo_flowpath = QgsMapLayerComboBox()
        self.combo_flowpath.setFilters(_LINE_FILTER)
        self.combo_flowpath.setAllowEmptyLayer(True)
        self.combo_flowpath.setCurrentIndex(0)
        g1.addWidget(self.combo_flowpath, row, 1); row += 1

        g1.addWidget(_row_label('Flow Path ID Field'), row, 0)
        self.field_fp_id = _field_combo(self.combo_flowpath)
        g1.addWidget(self.field_fp_id, row, 1); row += 1

        g1.addWidget(_row_label('Catchment Layer'), row, 0)
        self.combo_catchment = QgsMapLayerComboBox()
        self.combo_catchment.setFilters(_POLYGON_FILTER)
        self.combo_catchment.setAllowEmptyLayer(True)
        self.combo_catchment.setCurrentIndex(0)
        g1.addWidget(self.combo_catchment, row, 1); row += 1

        g1.addWidget(_row_label('Catchment ID Field'), row, 0)
        self.field_cat_id = _field_combo(self.combo_catchment)
        g1.addWidget(self.field_cat_id, row, 1); row += 1

        g1.addWidget(_row_label('DEM Raster'), row, 0)
        self.combo_dem = QgsMapLayerComboBox()
        self.combo_dem.setFilters(_RASTER_FILTER)
        self.combo_dem.setAllowEmptyLayer(True)
        self.combo_dem.setCurrentIndex(0)
        g1.addWidget(self.combo_dem, row, 1)

        bv.addWidget(sec1)

        sec2, g2 = _section('  Advanced', _SLATE, '#ECEFF1', _HELP['adv'])
        row = 0

        g2.addWidget(_row_label('Sampling Method'), row, 0)
        self.combo_sampling = QComboBox()
        self.combo_sampling.addItems(['User-specified interval', 'At flow path vertices only'])
        self.combo_sampling.currentIndexChanged.connect(self._toggle_interval)
        g2.addWidget(self.combo_sampling, row, 1); row += 1

        g2.addWidget(_row_label('Sampling Interval (m)'), row, 0)
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.1, 9999.0)
        self.spin_interval.setValue(10.0)
        self.spin_interval.setSuffix('  m')
        self.spin_interval.setDecimals(1)
        g2.addWidget(self.spin_interval, row, 1); row += 1

        g2.addWidget(_row_label('Output Mode'), row, 0)
        self.combo_output_mode = QComboBox()
        self.combo_output_mode.addItems(['New output file', 'Edit input catchment layer in-place'])
        g2.addWidget(self.combo_output_mode, row, 1); row += 1

        g2.addWidget(_row_label('Plot Output Folder'), row, 0)
        self.fw_plots = QgsFileWidget()
        try:
            self.fw_plots.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        except AttributeError:
            self.fw_plots.setStorageMode(QgsFileWidget.GetDirectory)
        self.fw_plots.setFilePath('')
        g2.addWidget(self.fw_plots, row, 1)

        bv.addWidget(sec2)
        bv.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

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
            from ..algorithms.Subcatch_SlopeCalculation import __version__ as _algo_ver
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

        self._toggle_interval(0)

    def _toggle_interval(self, index):
        self.spin_interval.setEnabled(index == 0)

    def _run(self):
        flowpath  = self.combo_flowpath.currentLayer()
        catchment = self.combo_catchment.currentLayer()
        dem       = self.combo_dem.currentLayer()

        if flowpath is None or not flowpath.isValid():
            self.lbl_status.setText('No flow path layer selected.'); return
        if catchment is None or not catchment.isValid():
            self.lbl_status.setText('No catchment layer selected.'); return
        if dem is None or not dem.isValid():
            self.lbl_status.setText('No DEM raster selected.'); return

        fp_id  = self.field_fp_id.currentField()
        cat_id = self.field_cat_id.currentField()
        if not fp_id:
            self.lbl_status.setText('Flow path ID field not selected.'); return
        if not cat_id:
            self.lbl_status.setText('Catchment ID field not selected.'); return

        output_mode = self.combo_output_mode.currentIndex()
        plot_folder = self.fw_plots.filePath().strip() or None

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
                self._lbl.setText(f'⚠ {msg.strip()[-115:]}'); QApplication.processEvents()
            def reportError(self, error, fatal=False):
                if fatal:
                    self._lbl.setText(f'✗ {error.strip()[-115:]}'); QApplication.processEvents()

        fb = _FB(self.progress, self.lbl_status)

        try:
            params = {
                'INPUT_FLOWPATHS':    flowpath,
                'FLOWPATH_ID_FIELD':  fp_id,
                'INPUT_CATCHMENTS':   catchment,
                'CATCHMENT_ID_FIELD': cat_id,
                'INPUT_DEM':          dem,
                'SAMPLING_METHOD':    self.combo_sampling.currentIndex(),
                'DENSIFY_INTERVAL':   self.spin_interval.value(),
                'OUTPUT_MODE':        output_mode,
            }
            if output_mode == 0:
                params['OUTPUT_CATCHMENTS'] = 'TEMPORARY_OUTPUT'
            if plot_folder:
                params['OUTPUT_PLOT_FOLDER'] = plot_folder

            result = processing.run(self._ALGO_ID, params, feedback=fb)

            if output_mode == 0:
                self._load_output(result)
                self.lbl_status.setText('Done — slope layer loaded into project.')
            else:
                self.lbl_status.setText('Done — Sc_TP108 / Catch_L_m saved to catchment layer.')
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Slope calculation complete.\nSc_TP108 and Catch_L_m fields have been calculated.')

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer, get_pipeline_state
            set_combo_to_layer(self.combo_flowpath,  'flow_paths')
            set_combo_to_layer(self.combo_catchment, 'catchments', 'subcatchments')
            set_combo_to_layer(self.combo_dem,       'burned_dem', 'dem')
            # Explicitly push the current layer into each field combo.
            # Don't rely solely on the layerChanged signal — it may not fire if the
            # layer was already set, and setField() will silently fail on an empty model.
            fp_layer  = self.combo_flowpath.currentLayer()
            cat_layer = self.combo_catchment.currentLayer()
            if fp_layer:
                self.field_fp_id.setLayer(fp_layer)
            if cat_layer:
                self.field_cat_id.setLayer(cat_layer)
            # Flow path ID — the Longest Flow Path algorithm always outputs 'FLOWPATH_ID'
            fp_field = get_pipeline_state('flow_path_id_field') or 'FLOWPATH_ID'
            # Catchment ID — saved from Stream & Catchment; infer from layer fields if not saved
            cat_field = get_pipeline_state('catchment_id_field')
            if not cat_field and cat_layer:
                layer_field_names = {f.name() for f in cat_layer.fields()}
                for candidate in ('SubcatchID', 'basin_id'):
                    if candidate in layer_field_names:
                        cat_field = candidate
                        break
            if fp_field:
                self.field_fp_id.setField(fp_field)
            if cat_field:
                self.field_cat_id.setField(cat_field)
        except Exception:
            pass

    def _load_output(self, result):
        val = result.get('OUTPUT_CATCHMENTS')
        if not val:
            return
        if isinstance(val, QgsMapLayer):
            val.setName('Subcatchments (Slope)')
            QgsProject.instance().addMapLayer(val)
        else:
            lyr = QgsVectorLayer(str(val), 'Subcatchments (Slope)', 'ogr')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('slope_catchments', 'Subcatchments (Slope)')
        except Exception:
            pass
