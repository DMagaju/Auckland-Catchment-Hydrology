# -*- coding: utf-8 -*-
"""Dock tab — Generate Rainfall Data (placeholder, scope to be defined)."""

__author__  = "Dipendra Magaju"
__version__ = "V01"

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
)
from qgis.core import Qgis

_NAV  = '#1A237E'
_BLUE = '#1565C0'

try:
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _AlignCenter = Qt.AlignCenter                    # type: ignore

try:
    _NoFrame = QFrame.Shape.NoFrame
except AttributeError:
    _NoFrame = QFrame.NoFrame                         # type: ignore


class RainfallDataWidget(QWidget):
    """Placeholder tab — rainfall data generation workflow to be defined."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
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
        t = QLabel('Generate Rainfall Data')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
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

        msg = QLabel(
            'Coming soon.\n\n'
            'This tab will generate rainfall data for the catchment model. '
            'Scope and workflow to be defined.'
        )
        msg.setWordWrap(True)
        msg.setAlignment(_AlignCenter)
        msg.setStyleSheet(
            'font-size: 11px; color: #546E7A; background: white; '
            'border: 1px solid #CFD8DC; border-radius: 6px; padding: 20px;'
        )
        bv.addWidget(msg)
        bv.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ───────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet('background: #ECEFF1; border-top: 1px solid #CFD8DC;')
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(14, 8, 14, 10)
        try:
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(f'Developer: D Magaju   ·   Version: Beta-01   ·   QGIS: {_qgis_ver}')
        ver_lbl.setAlignment(_AlignCenter)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        fl.addWidget(ver_lbl)
        root.addWidget(footer)

    def refresh_inputs(self):
        pass
