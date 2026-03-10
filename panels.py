import bpy
from bpy.types import Panel
from .operators import _results, find_locator, all_locators_present, LANDMARK_NAMES, LANDMARK_LABELS
from . import bl_info, graph


# ── Panel ─────────────────────────────────────────────────────────────────────

class FOOTPRINT_PT_Main(Panel):
    bl_label       = "Track Profiler"
    bl_idname      = "FOOTPRINT_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "TrackProfiler"

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        ao     = context.active_object

        # ── Version header ──
        version = bl_info.get("version", ("?",))
        version_str = ".".join(str(v) for v in version)
        layout.label(text=f"Version v{version_str}")
        layout.separator()

        # ── Status message ──
        if scene.footprint_status:
            box = layout.box()
            box.label(text=scene.footprint_status, icon='INFO')
            layout.separator()

        # ── Active mesh section ──
        if ao and ao.type == 'MESH':
            layout.label(text=f"Mesh:  {ao.name}", icon='MESH_DATA')

            row = layout.row()
            row.scale_y = 1.3
            row.operator("footprint.initialize", icon='CURSOR')

            # Landmark checklist
            box = layout.box()
            box.label(text="Landmarks:", icon='EMPTY_AXIS')
            for lm, label in zip(LANDMARK_NAMES, LANDMARK_LABELS):
                obj  = find_locator(ao.name, lm)
                row  = box.row()
                icon = 'CHECKMARK' if obj else 'PANEL_CLOSE'
                row.label(text=f"  {label}", icon=icon)

            # Analyse button — greyed out until all 4 are present
            row = layout.row()
            row.scale_y = 1.3
            row.enabled = all_locators_present(ao.name)
            row.operator("footprint.analyse", icon='PLAY')

            if ao.name in _results:
                layout.label(text="  ✓ Data stored", icon='CHECKMARK')
        else:
            layout.label(text="Select a mesh object", icon='INFO')

        layout.separator()

        # ── Stored results ──
        layout.label(text=f"Analysed tracks:  {len(_results)}", icon='PRESET')
        if _results:
            graph_box = layout.box()
            graph_header = graph_box.row(align=True)
            graph_header.operator(
                "footprint.toggle_graph",
                text="Hide Graph" if scene.footprint_graph_enabled else "Show Graph",
                icon='HIDE_OFF' if scene.footprint_graph_enabled else 'HIDE_ON',
            )
            graph_header.prop(scene, "footprint_graph_x_mode", text="")

            box = layout.box()
            for name in _results:
                track = graph.get_track_display(scene, name)
                segs = _results[name]["seg_lengths"]

                row = box.row(align=True)
                if track is not None:
                    row.prop(
                        track,
                        "visible",
                        text="",
                        icon='HIDE_OFF' if track.visible else 'HIDE_ON',
                        emboss=False,
                    )
                    row.prop(track, "color", text="")
                    row.prop(
                        track,
                        "expand",
                        text="",
                        icon='DISCLOSURE_TRI_DOWN' if track.expand else 'DISCLOSURE_TRI_RIGHT',
                        emboss=False,
                    )
                row.label(text=name, icon='CHECKMARK')
                del_op = row.operator("footprint.delete_track", text="", icon='X', emboss=False)
                del_op.mesh_name = name

                if track is not None and track.expand:
                    sub = box.column(align=True)
                    sub.scale_y = 0.8
                    for seg_label, length in segs.items():
                        sub.label(text=f"      {seg_label}:  {length:.1f}")

            layout.separator()
            layout.operator("footprint.export_csv",    icon='EXPORT', text="Export CSV")
            layout.operator("footprint.clear_results", icon='TRASH',  text="Clear Results")
