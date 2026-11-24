# -*- coding: utf-8 -*-
"""
Custom styled dialog for Subcatchment Impervious Calculations.
Replaces the default flat QGIS Processing dialog with a banner header,
colour-coded sections, and a styled Run button.
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont, QIcon
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QCheckBox, QSpinBox, QComboBox,
    QFileDialog, QLineEdit, QProgressBar, QMessageBox,
    QScrollArea, QWidget, QFrame, QSizePolicy, QApplication,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox
from qgis.core import Qgis, QgsMapLayerProxyModel, QgsFieldProxyModel, QgsProcessingFeedback

# ---------------------------------------------------------------------------
# Qt6 / Qt5 enum compatibility
# ---------------------------------------------------------------------------
try:
    _POLY_FILTER = QgsMapLayerProxyModel.Filter.VectorLayer
except AttributeError:
    _POLY_FILTER = QgsMapLayerProxyModel.VectorLayer        # type: ignore

try:
    _NUMERIC_FILTER = QgsFieldProxyModel.Filter.Numeric
except AttributeError:
    _NUMERIC_FILTER = QgsFieldProxyModel.Numeric            # type: ignore

try:
    _ANY_FILTER = QgsFieldProxyModel.Filter.AllTypes
except AttributeError:
    _ANY_FILTER = QgsFieldProxyModel.AllTypes               # type: ignore

try:
    _EXPAND = QSizePolicy.Policy.Expanding
except AttributeError:
    _EXPAND = QSizePolicy.Expanding                         # type: ignore

try:
    _HLINE  = QFrame.Shape.HLine
    _SUNKEN = QFrame.Shadow.Sunken
except AttributeError:
    _HLINE  = QFrame.HLine                                  # type: ignore
    _SUNKEN = QFrame.Sunken                                 # type: ignore

try:
    _ALIGN_C = Qt.AlignmentFlag.AlignCenter
    _ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
except AttributeError:
    _ALIGN_C = Qt.AlignCenter                               # type: ignore
    _ALIGN_L = Qt.AlignLeft | Qt.AlignVCenter               # type: ignore

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_NAV   = '#1A237E'   # dark navy — banner
_BLUE  = '#1565C0'   # inputs section
_GREEN = '#2E7D32'   # soil section
_PURP  = '#4A148C'   # CN section
_ORNG  = '#E65100'   # comparison section
_SLATE = '#37474F'   # output section


# ---------------------------------------------------------------------------
# Help texts
# ---------------------------------------------------------------------------

_HELP = {

'tool': (
    "Subcatchment Impervious Calculations\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "PURPOSE\n"
    "Intersects subcatchment polygons with land use and (optionally) soil layers "
    "to calculate area-weighted impervious percentages and composite Curve Numbers "
    "for each subcatchment, following Auckland TP108 methodology.\n\n"
    "REQUIRED INPUTS\n"
    "  • Subcatchment polygon layer — results written back to this layer\n"
    "  • AUP land use polygon layer — must contain an impervious % field\n\n"
    "OPTIONAL INPUTS\n"
    "  • Soil / Geology layer — for soil-group breakdown and CN calculation\n"
    "  • Existing impervious extent layer — for source comparison\n\n"
    "OUTPUT FIELDS  (added to the catchment layer)\n"
    "  Imperv[ha]    Pervious area in hectares\n"
    "  Perv[ha]      Pervious area in hectares\n"
    "  TArea[ha]     Total catchment area in hectares\n"
    "  Imperv[%]     Impervious fraction\n"
    "  Perv[%]       Pervious fraction\n"
    "  [Group][ha/%) Per-soil-group pervious area (if soil enabled)\n"
    "  PervCN        Pervious curve number\n"
    "  Catch_CN      Composite catchment curve number\n\n"
    "NOTE\n"
    "All input layers must share the same CRS. Results are written in-place to "
    "the catchment layer — no separate output file is produced."
),

'primary': (
    "Primary Inputs\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Subcatchment Layer\n"
    "  The polygon layer representing your drainage catchments. New attribute "
    "fields (impervious %, area, CN) are written directly back to this layer "
    "— no separate output layer is created.\n\n"
    "Land Use Layer\n"
    "  The AUP land use polygon layer. Must share the CRS of the catchment "
    "layer. The tool intersects each land use zone with each catchment and uses "
    "the impervious % field to weight its contribution.\n\n"
    "Subcatchment ID Field\n"
    "  A unique identifier field on the catchment layer (e.g. SCID, CatchID). "
    "Used to group intersection fragments back to their parent catchment.\n\n"
    "Impervious % Field\n"
    "  Numeric field in the land use layer (range 0–100) containing the impervious "
    "surface fraction for each land use zone. Default field name: MPDIMPERV.\n\n"
    "Land Use Classification Field  (optional)\n"
    "  Text description field, e.g. ZONE_DESCR. Used only in the log file for "
    "readability — it has no effect on the calculation."
),

'soil': (
    "Soil Assessment\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "When enabled, the soil/geology layer is intersected with each subcatchment "
    "and the proportion of catchment area falling within each soil group is "
    "calculated.\n\n"
    "Output fields added per soil group:\n"
    "  [Group][ha]  Attributed pervious area in hectares\n"
    "  [Group][%]   As a % of total catchment area\n"
    "  NULL[ha/%]   Area with no soil data (gap coverage)\n\n"
    "Soil Group Layer\n"
    "  Polygon layer of soil or geology zones (e.g. Auckland Geology layer). "
    "Must share the CRS of the catchment layer.\n\n"
    "Soil Group Field\n"
    "  Field in the soil layer containing the group identifier (e.g. A / B / C / D "
    "for hydrological soil groups, or geology codes). Values become field name "
    "prefixes on the output — short codes (≤ 6 characters) are recommended."
),

'cn': (
    "Curve Number (CN)\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Calculates a composite runoff CN per subcatchment using the SCS/NRCS "
    "area-weighted method:\n\n"
    "  Catch_CN = (ImpervArea × CN_imp + PervArea × CN_perv) / TotalArea\n\n"
    "Impervious Area CN\n"
    "  CN value assigned to all impervious surfaces. Standard default: 98.\n\n"
    "Pervious CN Method\n"
    "  User-supplied: one CN value applied uniformly to all pervious area.\n"
    "  From Soil layer: computes an area-weighted CN from the CN field on the "
    "soil layer; requires Soil Assessment to be enabled.\n\n"
    "Pervious Area CN  (user-supplied)\n"
    "  Applied to all pervious area when using the User-supplied method. Typical "
    "values range from 61 (sandy soils, good cover) to 80 (clay soils).\n\n"
    "Default CN  (no soil data)\n"
    "  Fallback CN applied to pervious areas not covered by the soil layer "
    "(i.e. gap areas). Defaults to 74 if not set.\n\n"
    "CN Attribute Field  (Soil/Geology layer)\n"
    "  Numeric field on the soil layer containing a pre-assigned CN value per "
    "soil unit. Required when using the 'From Soil layer' method."
),

'comparison': (
    "Comparison  (optional)\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Compares the land-use-derived impervious % against an independently "
    "mapped impervious extent layer. The higher of the two values is used as "
    "the final result for each subcatchment.\n\n"
    "This is useful when a roof / road / hardstand mapping layer is more precise "
    "than AUP zoning percentages — particularly for industrial or commercial "
    "catchments.\n\n"
    "Additional output fields:\n"
    "  LU_Imp[%]   Land-use-derived impervious %\n"
    "  EI_Imp[%]   Existing-extent-derived impervious %\n"
    "  Imp_Src     Source used ('LU' or 'EI')\n\n"
    "Existing Impervious Layer\n"
    "  Polygon layer of known impervious surfaces (e.g. mapped roofs and "
    "roads). The intersection area between this layer and each subcatchment "
    "is divided by the catchment area to derive the impervious percentage."
),

'output': (
    "Output Options\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Check for overlapping polygons\n"
    "  Scans every input layer for overlapping features before processing. "
    "Overlapping polygons cause area double-counting and will produce incorrect "
    "results — they must be fixed in the source data. This check can be slow "
    "on large layers; enable it only when data quality is uncertain.\n\n"
    "Save Results to Log File\n"
    "  Writes a detailed per-subcatchment breakdown to a text file, including:\n"
    "    • Land use intersection area table with impervious % per zone\n"
    "    • Soil group proportion calculations\n"
    "    • Step-by-step CN derivation with intermediate values\n\n"
    "Log File Path\n"
    "  Destination for the log file. Leave blank to save to a temporary file. "
    "Click '…' to browse for a location."
),

}


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

class _HelpDialog(QDialog):
    """Lightweight white-background help popup — immune to QGIS dark themes."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title.strip())
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setSizeGripEnabled(True)

        # Force white background on the whole dialog
        self.setStyleSheet("""
            QDialog, QWidget { background-color: #FAFAFA; color: #212121; }
            QScrollArea { background: #FAFAFA; border: none; }
            QLabel       { background: transparent; color: #212121; font-size: 11px; }
            QPushButton  {
                background: #1565C0; color: white; border: none;
                border-radius: 4px; padding: 5px 22px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover   { background: #1976D2; }
            QPushButton:pressed { background: #0D47A1; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Coloured title bar matching the section colours
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f'background: {_NAV}; border-radius: 0px;')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 14, 0)
        lbl = QLabel(title.strip())
        lbl.setStyleSheet(
            'color: white; font-size: 12px; font-weight: bold; background: transparent;'
        )
        bl.addWidget(lbl)
        root.addWidget(bar)

        # Scrollable text body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame if hasattr(QFrame, 'Shape') else QFrame.NoFrame)  # type: ignore

        body = QWidget()
        body.setStyleSheet('background: #FAFAFA;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(16, 14, 16, 10)

        txt = QLabel(text)
        txt.setWordWrap(True)
        txt.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            if hasattr(Qt, 'TextInteractionFlag')
            else Qt.TextSelectableByMouse  # type: ignore
        )
        txt.setStyleSheet(
            'font-size: 11px; color: #212121; background: transparent; line-height: 150%;'
        )
        bv.addWidget(txt)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Footer with OK button
        foot = QWidget()
        foot.setStyleSheet('background: #F0F0F0; border-top: 1px solid #DCDCDC;')
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 8, 14, 10)
        fl.addStretch()
        ok = QPushButton('OK')
        ok.setFixedWidth(80)
        ok.clicked.connect(self.accept)
        fl.addWidget(ok)
        root.addWidget(foot)

        # Auto-size height to content, capped at 520 px
        txt.adjustSize()
        preferred = txt.sizeHint().height() + 36 + 56 + 28
        self.resize(480, min(preferred, 520))


def _help_btn(title, text, anchor, size=18):
    """Small circular ? button; clicking opens a white-background help dialog."""
    btn = QToolButton()
    btn.setText('?')
    btn.setFixedSize(size, size)
    try:
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
    except AttributeError:
        btn.setCursor(Qt.PointingHandCursor)       # type: ignore
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


def _sep():
    line = QFrame()
    line.setFrameShape(_HLINE)
    line.setFrameShadow(_SUNKEN)
    line.setStyleSheet('color: #CFD8DC;')
    return line


def _section(title, color, body_color, help_text=''):
    """Returns (group_widget, inner_layout) — a styled section block."""
    outer = QWidget()
    outer.setObjectName('section')
    outer.setStyleSheet(
        f'QWidget#section {{ background: {body_color}; '
        f'border: 1px solid {color}44; border-radius: 6px; }}'
    )
    v = QVBoxLayout(outer)
    v.setContentsMargins(0, 0, 0, 8)
    v.setSpacing(0)

    # Coloured title bar
    bar = QWidget()
    bar.setFixedHeight(28)
    bar.setStyleSheet(
        f'background: {color}; border-radius: 5px 5px 0 0;'
    )
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

    # Inner grid
    inner_widget = QWidget()
    inner_widget.setStyleSheet('background: transparent;')
    grid = QGridLayout(inner_widget)
    grid.setContentsMargins(12, 8, 12, 4)
    grid.setVerticalSpacing(6)
    grid.setHorizontalSpacing(8)
    grid.setColumnStretch(1, 1)
    v.addWidget(inner_widget)

    return outer, grid


def _row_label(text, optional=False):
    lbl = QLabel(text)
    suffix = '  <span style="color:#90A4AE; font-size:10px;">(optional)</span>' if optional else ''
    lbl.setText(text + suffix)
    lbl.setStyleSheet('font-size: 11px; background: transparent;')
    lbl.setAlignment(_ALIGN_L)
    return lbl


def _layer_box():
    cb = QgsMapLayerComboBox()
    cb.setFilters(_POLY_FILTER)
    cb.setAllowEmptyLayer(True)
    cb.setCurrentIndex(0)
    return cb


def _field_box(filter_type=None):
    cb = QgsFieldComboBox()
    if filter_type is not None:
        try:
            cb.setFilters(filter_type)
        except Exception:
            pass
    return cb


def _spinbox(default, lo=0, hi=100):
    sb = QSpinBox()
    sb.setRange(lo, hi)
    sb.setValue(default)
    return sb


def _checkbox(text, checked=True):
    cb = QCheckBox(text)
    cb.setChecked(checked)
    cb.setStyleSheet('background: transparent;')
    return cb


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class ImperviousWidget(QWidget):
    """Embeddable tab widget — no window chrome, designed for dock embedding."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet("""
            QgsMapLayerComboBox, QgsFieldComboBox, QComboBox, QSpinBox, QLineEdit {
                background-color: white;
                color: black;
                border: 1px solid #B0BEC5;
                border-radius: 3px;
                padding: 2px 4px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow { width: 12px; height: 12px; }
            QComboBox QAbstractItemView {
                background-color: white; color: #212121;
                selection-background-color: #1565C0; selection-color: white;
                border: 1px solid #B0BEC5;
            }
            QSpinBox::up-button, QSpinBox::down-button { width: 16px; }
            QCheckBox { color: #212121; font-size: 11px; }
        """)
        self._build()

    # ------------------------------------------------------------------ build

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Compact header strip ─────────────────────────────────────────────
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
        t = QLabel('Subcatchment Impervious Calculations')
        t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Subcatchment Impervious Calculations', _HELP['tool'], header, size=20))
        hl.addSpacing(2)
        root.addWidget(header)

        # ── Scrollable body ─────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame if hasattr(QFrame, 'Shape') else QFrame.NoFrame)  # type: ignore
        scroll.setStyleSheet('background: #F5F5F5;')

        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        # ── Section 1: Primary Inputs ────────────────────────────────────────
        sec1, g1 = _section('  Primary Inputs', _BLUE, '#EEF4FF', _HELP['primary'])
        row = 0

        g1.addWidget(_row_label('Subcatchment Layer'), row, 0)
        self.cb_catch = _layer_box()
        g1.addWidget(self.cb_catch, row, 1); row += 1

        g1.addWidget(_row_label('Land Use Layer'), row, 0)
        self.cb_lu = _layer_box()
        g1.addWidget(self.cb_lu, row, 1); row += 1

        g1.addWidget(_row_label('Subcatchment ID Field'), row, 0)
        self.fb_catch_id = _field_box(_ANY_FILTER)
        self.cb_catch.layerChanged.connect(self.fb_catch_id.setLayer)
        g1.addWidget(self.fb_catch_id, row, 1); row += 1

        g1.addWidget(_row_label('Impervious % Field'), row, 0)
        self.fb_imp = _field_box(_NUMERIC_FILTER)
        self.fb_imp.setField('MPDIMPERV')
        self.cb_lu.layerChanged.connect(self.fb_imp.setLayer)
        g1.addWidget(self.fb_imp, row, 1); row += 1

        g1.addWidget(_row_label('Land Use Classification Field', optional=True), row, 0)
        self.fb_lu_type = _field_box(_ANY_FILTER)
        self.fb_lu_type.setField('ZONE_DESCR')
        self.cb_lu.layerChanged.connect(self.fb_lu_type.setLayer)
        g1.addWidget(self.fb_lu_type, row, 1)

        bv.addWidget(sec1)

        # ── Section 2: Soil Assessment ────────────────────────────────────────
        sec2, g2 = _section('  Soil Assessment', _GREEN, '#F0FFF2', _HELP['soil'])
        row = 0

        self.chk_soil = _checkbox('Perform Soil Breakdown', checked=True)
        g2.addWidget(self.chk_soil, row, 0, 1, 2); row += 1

        g2.addWidget(_row_label('Soil Group Layer', optional=True), row, 0)
        self.cb_soil = _layer_box()
        g2.addWidget(self.cb_soil, row, 1); row += 1

        g2.addWidget(_row_label('Soil Group Field', optional=True), row, 0)
        self.fb_soil = _field_box(_ANY_FILTER)
        self.fb_soil.setField('id')
        self.cb_soil.layerChanged.connect(self.fb_soil.setLayer)
        g2.addWidget(self.fb_soil, row, 1)

        self.chk_soil.toggled.connect(self.cb_soil.setEnabled)
        self.chk_soil.toggled.connect(self.fb_soil.setEnabled)

        bv.addWidget(sec2)

        # ── Section 3: Curve Number ────────────────────────────────────────────
        sec3, g3 = _section('  Curve Number (CN)', _PURP, '#FAF0FF', _HELP['cn'])
        row = 0

        self.chk_cn = _checkbox('Calculate Weighted Curve Number', checked=True)
        g3.addWidget(self.chk_cn, row, 0, 1, 2); row += 1

        g3.addWidget(_row_label('Impervious Area CN'), row, 0)
        self.sb_cn_imp = _spinbox(98)
        g3.addWidget(self.sb_cn_imp, row, 1); row += 1

        g3.addWidget(_row_label('Pervious CN Method'), row, 0)
        self.combo_cn_method = QComboBox()
        self.combo_cn_method.addItems(['User-supplied value',
                                       'Calculate from Soil/Geology layer (weighted)'])
        g3.addWidget(self.combo_cn_method, row, 1); row += 1

        g3.addWidget(_row_label('Pervious Area CN (user-supplied)', optional=True), row, 0)
        self.sb_cn_perv = _spinbox(74)
        g3.addWidget(self.sb_cn_perv, row, 1); row += 1

        g3.addWidget(_row_label('Default CN (areas with no soil data)', optional=True), row, 0)
        self.sb_cn_default = _spinbox(74)
        g3.addWidget(self.sb_cn_default, row, 1); row += 1

        g3.addWidget(_row_label('CN Attribute Field (Soil/Geology layer)', optional=True), row, 0)
        self.fb_cn = _field_box(_NUMERIC_FILTER)
        self.fb_cn.setField('CN')
        self.cb_soil.layerChanged.connect(self.fb_cn.setLayer)
        g3.addWidget(self.fb_cn, row, 1)

        def _toggle_cn(on):
            for w in (self.sb_cn_imp, self.combo_cn_method,
                      self.sb_cn_perv, self.sb_cn_default, self.fb_cn):
                w.setEnabled(on)
        self.chk_cn.toggled.connect(_toggle_cn)

        bv.addWidget(sec3)

        # ── Section 4: Comparison ──────────────────────────────────────────────
        sec4, g4 = _section('  Comparison (optional)', _ORNG, '#FFF8F0', _HELP['comparison'])
        row = 0

        self.chk_ei = _checkbox('Compare with Existing Impervious Extent Layer', checked=False)
        g4.addWidget(self.chk_ei, row, 0, 1, 2); row += 1

        g4.addWidget(_row_label('Existing Impervious Layer', optional=True), row, 0)
        self.cb_ei = _layer_box()
        self.cb_ei.setEnabled(False)
        g4.addWidget(self.cb_ei, row, 1)

        self.chk_ei.toggled.connect(self.cb_ei.setEnabled)

        bv.addWidget(sec4)

        # ── Section 5: Output Options ──────────────────────────────────────────
        sec5, g5 = _section('  Output Options', _SLATE, '#F5F7F8', _HELP['output'])
        row = 0

        self.chk_overlaps = _checkbox(
            'Check input layers for overlapping polygons  (slow for large layers)',
            checked=False)
        g5.addWidget(self.chk_overlaps, row, 0, 1, 2); row += 1

        self.chk_log = _checkbox('Save Results to Log File', checked=True)
        g5.addWidget(self.chk_log, row, 0, 1, 2); row += 1

        g5.addWidget(_row_label('Log File Path', optional=True), row, 0)
        log_row = QHBoxLayout()
        self.le_log = QLineEdit()
        self.le_log.setPlaceholderText('[Save to temporary file]')
        btn_log = QPushButton('…')
        btn_log.setFixedWidth(28)
        btn_log.setStyleSheet(
            'QPushButton { background: white; border: 1px solid #B0BEC5;'
            '  border-radius: 3px; padding: 2px 6px; font-size: 11px; color: black; }'
            'QPushButton:hover { background: #ECEFF1; }'
        )
        btn_log.clicked.connect(self._browse_log)
        log_row.addWidget(self.le_log)
        log_row.addWidget(btn_log)
        g5.addLayout(log_row, row, 1)

        self.chk_log.toggled.connect(self.le_log.setEnabled)
        self.chk_log.toggled.connect(btn_log.setEnabled)

        bv.addWidget(sec5)
        bv.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ─────────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet('background: #ECEFF1; border-top: 1px solid #CFD8DC;')
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(14, 8, 14, 12)
        fl.setSpacing(6)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            'QProgressBar { border: 1px solid #B0BEC5; border-radius: 4px;'
            '  background: white; height: 16px; text-align: center; font-size: 10px; }'
            'QProgressBar::chunk { background: #1565C0; border-radius: 3px; }'
        )
        fl.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.lbl_status = QLabel('')
        self.lbl_status.setStyleSheet('font-size: 10px; color: #546E7A;')
        btn_row.addWidget(self.lbl_status, 1)

        self.btn_run = QPushButton('  ▶  Run')
        self.btn_run.setFixedWidth(100)
        self.btn_run.setStyleSheet(
            f'QPushButton {{ background: {_BLUE}; color: white; border-radius: 4px;'
            f'  padding: 6px 14px; font-size: 12px; font-weight: bold; }}'
            f'QPushButton:hover {{ background: #1976D2; }}'
            f'QPushButton:pressed {{ background: #0D47A1; }}'
            f'QPushButton:disabled {{ background: #90A4AE; }}'
        )
        self.btn_run.clicked.connect(self._run)

        btn_row.addWidget(self.btn_run)
        fl.addLayout(btn_row)

        # ── Version strip ───────────────────────────────────────────────────
        try:
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(
            f'Developer: D Magaju   ·   Version: Beta-01   ·   QGIS: {_qgis_ver}'
        )
        ver_lbl.setAlignment(_ALIGN_C)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        fl.addWidget(ver_lbl)

        root.addWidget(footer)

    # ------------------------------------------------------------------ slots

    def _browse_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Log File', '', 'Text files (*.txt)')
        if path:
            self.le_log.setText(path)

    def _run(self):
        catch_layer = self.cb_catch.currentLayer()
        lu_layer    = self.cb_lu.currentLayer()
        if catch_layer is None or lu_layer is None:
            QMessageBox.warning(self, 'Missing Input',
                                'Please select both a Subcatchment layer and a Land Use layer.')
            return

        catch_id_field = self.fb_catch_id.currentField()
        imp_field      = self.fb_imp.currentField()
        if not catch_id_field or not imp_field:
            QMessageBox.warning(self, 'Missing Fields',
                                'Please select the Subcatchment ID field and Impervious % field.')
            return

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Running…')
        self.progress.setValue(0)
        QApplication.processEvents()

        params = {
            'INPUT_CATCHMENT':        catch_layer,
            'INPUT_LANDUSE':          lu_layer,
            'INPUT_CATCHMENT_ID_FIELD': catch_id_field,
            'INPUT_IMPERVIOUS_FIELD': imp_field,
            'INPUT_LANDUSE_TYPE_FIELD': self.fb_lu_type.currentField() or None,

            'PERFORM_SOIL_ASSESSMENT': self.chk_soil.isChecked(),
            'INPUT_SOIL':              self.cb_soil.currentLayer() if self.chk_soil.isChecked() else None,
            'INPUT_SOIL_GROUP_FIELD':  self.fb_soil.currentField() if self.chk_soil.isChecked() else None,

            'PERFORM_CN_CALCULATION':  self.chk_cn.isChecked(),
            'CN_IMP':                  self.sb_cn_imp.value(),
            'CN_PERV_METHOD':          self.combo_cn_method.currentIndex(),
            'CN_PERV_USER_VALUE':      self.sb_cn_perv.value(),
            'CN_DEFAULT_VALUE':        self.sb_cn_default.value(),
            'CN_SOIL_FIELD':           self.fb_cn.currentField() or None,

            'USE_EXISTING_IMP':        self.chk_ei.isChecked(),
            'INPUT_EXISTING_IMP':      self.cb_ei.currentLayer() if self.chk_ei.isChecked() else None,

            'CHECK_OVERLAPS':          self.chk_overlaps.isChecked(),
            'SAVE_LOG':                self.chk_log.isChecked(),
            'LOG_FILE_PATH':           self.le_log.text().strip() or None,
        }

        class _FB(QgsProcessingFeedback):
            def __init__(self, bar, status_lbl):
                super().__init__()
                self._bar = bar
                self._lbl = status_lbl

            def setProgress(self, v):
                self._bar.setValue(int(v))
                QApplication.processEvents()

            def pushInfo(self, msg):
                self._lbl.setText(msg[-80:] if len(msg) > 80 else msg)
                QApplication.processEvents()

            def pushWarning(self, msg):
                self._lbl.setText(f'⚠ {msg[-75:]}')
                QApplication.processEvents()

            def reportError(self, msg, fatal=False):
                self._lbl.setText(f'✗ {msg[-75:]}')
                QApplication.processEvents()

        feedback = _FB(self.progress, self.lbl_status)

        try:
            import processing as _proc
            from qgis.core import QgsProcessingContext
            ctx = QgsProcessingContext()
            _proc.run(
                'subcatchmentassessment:calculate_impervious_area',
                params,
                context=ctx,
                feedback=feedback,
            )
            self.progress.setValue(100)
            self.lbl_status.setText('✓  Complete — fields added to catchment layer.')
            QMessageBox.information(
                self, 'Done',
                'Impervious calculation complete.\n'
                'New fields have been added to the catchment layer.'
            )
        except Exception as exc:
            self.lbl_status.setText(f'✗  {str(exc)[:80]}')
            QMessageBox.critical(self, 'Error', f'Algorithm failed:\n\n{exc}')
        finally:
            self.btn_run.setEnabled(True)

    def refresh_inputs(self):
        try:
            from ..pipeline_state import set_combo_to_layer
            set_combo_to_layer(self.cb_catch,
                               'slope_catchments', 'catchments', 'subcatchments')
        except Exception:
            pass
