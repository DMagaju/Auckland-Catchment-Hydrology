# -*- coding: utf-8 -*-
def classFactory(iface):
    from .plugin import SubCatchmentAssessmentPlugin
    return SubCatchmentAssessmentPlugin(iface)
