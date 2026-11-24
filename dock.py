# -*- coding: utf-8 -*-
"""
Subcatchment Assessment — Docked panel.

Tabs (in order):
  1.  Download DEM
  2.  DEM Channel Burn
  3.  Streams and Catchment
  4.  Merge and Split
  5.  Longest Flow Path
  6.  Slope Calculation
  7.  Impervious Calculation
  8.  Time of Concentration
  9.  Flow Calculation [TP108]
  (10. Catchment Loading — disabled, code kept in SA_Loading_Dialog.py)

  Generate Rainfall Data tab (SA_RainfallData_Dialog.py) is written but
  not yet wired in here — removed from the dock until its scope is
  defined; re-add between Time of Concentration and Flow Calculation.
"""

from qgis.PyQt.QtCore import Qt, QSize, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QLabel, QPushButton,
    QStackedWidget, QSizePolicy,
)

try:
    _LeftDock  = Qt.DockWidgetArea.LeftDockWidgetArea
    _RightDock = Qt.DockWidgetArea.RightDockWidgetArea
except AttributeError:
    _LeftDock  = Qt.LeftDockWidgetArea
    _RightDock = Qt.RightDockWidgetArea

try:
    _AlignCenter  = Qt.AlignmentFlag.AlignCenter
    _WA_Passthru  = Qt.WidgetAttribute.WA_TransparentForMouseEvents
except AttributeError:
    _AlignCenter  = Qt.AlignCenter
    _WA_Passthru  = Qt.WA_TransparentForMouseEvents

_NAV  = '#1A237E'
_BLUE = '#1565C0'

# No explicit font-size — inherits the system default (Segoe UI ~9pt on Windows).
_BTN_INACTIVE = (
    'QPushButton {'
    '  background: #CFD8DC;'
    '  border: 1px solid #90A4AE; border-radius: 3px;'
    '  padding: 2px 8px;'
    '}'
    'QPushButton:hover {'
    '  background: #B0BEC5;'
    '}'
)

_BTN_ACTIVE = (
    f'QPushButton {{'
    f'  background: {_BLUE};'
    f'  border: 1px solid #0D47A1; border-radius: 3px;'
    f'  padding: 2px 8px;'
    f'}}'
    f'QPushButton:hover {{'
    f'  background: #1976D2;'
    f'}}'
)

_LBL_INACTIVE = 'color: #37474F; background: transparent; font-weight: normal;'
_LBL_ACTIVE   = 'color: white;   background: transparent; font-weight: bold;'


class _TabButton(QPushButton):
    """
    A navigation tab button that word-wraps its label.

    The button itself carries no text (so QPushButton's single-line clip is avoided).
    A transparent QLabel child paints the wrapped text instead.
    """

    def __init__(self, label: str, parent=None):
        super().__init__('', parent)
        self._full_text = label
        lbl = QLabel(label, self)
        lbl.setWordWrap(True)
        lbl.setAlignment(_AlignCenter)
        lbl.setAttribute(_WA_Passthru, True)
        lbl.setStyleSheet(_LBL_INACTIVE)
        self._lbl = lbl

    def resizeEvent(self, event):
        self._lbl.setGeometry(self.rect().adjusted(4, 2, -4, -2))
        super().resizeEvent(event)

    def setActive(self, active: bool):
        self._lbl.setStyleSheet(_LBL_ACTIVE if active else _LBL_INACTIVE)

    def sizeHint(self):
        fm = self._lbl.fontMetrics()
        w  = fm.horizontalAdvance(self._full_text) + 20
        return QSize(w, _WrapTabBar._BTN_H)


class _WrapTabBar(QWidget):
    """
    Tab bar that distributes buttons equally across rows when the dock narrows.
    Equal per-row count; buttons stretch to fill the row width.
    Text never clips because _TabButton word-wraps onto a second line.
    """

    tab_changed = pyqtSignal(int)

    _BTN_H = 28   # single-line height with padding
    _PAD   = 4
    _SP    = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list[_TabButton] = []
        self._current = 0
        try:
            _exp  = QSizePolicy.Policy.Expanding
            _pref = QSizePolicy.Policy.Preferred
        except AttributeError:
            _exp  = QSizePolicy.Expanding
            _pref = QSizePolicy.Preferred
        self.setSizePolicy(_exp, _pref)

    def addTab(self, label: str) -> int:
        btn = _TabButton(label, self)
        btn.setStyleSheet(_BTN_INACTIVE)
        btn.setFixedHeight(self._BTN_H)
        idx = len(self._buttons)
        btn.clicked.connect(lambda checked=False, i=idx: self._select(i))
        self._buttons.append(btn)
        if idx == 0:
            btn.setStyleSheet(_BTN_ACTIVE)
            btn.setActive(True)
        return idx

    def _select(self, index: int):
        if self._current == index:
            return
        self._buttons[self._current].setStyleSheet(_BTN_INACTIVE)
        self._buttons[self._current].setActive(False)
        self._current = index
        self._buttons[index].setStyleSheet(_BTN_ACTIVE)
        self._buttons[index].setActive(True)
        self.tab_changed.emit(index)

    def setCurrentIndex(self, index: int):
        if 0 <= index < len(self._buttons) and index != self._current:
            self._buttons[self._current].setStyleSheet(_BTN_INACTIVE)
            self._buttons[self._current].setActive(False)
            self._current = index
            self._buttons[index].setStyleSheet(_BTN_ACTIVE)
            self._buttons[index].setActive(True)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._compute_layout(width, apply=False)

    def _compute_layout(self, width, apply=True):
        """
        Find the minimum number of rows such that n buttons split equally
        (base = n//rows; first 'extra' rows carry one more) all fit within
        the usable width by their natural sizeHint.  Then stretch each row's
        buttons to fill the full usable width.
        """
        PAD, SP, H = self._PAD, self._SP, self._BTN_H
        n = len(self._buttons)
        if n == 0:
            return PAD * 2

        btn_nat = [b.sizeHint().width() for b in self._buttons]
        usable  = max(1, width - 2 * PAD)

        rows_needed = n
        for rows in range(1, n + 1):
            base  = n // rows
            extra = n % rows
            ok    = True
            idx   = 0
            for r in range(rows):
                sz   = base + (1 if r < extra else 0)
                rnat = sum(btn_nat[idx: idx + sz]) + SP * max(0, sz - 1)
                if rnat > usable:
                    ok = False
                    break
                idx += sz
            if ok:
                rows_needed = rows
                break

        base    = n // rows_needed
        extra   = n % rows_needed
        total_h = PAD + rows_needed * H + max(0, rows_needed - 1) * SP + PAD

        if apply:
            y   = PAD
            idx = 0
            for r in range(rows_needed):
                sz    = base + (1 if r < extra else 0)
                avail = usable - SP * max(0, sz - 1)
                bw    = avail // sz
                rem   = avail - bw * sz
                x     = PAD
                for i in range(sz):
                    w = bw + (1 if i < rem else 0)
                    self._buttons[idx + i].setGeometry(x, y, w, H)
                    x += w + SP
                y   += H + SP
                idx += sz

        return total_h

    def resizeEvent(self, event):
        h = self._compute_layout(event.size().width(), apply=True)
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)
            self.updateGeometry()
        super().resizeEvent(event)

    def sizeHint(self):
        w = self.parentWidget().width() if self.parentWidget() else 460
        return QSize(w, self.heightForWidth(w))


class _WrapTabWidget(QWidget):
    """Drop-in replacement for QTabWidget with an equal-distribution wrapping tab bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar_wrap = QWidget()
        bar_wrap.setStyleSheet(f'background: {_NAV};')
        bv = QVBoxLayout(bar_wrap)
        bv.setContentsMargins(4, 4, 4, 4)
        bv.setSpacing(0)
        self._bar = _WrapTabBar(bar_wrap)
        self._bar.setStyleSheet('background: transparent;')
        bv.addWidget(self._bar)
        v.addWidget(bar_wrap)

        self._stack = QStackedWidget()
        v.addWidget(self._stack, 1)

        self._bar.tab_changed.connect(self._stack.setCurrentIndex)

    def addTab(self, widget, label):
        self._bar.addTab(label)
        self._stack.addWidget(widget)

    def setCurrentIndex(self, index):
        self._bar.setCurrentIndex(index)
        self._stack.setCurrentIndex(index)

    def currentIndex(self):
        return self._stack.currentIndex()


class SubCatchmentDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__('Auckland Catchment & Hydrology', parent)
        self.iface = iface
        self.setObjectName('SubCatchmentAssessmentDock')
        self.setAllowedAreas(_LeftDock | _RightDock)
        self._tab_widgets = []
        self._build()

    def _build(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = _WrapTabWidget()
        layout.addWidget(self.tabs)

        def _add(widget_cls, label, *args):
            w = widget_cls(*args)
            self.tabs.addTab(w, label)
            self._tab_widgets.append(w)

        # Tab 1 — Download DEM
        from .ui.SA_DownloadLidar_Dialog import DownloadLidarWidget
        _add(DownloadLidarWidget, 'Download DEM', self.iface, container)

        # Tab 2 — DEM Channel Burn
        from .ui.SA_DEMChannelBurn_Dialog import DEMChannelBurnWidget
        _add(DEMChannelBurnWidget, 'DEM Channel Burn', self.iface, container)

        # Tab 3 — Streams and Catchment
        from .ui.SA_StreamCatchment_Dialog import StreamCatchmentWidget
        _add(StreamCatchmentWidget, 'Streams and Catchment', self.iface, container)

        # Tab 4 — Merge and Split
        from .ui.SA_MergeSplit_Dialog import MergeSplitWidget
        _add(MergeSplitWidget, 'Merge and Split', self.iface, container)

        # Tab 5 — Longest Flow Path
        from .ui.SA_LongestFlowPath_Dialog import LongestFlowPathWidget
        _add(LongestFlowPathWidget, 'Longest Flow Path', self.iface, container)

        # Tab 6 — Slope Calculation
        from .ui.SA_SlopeCalculation_Dialog import SlopeCalculationWidget
        _add(SlopeCalculationWidget, 'Slope Calculation', self.iface, container)

        # Tab 7 — Impervious Calculation
        from .ui.SA_Impervious_Dialog import ImperviousWidget
        _add(ImperviousWidget, 'Impervious Calculation', self.iface, container)

        # Tab 8 — Time of Concentration
        from .ui.SA_TimeOfConcentration_Dialog import TimeOfConcentrationWidget
        _add(TimeOfConcentrationWidget, 'Time of Concentration', self.iface, container)

        # Tab 9 — Flow Calculation [TP108]
        from .ui.SA_FlowCalculation_Dialog import FlowCalculationWidget
        _add(FlowCalculationWidget, 'Flow Calculation [TP108]', self.iface, container)

        # Tab 10 — Catchment Loading (disabled — code kept in SA_Loading_Dialog.py)
        # from .ui.SA_Loading_Dialog import LoadingWidget
        # _add(LoadingWidget, 'Catchment Loading', self.iface, container)

        # Call refresh_inputs() on each tab when it becomes visible
        self.tabs._bar.tab_changed.connect(self._on_tab_changed)

        self.setWidget(container)

    def _on_tab_changed(self, idx):
        if 0 <= idx < len(self._tab_widgets):
            w = self._tab_widgets[idx]
            if hasattr(w, 'refresh_inputs'):
                w.refresh_inputs()
