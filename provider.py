# -*- coding: utf-8 -*-
"""Auckland Catchment & Hydrology — QgsProcessingProvider."""

import os

from qgis.core import QgsProcessingProvider, QgsMessageLog, Qgis
from qgis.PyQt.QtGui import QIcon

_PLUGIN_DIR = os.path.realpath(os.path.dirname(__file__))
_LOG_TAG    = 'AucklandCatchmentHydrology'


class SubCatchmentProvider(QgsProcessingProvider):

    def id(self):
        return 'subcatchmentassessment'

    def name(self):
        return 'Auckland Catchment & Hydrology'

    def longName(self):
        return 'Auckland Catchment & Hydrology'

    def icon(self):
        svg = os.path.join(_PLUGIN_DIR, 'icon.svg')
        png = os.path.join(_PLUGIN_DIR, 'icon.png')
        path = svg if os.path.exists(svg) else png
        return QIcon(path) if os.path.exists(path) else super().icon()

    def loadAlgorithms(self):
        # Deferred imports — algorithms pull in `import processing` and other
        # heavy modules; loading them here (not at module level) ensures QGIS
        # is fully initialised before any of that runs.
        from .algorithms.Subcatch_DownloadLidar          import TmDownloadLiDAR
        from .algorithms.Subcatch_DEMChannelBurn         import SubcatchDEMChannelBurn
        from .algorithms.Subcatch_StreamCatchment        import StreamCatchmentGenerator
        from .algorithms.Subcatch_OutletCatchment        import OutletCatchmentGenerator
        from .algorithms.Subcatch_MergeSplit             import SubcatchMergeSplit
        from .algorithms.Subcatch_LongestFlowPath        import LongestFlowPathAlgorithm
        from .algorithms.Subcatch_SlopeCalculation       import CalculateSlopeForToC
        from .algorithms.Subcatch_ImperviousCalculations import CalculateImperviousAreas
        from .algorithms.Subcatch_TimeOfConcentration    import SubcatchmentToC
        from .algorithms.Subcatch_FlowCalculation        import SubcatchFlowCalculationAlgorithm
        from .algorithms.Subcatch_Naming                 import CalculateSubcatchmentIDs
        from .algorithms.Subcatch_Loading                import SubcatchmentLoadingAlgorithm

        for cls in (
            TmDownloadLiDAR,
            SubcatchDEMChannelBurn,
            StreamCatchmentGenerator,
            OutletCatchmentGenerator,
            SubcatchMergeSplit,
            LongestFlowPathAlgorithm,
            CalculateSlopeForToC,
            CalculateImperviousAreas,
            SubcatchmentToC,
            SubcatchFlowCalculationAlgorithm,
            CalculateSubcatchmentIDs,
            SubcatchmentLoadingAlgorithm,
        ):
            try:
                self.addAlgorithm(cls())
            except Exception as exc:
                QgsMessageLog.logMessage(
                    f'Failed to load {cls.__name__}: {exc}', _LOG_TAG, Qgis.Warning)
