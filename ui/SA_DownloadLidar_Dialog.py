# -*- coding: utf-8 -*-
"""
Custom dock-tab widget for the Download LINZ LiDAR DEM tool.
Follows the same design system as SA_Impervious_Dialog.py.

API keys are stored exclusively in QgsSettings (QGIS application settings,
i.e. the Windows Registry / ~/.config on Linux/Mac).  They are NEVER written
to any file inside the plugin directory and are therefore never distributed
or shared when the plugin is packaged or copied.
"""

import json
import os

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QLineEdit,
    QProgressBar, QMessageBox, QScrollArea, QFrame,
    QDoubleSpinBox, QFileDialog, QApplication,
    QComboBox, QInputDialog, QDialog,
)
from qgis.gui import QgsExtentGroupBox, QgsProjectionSelectionWidget, QgsRubberBand
from qgis.core import (
    Qgis, QgsSettings, QgsCoordinateReferenceSystem, QgsReferencedRectangle,
    QgsCoordinateTransform, QgsProject, QgsProcessingFeedback,
    QgsRasterLayer, QgsGeometry, QgsRectangle, QgsWkbTypes,
)

# ---------------------------------------------------------------------------
# NZ region bounds in NZTM2000 (EPSG:2193) — mirrors the algorithm constant.
# Used only for the live region-hint label in the dialog; no network calls.
# ---------------------------------------------------------------------------
_REGION_BOUNDS = {
    'Northland':           (1620000, 1720000, 6040000, 6200000),
    'Auckland':            (1700000, 1820000, 5870000, 5990000),
    'Waikato':             (1700000, 1850000, 5740000, 5880000),
    'Bay of Plenty':       (1780000, 1940000, 5760000, 5870000),
    'Gisborne':            (1870000, 2000000, 5620000, 5760000),
    'Hawkes Bay':          (1820000, 1990000, 5490000, 5650000),
    'Taranaki':            (1640000, 1740000, 5620000, 5740000),
    'Manawatu-Whanganui':  (1700000, 1860000, 5440000, 5640000),
    'Wellington':          (1730000, 1810000, 5380000, 5470000),
    'Nelson-Marlborough':  (1530000, 1700000, 5340000, 5480000),
    'West Coast':          (1330000, 1570000, 5100000, 5380000),
    'Canterbury':          (1430000, 1650000, 5060000, 5320000),
    'Otago':               (1230000, 1490000, 4900000, 5150000),
    'Southland':           (1060000, 1320000, 4760000, 5010000),
}

_NZTM = QgsCoordinateReferenceSystem('EPSG:2193')

# ---------------------------------------------------------------------------
# Stylesheet applied to QgsProjectionSelectionDialog.
# Required because QGIS dark theme colours the native dialog black.
# Apply to EVERY dialog that opens a CRS selector — never rely on inheritance.
# ---------------------------------------------------------------------------
_CRS_DIALOG_STYLE = """
QDialog, QWidget, QFrame, QAbstractScrollArea {
    background-color: #FAFAFA; color: #212121;
}
QListView, QTreeView, QTableView {
    background-color: white; color: #212121;
    alternate-background-color: #F5F5F5;
    selection-background-color: #BBDEFB;
    selection-color: #0D47A1;
}
QHeaderView::section {
    background-color: #ECEFF1; color: #212121;
    border: 1px solid #CFD8DC; padding: 3px 6px;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: white; color: #212121;
    border: 1px solid #B0BEC5; border-radius: 3px; padding: 2px 4px;
}
QGroupBox {
    color: #212121; border: 1px solid #CFD8DC;
    border-radius: 4px; margin-top: 8px; padding-top: 6px;
}
QGroupBox::title { color: #212121; subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLabel { color: #212121; background: transparent; }
QCheckBox, QRadioButton { color: #212121; background: transparent; }
QPushButton {
    background-color: #1565C0; color: white; border: none;
    border-radius: 4px; padding: 5px 16px;
}
QPushButton:hover   { background-color: #1976D2; }
QPushButton:pressed { background-color: #0D47A1; }
QTabBar::tab { background: #ECEFF1; color: #212121; padding: 4px 10px;
    border: 1px solid #CFD8DC; border-bottom: none; }
QTabBar::tab:selected { background: white; color: #1565C0; font-weight: bold; }
QSplitter::handle { background-color: #ECEFF1; }
QScrollBar:vertical   { background: #F5F5F5; width: 12px; }
QScrollBar:horizontal { background: #F5F5F5; height: 12px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #B0BEC5; border-radius: 3px; min-length: 20px;
}
"""


# ---------------------------------------------------------------------------
# Shared design constants
# ---------------------------------------------------------------------------
_NAV   = '#1A237E'
_BLUE  = '#1565C0'
_GREEN = '#2E7D32'
_SLATE = '#37474F'

try:
    _ALIGN_C = Qt.AlignmentFlag.AlignCenter
    _ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
except AttributeError:
    _ALIGN_C = Qt.AlignCenter                        # type: ignore
    _ALIGN_L = Qt.AlignLeft | Qt.AlignVCenter        # type: ignore

try:
    _HLINE  = QFrame.Shape.HLine
    _SUNKEN = QFrame.Shadow.Sunken
except AttributeError:
    _HLINE  = QFrame.HLine                           # type: ignore
    _SUNKEN = QFrame.Sunken                          # type: ignore

try:
    _PASSWORD = QLineEdit.EchoMode.Password
    _NORMAL   = QLineEdit.EchoMode.Normal
except AttributeError:
    _PASSWORD = QLineEdit.Password                   # type: ignore
    _NORMAL   = QLineEdit.Normal                     # type: ignore

from ..dialog_style import watch_for_popup

# ---------------------------------------------------------------------------
# Secure API key store  — QgsSettings only, never touches plugin files
# ---------------------------------------------------------------------------

class _ApiKeyStore:
    """
    Manages saved LINZ API keys via QgsSettings.

    Storage location (read-only from the plugin's perspective):
      Windows : HKEY_CURRENT_USER\\Software\\QGIS\\QGIS3\\...
      Linux   : ~/.config/QGIS/QGIS3/...
      macOS   : ~/Library/Preferences/QGIS/QGIS3/...

    These locations are completely outside the plugin directory and are
    therefore NEVER included in plugin distributions or source commits.
    """

    _KEY = 'SubcatchmentAssessment/lidar_api_keys'

    @classmethod
    def load(cls):
        """Return list of {'label': str, 'key': str} dicts, sorted by label."""
        raw = QgsSettings().value(cls._KEY, None)
        if raw is None:
            return []
        try:
            entries = json.loads(str(raw)) if isinstance(raw, str) else list(raw)
            # Validate structure — drop malformed entries silently
            return sorted(
                [e for e in entries if isinstance(e, dict) and 'label' in e and 'key' in e],
                key=lambda e: e['label'].lower(),
            )
        except Exception:
            return []

    @classmethod
    def save(cls, label, key):
        """Add or update a key entry. No-op if label or key is blank."""
        label, key = label.strip(), key.strip()
        if not label or not key:
            return
        entries = cls.load()
        for e in entries:
            if e['label'] == label:
                e['key'] = key
                break
        else:
            entries.append({'label': label, 'key': key})
        QgsSettings().setValue(cls._KEY, json.dumps(entries))

    @classmethod
    def delete(cls, label):
        """Remove the entry with the given label."""
        entries = [e for e in cls.load() if e['label'] != label]
        QgsSettings().setValue(cls._KEY, json.dumps(entries))


# ---------------------------------------------------------------------------
# Help texts
# ---------------------------------------------------------------------------
_HELP = {

'tool': (
    "Download LINZ LiDAR DEM\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "PURPOSE\n"
    "Downloads all LINZ 1m LiDAR DEM surveys that intersect a drawn map extent "
    "as separate GeoTIFFs with metadata sidecars. Falls back to the LINZ 8m DEM "
    "(2012) via WCS if no 1m LiDAR coverage exists for the area.\n\n"
    "SOURCE SELECTION  (automatic)\n"
    "  1. NZ 1m LiDAR DEM — AWS open-data STAC catalog. No API key needed.\n"
    "     One GeoTIFF per survey year — surveys from different years may use\n"
    "     different vertical datums and should not be merged without verification.\n"
    "  2. NZ 8m DEM (2012) — LINZ WCS fallback. Requires a free API key.\n\n"
    "HOW GEOGRAPHIC FILTERING WORKS\n"
    "The tool identifies which NZ region your extent falls in and limits the\n"
    "STAC catalog search to surveys in that region. It then checks each survey's\n"
    "bounding box against your extent, and finally pre-filters individual tiles\n"
    "in parallel (16 workers). Only tiles that genuinely overlap your extent are\n"
    "downloaded — the log messages you see during the search are progress updates,\n"
    "not extra downloads.\n\n"
    "OUTPUT FILES  (per survey)\n"
    "  DEM_LiDAR_1m_<year>.tif         Clipped raster at requested resolution\n"
    "  DEM_LiDAR_1m_<year>_metadata.json  Full STAC collection JSON\n"
    "  DEM_LiDAR_1m_<year>_metadata.txt   LINZ-style metadata report\n"
    "  download_summary.txt            Aggregate summary for all files\n\n"
    "PREREQUISITES\n"
    "  • Internet access to AWS Open Data (STAC)\n"
    "  • LINZ API key only if the 8m DEM fallback is needed\n"
    "  • GDAL Python bindings (bundled with QGIS)\n\n"
    "NETWORK & CONNECTIVITY\n"
    "The tool downloads tiles using two methods, tried in order:\n\n"
    "  1. GDAL /vsicurl/ streaming  (preferred)\n"
    "     Tiles are streamed directly from AWS — fast and efficient.\n"
    "     Requires GDAL's own network (curl) layer to reach AWS.\n\n"
    "  2. Direct HTTP download  (automatic fallback)\n"
    "     If /vsicurl/ fails the tool automatically retries by downloading each\n"
    "     tile as a file to a temporary folder, then processes them locally.\n"
    "     This method uses Python's urllib which honours the OS/system proxy\n"
    "     settings — so it works in corporate environments where IT firewalls\n"
    "     or proxies block GDAL's curl layer.\n\n"
    "  What triggers the fallback?\n"
    "  The fallback is triggered automatically when GDAL cannot open the remote\n"
    "  tiles. Common causes in office environments:\n"
    "    • Corporate firewall blocking outbound HTTPS to AWS\n"
    "    • A web proxy that GDAL does not pick up automatically\n"
    "    • SSL certificate inspection by security software\n\n"
    "  If both methods fail, open the first tile URL shown in the log in a web\n"
    "  browser. If it does not load, the firewall is blocking AWS — ask IT to\n"
    "  whitelist nz-elevation.s3.ap-southeast-2.amazonaws.com.\n\n"
    "  Note: the fallback downloads full tile files to a system temp folder\n"
    "  before processing. Ensure a few hundred MB of free disk space on C:\\"
),

'area': (
    "Area & Resolution\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Download Extent\n"
    "  Draw the area on the map canvas using the Draw on Canvas button, or enter\n"
    "coordinates manually. All LiDAR surveys that overlap this extent will be\n"
    "downloaded as separate files.\n\n"
    "  The NZ Region indicator updates as you draw — it shows which regions the\n"
    "  tool will search, so you can confirm the extent is in the right place\n"
    "  before clicking Download.\n\n"
    "  Tip: Keep extents as small as practical. Large extents can span many survey\n"
    "  tiles and take several minutes to pre-filter and download.\n\n"
    "Output Resolution [m]\n"
    "  Pixel size of the output GeoTIFF. Native source resolution is 1m.\n"
    "  Any value ≥ 1m resamples the data (faster, smaller file).\n"
    "  Typical: 1.0m (native), 2.0m, 5.0m."
),

'output': (
    "Output Settings\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Output CRS\n"
    "  Coordinate Reference System for the output raster.\n"
    "  Default: EPSG:2193 (NZTM2000) — the standard NZ projection.\n"
    "  Selecting another CRS will reproject the raster on the fly."
),

'project_folder': (
    "Project Folder\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "The working folder for this project. Two things use it:\n\n"
    "  1. Download LiDAR DEM — a timestamped subfolder "
    "'LINZ_YYYYMMDD_HHMMSS' is created inside it, and all downloaded files "
    "for this run are placed there.\n\n"
    "  2. Later steps in the pipeline (e.g. Streams and Catchment — flow "
    "accumulation and flow direction rasters) default to saving their "
    "generated files here too, instead of QGIS's temporary scratch folder, "
    "so everything for one project ends up in one place.\n\n"
    "Default: ~/Downloads"
),

'api': (
    "LINZ API Key  (optional)\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "WHEN IS IT NEEDED?\n"
    "Required only if no 1m LiDAR coverage exists for your extent and the tool\n"
    "falls back to the LINZ 8m DEM (2012). For most of NZ's urban areas (including\n"
    "Auckland) 1m LiDAR is available and no API key is needed.\n\n"
    "HOW TO GET A FREE KEY\n"
    "  1. Go to data.linz.govt.nz\n"
    "  2. Sign in or create a free account\n"
    "  3. Username menu → API Keys → Create new API key\n"
    "  4. Copy the key and paste it here, then click Save\n\n"
    "SECURITY\n"
    "Saved keys are stored in QGIS application settings (Windows Registry /\n"
    "~/.config on Linux). They are stored on THIS machine only and are NEVER\n"
    "written to any plugin file, committed to source control, or distributed\n"
    "when the plugin is shared. Each user's QGIS installation has its own\n"
    "independent key store."
),

}


# ---------------------------------------------------------------------------
# Shared UI helpers (mirrors SA_Impervious_Dialog.py pattern)
# ---------------------------------------------------------------------------

class _HelpDialog:
    """White-background help popup — immune to QGIS dark themes."""

    def __init__(self, title, text, parent=None):
        from qgis.PyQt.QtWidgets import QDialog
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
        try:
            scroll.setFrameShape(QFrame.Shape.NoFrame)
        except AttributeError:
            scroll.setFrameShape(QFrame.NoFrame)             # type: ignore

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
    try:
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
    except AttributeError:
        btn.setCursor(Qt.PointingHandCursor)                 # type: ignore
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
    grid.setVerticalSpacing(8)
    grid.setHorizontalSpacing(8)
    grid.setColumnStretch(1, 1)
    v.addWidget(inner)
    return outer, grid


def _row_label(text, optional=False):
    lbl = QLabel(text)
    if optional:
        lbl.setText(
            text + '  <span style="color:#90A4AE;font-size:10px;">(optional)</span>'
        )
    lbl.setStyleSheet('font-size: 11px; background: transparent;')
    lbl.setAlignment(_ALIGN_L)
    return lbl


def _icon_btn(symbol, tooltip, width=28):
    btn = QPushButton(symbol)
    btn.setFixedWidth(width)
    btn.setToolTip(tooltip)
    btn.setStyleSheet(
        'QPushButton { background: white; border: 1px solid #B0BEC5;'
        '  border-radius: 3px; padding: 2px 4px; font-size: 11px; color: #37474F; }'
        'QPushButton:hover   { background: #E3F2FD; }'
        'QPushButton:pressed { background: #BBDEFB; }'
    )
    return btn


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class DownloadLidarWidget(QWidget):
    """Embeddable tab widget for the Download LINZ LiDAR DEM tool."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._rubber_band = None
        self.setStyleSheet("""
            QDoubleSpinBox, QLineEdit, QComboBox {
                background-color: white; color: black;
                border: 1px solid #B0BEC5; border-radius: 3px;
                padding: 2px 4px; font-size: 11px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 16px; }
            QComboBox::drop-down { border: none; }
            QgsExtentGroupBox { font-size: 11px; }
        """)
        self._build()

    # ------------------------------------------------------------------ build

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Compact header ────────────────────────────────────────────────────
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
        t = QLabel('Download LINZ LiDAR DEM')
        t.setStyleSheet(
            'color: white; font-size: 12px; font-weight: bold; background: transparent;'
        )
        hl.addWidget(t, 1)
        hl.addWidget(_help_btn('Download LINZ LiDAR DEM', _HELP['tool'], header, size=20))
        hl.addSpacing(2)
        root.addWidget(header)

        # ── Scrollable body ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            scroll.setFrameShape(QFrame.Shape.NoFrame)
        except AttributeError:
            scroll.setFrameShape(QFrame.NoFrame)             # type: ignore
        scroll.setStyleSheet('background: #F5F5F5;')

        body = QWidget()
        body.setStyleSheet('background: #F5F5F5;')
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 14, 14, 14)
        bv.setSpacing(10)

        # ── Section 0: Project Folder ────────────────────────────────────────
        sec0, g0 = _section('  Project Folder', _SLATE, '#F5F7F8', _HELP['project_folder'])
        row = 0

        g0.addWidget(_row_label('Project Folder'), row, 0)
        folder_row = QHBoxLayout()
        self.le_folder = QLineEdit()
        self.le_folder.setPlaceholderText(
            os.path.join(os.path.expanduser('~'), 'Downloads')
        )
        self.le_folder.editingFinished.connect(self._save_project_folder)
        btn_folder = _icon_btn('…', 'Browse for project folder')
        btn_folder.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.le_folder)
        folder_row.addWidget(btn_folder)
        g0.addLayout(folder_row, row, 1); row += 1

        try:
            from ..pipeline_state import get_pipeline_state
            saved_folder = get_pipeline_state('project_folder')
            if saved_folder:
                self.le_folder.setText(saved_folder)
        except Exception:
            pass

        bv.addWidget(sec0)

        # ── Section 1: Area & Resolution ─────────────────────────────────────
        sec1, g1 = _section('  Area & Resolution', _BLUE, '#EEF4FF', _HELP['area'])
        row = 0

        g1.addWidget(_row_label('Download Extent'), row, 0)
        self.extent_widget = QgsExtentGroupBox()
        self.extent_widget.setMapCanvas(self.iface.mapCanvas())
        self.extent_widget.setOutputCrs(_NZTM)
        self.extent_widget.setCurrentExtent(
            self.iface.mapCanvas().extent(),
            self.iface.mapCanvas().mapSettings().destinationCrs(),
        )
        self.extent_widget.setStyleSheet('background: white; font-size: 11px;')
        g1.addWidget(self.extent_widget, row, 1); row += 1

        # Live region indicator — updates whenever extent changes
        self.lbl_region = QLabel('  Draw an extent to see the NZ region')
        self.lbl_region.setStyleSheet(
            'font-size: 10px; color: #78909C; background: transparent; padding: 0 2px;'
        )
        g1.addWidget(self.lbl_region, row, 0, 1, 2); row += 1

        g1.addWidget(_row_label('Output Resolution [m]'), row, 0)
        self.sb_res = QDoubleSpinBox()
        self.sb_res.setRange(0.5, 100.0)
        self.sb_res.setSingleStep(0.5)
        self.sb_res.setDecimals(1)
        self.sb_res.setValue(1.0)
        self.sb_res.setSuffix(' m')
        g1.addWidget(self.sb_res, row, 1)

        bv.addWidget(sec1)

        # Connect extent change signal to region detector (delayed 300ms to avoid
        # firing on every keystroke)
        self._region_timer = QTimer()
        self._region_timer.setSingleShot(True)
        self._region_timer.timeout.connect(self._update_region_hint)
        try:
            self.extent_widget.extentChanged.connect(
                lambda _: self._region_timer.start(300)
            )
        except Exception:
            pass  # signal not available on this QGIS build — region hint stays static

        # ── Section 2: Output Settings ────────────────────────────────────────
        sec2, g2 = _section('  Output Settings', _GREEN, '#F0FFF2', _HELP['output'])
        row = 0

        g2.addWidget(_row_label('Output CRS'), row, 0)
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem.fromEpsgId(2193))
        watch_for_popup(self.crs_widget, _CRS_DIALOG_STYLE)
        g2.addWidget(self.crs_widget, row, 1); row += 1

        bv.addWidget(sec2)

        # ── Section 3: LINZ API Key ───────────────────────────────────────────
        sec3, g3 = _section('  LINZ API Key  (optional)', _SLATE, '#F5F7F8', _HELP['api'])
        row = 0

        # Saved-keys dropdown
        g3.addWidget(_row_label('Saved Keys', optional=True), row, 0)
        self.combo_keys = QComboBox()
        self.combo_keys.addItem('— enter new key below —')
        g3.addWidget(self.combo_keys, row, 1); row += 1

        # API key field + show/hide + save + delete
        g3.addWidget(_row_label('API Key', optional=True), row, 0)
        key_row = QHBoxLayout()
        key_row.setSpacing(4)

        self.le_api = QLineEdit()
        self.le_api.setEchoMode(_PASSWORD)
        self.le_api.setPlaceholderText('Paste your LINZ API key here…')
        key_row.addWidget(self.le_api, 1)

        self.btn_show = _icon_btn('👁', 'Show / hide key', width=30)
        self.btn_show.setCheckable(True)
        self.btn_show.toggled.connect(
            lambda on: self.le_api.setEchoMode(_NORMAL if on else _PASSWORD)
        )
        key_row.addWidget(self.btn_show)

        btn_save_key = _icon_btn('💾', 'Save this key to local QGIS settings', width=30)
        btn_save_key.clicked.connect(self._save_api_key)
        key_row.addWidget(btn_save_key)

        self.btn_del_key = _icon_btn('✕', 'Delete selected saved key', width=30)
        self.btn_del_key.setEnabled(False)
        self.btn_del_key.clicked.connect(self._delete_api_key)
        key_row.addWidget(self.btn_del_key)

        g3.addLayout(key_row, row, 1); row += 1

        # Security note
        sec_note = QLabel(
            '🔒  Keys are stored in QGIS settings on this machine only — '
            'never in plugin files, never shared or distributed.'
        )
        sec_note.setWordWrap(True)
        sec_note.setStyleSheet(
            'font-size: 10px; color: #37474F; background: transparent; padding: 0 2px;'
        )
        g3.addWidget(sec_note, row, 0, 1, 2)

        bv.addWidget(sec3)
        bv.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Footer ────────────────────────────────────────────────────────────
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

        self.btn_run = QPushButton('  ▶  Download')
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
        fl.addLayout(btn_row)

        # Version strip
        try:
            from ..algorithms.Subcatch_DownloadLidar import __version__ as _ver
        except Exception:
            _ver = 'V01'
        try:
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(
            f'Developer: D Magaju   ·   Version: {_ver}   ·   QGIS: {_qgis_ver}'
        )
        ver_lbl.setAlignment(_ALIGN_C)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;')
        fl.addWidget(ver_lbl)

        root.addWidget(footer)

        # Populate the saved-keys dropdown after all widgets are created
        self._reload_key_combo()

        # Wire dropdown selection
        self.combo_keys.currentIndexChanged.connect(self._on_key_selected)

    # ---------------------------------------------------------- region hint

    def _update_region_hint(self):
        """Detects which NZ regions intersect the current extent and updates the label."""
        try:
            ext = self.extent_widget.outputExtent()   # in output CRS (NZTM by default)
            out_crs = self.extent_widget.outputCrs()

            # Reproject extent to NZTM if output CRS differs
            if out_crs.authid() != 'EPSG:2193':
                xform = QgsCoordinateTransform(out_crs, _NZTM, QgsProject.instance())
                ext = xform.transformBoundingBox(ext)

            xmin, xmax = ext.xMinimum(), ext.xMaximum()
            ymin, ymax = ext.yMinimum(), ext.yMaximum()

            if ext.isEmpty() or (xmin == 0 and xmax == 0):
                self.lbl_region.setText('  Draw an extent to see the NZ region')
                self.lbl_region.setStyleSheet(
                    'font-size: 10px; color: #78909C; background: transparent; padding: 0 2px;'
                )
                return

            hits = [
                name for name, (rx0, rx1, ry0, ry1) in _REGION_BOUNDS.items()
                if xmax >= rx0 and xmin <= rx1 and ymax >= ry0 and ymin <= ry1
            ]

            if hits:
                self.lbl_region.setText(
                    '  Region: ' + ', '.join(sorted(hits)) +
                    '  — only surveys in this region will be searched'
                )
                self.lbl_region.setStyleSheet(
                    'font-size: 10px; color: #1565C0; background: transparent; padding: 0 2px;'
                )
            else:
                self.lbl_region.setText(
                    '  ⚠ Extent outside known NZ regions — '
                    'full catalog scan will run (may be slow)'
                )
                self.lbl_region.setStyleSheet(
                    'font-size: 10px; color: #E65100; background: transparent; padding: 0 2px;'
                )
        except Exception:
            pass

    # ---------------------------------------------------------- API key UI

    def _reload_key_combo(self):
        self.combo_keys.blockSignals(True)
        self.combo_keys.clear()
        self.combo_keys.addItem('— enter new key below —', userData=None)
        for entry in _ApiKeyStore.load():
            self.combo_keys.addItem(entry['label'], userData=entry['key'])
        self.combo_keys.blockSignals(False)
        self.btn_del_key.setEnabled(False)

    def _on_key_selected(self, idx):
        key = self.combo_keys.itemData(idx)
        if key:
            self.le_api.setText(key)
            self.btn_del_key.setEnabled(True)
        else:
            self.le_api.clear()
            self.btn_del_key.setEnabled(False)

    def _save_api_key(self):
        key = self.le_api.text().strip()
        if not key:
            QMessageBox.warning(self, 'No Key', 'Please enter an API key before saving.')
            return
        label, ok = QInputDialog.getText(
            self, 'Save API Key',
            'Enter a label for this key\n(e.g. "Personal key", "Work key"):',
        )
        if not ok or not label.strip():
            return
        _ApiKeyStore.save(label.strip(), key)
        self._reload_key_combo()
        # Select the just-saved key in the dropdown
        idx = self.combo_keys.findText(label.strip())
        if idx >= 0:
            self.combo_keys.setCurrentIndex(idx)
        QMessageBox.information(
            self, 'Key Saved',
            f'API key "{label.strip()}" saved to local QGIS settings.\n\n'
            'It is stored on this machine only and is never shared or distributed.',
        )

    def _delete_api_key(self):
        label = self.combo_keys.currentText()
        if label.startswith('—'):
            return
        reply = QMessageBox.question(
            self, 'Delete Key',
            f'Delete saved key "{label}"?\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            if hasattr(QMessageBox, 'StandardButton')
            else QMessageBox.Yes | QMessageBox.No,   # type: ignore
        )
        yes = (QMessageBox.StandardButton.Yes
               if hasattr(QMessageBox, 'StandardButton')
               else QMessageBox.Yes)                 # type: ignore
        if reply == yes:
            _ApiKeyStore.delete(label)
            self._reload_key_combo()
            self.le_api.clear()

    # ---------------------------------------------------------- map canvas helpers

    def _show_extent_rubber_band(self, ext_nztm):
        """Draw a semi-transparent rectangle on the map canvas for the download extent."""
        try:
            canvas = self.iface.mapCanvas()
            canvas_crs = canvas.mapSettings().destinationCrs()

            if canvas_crs.authid() != 'EPSG:2193':
                xf = QgsCoordinateTransform(_NZTM, canvas_crs, QgsProject.instance())
                ext_canvas = xf.transformBoundingBox(ext_nztm)
            else:
                ext_canvas = ext_nztm

            if self._rubber_band is not None:
                self._rubber_band.reset()
                self._rubber_band = None

            try:
                geom_type = QgsWkbTypes.PolygonGeometry
            except AttributeError:
                geom_type = 2                                    # type: ignore

            self._rubber_band = QgsRubberBand(canvas, geom_type)
            self._rubber_band.setColor(QColor(21, 101, 192, 45))      # blue, 18% opacity fill
            self._rubber_band.setStrokeColor(QColor(21, 101, 192, 210))  # solid blue border
            self._rubber_band.setWidth(2)
            self._rubber_band.setLineStyle(Qt.PenStyle.DashLine
                                           if hasattr(Qt, 'PenStyle')
                                           else Qt.DashLine)          # type: ignore
            self._rubber_band.setToGeometry(QgsGeometry.fromRect(ext_canvas))
            self._rubber_band.show()
            canvas.refresh()
        except Exception:
            pass

    def _hide_extent_rubber_band(self):
        """Remove the download-extent overlay from the map canvas."""
        try:
            if self._rubber_band is not None:
                self._rubber_band.reset()
                self._rubber_band = None
            self.iface.mapCanvas().refresh()
        except Exception:
            pass

    def _load_downloaded_layers(self, folder):
        """
        Load every .tif in *folder* as a raster layer and zoom the canvas to
        the combined extent of all successfully loaded layers.
        Returns the count of layers added.
        """
        try:
            import glob as _glob
            tif_files = sorted(_glob.glob(os.path.join(folder, '*.tif')))
            if not tif_files:
                return 0

            added = []
            for path in tif_files:
                name = os.path.splitext(os.path.basename(path))[0]
                lyr = QgsRasterLayer(path, name)
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    added.append(lyr)

            if added:
                # Save the first (latest) DEM to the pipeline state
                try:
                    from ..pipeline_state import save_pipeline_state
                    save_pipeline_state('dem', added[0].name())
                except Exception:
                    pass
                # Zoom canvas to combined extent of newly loaded layers
                canvas = self.iface.mapCanvas()
                canvas_crs = canvas.mapSettings().destinationCrs()
                combined = QgsRectangle()
                for lyr in added:
                    lyr_ext = lyr.extent()
                    if lyr.crs() != canvas_crs:
                        try:
                            xf = QgsCoordinateTransform(
                                lyr.crs(), canvas_crs, QgsProject.instance()
                            )
                            lyr_ext = xf.transformBoundingBox(lyr_ext)
                        except Exception:
                            pass
                    combined.combineExtentWith(lyr_ext)
                if not combined.isEmpty():
                    buf = max(combined.width(), combined.height()) * 0.05
                    canvas.setExtent(combined.buffered(buf))
                    canvas.refresh()

            return len(added)
        except Exception:
            return 0

    # ------------------------------------------------------------------ slots

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, 'Select Project Folder',
            self.le_folder.text() or os.path.expanduser('~'),
        )
        if folder:
            self.le_folder.setText(folder)
            self._save_project_folder()

    def _save_project_folder(self):
        try:
            from ..pipeline_state import save_pipeline_state
            save_pipeline_state('project_folder', self.le_folder.text().strip())
        except Exception:
            pass

    def _run(self):
        ext = self.extent_widget.outputExtent()
        if ext.isEmpty():
            QMessageBox.warning(self, 'No Extent', 'Please draw or enter a download extent.')
            return

        crs = self.crs_widget.crs()
        if not crs.isValid():
            QMessageBox.warning(self, 'Invalid CRS', 'Please select a valid output CRS.')
            return

        out_folder = self.le_folder.text().strip() or os.path.join(
            os.path.expanduser('~'), 'Downloads'
        )
        self._save_project_folder()
        api_key = self.le_api.text().strip()

        self.btn_run.setEnabled(False)
        self.lbl_status.setText('Connecting to LINZ STAC catalog…')
        self.progress.setValue(0)
        self._show_extent_rubber_band(ext)   # transparent overlay on map canvas
        QApplication.processEvents()

        ext_crs = self.extent_widget.outputCrs()
        ref_rect = QgsReferencedRectangle(ext, ext_crs)

        params = {
            'EXTENT':        ref_rect,
            'RESOLUTION':    self.sb_res.value(),
            'API_KEY':       api_key,
            'OUTPUT_CRS':    crs,
            'OUTPUT_FOLDER': out_folder,
        }

        class _FB(QgsProcessingFeedback):
            def __init__(self, bar, lbl):
                super().__init__()
                self._bar = bar
                self._lbl = lbl

            def setProgress(self, v):
                self._bar.setValue(int(v))
                QApplication.processEvents()

            def pushInfo(self, msg):
                self._lbl.setText(msg[-90:] if len(msg) > 90 else msg)
                QApplication.processEvents()

            def pushWarning(self, msg):
                self._lbl.setText(f'⚠ {msg[-85:]}')
                QApplication.processEvents()

            def reportError(self, msg, fatal=False):
                self._lbl.setText(f'✗ {msg[-85:]}')
                QApplication.processEvents()

        feedback = _FB(self.progress, self.lbl_status)

        try:
            import processing as _proc
            from qgis.core import QgsProcessingContext
            ctx = QgsProcessingContext()
            result = _proc.run(
                'subcatchmentassessment:tm_download_lidar_v01',
                params,
                context=ctx,
                feedback=feedback,
            )
            self.progress.setValue(100)
            out = result.get('OUTPUT_FOLDER', out_folder)

            # Load downloaded layers into the QGIS project and zoom to them
            self.lbl_status.setText('Loading layers into project…')
            QApplication.processEvents()
            n = self._load_downloaded_layers(out)

            self.lbl_status.setText(
                f'✓  Complete · {n} layer{"s" if n != 1 else ""} added to project'
            )
            QMessageBox.information(
                self, 'Download Complete',
                f'LiDAR data downloaded successfully.\n\n'
                f'Output folder:\n{out}\n\n'
                f'{n} raster layer{"s" if n != 1 else ""} added to the project '
                f'and map zoomed to extent.',
            )
        except Exception as exc:
            self.lbl_status.setText(f'✗  {str(exc)[:90]}')
            QMessageBox.critical(self, 'Error', f'Download failed:\n\n{exc}')
        finally:
            self.btn_run.setEnabled(True)
            self._hide_extent_rubber_band()   # always clean up the canvas overlay
