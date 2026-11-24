# -*- coding: utf-8 -*-
"""Dock tab — Subcatchment Merge / Split Editor."""

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
    QgsMapLayerProxyModel, QgsProject, QgsMapLayer,
    QgsVectorLayer, QgsProcessingFeedback,
)
from qgis.gui import QgsMapLayerComboBox
import processing

_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_GREEN = '#2E7D32'

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
"""

_HELP = {
    'tool': (
        "Interactively merge or split subcatchment polygons using a floating panel.\n\n"
        "HOW TO USE:\n"
        "1. Select a subcatchment layer above.\n"
        "2. Click 'Launch Interactive Editor' — a floating panel appears.\n"
        "3. MERGE: select 2+ polygons on the map, click Merge.\n"
        "4. SPLIT: select 1 polygon, click Split, draw a line, right-click to finish.\n"
        "5. Use Undo to step back.\n"
        "6. Click Done in the floating panel.\n"
        "7. Click 'Run (Export Result)' to save as a new layer.\n\n"
        "Note: If no editing session has been completed, Run exports the input layer as-is."
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


class MergeSplitWidget(QWidget):

    _ALGO_ID = 'subcatchmentassessment:sch_merge_split_editor_v01'

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._session = None
        self.setStyleSheet(_WIDGET_STYLE)
        self._build()

    def showEvent(self, event):
        super().showEvent(event)
        self._auto_pick_layer()

    def _auto_pick_layer(self):
        """Select the first polygon layer whose name contains 'subcatch'."""
        for lyr in QgsProject.instance().mapLayers().values():
            if (isinstance(lyr, QgsVectorLayer) and
                    'subcatch' in lyr.name().lower()):
                self.combo_layer.setLayer(lyr)
                return

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
        t = QLabel('Merge / Split Subcatchments')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Merge / Split Subcatchments', _HELP['tool'], header, size=20))
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

        sec1, g1 = _section('  Input Layer', _BLUE, '#EEF4FF', _HELP['tool'])
        row = 0
        g1.addWidget(_row_label('Subcatchment Layer'), row, 0)
        self.combo_layer = QgsMapLayerComboBox()
        self.combo_layer.setFilters(_POLYGON_FILTER)
        self.combo_layer.setAllowEmptyLayer(True)
        self.combo_layer.setCurrentIndex(0)
        g1.addWidget(self.combo_layer, row, 1)
        bv.addWidget(sec1)

        info = QLabel(
            "Select a subcatchment layer above, then click "
            "<b>Launch Interactive Editor</b> to open the editing panel.\n"
            "Merge or split features interactively, then click <b>Done</b> in the panel.\n"
            "Finally click <b>Run (Export Result)</b> below to save the output."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            'background: #FFF8E1; border: 1px solid #FFE082; border-radius: 4px;'
            'padding: 8px; font-size: 11px; color: #5D4037;'
        )
        bv.addWidget(info)

        self.btn_launch = QPushButton('  ▶  Launch Interactive Editor')
        self.btn_launch.setStyleSheet(
            f'QPushButton {{ background: {_GREEN}; color: white; border-radius: 4px;'
            f'  padding: 8px 14px; font-size: 12px; font-weight: bold; }}'
            f'QPushButton:hover   {{ background: #388E3C; }}'
            f'QPushButton:pressed {{ background: #1B5E20; }}'
            f'QPushButton:disabled {{ background: #90A4AE; }}'
        )
        self.btn_launch.clicked.connect(self._launch_editor)
        bv.addWidget(self.btn_launch)

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
        self.lbl_status = QLabel('Ready — select a layer and launch the editor.')
        self.lbl_status.setStyleSheet('font-size: 10px; color: #546E7A;')
        btn_row.addWidget(self.lbl_status, 1)

        self.btn_run = QPushButton('  ▶  Run (Export)')
        self.btn_run.setFixedWidth(120)
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

    def _launch_editor(self):
        layer = self.combo_layer.currentLayer()
        if layer is None or not layer.isValid():
            self.lbl_status.setText('No subcatchment layer selected.')
            return

        try:
            from ..algorithms.Subcatch_MergeSplit import SubcatchEditSession, _active_edit_session
            self._session = SubcatchEditSession(
                self.iface.mapCanvas(), layer,
                status_label=self.lbl_status
            )
            self._session.start()
            self.lbl_status.setText('Editor launched — use the floating panel to merge/split.')
        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Failed to launch editor.')
            _HelpDialog('Launch Failed', err, self).exec()

    def _run(self):
        layer = self.combo_layer.currentLayer()
        if layer is None or not layer.isValid():
            self.lbl_status.setText('No subcatchment layer selected.')
            return

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Exporting — please wait…')
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

        fb = _FB(self.progress, self.lbl_status)

        try:
            result = processing.run(
                self._ALGO_ID,
                {'INPUT_LAYER': layer, 'OUTPUT_LAYER': 'TEMPORARY_OUTPUT'},
                feedback=fb,
            )
            self._load_output(result)
            self.lbl_status.setText('Done — exported layer loaded into project.')
            self.progress.setValue(100)
            QMessageBox.information(self, 'Done', 'Export complete.\nThe subcatchment layer has been loaded into the project.')
        except Exception:
            err = traceback.format_exc()
            self.lbl_status.setText('Export failed — see error dialog.')
            _HelpDialog('Export Failed', err, self).exec()
        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.combo_layer, 'catchments', 'subcatchments')
        except Exception:
            pass

    def _load_output(self, result):
        val = result.get('OUTPUT_LAYER')
        if not val:
            return
        if isinstance(val, QgsMapLayer):
            val.setName('Subcatchments (Edited)')
            QgsProject.instance().addMapLayer(val)
        else:
            lyr = QgsVectorLayer(str(val), 'Subcatchments (Edited)', 'ogr')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('catchments', 'Subcatchments (Edited)')
        except Exception:
            pass
