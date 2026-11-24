# -*- coding: utf-8 -*-
"""
Subcatchment Loading — Assign loading nodes to subcatchment polygons.

Author: Dipendra Magaju
Tested On: QGIS 3.44.2 (Python 3.12)
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V01"
__status__     = "Beta"
_LINKED_PROMPT = "SCH_SubcatchmentLoadingPrompt_GM.txt"

from qgis.PyQt.QtCore import (
    QCoreApplication, QVariant, Qt, pyqtSignal, QObject, QTimer
)
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QMessageBox, QSizePolicy, QAbstractItemView,
    QFrame,
)
from qgis.PyQt.QtGui import QColor, QFont, QCursor
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsFields, QgsField, QgsFeature, QgsFeatureRequest, QgsGeometry,
    QgsPointXY, QgsWkbTypes, QgsVectorLayer,
    QgsVectorFileWriter, QgsProject, QgsCoordinateTransform,
    QgsSnappingConfig, QgsTolerance, QgsPointLocator,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator
import os

# Module-level reference keeps the session alive after processAlgorithm returns
_active_session = None


def _release_session():
    global _active_session
    _active_session = None


# ── Constants ─────────────────────────────────────────────────────────────────
DRAINS_NODE = "Node"
DRAINS_PS   = "2D point source"

# ── PyQt5/6 enum compatibility ─────────────────────────────────────────────────
try:
    _Qt_StaysOnTop  = Qt.WindowType.WindowStaysOnTopHint
    _Qt_CrossCursor = Qt.CursorShape.CrossCursor
    _Qt_LeftButton  = Qt.MouseButton.LeftButton
except AttributeError:
    _Qt_StaysOnTop  = Qt.WindowStaysOnTopHint
    _Qt_CrossCursor = Qt.CrossCursor
    _Qt_LeftButton  = Qt.LeftButton

try:
    _QDialog_Accepted = QDialog.DialogCode.Accepted
except AttributeError:
    _QDialog_Accepted = QDialog.Accepted

try:
    _Header_Stretch  = QHeaderView.ResizeMode.Stretch
    _Header_FitWidth = QHeaderView.ResizeMode.ResizeToContents
except AttributeError:
    _Header_Stretch  = QHeaderView.Stretch
    _Header_FitWidth = QHeaderView.ResizeToContents

try:
    _Table_SelectRows = QAbstractItemView.SelectionBehavior.SelectRows
    _Table_SingleSel  = QAbstractItemView.SelectionMode.SingleSelection
    _Table_NoEdit     = QAbstractItemView.EditTrigger.NoEditTriggers
except AttributeError:
    _Table_SelectRows = QTableWidget.SelectRows
    _Table_SingleSel  = QTableWidget.SingleSelection
    _Table_NoEdit     = QTableWidget.NoEditTriggers

try:
    _GeomLine    = QgsWkbTypes.GeometryType.LineGeometry
    _GeomPoint   = QgsWkbTypes.GeometryType.PointGeometry
    _GeomPolygon = QgsWkbTypes.GeometryType.PolygonGeometry
except AttributeError:
    _GeomLine    = QgsWkbTypes.LineGeometry
    _GeomPoint   = QgsWkbTypes.PointGeometry
    _GeomPolygon = QgsWkbTypes.PolygonGeometry

try:
    _WkbMultiLineString = QgsWkbTypes.Type.MultiLineString
except AttributeError:
    _WkbMultiLineString = QgsWkbTypes.MultiLineString


# ─────────────────────────────────────────────────────────────────────────────
#  Processing Algorithm
# ─────────────────────────────────────────────────────────────────────────────

class SubcatchmentLoadingAlgorithm(QgsProcessingAlgorithm):

    CATCHMENTS = 'CATCHMENTS'
    NODES      = 'NODES'
    POINT_SRC  = 'POINT_SOURCES'
    NODE_FIELD = 'NODE_ID_FIELD'
    PS_FIELD   = 'PS_ID_FIELD'
    OUTPUT     = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return SubcatchmentLoadingAlgorithm()

    def name(self):
        return 'sch_loading_v01'

    def displayName(self):
        return self.tr('Subcatchment Loading V01')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def flags(self):
        try:
            return super().flags() | QgsProcessingAlgorithm.Flag.FlagNoThreading
        except AttributeError:
            return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return self.tr(
            "Interactively assigns a loading node to each subcatchment polygon "
            "for ICM 2D model setup.\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Workflow:\n"
            "  1. Run the tool — a floating control panel appears.\n"
            "  2. Click any subcatchment polygon on the map canvas.\n"
            "  3. Choose 'Manhole/Node' or '2D Point Source', then select from the list.\n"
            "  4. A blue connector line is drawn from the centroid to the loading node.\n"
            "  5. Repeat for all catchments. Re-click a catchment to change its assignment.\n"
            "  6. Click 'Finish & Save' to export the output.\n\n"
            "Outputs:\n"
            "  • Connector arrow lines (.shp / .gpkg)\n"
            "  • Fields: Catch_ID, drains_to, node_id, 2d_point_i\n"
            "  • Companion .qml blue-arrow symbology file\n\n"
            "Notes:\n"
            "  • All layers must share the same CRS.\n"
            "  • Requires QGIS Desktop — cannot run headless.\n"
            "  • Output is only saved when 'Finish & Save' is clicked."
        )

    def help(self):
        return self.tr(
            "<html><body>"
            "<h2>Subcatchment Loading</h2>"
            "<p><b>Author:</b> Dipendra Magaju &nbsp;|&nbsp; "
            "<b>Tested On:</b> QGIS 3.44.2</p>"
            "<h3>Purpose</h3>"
            "<p>Interactively assigns a loading node (manhole or 2D point source) "
            "to each subcatchment polygon for ICM 2D stormwater model setup. "
            "Outputs connector arrow lines from subcatchment centroids to the "
            "assigned loading nodes.</p>"
            "<h3>Output Fields</h3>"
            "<ul>"
            "<li><b>Catch_ID:</b> Feature ID of the subcatchment</li>"
            "<li><b>drains_to:</b> 'Node' or '2D point source'</li>"
            "<li><b>node_id:</b> Value from the selected node ID field</li>"
            "<li><b>2d_point_i:</b> Value from the selected point source ID field</li>"
            "</ul>"
            "<h3>Important Notes</h3>"
            "<ul>"
            "<li>All input layers must share the same CRS.</li>"
            "<li>Requires QGIS Desktop — will not run headless.</li>"
            "<li>Output is saved only when 'Finish &amp; Save' is clicked.</li>"
            "<li>Re-clicking an assigned catchment overwrites its assignment.</li>"
            "</ul>"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CATCHMENTS,
                self.tr('Subcatchment Polygons'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NODES,
                self.tr('Nodes / Manholes (optional)'),
                [QgsProcessing.TypeVectorPoint],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.POINT_SRC,
                self.tr('2D Point Sources (optional — leave blank to create on the fly)'),
                [QgsProcessing.TypeVectorPoint],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.NODE_FIELD,
                self.tr('Node ID Field (stored as node_id in output)'),
                parentLayerParameterName=self.NODES,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PS_FIELD,
                self.tr('Point Source ID Field (stored as 2d_point_i in output)'),
                parentLayerParameterName=self.POINT_SRC,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                self.tr('Output Connector Lines'),
                fileFilter='Shapefile (*.shp);;GeoPackage (*.gpkg)'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        try:
            from qgis.utils import iface
            if iface is None:
                raise ImportError
        except (ImportError, AttributeError):
            raise QgsProcessingException(
                "Subcatchment Loading requires QGIS Desktop with an active canvas."
            )

        catchment_layer = self.parameterAsVectorLayer(parameters, self.CATCHMENTS, context)
        node_layer      = self.parameterAsVectorLayer(parameters, self.NODES, context)
        ps_layer        = self.parameterAsVectorLayer(parameters, self.POINT_SRC, context)
        node_field      = self.parameterAsString(parameters, self.NODE_FIELD, context)
        ps_field        = self.parameterAsString(parameters, self.PS_FIELD, context)
        output_path     = self.parameterAsFileOutput(parameters, self.OUTPUT, context)

        if node_layer is None and ps_layer is None:
            raise QgsProcessingException(
                "Provide at least one of 'Nodes / Manholes' or '2D Point Sources'.\n"
                "Leave '2D Point Sources' blank to create a new layer during the session."
            )

        for lyr in [catchment_layer, node_layer, ps_layer]:
            if lyr is not None and not QgsProject.instance().mapLayer(lyr.id()):
                QgsProject.instance().addMapLayer(lyr)

        iface.mapCanvas().setExtent(catchment_layer.extent())
        iface.mapCanvas().refresh()

        global _active_session
        _active_session = LoadingSession(
            canvas          = iface.mapCanvas(),
            catchment_layer = catchment_layer,
            node_layer      = node_layer,
            ps_layer        = ps_layer,
            node_field      = node_field,
            ps_field        = ps_field,
            output_path     = output_path,
        )
        QTimer.singleShot(600, lambda: _active_session.start() if _active_session else None)

        feedback.pushInfo("Subcatchment Loading session is starting.")
        feedback.pushInfo("Use the floating control panel to assign loading nodes.")
        feedback.pushInfo("Click 'Finish & Save' in the control panel when done.")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  Session Manager
# ─────────────────────────────────────────────────────────────────────────────

class LoadingSession(QObject):

    def __init__(self, canvas, catchment_layer, node_layer, ps_layer,
                 node_field, ps_field, output_path):
        super().__init__()
        self.canvas          = canvas
        self.catchment_layer = catchment_layer
        self.node_layer      = node_layer
        self.ps_layer        = ps_layer
        self.node_field      = node_field
        self.ps_field        = ps_field
        self.output_path     = output_path
        self.assignments     = {}
        self.rubber_bands    = {}
        self.unassigned_bands = {}
        self._prev_tool      = canvas.mapTool()

    def start(self):
        self._load_existing_assignments()
        total = self.catchment_layer.featureCount()
        self.control_panel = ControlPanel(total, self)
        self.control_panel.finish_requested.connect(self._on_finish)
        self.control_panel.cancel_requested.connect(self._on_cancel)
        self.control_panel.show()
        self.map_tool = SubcatchmentClickTool(self.canvas, self)
        self.canvas.setMapTool(self.map_tool)
        self._draw_unassigned_highlights()
        for fid, data in self.assignments.items():
            self._redraw_rubber_band(fid, data['centroid'], data['load_pt'])
        if self.assignments:
            self.control_panel.update_progress(len(self.assignments))
            if len(self.assignments) == total:
                QMessageBox.information(
                    self.control_panel, "All Catchments Assigned",
                    f"All {total} subcatchment(s) already have loading assignments.\n\n"
                    "The tool remains open — click any subcatchment to review or change."
                )

    def _to_map_crs(self, pt, layer):
        map_crs = self.canvas.mapSettings().destinationCrs()
        if layer.crs() != map_crs:
            xform = QgsCoordinateTransform(layer.crs(), map_crs, QgsProject.instance())
            try:
                return xform.transform(pt)
            except Exception:
                pass
        return pt

    def _load_existing_assignments(self):
        layer       = self.catchment_layer
        field_names = [f.name() for f in layer.fields()]
        if 'drains_to' not in field_names:
            return
        has_node  = 'node_id'    in field_names
        has_point = '2d_point_i' in field_names
        for feat in layer.getFeatures():
            drains_to = feat['drains_to']
            if not drains_to:
                continue
            node_id  = (str(feat['node_id'])    if has_node  and feat['node_id']  else '')
            point_id = (str(feat['2d_point_i']) if has_point and feat['2d_point_i'] else '')
            load_pt  = self._find_load_point(drains_to, node_id, point_id)
            if load_pt is None:
                continue
            centroid = self._to_map_crs(feat.geometry().centroid().asPoint(), layer)
            self.assignments[feat.id()] = {
                'drains_to': drains_to, 'node_id': node_id,
                'point_id': point_id,   'load_pt': load_pt, 'centroid': centroid,
            }

    def _find_load_point(self, drains_to, node_id, point_id):
        if drains_to == DRAINS_NODE and node_id:
            if self.node_layer is None:
                return None
            layer, id_field, search_id = self.node_layer, self.node_field, node_id
        elif drains_to == DRAINS_PS and point_id:
            if self.ps_layer is None:
                return None
            layer, id_field, search_id = self.ps_layer, self.ps_field, point_id
        else:
            return None
        field_names = [f.name() for f in layer.fields()]
        if id_field not in field_names:
            return None
        for feat in layer.getFeatures():
            if str(feat[id_field]) == search_id:
                return self._to_map_crs(feat.geometry().asPoint(), layer)
        return None

    def _update_catchment_layer(self):
        layer       = self.catchment_layer
        field_names = [f.name() for f in layer.fields()]
        new_fields  = []
        if 'drains_to'  not in field_names:
            new_fields.append(QgsField('drains_to',  QVariant.String, len=20))
        if 'node_id'    not in field_names:
            new_fields.append(QgsField('node_id',    QVariant.String, len=100))
        if '2d_point_i' not in field_names:
            new_fields.append(QgsField('2d_point_i', QVariant.String, len=100))
        layer.startEditing()
        if new_fields:
            layer.dataProvider().addAttributes(new_fields)
            layer.updateFields()
        idx_drains = layer.fields().indexOf('drains_to')
        idx_node   = layer.fields().indexOf('node_id')
        idx_point  = layer.fields().indexOf('2d_point_i')
        for fid, data in self.assignments.items():
            layer.changeAttributeValue(fid, idx_drains, data['drains_to'])
            layer.changeAttributeValue(fid, idx_node,   data['node_id']  or '')
            layer.changeAttributeValue(fid, idx_point,  data['point_id'] or '')
        if not layer.commitChanges():
            errs = '; '.join(layer.commitErrors())
            layer.rollBack()
            raise RuntimeError(f"Could not save attributes to catchment layer: {errs}")

    def on_catchment_clicked(self, feature):
        fid      = feature.id()
        centroid = self._to_map_crs(feature.geometry().centroid().asPoint(), self.catchment_layer)
        if getattr(self, '_dlg', None):
            try:
                self._dlg.assignment_accepted.disconnect()
                self._dlg.pick_on_map_requested.disconnect()
                self._dlg.draw_on_map_requested.disconnect()
                self._dlg.finished.disconnect()
            except Exception:
                pass
            self._dlg.close()
        self._highlight_catchment(feature)
        self._pending_fid      = fid
        self._pending_centroid = centroid
        dlg = AssignmentDialog(
            parent            = self.control_panel,
            catchment_feature = feature,
            node_layer        = self.node_layer,
            ps_layer          = self.ps_layer,
            node_field        = self.node_field,
            ps_field          = self.ps_field,
            canvas            = self.canvas,
            current           = self.assignments.get(fid),
        )
        self._dlg = dlg
        dlg.assignment_accepted.connect(self._apply_assignment)
        dlg.pick_on_map_requested.connect(self._start_node_pick)
        dlg.draw_on_map_requested.connect(self._start_ps_draw)
        dlg.finished.connect(self._on_dlg_finished)
        dlg.show()
        dlg.raise_()

    def _draw_unassigned_highlights(self):
        _fill   = QColor(255, 140, 0, 60)
        _border = QColor(255, 140, 0, 40)
        for feat in self.catchment_layer.getFeatures():
            fid = feat.id()
            if fid in self.assignments:
                continue
            rb = QgsRubberBand(self.canvas, _GeomPolygon)
            rb.setFillColor(_fill)
            rb.setColor(_border)
            rb.setWidth(1)
            rb.setToGeometry(feat.geometry(), self.catchment_layer)
            self.unassigned_bands[fid] = rb

    def _redraw_rubber_band(self, fid, centroid, load_pt):
        for rb in self.rubber_bands.pop(fid, []):
            rb.reset()
        _blue = QColor(0, 120, 212, 230)
        line_rb = QgsRubberBand(self.canvas, _GeomLine)
        line_rb.setColor(_blue)
        line_rb.setWidth(2)
        line_rb.addPoint(centroid)
        line_rb.addPoint(load_pt)
        rbs = [line_rb]
        dx = load_pt.x() - centroid.x()
        dy = load_pt.y() - centroid.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length > 0:
            mid_pt   = QgsPointXY(centroid.x() + dx * 0.5, centroid.y() + dy * 0.5)
            ux, uy   = dx / length, dy / length
            px, py   = -uy, ux
            s        = length * 0.12
            left_pt  = QgsPointXY(mid_pt.x() - ux * s + px * s * 0.5,
                                   mid_pt.y() - uy * s + py * s * 0.5)
            right_pt = QgsPointXY(mid_pt.x() - ux * s - px * s * 0.5,
                                   mid_pt.y() - uy * s - py * s * 0.5)
            for barb_pt in (left_pt, right_pt):
                barb = QgsRubberBand(self.canvas, _GeomLine)
                barb.setColor(_blue)
                barb.setWidth(2)
                barb.addPoint(mid_pt)
                barb.addPoint(barb_pt)
                rbs.append(barb)
        self.rubber_bands[fid] = rbs

    def _highlight_catchment(self, feature):
        self._clear_selection_highlight()
        rb = QgsRubberBand(self.canvas, _GeomPolygon)
        rb.setColor(QColor(255, 200, 0, 220))
        rb.setFillColor(QColor(255, 200, 0, 50))
        rb.setWidth(3)
        rb.setToGeometry(feature.geometry(), self.catchment_layer)
        self._sel_rb = rb

    def _clear_selection_highlight(self):
        rb = getattr(self, '_sel_rb', None)
        if rb:
            rb.reset()
            self._sel_rb = None

    def _apply_assignment(self):
        dlg  = self._dlg
        fid  = self._pending_fid
        cent = self._pending_centroid
        self.assignments[fid] = {
            'drains_to': dlg.drains_to,
            'node_id':   dlg.selected_node_id,
            'point_id':  dlg.selected_point_id,
            'load_pt':   dlg.selected_point,
            'centroid':  cent,
        }
        self._redraw_rubber_band(fid, cent, dlg.selected_point)
        rb = self.unassigned_bands.pop(fid, None)
        if rb:
            rb.reset()
        self.control_panel.update_progress(len(self.assignments))
        self._update_live_layer()

    def _on_dlg_finished(self):
        self._clear_selection_highlight()

    def _start_node_pick(self, use_nodes):
        self._pick_use_nodes = use_nodes
        self._dlg.hide()
        self._activate_pick_tool()
        self._pick_bar = PickModeBar(self.canvas, use_nodes)
        self._pick_bar.switch_requested.connect(self._switch_pick_type)
        self._pick_bar.cancel_requested.connect(self._cancel_node_pick)

    def _activate_pick_tool(self):
        layer = self.node_layer if self._pick_use_nodes else self.ps_layer
        if layer is None:
            return
        self._node_pick_tool = NodePickTool(self.canvas, layer)
        self._node_pick_tool.feature_picked.connect(self._on_node_picked)
        self._node_pick_tool.cancelled.connect(self._cancel_node_pick)
        self.canvas.setMapTool(self._node_pick_tool)

    def _switch_pick_type(self):
        self._pick_use_nodes = not self._pick_use_nodes
        self._activate_pick_tool()
        if getattr(self, '_pick_bar', None):
            self._pick_bar.update_type(self._pick_use_nodes)

    def _hide_pick_bar(self):
        bar = getattr(self, '_pick_bar', None)
        if bar:
            bar.hide()
            bar.deleteLater()
            self._pick_bar = None

    def _on_node_picked(self, feature):
        self._hide_pick_bar()
        self.canvas.setMapTool(self.map_tool)
        id_field    = self.node_field if self._pick_use_nodes else self.ps_field
        field_names = [f.name() for f in feature.fields()]
        id_val      = str(feature[id_field]) if id_field in field_names else str(feature.id())
        pt_layer    = self.node_layer if self._pick_use_nodes else self.ps_layer
        load_pt     = self._to_map_crs(feature.geometry().asPoint(), pt_layer)
        fid         = self._pending_fid
        centroid    = self._pending_centroid
        self.assignments[fid] = {
            'drains_to': DRAINS_NODE if self._pick_use_nodes else DRAINS_PS,
            'node_id':   id_val if self._pick_use_nodes else '',
            'point_id':  '' if self._pick_use_nodes else id_val,
            'load_pt':   load_pt,
            'centroid':  centroid,
        }
        self._redraw_rubber_band(fid, centroid, load_pt)
        rb = self.unassigned_bands.pop(fid, None)
        if rb:
            rb.reset()
        self.control_panel.update_progress(len(self.assignments))
        self._update_live_layer()
        self._clear_selection_highlight()
        if getattr(self, '_dlg', None):
            try:
                self._dlg.assignment_accepted.disconnect()
                self._dlg.pick_on_map_requested.disconnect()
                self._dlg.draw_on_map_requested.disconnect()
                self._dlg.finished.disconnect()
            except Exception:
                pass
            self._dlg.close()
            self._dlg = None

    def _cancel_node_pick(self):
        self._hide_pick_bar()
        self.canvas.setMapTool(self.map_tool)
        if self._dlg:
            self._dlg.show()
            self._dlg.raise_()

    def _start_ps_draw(self):
        self._dlg.hide()
        self._draw_tool = DrawPointTool(self.canvas)
        self._draw_tool.point_drawn.connect(self._on_ps_drawn)
        self._draw_tool.cancelled.connect(self._cancel_ps_draw)
        self.canvas.setMapTool(self._draw_tool)
        self._draw_bar = DrawModeBar(self.canvas)
        self._draw_bar.cancel_requested.connect(self._cancel_ps_draw)

    def _hide_draw_bar(self):
        bar = getattr(self, '_draw_bar', None)
        if bar:
            bar.hide()
            bar.deleteLater()
            self._draw_bar = None

    def _cancel_ps_draw(self):
        self._hide_draw_bar()
        self.canvas.setMapTool(self.map_tool)
        if getattr(self, '_dlg', None):
            self._dlg.show()
            self._dlg.raise_()

    def _next_ps_name(self):
        import re
        pattern  = re.compile(r'^PS_(\d+)$', re.IGNORECASE)
        id_field = self.ps_field or 'PS_ID'
        max_num  = 0
        if self.ps_layer is not None:
            field_names = [f.name() for f in self.ps_layer.fields()]
            if id_field in field_names:
                for feat in self.ps_layer.getFeatures():
                    val = feat[id_field]
                    if val is None:
                        continue
                    m = pattern.match(str(val))
                    if m:
                        num = int(m.group(1))
                        if num > max_num:
                            max_num = num
        return f"PS_{max_num + 1:04d}"

    def _create_ps_layer(self):
        crs_id    = self.catchment_layer.crs().authid()
        self.ps_field = 'PS_ID'
        lyr = QgsVectorLayer(f'Point?crs={crs_id}&field=PS_ID:string(50)',
                             '2D_Point_Sources', 'memory')
        lyr.updateFields()
        QgsProject.instance().addMapLayer(lyr)
        self.ps_layer = lyr
        if getattr(self, '_dlg', None):
            self._dlg.ps_layer  = lyr
            self._dlg.ps_field  = self.ps_field

    def _on_ps_drawn(self, map_pt):
        self._hide_draw_bar()
        self.canvas.setMapTool(self.map_tool)
        if self.ps_layer is None:
            self._create_ps_layer()
        layer_pt = map_pt
        map_crs  = self.canvas.mapSettings().destinationCrs()
        if self.ps_layer.crs() != map_crs:
            xform = QgsCoordinateTransform(map_crs, self.ps_layer.crs(), QgsProject.instance())
            try:
                layer_pt = xform.transform(map_pt)
            except Exception:
                pass
        new_name = self._next_ps_name()
        new_feat = QgsFeature(self.ps_layer.fields())
        new_feat.setGeometry(QgsGeometry.fromPointXY(layer_pt))
        field_names = [f.name() for f in self.ps_layer.fields()]
        if self.ps_field in field_names:
            new_feat[self.ps_field] = new_name
        if not self.ps_layer.isEditable():
            self.ps_layer.startEditing()
        ok = self.ps_layer.addFeature(new_feat)
        if ok:
            self.ps_layer.commitChanges()
        else:
            self.ps_layer.rollBack()
            if getattr(self, '_dlg', None):
                self._dlg.show(); self._dlg.raise_()
            return
        self.canvas.refresh()
        fid      = self._pending_fid
        centroid = self._pending_centroid
        self.assignments[fid] = {
            'drains_to': DRAINS_PS, 'node_id': '',
            'point_id':  new_name,  'load_pt': map_pt, 'centroid': centroid,
        }
        self._redraw_rubber_band(fid, centroid, map_pt)
        rb = self.unassigned_bands.pop(fid, None)
        if rb:
            rb.reset()
        self.control_panel.update_progress(len(self.assignments))
        self._update_live_layer()
        self._clear_selection_highlight()
        if getattr(self, '_dlg', None):
            try:
                self._dlg.assignment_accepted.disconnect()
                self._dlg.pick_on_map_requested.disconnect()
                self._dlg.draw_on_map_requested.disconnect()
                self._dlg.finished.disconnect()
            except Exception:
                pass
            self._dlg.close()
            self._dlg = None

    def _on_finish(self):
        if not self.assignments:
            QMessageBox.warning(
                self.control_panel, "No Assignments",
                "No catchments have been assigned yet.\n"
                "Click at least one subcatchment polygon before saving."
            )
            return
        try:
            self._save_output()
        except Exception as e:
            QMessageBox.critical(self.control_panel, "Save Error",
                                 f"Could not save output:\n{e}")
            return
        self._cleanup()
        _release_session()

    def _on_cancel(self):
        self._cleanup()
        _release_session()

    def _cleanup(self):
        self._hide_pick_bar()
        self._hide_draw_bar()
        for rbs in self.rubber_bands.values():
            for rb in rbs:
                rb.reset()
        self.rubber_bands.clear()
        for rb in self.unassigned_bands.values():
            rb.reset()
        self.unassigned_bands.clear()
        if self._prev_tool:
            self.canvas.setMapTool(self._prev_tool)
        self.control_panel.close()

    def _update_live_layer(self):
        layer_name = 'SubCatchment_Connector'
        for old in QgsProject.instance().mapLayersByName(layer_name):
            QgsProject.instance().removeMapLayer(old.id())
        QCoreApplication.processEvents()
        if not self.assignments:
            return
        crs_id    = self.catchment_layer.crs().authid()
        mem_layer = QgsVectorLayer(f'MultiLineString?crs={crs_id}', layer_name, 'memory')
        pr        = mem_layer.dataProvider()
        pr.addAttributes([
            QgsField('Catch_ID',   QVariant.Int),
            QgsField('drains_to',  QVariant.String),
            QgsField('node_id',    QVariant.String),
            QgsField('2d_point_i', QVariant.String),
        ])
        mem_layer.updateFields()
        feats  = []
        fields = mem_layer.fields()
        for fid, data in self.assignments.items():
            feat     = QgsFeature(fields)
            centroid = data['centroid']
            load_pt  = data['load_pt']
            dx = load_pt.x() - centroid.x()
            dy = load_pt.y() - centroid.y()
            length    = (dx * dx + dy * dy) ** 0.5
            polylines = [[centroid, load_pt]]
            if length > 0:
                mid_pt   = QgsPointXY(centroid.x() + dx * 0.5, centroid.y() + dy * 0.5)
                ux, uy   = dx / length, dy / length
                px, py   = -uy, ux
                s        = length * 0.12
                left_pt  = QgsPointXY(mid_pt.x() - ux*s + px*s*0.5,
                                       mid_pt.y() - uy*s + py*s*0.5)
                right_pt = QgsPointXY(mid_pt.x() - ux*s - px*s*0.5,
                                       mid_pt.y() - uy*s - py*s*0.5)
                polylines.extend([[mid_pt, left_pt], [mid_pt, right_pt]])
            feat.setGeometry(QgsGeometry.fromMultiPolylineXY(polylines))
            feat['Catch_ID']   = int(fid)
            feat['drains_to']  = data['drains_to']
            feat['node_id']    = data['node_id']  or ''
            feat['2d_point_i'] = data['point_id'] or ''
            feats.append(feat)
        pr.addFeatures(feats)
        mem_layer.updateExtents()
        qml_path = os.path.splitext(self.output_path)[0] + '.qml'
        if not os.path.exists(qml_path):
            out_dir = os.path.dirname(self.output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            _write_qml(self.output_path)
        if os.path.exists(qml_path):
            mem_layer.loadNamedStyle(qml_path)
        QgsProject.instance().addMapLayer(mem_layer)

    def _export_connector_lines(self):
        layer_name = 'SubCatchment_Connector'
        for old in QgsProject.instance().mapLayersByName(layer_name):
            QgsProject.instance().removeMapLayer(old.id())
        QCoreApplication.processEvents()
        out_dir = os.path.dirname(self.output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _ext  = os.path.splitext(self.output_path)[1].lower()
        if _ext == '.gpkg':
            _stale = [self.output_path]
        else:
            _base  = os.path.splitext(self.output_path)[0]
            _stale = [_base + s for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx')]
        for _p in _stale:
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except OSError:
                    pass
        fields = QgsFields()
        fields.append(QgsField('Catch_ID',   QVariant.Int))
        fields.append(QgsField('drains_to',  QVariant.String, len=20))
        fields.append(QgsField('node_id',    QVariant.String, len=100))
        fields.append(QgsField('2d_point_i', QVariant.String, len=100))
        crs    = self.catchment_layer.crs()
        ext    = os.path.splitext(self.output_path)[1].lower()
        driver = 'GPKG' if ext == '.gpkg' else 'ESRI Shapefile'
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName   = driver
        options.fileEncoding = 'UTF-8'
        try:
            options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        except AttributeError:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        transform_context = QgsProject.instance().transformContext()
        _wr_result = QgsVectorFileWriter.create(
            self.output_path, fields, _WkbMultiLineString, crs,
            transform_context, options
        )
        writer = _wr_result[0] if isinstance(_wr_result, tuple) else _wr_result
        if writer.hasError() != 0:
            raise RuntimeError(writer.errorMessage())
        for fid, data in self.assignments.items():
            feat     = QgsFeature(fields)
            centroid = data['centroid']
            load_pt  = data['load_pt']
            dx = load_pt.x() - centroid.x()
            dy = load_pt.y() - centroid.y()
            length    = (dx * dx + dy * dy) ** 0.5
            polylines = [[centroid, load_pt]]
            if length > 0:
                mid_pt   = QgsPointXY(centroid.x() + dx * 0.5, centroid.y() + dy * 0.5)
                ux, uy   = dx / length, dy / length
                px, py   = -uy, ux
                s        = length * 0.12
                left_pt  = QgsPointXY(mid_pt.x() - ux*s + px*s*0.5,
                                       mid_pt.y() - uy*s + py*s*0.5)
                right_pt = QgsPointXY(mid_pt.x() - ux*s - px*s*0.5,
                                       mid_pt.y() - uy*s - py*s*0.5)
                polylines.extend([[mid_pt, left_pt], [mid_pt, right_pt]])
            feat.setGeometry(QgsGeometry.fromMultiPolylineXY(polylines))
            feat['Catch_ID']   = int(fid)
            feat['drains_to']  = data['drains_to']
            feat['node_id']    = data['node_id']  or ''
            feat['2d_point_i'] = data['point_id'] or ''
            writer.addFeature(feat)
        del writer
        _write_qml(self.output_path)
        result_layer = QgsVectorLayer(self.output_path, layer_name, 'ogr')
        if result_layer.isValid():
            qml_path = os.path.splitext(self.output_path)[0] + '.qml'
            if os.path.exists(qml_path):
                result_layer.loadNamedStyle(qml_path)
            QgsProject.instance().addMapLayer(result_layer)

    def _save_output(self):
        self._export_connector_lines()
        self._update_catchment_layer()


# ─────────────────────────────────────────────────────────────────────────────
#  Custom Map Tools
# ─────────────────────────────────────────────────────────────────────────────

class SubcatchmentClickTool(QgsMapTool):

    def __init__(self, canvas, session):
        super().__init__(canvas)
        self.session = session
        self.setCursor(QCursor(_Qt_CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() != _Qt_LeftButton:
            return
        click_pt   = self.toMapCoordinates(event.pos())
        click_geom = QgsGeometry.fromPointXY(click_pt)
        for feat in self.session.catchment_layer.getFeatures():
            if feat.geometry() and feat.geometry().contains(click_geom):
                self.session.on_catchment_clicked(feat)
                return


class NodePickTool(QgsMapTool):
    feature_picked = pyqtSignal(object)
    cancelled      = pyqtSignal()

    def __init__(self, canvas, layer):
        super().__init__(canvas)
        self.layer           = layer
        self._snap_indicator = QgsSnapIndicator(canvas)
        self._prev_snap_config = None
        self.setCursor(QCursor(_Qt_CrossCursor))

    def activate(self):
        super().activate()
        self._enable_snapping()

    def _enable_snapping(self):
        utils = self.canvas().snappingUtils()
        self._prev_snap_config = utils.config()
        config = QgsSnappingConfig()
        config.setEnabled(True)
        config.setTolerance(15)
        try:
            config.setMode(QgsSnappingConfig.SnappingMode.AdvancedConfiguration)
            config.setUnits(QgsTolerance.UnitType.Pixels)
            ind = QgsSnappingConfig.IndividualLayerSettings(
                True, QgsSnappingConfig.SnappingType.Vertex, 15, QgsTolerance.UnitType.Pixels)
        except AttributeError:
            config.setMode(QgsSnappingConfig.AdvancedConfiguration)
            config.setUnits(QgsTolerance.Pixels)
            ind = QgsSnappingConfig.IndividualLayerSettings(
                True, QgsSnappingConfig.Vertex, 15, QgsTolerance.Pixels)
        config.setIndividualLayerSettings(self.layer, ind)
        utils.setConfig(config)

    def deactivate(self):
        self._snap_indicator.setMatch(QgsPointLocator.Match())
        if self._prev_snap_config is not None:
            self.canvas().snappingUtils().setConfig(self._prev_snap_config)
        super().deactivate()

    def canvasMoveEvent(self, event):
        match = self.canvas().snappingUtils().snapToMap(event.pos())
        self._snap_indicator.setMatch(match)

    def canvasReleaseEvent(self, event):
        if event.button() == _Qt_LeftButton:
            self._pick(event)
        else:
            self.cancelled.emit()

    def _pick(self, event):
        match    = self.canvas().snappingUtils().snapToMap(event.pos())
        click_pt = match.point() if match.isValid() else self.toMapCoordinates(event.pos())
        radius   = self.searchRadiusMU(self.canvas())
        bbox     = QgsGeometry.fromPointXY(click_pt).buffer(radius, 4).boundingBox()
        request  = QgsFeatureRequest().setFilterRect(bbox)
        nearest  = None
        min_dist = float('inf')
        for feat in self.layer.getFeatures(request):
            if feat.geometry():
                d = feat.geometry().distance(QgsGeometry.fromPointXY(click_pt))
                if d < min_dist:
                    min_dist = d
                    nearest  = feat
        if nearest:
            self.feature_picked.emit(nearest)


class DrawPointTool(QgsMapTool):
    point_drawn = pyqtSignal(object)
    cancelled   = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(QCursor(_Qt_CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == _Qt_LeftButton:
            self.point_drawn.emit(self.toMapCoordinates(event.pos()))
        else:
            self.cancelled.emit()


# ─────────────────────────────────────────────────────────────────────────────
#  Assignment Dialog
# ─────────────────────────────────────────────────────────────────────────────

class AssignmentDialog(QDialog):
    assignment_accepted   = pyqtSignal()
    pick_on_map_requested = pyqtSignal(bool)
    draw_on_map_requested = pyqtSignal()

    def __init__(self, parent, catchment_feature, node_layer, ps_layer,
                 node_field, ps_field, canvas, current=None):
        super().__init__(parent)
        self.node_layer  = node_layer
        self.ps_layer    = ps_layer
        self.node_field  = node_field
        self.ps_field    = ps_field
        self.canvas      = canvas
        self.drains_to         = None
        self.selected_node_id  = ''
        self.selected_point_id = ''
        self.selected_point    = None
        self._row_data = []
        self._build_ui(catchment_feature, current)

    def _build_ui(self, feature, current):
        self.setWindowTitle('Assign Loading Node')
        self.setMinimumSize(540, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        try:
            id_val = feature[feature.fields().names()[0]]
        except Exception:
            id_val = feature.id()
        hdr = QLabel(f"<b>Subcatchment:</b>  {id_val}  "
                     f"<span style='color:#666'>(FID {feature.id()})</span>")
        hdr.setFont(QFont("Segoe UI", 10))
        layout.addWidget(hdr)
        if current:
            layout.addWidget(QLabel(
                f"<i>Current: {current['drains_to']}  —  "
                f"NodeID: {current['node_id'] or '—'}  /  "
                f"PointID: {current['point_id'] or '—'}</i>"
            ))
        type_box    = QGroupBox("Drains to")
        type_layout = QHBoxLayout(type_box)
        self.radio_node = QRadioButton("Manhole / Node")
        self.radio_ps   = QRadioButton("2D Point Source")
        if node_layer is None:
            self.radio_node.setEnabled(False)
        type_layout.addWidget(self.radio_node)
        type_layout.addWidget(self.radio_ps)
        layout.addWidget(type_box)
        if current and current['drains_to'] == DRAINS_PS:
            self.radio_ps.setChecked(True)
        elif node_layer is None:
            self.radio_ps.setChecked(True)
        else:
            self.radio_node.setChecked(True)
        self.radio_node.toggled.connect(self._refresh_table)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by ID…")
        self.search_box.textChanged.connect(self._refresh_table)
        search_row.addWidget(self.search_box)
        layout.addLayout(search_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['ID / Name', 'X', 'Y'])
        self.table.horizontalHeader().setSectionResizeMode(0, _Header_Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, _Header_FitWidth)
        self.table.horizontalHeader().setSectionResizeMode(2, _Header_FitWidth)
        self.table.setSelectionBehavior(_Table_SelectRows)
        self.table.setSelectionMode(_Table_SingleSel)
        self.table.setEditTriggers(_Table_NoEdit)
        self.table.doubleClicked.connect(self._on_accept)
        layout.addWidget(self.table)
        btn_row  = QHBoxLayout()
        btn_pick = QPushButton("Pick from Map")
        btn_draw = QPushButton("Draw on Map")
        btn_cancel = QPushButton("Cancel")
        btn_assign = QPushButton("Assign")
        btn_assign.setDefault(True)
        btn_assign.setStyleSheet(
            "QPushButton { background:#0078d4; color:white; padding:6px 22px; "
            "border-radius:4px; } QPushButton:hover { background:#005fa3; }"
        )
        self._btn_pick = btn_pick
        self._btn_draw = btn_draw
        btn_pick.clicked.connect(self._on_pick_map)
        btn_draw.clicked.connect(self._on_draw_map)
        btn_cancel.clicked.connect(self.reject)
        btn_assign.clicked.connect(self._on_accept)
        btn_row.addWidget(btn_pick)
        btn_row.addWidget(btn_draw)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_assign)
        layout.addLayout(btn_row)
        self._refresh_table()
        if current:
            prev_id = (current['point_id'] if current['drains_to'] == DRAINS_PS
                       else current['node_id'])
            self._pre_select(prev_id)

    def _current_layer(self):
        return self.node_layer if self.radio_node.isChecked() else self.ps_layer

    def _current_id_field(self):
        return (self.node_field or '') if self.radio_node.isChecked() else (self.ps_field or '')

    def _refresh_table(self):
        if hasattr(self, '_btn_draw'):
            self._btn_draw.setVisible(self.radio_ps.isChecked())
        layer = self._current_layer()
        if hasattr(self, '_btn_pick'):
            self._btn_pick.setEnabled(layer is not None)
        self.table.setRowCount(0)
        self._row_data = []
        if layer is None:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(
                "No layer loaded. Use 'Draw on Map' to create points."))
            return
        id_field    = self._current_id_field()
        filter_     = self.search_box.text().strip().lower()
        field_names = [f.name() for f in layer.fields()]
        map_crs = self.canvas.mapSettings().destinationCrs()
        xform = (QgsCoordinateTransform(layer.crs(), map_crs, QgsProject.instance())
                 if layer.crs() != map_crs else None)
        for feat in layer.getFeatures():
            id_val = (str(feat[id_field]) if id_field in field_names else str(feat.id()))
            if filter_ and filter_ not in id_val.lower():
                continue
            pt = feat.geometry().asPoint()
            if xform:
                try:
                    pt = xform.transform(pt)
                except Exception:
                    pass
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(id_val))
            self.table.setItem(row, 1, QTableWidgetItem(f"{pt.x():.3f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{pt.y():.3f}"))
            self._row_data.append((id_val, pt))

    def _pre_select(self, target_id):
        if not target_id:
            return
        for row, (id_val, _) in enumerate(self._row_data):
            if id_val == target_id:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                break

    def _on_pick_map(self):
        use_nodes = self.radio_node.isChecked()
        layer = self.node_layer if use_nodes else self.ps_layer
        if layer is None:
            QMessageBox.warning(self, "No Layer", "No layer available for this type.")
            return
        self.pick_on_map_requested.emit(use_nodes)

    def _on_draw_map(self):
        self.draw_on_map_requested.emit()

    def _on_accept(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            QMessageBox.warning(self, "No Selection", "Please select a feature from the list.")
            return
        id_val, pt = self._row_data[sel[0].row()]
        if self.radio_node.isChecked():
            self.drains_to         = DRAINS_NODE
            self.selected_node_id  = id_val
            self.selected_point_id = ''
        else:
            self.drains_to         = DRAINS_PS
            self.selected_node_id  = ''
            self.selected_point_id = id_val
        self.selected_point = pt
        self.assignment_accepted.emit()
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  Control Panel
# ─────────────────────────────────────────────────────────────────────────────

class ControlPanel(QDialog):
    finish_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, total, session):
        super().__init__()
        self.total   = total
        self.session = session
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle('Subcatchment Loading')
        self.setWindowFlags(self.windowFlags() | _Qt_StaysOnTop)
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("<b>Subcatchment Loading</b>")
        title.setFont(QFont("Segoe UI", 11))
        layout.addWidget(title)
        instr = QLabel(
            "Click a <b>subcatchment polygon</b> on the map canvas\n"
            "to assign its loading node or 2D point source.\n\n"
            "Re-click an assigned catchment to change it."
        )
        instr.setWordWrap(True)
        instr.setStyleSheet("color: #444;")
        layout.addWidget(instr)
        self.progress_label = QLabel(f"Assigned:  0 / {self.total}")
        self.progress_label.setFont(QFont("Segoe UI", 10))
        self.progress_label.setStyleSheet("font-weight: bold; color: #107c10;")
        layout.addWidget(self.progress_label)
        layout.addSpacing(4)
        btn_finish = QPushButton("Finish && Save")
        btn_finish.setStyleSheet(
            "QPushButton { background:#107c10; color:white; padding:8px; "
            "border-radius:4px; font-weight:bold; } "
            "QPushButton:hover { background:#0b5c0b; }"
        )
        btn_cancel = QPushButton("Cancel  (discard all)")
        btn_cancel.setStyleSheet("padding:6px;")
        btn_finish.clicked.connect(self.finish_requested.emit)
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(btn_finish)
        layout.addWidget(btn_cancel)

    def update_progress(self, count):
        self.progress_label.setText(f"Assigned:  {count} / {self.total}")

    def closeEvent(self, event):
        self.cancel_requested.emit()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  Overlay Bars
# ─────────────────────────────────────────────────────────────────────────────

class PickModeBar(QFrame):
    switch_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, canvas, use_nodes):
        super().__init__(canvas)
        try:
            self.setFrameShape(QFrame.Shape.StyledPanel)
        except AttributeError:
            self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(30,30,30,210); border-radius:6px; }"
            "QLabel { color: white; font-weight: bold; }"
            "QPushButton { color: white; background: #444; border:1px solid #666; "
            "              border-radius:3px; padding:3px 10px; }"
            "QPushButton:hover { background: #0078d4; border-color:#0078d4; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)
        self._label = QLabel()
        layout.addWidget(self._label)
        self._btn_switch = QPushButton()
        self._btn_switch.clicked.connect(self.switch_requested.emit)
        layout.addWidget(self._btn_switch)
        btn_cancel = QPushButton("Cancel pick")
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(btn_cancel)
        self.update_type(use_nodes)
        self.adjustSize()
        x = (canvas.width()  - self.width())  // 2
        y =  canvas.height() - self.height() - 14
        self.move(x, y)
        self.show()
        self.raise_()

    def update_type(self, use_nodes):
        kind  = "Manhole / Node" if use_nodes else "2D Point Source"
        other = "2D Point Source" if use_nodes else "Manhole / Node"
        self._label.setText(f"Picking: {kind}   |   right-click to cancel")
        self._btn_switch.setText(f"Switch to {other}")
        self.adjustSize()


class DrawModeBar(QFrame):
    cancel_requested = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        try:
            self.setFrameShape(QFrame.Shape.StyledPanel)
        except AttributeError:
            self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(30,30,30,210); border-radius:6px; }"
            "QLabel { color: white; font-weight: bold; }"
            "QPushButton { color: white; background: #444; border:1px solid #666; "
            "              border-radius:3px; padding:3px 10px; }"
            "QPushButton:hover { background: #d13438; border-color:#d13438; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Click to place new 2D Point Source   |   right-click to cancel"))
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(btn_cancel)
        self.adjustSize()
        x = (canvas.width()  - self.width())  // 2
        y =  canvas.height() - self.height() - 14
        self.move(x, y)
        self.show()
        self.raise_()


# ─────────────────────────────────────────────────────────────────────────────
#  QML Arrow Symbology
# ─────────────────────────────────────────────────────────────────────────────

def _write_qml(shp_path):
    qml_path = os.path.splitext(shp_path)[0] + '.qml'
    qml = (
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
        "<qgis version=\"3.0\" styleCategories=\"Symbology\">\n"
        "  <renderer-v2 type=\"singleSymbol\" forceraster=\"0\">\n"
        "    <symbols>\n"
        "      <symbol name=\"0\" type=\"line\" alpha=\"1\" clip_to_extent=\"1\">\n"
        "        <layer class=\"SimpleLine\" pass=\"0\" enabled=\"1\" locked=\"0\">\n"
        "          <Option type=\"Map\">\n"
        "            <Option name=\"line_color\" value=\"0,120,212,255\" type=\"QString\"/>\n"
        "            <Option name=\"line_width\" value=\"0.6\" type=\"QString\"/>\n"
        "            <Option name=\"line_style\" value=\"solid\" type=\"QString\"/>\n"
        "            <Option name=\"capstyle\" value=\"square\" type=\"QString\"/>\n"
        "          </Option>\n"
        "        </layer>\n"
        "      </symbol>\n"
        "    </symbols>\n"
        "    <rotation/>\n"
        "    <sizescale/>\n"
        "  </renderer-v2>\n"
        "</qgis>\n"
    )
    with open(qml_path, 'w', encoding='utf-8') as f:
        f.write(qml)
