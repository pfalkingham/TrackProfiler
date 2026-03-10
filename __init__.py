bl_info = {
    "name": "Track Depth Profiler",
    "version": (1, 0, 2),
    "blender": (3, 0, 0),
    "location": "View3D > N-Panel > Footprint",
    "description": (
        "Click 4 landmarks on a footprint mesh (Hallux, MT1, MT5, Heel), "
        "then extract depth profiles along the 4 transects. "
        "Supports multiple tracks per scene; exports all to a single CSV."
    ),
    "category": "Object",
}

import bpy
from . import graph, operators, panels

_classes = [
    operators.FOOTPRINT_OT_Initialize,
    operators.FOOTPRINT_OT_Analyse,
    operators.FOOTPRINT_OT_ExportCSV,
    operators.FOOTPRINT_OT_DeleteTrack,
    operators.FOOTPRINT_OT_ClearResults,
    panels.FOOTPRINT_PT_Main,
]


def register():
    graph.register()
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.footprint_status = bpy.props.StringProperty(
        name="Footprint Status",
        default="",
    )


def unregister():
    graph.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.footprint_status


if __name__ == "__main__":
    register()
