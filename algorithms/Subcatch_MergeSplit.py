# -*- coding: utf-8 -*-
"""
QGIS Processing script for interactive subcatchment merge and split editing.

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V01"
__status__     = "Beta"
_LINKED_PROMPT = "SCH_SubcatchmentMergeSplitEditorPrompt_GM.txt"

from qgis.PyQt.QtCore import QCoreApplication, Qt, pyqtSignal, QObject, QTimer
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox, QSizePolicy,
)
from qgis.PyQt.QtGui import QColor, QCursor, QFont
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsProject,
    QgsCoordinateTransformContext,
    QgsSingleSymbolRenderer,
    QgsFillSymbol,
    QgsSimpleFillSymbolLayer,
)
from qgis.gui import QgsMapTool, QgsRubberBand
import processing
import os

# ── Module-level state ────────────────────────────────────────────────────────
_active_edit_session = None
_edited_layer        = None

def _release_edit_session():
    global _active_edit_session
    _active_edit_session = None


# ── SubcatchID naming helpers ─────────────────────────────────────────────────

def _parse_subcatch_id(val):
    """
    Split a SubcatchID like 'SC_0001' or 'CA_0003' into (prefix, number).
    Returns (None, None) if the value doesn't match the expected pattern.
    """
    if not val or not isinstance(val, str):
        return None, None
    parts = val.rsplit('_', 1)
    if len(parts) != 2:
        return None, None
    prefix, suffix = parts
    if not prefix or not suffix.isdigit():
        return None, None
    try:
        return prefix, int(suffix)
    except ValueError:
        return None, None


def _subcatch_info(layer):
    """
    Scan a layer's SubcatchID field and return (prefix, set_of_numbers).
    prefix is the most common prefix found; falls back to 'SC'.
    """
    idx = layer.fields().indexOf('SubcatchID')
    if idx < 0:
        return 'SC', set()
    prefix_counts: dict = {}
    nums: set = set()
    for feat in layer.getFeatures():
        p, n = _parse_subcatch_id(feat.attribute(idx))
        if p is not None:
            prefix_counts[p] = prefix_counts.get(p, 0) + 1
            nums.add(n)
    prefix = max(prefix_counts, key=prefix_counts.get) if prefix_counts else 'SC'
    return prefix, nums


def _next_subcatch_id(layer, already_used=None):
    """
    Return the next available SubcatchID string using the prefix already in
    the layer.  Gap-fill rule: scan [min, max] for the first missing number;
    if no gap exists, return max+1.
    already_used: set of integer suffixes to treat as occupied beyond what is
                  already in the layer (for batching new IDs in one call).
    """
    prefix, nums = _subcatch_info(layer)
    if already_used:
        nums = nums | already_used
    if not nums:
        return f'{prefix}_0001'
    lo, hi = min(nums), max(nums)
    for n in range(lo, hi + 1):
        if n not in nums:
            return f'{prefix}_{n:04d}'
    return f'{prefix}_{hi + 1:04d}'


def _lowest_subcatch_id(features, layer):
    """Return the SubcatchID with the smallest suffix number from a list of features."""
    idx = layer.fields().indexOf('SubcatchID')
    if idx < 0:
        return None
    best_num, best_id = None, None
    for feat in features:
        p, n = _parse_subcatch_id(feat.attribute(idx))
        if p is not None:
            if best_num is None or n < best_num:
                best_num, best_id = n, feat.attribute(idx)
    return best_id

# ── Qt constants (PyQt5 / PyQt6 compatible) ───────────────────────────────────
try:
    _Qt_StaysOnTop  = Qt.WindowType.WindowStaysOnTopHint
except AttributeError:
    _Qt_StaysOnTop  = Qt.WindowStaysOnTopHint

try:
    _Qt_CrossCursor = Qt.CursorShape.CrossCursor
except AttributeError:
    _Qt_CrossCursor = Qt.CrossCursor

try:
    _Qt_LeftButton  = Qt.MouseButton.LeftButton
except AttributeError:
    _Qt_LeftButton  = Qt.LeftButton

try:
    _Qt_RightButton = Qt.MouseButton.RightButton
except AttributeError:
    _Qt_RightButton = Qt.RightButton


class SubcatchMergeSplit(QgsProcessingAlgorithm):

    INPUT_LAYER  = 'INPUT_LAYER'
    OUTPUT_LAYER = 'OUTPUT_LAYER'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return SubcatchMergeSplit()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'sch_merge_split_editor_v01'

    def displayName(self):
        return self.tr('Subcatchment Merge Split Editor V01')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Subcatchment Merge / Split Editor\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Interactively merge or split subcatchment polygons.\n\n"
            "─────────────────────────────────────\n"
            "HOW TO USE:\n"
            "  1. Select the subcatchment layer and click\n"
            "     'Launch Editor'.\n"
            "  2. A floating panel appears.\n"
            "  3. To MERGE: select 2+ polygons on the map,\n"
            "     then click Merge.\n"
            "  4. To SPLIT: select 1 polygon, click Split,\n"
            "     draw a line across it, right-click to finish.\n"
            "  5. Use Undo to reverse the last operation.\n"
            "  6. Click Done → then click Run to save output.\n"
            "─────────────────────────────────────"
        )

    def createCustomParametersWidget(self, parent):
        try:
            from processing.gui.AlgorithmDialog import AlgorithmDialog
        except ImportError:
            return None

        dlg = AlgorithmDialog(self, False, parent)
        self._algorithm_dlg = dlg

        try:
            btn = QPushButton("▶   Launch Editor")
            btn.setToolTip(
                "Open the interactive merge/split panel.\n"
                "When done, click Run to save the output."
            )
            btn.setStyleSheet(
                "QPushButton { padding:8px; font-weight:bold; border:1px solid #aaa;"
                "  border-radius:4px; background:#f5f5f5; margin:4px 0px; }"
                "QPushButton:hover { background:#e3f0ff; border-color:#0078d4;"
                "  color:#0078d4; }"
            )
            btn.clicked.connect(self._on_launch_clicked)

            self._status_label = QLabel("")
            self._status_label.setStyleSheet(
                "color:#107c10; font-size:11px; padding:2px; margin-bottom:4px;"
            )
            self._status_label.setWordWrap(True)

            main_layout = dlg.layout()
            idx = max(0, main_layout.count() - 1)
            main_layout.insertWidget(idx,     btn)
            main_layout.insertWidget(idx + 1, self._status_label)
        except Exception:
            pass

        return dlg

    def _on_launch_clicked(self):
        try:
            from qgis.utils import iface
            if iface is None:
                raise ImportError
        except (ImportError, AttributeError):
            QMessageBox.warning(
                None, "Not Available",
                "This tool requires QGIS Desktop with an active map canvas."
            )
            return

        input_layer = None
        try:
            dlg = getattr(self, '_algorithm_dlg', None)
            if dlg is not None:
                try:
                    panel = dlg.mainWidget()
                    wrapper = getattr(panel, 'wrappers', {}).get(self.INPUT_LAYER)
                    if wrapper is not None:
                        val = wrapper.parameterValue()
                        if val:
                            lyr = QgsProject.instance().mapLayer(val)
                            if lyr and lyr.isValid():
                                input_layer = lyr
                except Exception:
                    pass
                if input_layer is None:
                    from qgis.gui import QgsMapLayerComboBox
                    for cb in dlg.findChildren(QgsMapLayerComboBox):
                        lyr = cb.currentLayer()
                        if (lyr and lyr.isValid()
                                and hasattr(lyr, 'geometryType')
                                and lyr.geometryType() == QgsWkbTypes.PolygonGeometry):
                            input_layer = lyr
                            break
        except Exception:
            pass

        if input_layer is None:
            QMessageBox.warning(
                None, "No Layer Selected",
                "Please select a polygon layer in the 'Subcatchment layer' dropdown first."
            )
            return

        global _active_edit_session
        _active_edit_session = SubcatchEditSession(
            iface.mapCanvas(), input_layer,
            status_label=getattr(self, '_status_label', None)
        )
        _active_edit_session.start()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER,
                self.tr('Subcatchment layer'),
                [QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_LAYER,
                self.tr('Output (edited) layer'),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        global _edited_layer

        source = _edited_layer
        if source is None or not source.isValid():
            source = self.parameterAsVectorLayer(
                parameters, self.INPUT_LAYER, context
            )

        if source is None or not source.isValid():
            raise QgsProcessingException(
                self.tr("No valid layer to export. "
                        "Launch the editor, make changes, click Done, then Run.")
            )

        output_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_LAYER, context
        )

        feedback.pushInfo(f"Writing {source.featureCount()} feature(s) to output...")

        _edited_layer = None

        if source.fields().indexOf('SubcatchID') >= 0:
            # Keep only SubcatchID — strip any stray fields (outlet_id, area_ha, etc.)
            # native:retainfields requires at least one field name; the branch
            # below handles the rare case where SubcatchID is absent.
            retained = processing.run(
                "native:retainfields",
                {'INPUT': source, 'FIELDS': ['SubcatchID'], 'OUTPUT': output_path},
                context=context, feedback=feedback,
            )
            out_val = retained['OUTPUT']
        else:
            # No SubcatchID field — write as-is (no field stripping).
            copied = processing.run(
                'native:fixgeometries',
                {'INPUT': source, 'METHOD': 1, 'OUTPUT': output_path},
                context=context, feedback=feedback,
            )
            out_val = copied['OUTPUT']

        feedback.pushInfo("Done.")
        return {self.OUTPUT_LAYER: out_val}


class SubcatchEditSession(QObject):

    def __init__(self, canvas, input_layer, status_label=None):
        super().__init__()
        self.canvas          = canvas
        self._layer          = input_layer
        self._status_label   = status_label
        self._prev_renderer  = None
        self._panel          = None
        self._split_tool     = None
        self._prev_tool      = canvas.mapTool()
        self._done           = False
        self._drawing_split  = False

    def start(self):
        if not self._layer.isEditable():
            self._layer.startEditing()

        self._prev_renderer = self._layer.renderer().clone()
        self._apply_outline_renderer()
        self._layer.triggerRepaint()

        self._prev_selection_color = self.canvas.selectionColor()
        self.canvas.setSelectionColor(QColor(255, 220, 0, 80))

        self._panel = SubcatchEditPanel()
        self._panel.merge_requested.connect(self._do_merge)
        self._panel.split_requested.connect(self._start_split)
        self._panel.stop_requested.connect(self._stop_current_op)
        self._panel.undo_requested.connect(self._do_undo)
        self._panel.done_requested.connect(self._do_done)
        self._panel.cancel_requested.connect(self._do_cancel)
        self._panel.show()

        try:
            from qgis.utils import iface as _iface
            _iface.setActiveLayer(self._layer)
            QTimer.singleShot(150, _iface.actionSelectFeatures().trigger)
        except Exception:
            pass

        self._layer.selectionChanged.connect(self._on_selection_changed)
        self._on_selection_changed()

    def _apply_outline_renderer(self):
        try:
            fill_layer = QgsSimpleFillSymbolLayer()
            try:
                fill_layer.setBrushStyle(Qt.BrushStyle.NoBrush)
            except AttributeError:
                fill_layer.setBrushStyle(Qt.NoBrush)
            fill_layer.setStrokeColor(QColor(30, 100, 220))
            fill_layer.setStrokeWidth(0.6)
            symbol = QgsFillSymbol()
            symbol.changeSymbolLayer(0, fill_layer)
            self._layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        except Exception:
            pass

    def _restore_renderer(self):
        if self._prev_renderer is not None:
            try:
                self._layer.setRenderer(self._prev_renderer.clone())
            except Exception:
                pass

    def _on_selection_changed(self):
        if self._panel and not self._drawing_split:
            self._panel.update_status(self._layer.selectedFeatureCount())

    def _do_merge(self):
        selected = self._layer.selectedFeatures()
        if len(selected) < 2:
            QMessageBox.warning(self._panel, "Merge",
                "Select at least 2 features on the map before merging.")
            return

        union_geom = selected[0].geometry()
        for f in selected[1:]:
            union_geom = union_geom.combine(f.geometry())

        if union_geom is None or union_geom.isEmpty():
            QMessageBox.warning(self._panel, "Merge Failed",
                "Could not compute the union geometry.")
            return

        # Merged polygon inherits the lowest-suffix SubcatchID among selected
        parent_id = _lowest_subcatch_id(selected, self._layer)

        merged = QgsFeature(self._layer.fields())
        merged.setGeometry(union_geom)
        idx = self._layer.fields().indexOf('SubcatchID')
        if parent_id and idx >= 0:
            merged.setAttribute(idx, parent_id)

        self._layer.beginEditCommand("Merge subcatchments")
        for f in selected:
            self._layer.deleteFeature(f.id())
        self._layer.addFeature(merged)
        self._layer.endEditCommand()

        self._layer.removeSelection()
        self._layer.triggerRepaint()
        ids = sorted(
            (f.attribute('SubcatchID') or '' for f in selected),
            key=lambda x: _parse_subcatch_id(x)[1] or 0,
        )
        self._panel.set_message(
            f"Merged {len(selected)} subcatchments → {parent_id}  "
            f"(freed: {', '.join(i for i in ids if i != parent_id)})"
        )

    def _start_split(self):
        selected = self._layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self._panel, "Split",
                "Select exactly 1 feature on the map before splitting.")
            return

        self._drawing_split = True
        self._panel.set_split_mode(True)

        self._split_tool = SplitLineTool(self.canvas)
        self._split_tool.line_completed.connect(self._on_split_line_done)
        self._split_tool.cancelled.connect(self._on_split_cancelled)
        self.canvas.setMapTool(self._split_tool)

    def _stop_current_op(self):
        if self._drawing_split and self._split_tool is not None:
            self._split_tool.force_cancel()
            self.canvas.unsetMapTool(self._split_tool)
            self._split_tool = None
        self._drawing_split = False
        self._panel.set_split_mode(False)
        self._layer.removeSelection()
        self._layer.triggerRepaint()
        self._panel.set_message("Operation stopped. Select a feature to continue.")

    def _on_split_line_done(self, points):
        self.canvas.unsetMapTool(self._split_tool)
        self._split_tool = None
        self._drawing_split = False
        self._panel.set_split_mode(False)

        selected = self._layer.selectedFeatures()
        if len(selected) != 1:
            self._layer.removeSelection()
            self._panel.set_message("Split cancelled — selection changed.")
            return

        feat = selected[0]
        geom = QgsGeometry(feat.geometry())

        result, new_geoms, _ = geom.splitGeometry(points, False)

        if result != 0 or not new_geoms:
            QMessageBox.warning(self._panel, "Split Failed",
                "The split line did not intersect the selected polygon.\n"
                "Make sure the line crosses from one side to the other.")
            self._layer.removeSelection()
            self._panel.set_message("Split failed — select the feature and try again.")
            self._panel.update_status(0)
            return

        all_parts = [geom] + list(new_geoms)

        # Part 0 keeps parent SubcatchID; extra parts get gap-fill or max+1.
        # Compute all new IDs upfront (layer still has parent, so its number
        # stays in _subcatch_numbers and is treated as still in use — correct,
        # because part 0 will re-use it).
        idx = self._layer.fields().indexOf('SubcatchID')
        parent_id = feat.attribute(idx) if idx >= 0 else None

        used_in_batch = set()
        _p, _n = _parse_subcatch_id(parent_id)
        if _n is not None:
            used_in_batch.add(_n)

        new_ids = [parent_id]
        for _ in range(1, len(all_parts)):
            new_id = _next_subcatch_id(self._layer, already_used=used_in_batch)
            new_ids.append(new_id)
            _p2, _n2 = _parse_subcatch_id(new_id)
            if _n2 is not None:
                used_in_batch.add(_n2)

        new_feats = []
        for i, part_geom in enumerate(all_parts):
            nf = QgsFeature(self._layer.fields())
            nf.setGeometry(part_geom)
            if idx >= 0 and new_ids[i]:
                nf.setAttribute(idx, new_ids[i])
            new_feats.append(nf)

        self._layer.beginEditCommand("Split subcatchment")
        self._layer.deleteFeature(feat.id())
        self._layer.addFeatures(new_feats)
        self._layer.endEditCommand()

        self._layer.removeSelection()
        self._layer.triggerRepaint()
        parts_desc = '  +  '.join(str(i) for i in new_ids if i)
        self._panel.set_message(
            f"Split into {len(all_parts)} parts: {parts_desc}\n"
            "Select the next feature to continue."
        )
        self._panel.update_status(0)

    def _on_split_cancelled(self):
        self.canvas.unsetMapTool(self._split_tool)
        self._split_tool = None
        self._drawing_split = False
        self._panel.set_split_mode(False)
        self._layer.removeSelection()
        self._panel.set_message("Split cancelled.")
        self._panel.update_status(0)

    def _do_undo(self):
        if self._drawing_split and self._split_tool is not None:
            self._split_tool.force_cancel()
            self.canvas.unsetMapTool(self._split_tool)
            self._split_tool = None
            self._drawing_split = False
            self._panel.set_split_mode(False)

        self._layer.undoStack().undo()
        self._layer.removeSelection()
        self._layer.triggerRepaint()
        self._panel.set_message("Undo successful.")
        self._panel.update_status(0)

    def _do_done(self):
        global _edited_layer
        self._done = True

        if self._drawing_split and self._split_tool is not None:
            self._split_tool.force_cancel()
            self.canvas.unsetMapTool(self._split_tool)
            self._split_tool = None
        self._drawing_split = False

        n = self._layer.featureCount()
        self._restore_renderer()
        self._layer.commitChanges()
        self._layer.removeSelection()
        self._layer.triggerRepaint()

        _edited_layer = self._layer

        if self._status_label is not None:
            try:
                self._status_label.setText(
                    f"✓  Editing complete — {n} subcatchment(s) ready. Click Run."
                )
            except RuntimeError:
                pass

        if self._panel:
            self._panel.close()
            self._panel = None

        if self._prev_tool:
            self.canvas.setMapTool(self._prev_tool)
        self.canvas.setSelectionColor(self._prev_selection_color)

        _release_edit_session()

        QMessageBox.information(
            None, "Editing Complete",
            f"{n} subcatchment(s) saved to layer.\n\n"
            "Click Run in the tool dialog to export the output."
        )

    def _do_cancel(self):
        if self._done:
            return

        if self._drawing_split and self._split_tool is not None:
            try:
                self._split_tool.force_cancel()
                self.canvas.unsetMapTool(self._split_tool)
            except Exception:
                pass
        self._drawing_split = False

        self._restore_renderer()
        self._layer.rollBack()
        self._layer.removeSelection()
        self._layer.triggerRepaint()

        if self._panel:
            self._panel.close()
            self._panel = None

        if self._prev_tool:
            self.canvas.setMapTool(self._prev_tool)
        self.canvas.setSelectionColor(self._prev_selection_color)

        _release_edit_session()



class SubcatchEditPanel(QDialog):

    merge_requested  = pyqtSignal()
    split_requested  = pyqtSignal()
    stop_requested   = pyqtSignal()
    undo_requested   = pyqtSignal()
    done_requested   = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle("Merge / Split Subcatchments")
        self.setWindowFlags(self.windowFlags() | _Qt_StaysOnTop)
        self.setFixedWidth(300)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("<b>Subcatchment Editor</b>")
        title.setFont(QFont("Segoe UI", 11))
        layout.addWidget(title)

        self._status = QLabel("Select features on the map to get started.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            "color:#333; background:#f0f0f0; border-radius:4px; padding:6px;"
        )
        self._status.setMinimumHeight(56)
        layout.addWidget(self._status)

        line = QFrame()
        try:
            line.setFrameShape(QFrame.Shape.HLine)
        except AttributeError:
            line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#ccc;")
        layout.addWidget(line)

        self._btn_merge = QPushButton("⊕  Merge Selected")
        self._btn_merge.setEnabled(False)
        self._btn_merge.setStyleSheet(
            "QPushButton { padding:8px; border:1px solid #aaa; border-radius:4px;"
            "  background:#f5f5f5; font-weight:bold; }"
            "QPushButton:enabled:hover { background:#e3f0ff; border-color:#0078d4;"
            "  color:#0078d4; }"
            "QPushButton:disabled { color:#aaa; }"
        )
        self._btn_merge.clicked.connect(self.merge_requested.emit)
        layout.addWidget(self._btn_merge)

        self._btn_split = QPushButton("✂  Split Selected")
        self._btn_split.setEnabled(False)
        self._btn_split.setStyleSheet(
            "QPushButton { padding:8px; border:1px solid #aaa; border-radius:4px;"
            "  background:#f5f5f5; font-weight:bold; }"
            "QPushButton:enabled:hover { background:#fff3e0; border-color:#e65100;"
            "  color:#e65100; }"
            "QPushButton:disabled { color:#aaa; }"
        )
        self._btn_split.clicked.connect(self.split_requested.emit)
        layout.addWidget(self._btn_split)

        self._btn_stop = QPushButton("■  Stop Drawing")
        self._btn_stop.setVisible(False)
        self._btn_stop.setStyleSheet(
            "QPushButton { padding:8px; border:1px solid #c00; border-radius:4px;"
            "  background:#fff0f0; font-weight:bold; color:#c00; }"
            "QPushButton:hover { background:#fdd; }"
        )
        self._btn_stop.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self._btn_stop)

        layout.addSpacing(4)

        self._btn_undo = QPushButton("↩  Undo Last Operation")
        self._btn_undo.setStyleSheet("padding:6px;")
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        layout.addWidget(self._btn_undo)

        layout.addSpacing(4)

        btn_done = QPushButton("✓  Done — Save & Return")
        btn_done.setStyleSheet(
            "QPushButton { background:#107c10; color:white; padding:8px; "
            "border-radius:4px; font-weight:bold; } "
            "QPushButton:hover { background:#0b5c0b; }"
        )
        btn_done.clicked.connect(self.done_requested.emit)
        layout.addWidget(btn_done)

        btn_cancel = QPushButton("Cancel — Discard All Changes")
        btn_cancel.setStyleSheet("padding:6px; color:#c00;")
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(btn_cancel)

    def update_status(self, n_selected):
        if n_selected == 0:
            self._status.setText("No features selected.\nClick or rubber-band select on the map.")
            self._btn_merge.setEnabled(False)
            self._btn_split.setEnabled(False)
        elif n_selected == 1:
            self._status.setText("1 feature selected.\nClick Split, then draw a line across it.")
            self._btn_merge.setEnabled(False)
            self._btn_split.setEnabled(True)
        else:
            self._status.setText(f"{n_selected} features selected.\nClick Merge to combine them.")
            self._btn_merge.setEnabled(True)
            self._btn_split.setEnabled(False)

    def set_split_mode(self, active):
        self._btn_stop.setVisible(active)
        self._btn_merge.setEnabled(False)
        self._btn_split.setEnabled(False)
        self._btn_undo.setEnabled(not active)
        if active:
            self._status.setText(
                "Drawing split line...\n"
                "Left-click: add vertex\n"
                "Right-click: finish  |  'Stop Drawing': cancel"
            )

    def set_message(self, msg):
        self._status.setText(msg)

    def closeEvent(self, event):
        self.cancel_requested.emit()
        event.accept()


class SplitLineTool(QgsMapTool):

    line_completed = pyqtSignal(list)
    cancelled      = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(QCursor(_Qt_CrossCursor))
        self._points = []

        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rubber_band.setColor(QColor(220, 50, 50))
        self._rubber_band.setWidth(2)

        self._preview_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._preview_band.setColor(QColor(220, 50, 50, 160))
        self._preview_band.setWidth(2)
        try:
            self._preview_band.setLineStyle(Qt.PenStyle.DashLine)
        except AttributeError:
            self._preview_band.setLineStyle(Qt.DashLine)

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        cur = self.toMapCoordinates(event.pos())
        self._preview_band.reset(QgsWkbTypes.LineGeometry)
        self._preview_band.addPoint(self._points[-1])
        self._preview_band.addPoint(cur)

    def canvasPressEvent(self, event):
        if event.button() == _Qt_LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self._points.append(pt)
            self._rubber_band.addPoint(pt)
            self._preview_band.reset(QgsWkbTypes.LineGeometry)
        elif event.button() == _Qt_RightButton:
            self._finish()

    def canvasDoubleClickEvent(self, event):
        self._finish()

    def _finish(self):
        self._rubber_band.reset()
        self._preview_band.reset()
        if len(self._points) >= 2:
            self.line_completed.emit(list(self._points))
        else:
            self.cancelled.emit()
        self._points = []

    def force_cancel(self):
        self._rubber_band.reset()
        self._preview_band.reset()
        self._points = []

    def deactivate(self):
        self._rubber_band.reset()
        self._preview_band.reset()
        super().deactivate()
