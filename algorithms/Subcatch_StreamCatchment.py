# -*- coding: utf-8 -*-
"""
Combined stream network + full-DEM sub-catchment delineation.
Runs r.watershed once and produces both outputs in a single pass.
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"
__status__  = "Stable"

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorDestination,
    QgsProcessingMultiStepFeedback,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProcessingOutputLayerDefinition,
)

try:
    from qgis.core import (
        QgsLayerPostProcessorInterface,
        QgsLineSymbol,
        QgsFillSymbol,
        QgsProject,
    )
    _HAS_POST_PROCESSOR = True
except ImportError:
    _HAS_POST_PROCESSOR = False
    class QgsLayerPostProcessorInterface:
        pass
import processing

from .Subcatch_Utils import _QuietFeedback, _grass_prefix, _validate_metric_dem, _dem_pixel_info


class _SubbasinPostProcessor(QgsLayerPostProcessorInterface):
    """Applies transparent fill style to the sub-catchment layer."""
    instance = None

    def postProcessLayer(self, layer, context, feedback):
        symbol = QgsFillSymbol.createSimple({
            'color'        : '0,0,0,0',
            'outline_color': '#546E7A',
            'outline_width': '0.26',
            'outline_style': 'solid',
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()


class _StreamPostProcessor(QgsLayerPostProcessorInterface):
    """Applies dark-blue line style to the stream layer and moves it above catchments."""
    instance = None

    def postProcessLayer(self, layer, context, feedback):
        symbol = QgsLineSymbol.createSimple({
            'color': '#1A237E',
            'width': '0.35',
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

        # Move stream layer to the top of its parent group in the layer tree
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node:
            parent = node.parent()
            if parent and parent.children().index(node) != 0:
                clone = node.clone()
                parent.insertChildNode(0, clone)
                parent.removeChildNode(node)


class StreamCatchmentGenerator(QgsProcessingAlgorithm):
    """
    Single-pass stream + sub-catchment delineation for the full DEM extent.
    r.watershed runs once; the stream raster feeds the vectorisation pipeline
    and the basin raster feeds the sub-catchment polygonisation pipeline.
    """

    INPUT_DEM           = 'INPUT_DEM'
    THRESHOLD_AREA      = 'THRESHOLD_AREA'
    MIN_STREAM_LENGTH   = 'MIN_STREAM_LENGTH'
    ASSIGN_SUBCATCH_ID  = 'ASSIGN_SUBCATCH_ID'
    SUBCATCH_PREFIX     = 'SUBCATCH_PREFIX'
    OUTPUT_STREAMS      = 'OUTPUT_STREAMS'
    OUTPUT_SUBBASINS    = 'OUTPUT_SUBBASINS'
    OUTPUT_DRAINAGE     = 'OUTPUT_DRAINAGE'
    OUTPUT_ACCUMULATION = 'OUTPUT_ACCUMULATION'

    def tr(self, s):
        return QCoreApplication.translate('Processing', s)

    def createInstance(self):
        return StreamCatchmentGenerator()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'stream_catchment_generator'

    def displayName(self):
        return self.tr('Stream and Catchment Generator')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Short description: Delineates stream network and sub-catchment polygons "
            "from a LiDAR DEM in a single r.watershed pass.\n\n"
            "Inputs:\n"
            "  - LiDAR DEM (projected CRS, metres - e.g. EPSG:2193 NZTM)\n"
            "  - Minimum contributing area (ha) - controls stream density "
            "and sub-catchment size\n"
            "  - Minimum stream segment length (m) - optional cleanup filter\n\n"
            "Outputs:\n"
            "  - Stream network (vector lines with Strahler order)\n"
            "  - Sub-catchment polygons (with basin_id and area_ha fields)\n\n"
            "Notes:\n"
            "  - GRASS GIS must be installed and enabled in Processing settings\n"
            "  - DEM CRS must be projected and use metres\n"
            "  - Use the Sub-Basin Generator tool if you need catchments "
            "defined by specific outlet points"
        )

    def help(self):
        return (
            True,
            """<html><body style="font-family:sans-serif;font-size:12px;">
<h2>Stream and Catchment Generator</h2>
<p><b>Author:</b> Dipendra Magaju &nbsp;|&nbsp;
   <b>Version:</b> V01 &nbsp;|&nbsp;
   <b>Tested On:</b> QGIS 3.44.2</p>

<h3>Purpose</h3>
<p>Delineates a stream network and full-DEM sub-catchment polygons from a
LiDAR DEM in a single GRASS r.watershed pass. Both outputs share the same
flow-accumulation grid, so the sub-catchment boundaries are perfectly
consistent with the stream network.</p>

<h3>Methodology</h3>
<ol>
  <li><b>r.watershed</b> - computes flow accumulation, drainage direction,
      stream raster, and basin raster in one pass.</li>
  <li><b>r.thin</b> - thins the stream skeleton to single-pixel width.</li>
  <li><b>r.to.vect</b> - vectorises the thinned stream raster to lines.</li>
  <li><i>(Optional)</i> <b>Extract by expression</b> - removes segments
      shorter than the specified minimum length.</li>
  <li><b>native:assignprojection</b> - stamps the DEM CRS onto the stream vector.</li>
  <li><b>gdal:polygonize</b> - converts the basin raster to polygons.</li>
  <li><b>Extract by expression</b> - removes NoData (basin_id = 0) polygons.</li>
  <li><b>native:dissolve</b> - merges raster-cell polygons by basin_id.</li>
  <li><b>native:fixgeometries</b> - corrects invalid geometries and overlaps.</li>
  <li><b>native:removenullgeometries</b> - removes empty/null polygons.</li>
  <li><b>native:fieldcalculator</b> - adds an area_ha field.</li>
</ol>

<h3>Input Requirements</h3>
<ul>
  <li>DEM must be a single-band raster in a projected CRS using metres
      (e.g. EPSG:2193 NZTM2000).</li>
  <li>GRASS GIS must be installed and the GRASS provider enabled under
      Processing - Options - Providers.</li>
</ul>

<h3>Output</h3>
<ul>
  <li><b>Stream network</b> - vector lines; attribute stream_order
      holds the Strahler order from GRASS.</li>
  <li><b>Sub-catchment polygons</b> - vector polygons; attributes
      basin_id and area_ha.</li>
</ul>

<h3>Important Notes and Restrictions</h3>
<ul>
  <li>Processing time scales with DEM area - allow several minutes for
      LiDAR tiles larger than 5 km2.</li>
  <li>Minimum contributing area controls both stream density and
      sub-catchment size: smaller values produce finer networks but
      increase runtime significantly.</li>
  <li>For catchments delineated from specific outlet points, use the
      Sub-Basin Generator tool instead.</li>
</ul>
</body></html>"""
        )

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DEM, self.tr('LiDAR DEM'),
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD_AREA,
                self.tr('Minimum contributing area (ha)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0, minValue=0.01,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_STREAM_LENGTH,
                self.tr('Minimum stream segment length to retain (m)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0, minValue=0.0, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_STREAMS, self.tr('Stream network'),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_SUBBASINS, self.tr('Sub-catchment polygons'),
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_DRAINAGE,
                self.tr('Drainage direction raster (use as input to Outlet Catchment Generator)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_ACCUMULATION,
                self.tr('Flow accumulation raster (use as input to Longest Flow Path)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ASSIGN_SUBCATCH_ID,
                self.tr('Assign SubcatchID field to sub-catchments'),
                defaultValue=True, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.SUBCATCH_PREFIX,
                self.tr('SubcatchID prefix (e.g. SC → SC_0001, CA → CA_0001)'),
                defaultValue='SC', optional=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        grass = _grass_prefix()
        if grass is None:
            raise QgsProcessingException(self.tr(
                "GRASS GIS provider not found. Install GRASS GIS and enable it "
                "under Processing - Options - Providers - GRASS GIS, then restart QGIS."
            ))

        dem = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        if dem is None:
            raise QgsProcessingException(self.tr("Invalid or missing DEM raster."))

        crs          = _validate_metric_dem(dem)
        threshold_ha = self.parameterAsDouble(parameters, self.THRESHOLD_AREA, context)
        min_length   = self.parameterAsDouble(parameters, self.MIN_STREAM_LENGTH, context)
        assign_ids   = self.parameterAsBool(parameters, self.ASSIGN_SUBCATCH_ID, context)
        raw_prefix   = self.parameterAsString(parameters, self.SUBCATCH_PREFIX, context)
        sc_prefix    = (raw_prefix.strip().replace("'", "") or 'SC')

        pixel_x, pixel_y, cell_area_m2, threshold_cells = _dem_pixel_info(dem, threshold_ha)

        feedback.pushInfo("--- Stream and Catchment Generator ---")
        feedback.pushInfo(f"  DEM         : {dem.name()}  ({crs.authid()})")
        feedback.pushInfo(f"  Pixel size  : {pixel_x:.3f} x {pixel_y:.3f} m")
        feedback.pushInfo(f"  Min area    : {threshold_ha} ha  =  {threshold_cells} cells")
        if min_length > 0:
            feedback.pushInfo(f"  Min length  : {min_length:.1f} m")
        feedback.pushInfo("")

        n_steps = 10 + (1 if min_length > 0 else 0) + (1 if assign_ids else 0)
        mfb     = QgsProcessingMultiStepFeedback(n_steps, feedback)
        step    = 0

        # ── 1. r.watershed ────────────────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(
            f"Step 1/{n_steps}: r.watershed - flow accumulation, stream and basin rasters...\n"
            "  (May take several minutes for large DEMs.)"
        )
        drainage_dest = self.parameterAsOutputLayer(parameters, self.OUTPUT_DRAINAGE, context)
        accum_dest    = self.parameterAsOutputLayer(parameters, self.OUTPUT_ACCUMULATION, context)
        ws = processing.run(
            f"{grass}:r.watershed",
            {
                'elevation'                      : dem,
                'threshold'                      : threshold_cells,
                '-4': False, '-s': True, '-m': False, '-b': False, '-a': True,
                'accumulation'   : accum_dest,
                'drainage'       : drainage_dest,
                'stream'         : QgsProcessing.TEMPORARY_OUTPUT,
                'basin'          : QgsProcessing.TEMPORARY_OUTPUT,
                'GRASS_REGION_PARAMETER'         : dem.extent(),
                'GRASS_REGION_CELLSIZE_PARAMETER': pixel_x,
                'GRASS_RASTER_FORMAT_OPT' : '',
                'GRASS_RASTER_FORMAT_META': '',
            },
            context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
        )
        stream_raster   = ws['stream']
        basin_raster    = ws['basin']
        drainage_raster = ws['drainage']
        accum_raster    = ws['accumulation']
        feedback.pushInfo("  Flow direction, stream and basin rasters ready.\n")

        # ── 2. r.thin ─────────────────────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step 2/{n_steps}: r.thin - thinning stream skeleton...")
        thin = processing.run(
            f"{grass}:r.thin",
            {
                'input': stream_raster, 'iterations': 200,
                'output': QgsProcessing.TEMPORARY_OUTPUT,
                'GRASS_REGION_PARAMETER'         : dem.extent(),
                'GRASS_REGION_CELLSIZE_PARAMETER': pixel_x,
                'GRASS_RASTER_FORMAT_OPT' : '',
                'GRASS_RASTER_FORMAT_META': '',
            },
            context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
        )
        feedback.pushInfo("  Stream skeleton thinned.\n")

        # ── 3. r.to.vect ──────────────────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step 3/{n_steps}: r.to.vect - vectorising stream network...")
        vect = processing.run(
            f"{grass}:r.to.vect",
            {
                'input' : thin['output'], 'type': 0,
                'column': 'stream_order',
                '-s': True, '-v': False, '-z': False, '-b': False, '-t': False,
                'output': QgsProcessing.TEMPORARY_OUTPUT,
                'GRASS_OUTPUT_TYPE_PARAMETER'    : 0,
                'GRASS_REGION_PARAMETER'         : dem.extent(),
                'GRASS_REGION_CELLSIZE_PARAMETER': pixel_x,
                'GRASS_VECTOR_DSCO': '', 'GRASS_VECTOR_LCO': '',
                'GRASS_VECTOR_EXPORT_NOCAT': False,
            },
            context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
        )
        streams_lyr = vect['output']
        feedback.pushInfo("  Stream network vectorised.\n")

        # ── 4. Filter short segments (optional) ───────────────────────────────
        if min_length > 0:
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo(
                f"Step 4/{n_steps}: Removing segments shorter than {min_length:.1f} m..."
            )
            filt = processing.run(
                "native:extractbyexpression",
                {
                    'INPUT': streams_lyr,
                    'EXPRESSION': f'$length >= {min_length}',
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=mfb, is_child_algorithm=True,
            )
            streams_lyr = filt['OUTPUT']
            feedback.pushInfo("  Short segments removed.\n")

        # ── 5. Assign CRS to stream output ────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Assigning CRS to stream network...")

        temp_lyr = QgsProcessingUtils.mapLayerFromString(streams_lyr, context)
        if temp_lyr is None:
            raise QgsProcessingException(self.tr(
                "Failed to load vectorised stream network for CRS assignment."
            ))
        temp_lyr.setCrs(crs)

        streams_def = parameters[self.OUTPUT_STREAMS]
        if not isinstance(streams_def, QgsProcessingOutputLayerDefinition):
            streams_def = QgsProcessingOutputLayerDefinition(streams_def)
        streams_def.destinationName = 'Streams'
        if _HAS_POST_PROCESSOR:
            _StreamPostProcessor.instance = _StreamPostProcessor()
            streams_def.setPostProcessor(_StreamPostProcessor.instance)

        stream_out = processing.run(
            "native:assignprojection",
            {'INPUT': temp_lyr, 'CRS': crs, 'OUTPUT': streams_def},
            context=context, feedback=mfb, is_child_algorithm=True,
        )['OUTPUT']
        feedback.pushInfo(f"  Stream network output ready ({crs.authid()}).\n")

        # ── 6. Polygonise basin raster ────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Polygonising basin raster...")
        poly = processing.run(
            "gdal:polygonize",
            {
                'INPUT': basin_raster, 'BAND': 1,
                'FIELD': 'basin_id', 'EIGHT_CONNECTEDNESS': False,
                'EXTRA': '', 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )

        filtered = processing.run(
            "native:extractbyexpression",
            {
                'INPUT': poly['OUTPUT'],
                'EXPRESSION': '"basin_id" > 0',
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )
        feedback.pushInfo("  Basin polygons extracted.\n")

        # ── 7. Dissolve by basin_id ───────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Dissolving sub-catchment polygons...")
        dissolved = processing.run(
            "native:dissolve",
            {
                'INPUT': filtered['OUTPUT'],
                'FIELD': ['basin_id'],
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )
        feedback.pushInfo("  Sub-catchments dissolved.\n")

        # ── 8. Fix geometries ─────────────────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Fixing invalid geometries and overlaps...")
        fixed = processing.run(
            "native:fixgeometries",
            {
                'INPUT' : dissolved['OUTPUT'],
                'METHOD': 1,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )
        feedback.pushInfo("  Geometries fixed.\n")

        # ── 9. Remove null/empty geometries ───────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Removing null and empty geometries...")
        cleaned = processing.run(
            "native:removenullgeometries",
            {
                'INPUT'            : fixed['OUTPUT'],
                'REMOVE_EMPTY'     : True,
                'OUTPUT'           : QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )

        n_fixed   = fixed['OUTPUT']
        n_cleaned = cleaned['OUTPUT']
        fixed_lyr   = QgsProcessingUtils.mapLayerFromString(n_fixed,   context)
        cleaned_lyr = QgsProcessingUtils.mapLayerFromString(n_cleaned, context)
        if fixed_lyr and cleaned_lyr:
            removed = fixed_lyr.featureCount() - cleaned_lyr.featureCount()
            if removed > 0:
                feedback.pushWarning(f"  Removed {removed} null/empty geometry polygon(s).")
            else:
                feedback.pushInfo("  No null or empty geometries found.")
        feedback.pushInfo("")

        # ── Helper: build subbasins output definition with post-processor ────────
        def _subbasins_def_with_post_processor():
            sd = parameters[self.OUTPUT_SUBBASINS]
            if not isinstance(sd, QgsProcessingOutputLayerDefinition):
                sd = QgsProcessingOutputLayerDefinition(sd)
            sd.destinationName = 'Sub-Catchments'
            if _HAS_POST_PROCESSOR:
                _SubbasinPostProcessor.instance = _SubbasinPostProcessor()
                sd.setPostProcessor(_SubbasinPostProcessor.instance)
            return sd

        # ── 10. Assign SubcatchID (optional) ──────────────────────────────────
        if assign_ids:
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo(
                f"Step {step}/{n_steps}: Assigning SubcatchID "
                f"({sc_prefix}_0001, {sc_prefix}_0002, …)..."
            )
            formula = f"concat('{sc_prefix}_', lpad(to_string(@row_number + 1), 4, '0'))"
            named_out = processing.run(
                "native:fieldcalculator",
                {
                    'INPUT'          : cleaned['OUTPUT'],
                    'FIELD_NAME'     : 'SubcatchID',
                    'FIELD_TYPE'     : 2,
                    'FIELD_LENGTH'   : 20,
                    'FIELD_PRECISION': 0,
                    'FORMULA'        : formula,
                    'OUTPUT'         : QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=mfb, is_child_algorithm=True,
            )
            retain_input  = named_out['OUTPUT']
            retain_fields = ['SubcatchID']
            feedback.pushInfo(f"  SubcatchID assigned using prefix '{sc_prefix}'.\n")
        else:
            retain_input  = cleaned['OUTPUT']
            retain_fields = []

        # ── Final step: output SubcatchID only (drops basin_id and all others) ─
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Step {step}/{n_steps}: Retaining SubcatchID field only...")
        retained = processing.run(
            "native:retainfields",
            {
                'INPUT'  : retain_input,
                'FIELDS' : retain_fields,
                'OUTPUT' : _subbasins_def_with_post_processor(),
            },
            context=context, feedback=mfb, is_child_algorithm=True,
        )
        final_subbasins = retained['OUTPUT']
        feedback.pushInfo("  Output schema: SubcatchID only.\n")

        feedback.pushInfo("  Sub-catchment polygons written.\n")
        feedback.pushInfo("--- Complete ---")
        feedback.pushInfo(
            f"  Min area  : {threshold_ha} ha  ({threshold_cells} cells)\n"
            "  TIP: Adjust the contributing area threshold to control "
            "stream density and sub-catchment size."
        )

        return {
            self.OUTPUT_SUBBASINS   : final_subbasins,
            self.OUTPUT_STREAMS     : stream_out,
            self.OUTPUT_DRAINAGE    : drainage_raster,
            self.OUTPUT_ACCUMULATION: accum_raster,
        }
