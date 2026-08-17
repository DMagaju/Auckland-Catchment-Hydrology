# -*- coding: utf-8 -*-
"""Dock tab — Catchment Loading (interactive connector lines)."""

__author__  = "Dipendra Magaju"
__version__ = "V01"

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QProgressBar, QMessageBox,
    QFrame, QScrollArea, QDialog, QApplication,
)
from qgis.core import (
    QgsMapLayerProxyModel, QgsProcessingFeedback,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsFileWidget
import processing

_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_GREEN = '#2E7D32'
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

try:
    _POINT_FILTER = QgsMapLayerProxyModel.Filter.PointLayer
except AttributeError:
    _POINT_FILTER = QgsMapLayerProxyModel.PointLayer

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
        "Interactively assigns a loading node (manhole or 2D point source) "
        "to each subcatchment polygon for ICM 2D stormwater model setup.\n\n"
        "WORKFLOW:\n"
        "1. Select your subcatchment, node/manhole, and point source layers below.\n"
        "2. Choose the ID fields to identify each node and point source.\n"
        "3. Set the output file path.\n"
        "4. Click 'Launch Interactive Session' — a floating control panel appears.\n"
        "5. Click any subcatchment polygon on the map.\n"
        "6. Select 'Manhole/Node' or '2D Point Source', then pick from the list "
        "(or click on the map, or draw a new point source).\n"
        "7. A blue connector arrow is drawn from the centroid to the loading node.\n"
        "8. Repeat for all catchments. Re-click any catchment to change its assignment.\n"
        "9. Click 'Finish & Save' in the floating panel.\n\n"
        "OUTPUTS:\n"
        "• Connector arrow lines (.shp or .gpkg)\n"
        "• Fields: Catch_ID, drains_to, node_id, 2d_point_i\n"
        "• Companion .qml blue-arrow symbology file\n"
        "• drains_to / node_id / 2d_point_i saved back to the catchment layer\n\n"
        "NOTES:\n"
        "• All input layers must share the same CRS.\n"
        "• Output is only saved when 'Finish & Save' is clicked.\n"
        "• If you leave '2D Point Sources' blank, you can create points on-the-fly "
        "using 'Draw on Map' in the assignment dialog."
    ),
    'output': (
        "Output Connector Lines\n"
        "  Choose a .shp or .gpkg file path for the output connector arrows.\n"
        "  A companion .qml blue-arrow style file is saved alongside it.\n\n"
        "The connector lines represent the drainage routing from each subcatchment "
        "centroid to its assigned loading node in the ICM model."
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


class LoadingWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_loading_v01'

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
        t = QLabel('Catchment Loading')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Catchment Loading', _HELP['tool'], header, size=20))
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

        # ── Inputs ──
        sec1, g1 = _section('  Inputs', _BLUE, '#EEF4FF', _HELP['tool'])
        row = 0

        g1.addWidget(_row_label('Subcatchment Layer'), row, 0)
        self.combo_catchment = QgsMapLayerComboBox()
        self.combo_catchment.setFilters(_POLYGON_FILTER)
        self.combo_catchment.setAllowEmptyLayer(True)
        self.combo_catchment.setCurrentIndex(0)
        g1.addWidget(self.combo_catchment, row, 1); row += 1

        g1.addWidget(_row_label('Nodes / Manholes (opt.)'), row, 0)
        self.combo_nodes = QgsMapLayerComboBox()
        self.combo_nodes.setFilters(_POINT_FILTER)
        self.combo_nodes.setAllowEmptyLayer(True)
        self.combo_nodes.setCurrentIndex(0)
        g1.addWidget(self.combo_nodes, row, 1); row += 1

        g1.addWidget(_row_label('Node ID Field'), row, 0)
        self.field_node_id = _field_combo(self.combo_nodes)
        g1.addWidget(self.field_node_id, row, 1); row += 1

        g1.addWidget(_row_label('2D Point Sources (opt.)'), row, 0)
        self.combo_ps = QgsMapLayerComboBox()
        self.combo_ps.setFilters(_POINT_FILTER)
        self.combo_ps.setAllowEmptyLayer(True)
        self.combo_ps.setCurrentIndex(0)
        g1.addWidget(self.combo_ps, row, 1); row += 1

        g1.addWidget(_row_label('Point Source ID Field'), row, 0)
        self.field_ps_id = _field_combo(self.combo_ps)
        g1.addWidget(self.field_ps_id, row, 1)

        bv.addWidget(sec1)

        # ── Output ──
        sec2, g2 = _section('  Output', _SLATE, '#ECEFF1', _HELP['output'])
        row = 0

        g2.addWidget(_row_label('Connector Lines File'), row, 0)
        self.fw_output = QgsFileWidget()
        try:
            self.fw_output.setStorageMode(QgsFileWidget.StorageMode.SaveFile)
        except AttributeError:
            self.fw_output.setStorageMode(QgsFileWidget.SaveFile)
        self.fw_output.setFilter('Shapefile (*.shp);;GeoPackage (*.gpkg)')
        self.fw_output.setFilePath('')
        g2.addWidget(self.fw_output, row, 1)

        bv.addWidget(sec2)

        # ── Info ──
        info = QLabel(
            "After launching, a floating control panel appears on the QGIS canvas. "
            "Click each subcatchment polygon to assign its loading node. "
            "Click <b>Finish &amp; Save</b> in the panel when done."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            'background: #FFF8E1; border: 1px solid #FFE082; border-radius: 4px;'
            'padding: 8px; font-size: 11px; color: #5D4037;'
        )
        bv.addWidget(info)
        bv.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ──
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
        self.lbl_status = QLabel('Ready — configure inputs above then launch.')
        self.lbl_status.setStyleSheet('font-size: 10px; color: #546E7A;')
        btn_row.addWidget(self.lbl_status, 1)

        self.btn_run = QPushButton('  ▶  Launch Session')
        self.btn_run.setFixedWidth(130)
        self.btn_run.setStyleSheet(
            f'QPushButton {{ background: {_GREEN}; color: white; border-radius: 4px;'
            f'  padding: 6px 14px; font-size: 12px; font-weight: bold; }}'
            f'QPushButton:hover   {{ background: #388E3C; }}'
            f'QPushButton:pressed {{ background: #1B5E20; }}'
            f'QPushButton:disabled {{ background: #90A4AE; }}'
        )
        self.btn_run.clicked.connect(self._run)
        btn_row.addWidget(self.btn_run)
        fv.addLayout(btn_row)

        try:
            from ..algorithms.Subcatch_Loading import __version__ as _algo_ver
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
        catchment = self.combo_catchment.currentLayer()
        if catchment is None or not catchment.isValid():
            self.lbl_status.setText('No subcatchment layer selected.'); return

        nodes = self.combo_nodes.currentLayer()
        ps    = self.combo_ps.currentLayer()
        if nodes is None and ps is None:
            self.lbl_status.setText('Provide at least Nodes or 2D Point Sources layer.'); return

        output_path = self.fw_output.filePath().strip()
        if not output_path:
            self.lbl_status.setText('Output file path not set.'); return

        node_field = self.field_node_id.currentField() or ''
        ps_field   = self.field_ps_id.currentField()   or ''

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Launching session — please wait…')
        self.progress.setValue(30)
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

        fb = _FB(self.progress, self.lbl_status)

        try:
            params = {
                'CATCHMENTS': catchment,
                'OUTPUT':     output_path,
            }
            if nodes:
                params['NODES'] = nodes
                if node_field:
                    params['NODE_ID_FIELD'] = node_field
            if ps:
                params['POINT_SOURCES'] = ps
                if ps_field:
                    params['PS_ID_FIELD'] = ps_field

            processing.run(self._ALGO_ID, params, feedback=fb)

            self.lbl_status.setText(
                'Session launched — use the floating panel on the map canvas.'
            )
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Merge/Split editor launched.\nUse the floating panel on the map canvas to edit subcatchments.')

        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed to launch — see error dialog.')
            _HelpDialog('Launch Failed', err, self).exec()

        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.combo_catchment,
                               'flow_catchments', 'toc_catchments',
                               'slope_catchments', 'catchments')
        except Exception:
            pass
