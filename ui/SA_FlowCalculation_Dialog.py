# -*- coding: utf-8 -*-
"""Dock tab — Subcatchment Flow Calculation (Auckland TP108)."""

__author__  = "Dipendra Magaju"
__version__ = "V02"

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar,
    QComboBox, QDoubleSpinBox, QMessageBox, QCheckBox, QLineEdit,
    QFrame, QScrollArea, QDialog, QApplication,
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
    QgsFieldComboBox { background-color: white; color: black;
        border: 1px solid #B0BEC5; border-radius: 3px;
        padding: 2px 4px; font-size: 11px; }
"""

_HELP = {
    'tool': (
        "Calculates peak flow (m³/s) and total runoff volume (m³) per subcatchment using "
        "the Auckland TP108 rainfall-runoff methodology.\n\n"
        "Methods:\n"
        "• Rainfall: 24-hour temporal profile (TP108 Table 2, Existing column)\n"
        "• Loss model: SCS Curve Number (impervious CN=98 Ia=0; pervious Ia=5 mm)\n"
        "• Hydrograph: Triangular unit hydrograph convolution\n\n"
        "Design-storm approach:\n"
        "  Enter the FINAL 24-hour depth in mm.\n"
        "  Apply AEP and climate-change adjustments BEFORE entry.\n\n"
        "Required fields (defaults match tool chain output):\n"
        "  TArea[ha]  — subcatchment area in hectares\n"
        "  ToC_min    — time of concentration in minutes\n"
        "  Catch_CN   — pervious SCS Curve Number\n"
        "  Imperv[%]  — % imperviousness (0–100)\n\n"
        "Outputs: PeakFlow_m3s and TotalVol_m3 fields, per-subcatchment CSV files, "
        "and 3-panel matplotlib plots (hyetograph, hydrograph, cumulative volume)."
    ),
    'design': (
        "Design Storm Rainfall Depth (mm)\n"
        "  The FINAL 24-hour design depth in mm, including any AEP and climate-change "
        "adjustments. Do not enter the raw base depth — pre-adjust externally.\n\n"
        "Storm Event Label\n"
        "  Free-text label used in plot titles and output filenames, "
        "e.g. '1% AEP CC3.8 Design Storm'. Leave blank to use 'Storm'.\n\n"
        "Event Date (YYYY-MM-DD)\n"
        "  Date of the design storm event. Used in output filenames.\n"
        "  Leave blank to use 'NoDate'.\n\n"
        "ID Field (optional)\n"
        "  Used to label CSV files and plot titles. If blank, feature IDs are used.\n\n"
        "Output Mode\n"
        "  'New output layer'           — adds peak flow fields to a copy of the layer.\n"
        "  'Edit input layer in-place'  — writes fields directly into the source.\n"
        "  'CSV & Plots only'           — no vector layer; CSVs and plots only.\n\n"
        "Export Rainfall CSV\n"
        "  Saves the 10-minute rainfall time series to Rainfall_{date}_{label}.csv.\n"
        "  Always enabled when Output Mode is 'CSV & Plots only'.\n\n"
        "Output Folder\n"
        "  Destination for CSV and PNG plot files."
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


class FlowCalculationWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_flow_calculation_v01'

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
        t = QLabel('Flow Calculation (TP108)')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Flow Calculation', _HELP['tool'], header, size=20))
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

        # ── Section 1: Subcatchment Inputs ───────────────────────────────
        sec1, g1 = _section('  Inputs', _BLUE, '#EEF4FF', _HELP['tool'])
        row = 0

        g1.addWidget(_row_label('Subcatchment Layer'), row, 0)
        self.combo_layer = QgsMapLayerComboBox()
        self.combo_layer.setFilters(_POLYGON_FILTER)
        self.combo_layer.setAllowEmptyLayer(True)
        self.combo_layer.setCurrentIndex(0)
        g1.addWidget(self.combo_layer, row, 1); row += 1

        g1.addWidget(_row_label('Area Field (ha)'), row, 0)
        self.field_area = _field_combo(self.combo_layer)
        self.field_area.setField('TArea[ha]')
        g1.addWidget(self.field_area, row, 1); row += 1

        g1.addWidget(_row_label('ToC Field (min)'), row, 0)
        self.field_toc = _field_combo(self.combo_layer)
        self.field_toc.setField('ToC_min')
        g1.addWidget(self.field_toc, row, 1); row += 1

        g1.addWidget(_row_label('Curve Number Field'), row, 0)
        self.field_cn = _field_combo(self.combo_layer)
        self.field_cn.setField('Catch_CN')
        g1.addWidget(self.field_cn, row, 1); row += 1

        g1.addWidget(_row_label('Imperviousness Field (%)'), row, 0)
        self.field_imp = _field_combo(self.combo_layer)
        self.field_imp.setField('Imperv[%]')
        g1.addWidget(self.field_imp, row, 1)

        bv.addWidget(sec1)

        # ── Section 2: Design Storm Event ───────────────────────────────
        sec2, g2 = _section('  Design Storm Event', _SLATE, '#ECEFF1', _HELP['design'])
        row = 0

        g2.addWidget(_row_label('Design Storm Depth (mm)'), row, 0)
        self.spin_depth = QDoubleSpinBox()
        self.spin_depth.setRange(1.0, 9999.0)
        self.spin_depth.setValue(50.0)
        self.spin_depth.setDecimals(1)
        self.spin_depth.setSuffix('  mm')
        self.spin_depth.setToolTip(
            'Final 24-hour design rainfall depth in mm.\n'
            'Apply AEP and climate-change adjustments before entry.'
        )
        g2.addWidget(self.spin_depth, row, 1); row += 1

        g2.addWidget(_row_label('Storm Event Label'), row, 0)
        self.edit_label = QLineEdit()
        self.edit_label.setPlaceholderText('e.g.  1% AEP CC3.8  (optional)')
        self.edit_label.setToolTip(
            'Free-text label used in plot titles and output filenames.'
        )
        g2.addWidget(self.edit_label, row, 1); row += 1

        g2.addWidget(_row_label('Event Date (YYYY-MM-DD)'), row, 0)
        self.edit_date = QLineEdit()
        self.edit_date.setPlaceholderText('e.g.  2026-08-17  (optional)')
        self.edit_date.setToolTip(
            'Date of the design storm event. Used in output filenames.'
        )
        g2.addWidget(self.edit_date, row, 1); row += 1

        g2.addWidget(_row_label('ID Field (optional)'), row, 0)
        self.field_id = _field_combo(self.combo_layer)
        g2.addWidget(self.field_id, row, 1); row += 1

        g2.addWidget(_row_label('Output Mode'), row, 0)
        self.combo_output_mode = QComboBox()
        self.combo_output_mode.addItems([
            'New output layer',
            'Edit input layer in-place',
            'CSV & Plots only (no vector layer)',
        ])
        self.combo_output_mode.setCurrentIndex(1)
        g2.addWidget(self.combo_output_mode, row, 1); row += 1

        g2.addWidget(_row_label('Export Rainfall CSV'), row, 0)
        self.chk_rainfall_csv = QCheckBox()
        self.chk_rainfall_csv.setToolTip(
            'Also save the 10-minute rainfall time series to\n'
            'Rainfall_{date}_{label}.csv.\n'
            'Always enabled when Output Mode is "CSV & Plots only".'
        )
        self.chk_rainfall_csv.setStyleSheet('background: transparent;')
        g2.addWidget(self.chk_rainfall_csv, row, 1); row += 1

        g2.addWidget(_row_label('Output Folder'), row, 0)
        self.fw_folder = QgsFileWidget()
        try:
            self.fw_folder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        except AttributeError:
            self.fw_folder.setStorageMode(QgsFileWidget.GetDirectory)
        self.fw_folder.setFilePath('')
        g2.addWidget(self.fw_folder, row, 1)

        bv.addWidget(sec2)
        bv.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ───────────────────────────────────────────────────────
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
            from ..algorithms.Subcatch_FlowCalculation import __version__ as _algo_ver
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

    def _run(self):
        layer = self.combo_layer.currentLayer()
        if layer is None or not layer.isValid():
            self.lbl_status.setText('No subcatchment layer selected.'); return

        field_area = self.field_area.currentField()
        field_toc  = self.field_toc.currentField()
        field_cn   = self.field_cn.currentField()
        field_imp  = self.field_imp.currentField()

        if not field_area:
            self.lbl_status.setText('Area field not selected.'); return
        if not field_toc:
            self.lbl_status.setText('ToC field not selected.'); return
        if not field_cn:
            self.lbl_status.setText('Curve Number field not selected.'); return
        if not field_imp:
            self.lbl_status.setText('Imperviousness field not selected.'); return

        output_folder = self.fw_folder.filePath().strip()
        if not output_folder:
            self.lbl_status.setText('Output folder not specified.'); return

        output_mode      = self.combo_output_mode.currentIndex()
        field_id         = self.field_id.currentField() or None
        storm_label      = self.edit_label.text().strip()
        event_date       = self.edit_date.text().strip()
        export_rain_csv  = self.chk_rainfall_csv.isChecked()

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
                'INPUT_LAYER':         layer,
                'FIELD_AREA':          field_area,
                'FIELD_TOC':           field_toc,
                'FIELD_CN':            field_cn,
                'FIELD_IMP':           field_imp,
                'RAINFALL_DEPTH':      self.spin_depth.value(),
                'STORM_LABEL':         storm_label,
                'EVENT_DATE':          event_date,
                'OUTPUT_MODE':         output_mode,
                'OUTPUT_RAINFALL_CSV': export_rain_csv,
                'OUTPUT_FOLDER':       output_folder,
            }
            if field_id:
                params['FIELD_ID'] = field_id
            if output_mode == 0:
                params['OUTPUT_LAYER'] = 'TEMPORARY_OUTPUT'

            result = processing.run(self._ALGO_ID, params, feedback=fb)

            if output_mode == 0:
                self._load_output(result)
                self.lbl_status.setText('Done — flow layer loaded into project.')
            else:
                self.lbl_status.setText('Done — PeakFlow_m3s / TotalVol_m3 saved.')
            self.progress.setValue(100)
            QMessageBox.information(
                self, 'Done',
                'Flow calculation complete.\nHydrograph CSV files saved to the output folder.'
            )

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed — see error dialog.')
            _HelpDialog('Processing Failed', err, self).exec()

        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer, get_pipeline_state
            set_combo_to_layer(self.combo_layer,
                               'toc_catchments', 'slope_catchments', 'catchments')
            lyr = self.combo_layer.currentLayer()
            if lyr:
                self.field_area.setLayer(lyr)
                self.field_toc.setLayer(lyr)
                self.field_cn.setLayer(lyr)
                self.field_imp.setLayer(lyr)
                self.field_id.setLayer(lyr)
                self.field_area.setField('TArea[ha]')
                self.field_toc.setField('ToC_min')
                self.field_cn.setField('Catch_CN')
                self.field_imp.setField('Imperv[%]')
                cat_id = get_pipeline_state('catchment_id_field') or 'SubcatchID'
                self.field_id.setField(cat_id)
        except Exception:
            pass

    def _load_output(self, result):
        val = result.get('OUTPUT_LAYER')
        if not val:
            return
        if isinstance(val, QgsMapLayer):
            val.setName('Subcatchments (Flow)')
            QgsProject.instance().addMapLayer(val)
        else:
            lyr = QgsVectorLayer(str(val), 'Subcatchments (Flow)', 'ogr')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('flow_catchments', 'Subcatchments (Flow)')
        except Exception:
            pass
