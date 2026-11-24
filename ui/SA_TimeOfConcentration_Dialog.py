# -*- coding: utf-8 -*-
"""Dock tab — Subcatchment Time of Concentration."""

__author__  = "Dipendra Magaju"
__version__ = "V01"

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar,
    QComboBox, QDoubleSpinBox, QCheckBox, QSizePolicy, QMessageBox,
    QFrame, QScrollArea, QDialog, QApplication,
)
from qgis.core import (
    QgsMapLayerProxyModel, QgsProject, QgsMapLayer,
    QgsVectorLayer, QgsProcessingFeedback,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox
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
    _POLYGON_FILTER = QgsMapLayerProxyModel.Filter.PolygonLayer
except AttributeError:
    _POLYGON_FILTER = QgsMapLayerProxyModel.PolygonLayer

_WIDGET_STYLE = """
    QgsMapLayerComboBox, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {
        background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px;
    }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView {
        background-color: white; color: #212121;
        selection-background-color: #1565C0; selection-color: white;
        border: 1px solid #B0BEC5;
    }
    QgsFieldComboBox { background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px; }
"""

_HELP = {
    'tool': (
        "Calculates Time of Concentration (ToC) in minutes for each subcatchment using "
        "the Auckland TP108 formula:\n\n"
        "    t_c = 0.14 × C × L^0.66 × (CN/(200-CN))^-0.55 × Sc^-0.30\n\n"
        "where:\n"
        "  L  = Catchment flow path length (km)\n"
        "  CN = SCS Curve Number\n"
        "  Sc = Equal-area catchment slope (m/m)\n"
        "  C  = Channelisation factor (default 0.6)\n\n"
        "Prerequisites:\n"
        "• CRS must be projected and use metres.\n"
        "• CN must be between 0 and 100 (exclusive).\n"
        "• Minimum ToC is enforced at 10 minutes.\n"
        "• Zero or negative slope is clamped to 0.0001 m/m.\n\n"
        "Field defaults match the output from the Slope Calculation tool."
    ),
    'adv': (
        "Output Mode\n"
        "  'Separate output layer' creates a new layer with ToC_min field added.\n"
        "  'Update input layer' writes ToC_min directly into the source layer.\n\n"
        "Check overlapping polygons\n"
        "  Validates that subcatchments do not overlap.  "
        "Slow for large datasets — disable if already validated."
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


def _field_combo(layer_combo):
    cb = QgsFieldComboBox()
    cb.setAllowEmptyFieldName(True)
    layer_combo.layerChanged.connect(cb.setLayer)
    if layer_combo.currentLayer():
        cb.setLayer(layer_combo.currentLayer())
    return cb


class TimeOfConcentrationWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_time_of_concentration_v01'

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
        t = QLabel('Time of Concentration (TP108)')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Time of Concentration', _HELP['tool'], header, size=20))
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

        g1.addWidget(_row_label('Subcatchment Layer'), row, 0)
        self.combo_layer = QgsMapLayerComboBox()
        self.combo_layer.setFilters(_POLYGON_FILTER)
        self.combo_layer.setAllowEmptyLayer(True)
        self.combo_layer.setCurrentIndex(0)
        g1.addWidget(self.combo_layer, row, 1); row += 1

        g1.addWidget(_row_label('Length Field (m)'), row, 0)
        self.field_length = _field_combo(self.combo_layer)
        self.field_length.setField('Catch_L_m')
        g1.addWidget(self.field_length, row, 1); row += 1

        g1.addWidget(_row_label('Curve Number (CN)'), row, 0)
        self._cn_mode = QComboBox()
        self._cn_mode.addItems(['From field', 'Fixed value for all'])
        g1.addWidget(self._cn_mode, row, 1); row += 1

        self._lbl_cn_field = _row_label('CN field')
        g1.addWidget(self._lbl_cn_field, row, 0)
        self.field_cn = _field_combo(self.combo_layer)
        self.field_cn.setField('Catch_CN')
        g1.addWidget(self.field_cn, row, 1); row += 1

        self._lbl_cn_value = _row_label('CN value')
        g1.addWidget(self._lbl_cn_value, row, 0)
        self._spin_cn = QDoubleSpinBox()
        self._spin_cn.setRange(0.1, 99.9)
        self._spin_cn.setValue(75.0)
        self._spin_cn.setDecimals(1)
        self._spin_cn.setToolTip('SCS Curve Number applied to all subcatchments (0 < CN < 100)')
        g1.addWidget(self._spin_cn, row, 1); row += 1

        self._cn_mode.currentIndexChanged.connect(self._on_cn_mode_changed)
        self._on_cn_mode_changed(0)

        g1.addWidget(_row_label('Slope Field (m/m)'), row, 0)
        self.field_slope = _field_combo(self.combo_layer)
        self.field_slope.setField('Sc_TP108')
        g1.addWidget(self.field_slope, row, 1); row += 1

        g1.addWidget(_row_label('Channelisation Factor C'), row, 0)
        self.spin_c = QDoubleSpinBox()
        self.spin_c.setRange(0.01, 10.0)
        self.spin_c.setValue(0.6)
        self.spin_c.setDecimals(2)
        self.spin_c.setToolTip('Default TP108 value is 0.6 (open channel / overland flow)')
        g1.addWidget(self.spin_c, row, 1)

        bv.addWidget(sec1)

        sec2, g2 = _section('  Advanced', _SLATE, '#ECEFF1', _HELP['adv'])
        row = 0

        g2.addWidget(_row_label('Output Mode'), row, 0)
        self.combo_output_mode = QComboBox()
        self.combo_output_mode.addItems(['Separate output layer', 'Update input layer'])
        self.combo_output_mode.setCurrentIndex(1)
        g2.addWidget(self.combo_output_mode, row, 1); row += 1

        g2.addWidget(_row_label('Check overlapping polygons'), row, 0)
        self.chk_overlaps = QCheckBox()
        self.chk_overlaps.setChecked(False)
        self.chk_overlaps.setToolTip('Validates that subcatchments do not overlap. Slow for large datasets.')
        g2.addWidget(self.chk_overlaps, row, 1)

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
            from ..algorithms.Subcatch_TimeOfConcentration import __version__ as _algo_ver
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

    def _on_cn_mode_changed(self, index):
        from_field = (index == 0)
        self._lbl_cn_field.setVisible(from_field)
        self.field_cn.setVisible(from_field)
        self._lbl_cn_value.setVisible(not from_field)
        self._spin_cn.setVisible(not from_field)

    def _run(self):
        layer = self.combo_layer.currentLayer()
        if layer is None or not layer.isValid():
            self.lbl_status.setText('No subcatchment layer selected.'); return

        field_L  = self.field_length.currentField()
        field_S  = self.field_slope.currentField()
        cn_from_field = (self._cn_mode.currentIndex() == 0)
        field_CN      = self.field_cn.currentField() if cn_from_field else ''
        default_cn    = self._spin_cn.value()        if not cn_from_field else -1.0

        if not field_L:
            self.lbl_status.setText('Length field not selected.'); return
        if cn_from_field and not field_CN:
            self.lbl_status.setText('Curve Number field not selected.'); return
        if not field_S:
            self.lbl_status.setText('Slope field not selected.'); return

        output_mode = self.combo_output_mode.currentIndex()

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
                'INPUT_CATCHMENT': layer,
                'FIELD_LENGTH':    field_L,
                'FIELD_CN':        field_CN,
                'DEFAULT_CN':      default_cn,
                'FIELD_SLOPE':     field_S,
                'CHANNEL_FACTOR':  self.spin_c.value(),
                'OUTPUT_MODE':     output_mode,
                'CHECK_OVERLAPS':  self.chk_overlaps.isChecked(),
                'SAVE_LOG':        False,
            }
            if output_mode == 0:
                params['OUTPUT_LAYER'] = 'TEMPORARY_OUTPUT'

            result = processing.run(self._ALGO_ID, params, feedback=fb)

            if output_mode == 0:
                self._load_output(result)
                self.lbl_status.setText('Done — ToC layer loaded into project.')
            else:
                self.lbl_status.setText('Done — ToC_min saved to source layer.')
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Time of Concentration calculation complete.\nToC_min fields have been calculated.')

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.combo_layer, 'slope_catchments', 'catchments')
            lyr = self.combo_layer.currentLayer()
            if lyr:
                self.field_length.setLayer(lyr)
                self.field_cn.setLayer(lyr)
                self.field_slope.setLayer(lyr)
                self.field_length.setField('Catch_L_m')
                self.field_cn.setField('Catch_CN')
                self.field_slope.setField('Sc_TP108')
        except Exception:
            pass

    def _load_output(self, result):
        val = result.get('OUTPUT_LAYER')
        if not val:
            return
        if isinstance(val, QgsMapLayer):
            val.setName('Subcatchments (ToC)')
            QgsProject.instance().addMapLayer(val)
        else:
            lyr = QgsVectorLayer(str(val), 'Subcatchments (ToC)', 'ogr')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('toc_catchments', 'Subcatchments (ToC)')
        except Exception:
            pass
