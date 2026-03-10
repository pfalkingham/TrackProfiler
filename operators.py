# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  — edit these constants to change addon behaviour
# ─────────────────────────────────────────────────────────────────────────────
SAMPLES_PER_SEGMENT  = 50     # points sampled along each transect (easy to change)
LOCATOR_DISPLAY_SIZE = 0.01   # sphere empty display size (in scene units; adjust to taste)

# Landmark order (fixed — user clicks in this sequence)
LANDMARK_NAMES  = ["HAL",    "MT1",      "MT5",      "HEL"]
LANDMARK_LABELS = ["Hallux", "MT1 Head", "MT5 Head", "Heel"]

# Transect definitions: (from, to, csv_label)
# Depth column is raw world Z.  Assumes substrate ≈ Z=0, footprint below Z=0.
# "distance_along_transect" and "depth" are in scene units (label assumes mm-scaled model).
SEGMENTS = [
    ("HAL", "MT1", "Hallux_MT1"),
    ("MT1", "MT5", "MT1_MT5"),
    ("MT5", "HEL", "MT5_Heel"),
    ("HEL", "MT1", "Heel_MT1"),   # medial arch — change second entry to HAL to close loop instead
]

# Fixed viewport / graph colours for each segment (RGBA, 0–1 range)
SEGMENT_COLORS = {
    "Hallux_MT1": (0.90, 0.22, 0.22, 1.0),   # red
    "MT1_MT5":    (0.22, 0.68, 0.30, 1.0),   # green
    "MT5_Heel":   (0.20, 0.55, 0.90, 1.0),   # blue
    "Heel_MT1":   (0.92, 0.65, 0.12, 1.0),   # amber
}
# ─────────────────────────────────────────────────────────────────────────────

import bpy
import bpy_extras.view3d_utils
import mathutils
import csv
import os
import json
from bpy.props import StringProperty
from bpy.types import Operator

# Module-level dict: mesh_name -> analysis result.
# Persists for the Blender session; cleared with the "Clear Results" button.
_results: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def locator_name(mesh_name: str, landmark: str) -> str:
    """Canonical name for a landmark empty: '<mesh>_<landmark>'."""
    return f"{mesh_name}_{landmark}"


def find_locator(mesh_name: str, landmark: str):
    """Return the empty object for a landmark, or None."""
    return bpy.data.objects.get(locator_name(mesh_name, landmark))


def all_locators_present(mesh_name: str) -> bool:
    return all(find_locator(mesh_name, lm) is not None for lm in LANDMARK_NAMES)


def segment_line_name(mesh_name: str, seg_label: str) -> str:
    """Canonical name for a segment line object: '<mesh>_LINE_<seg>'."""
    return f"{mesh_name}_LINE_{seg_label}"


def create_segment_lines(mesh_obj):
    """Create or replace 3D line objects connecting each segment's locator pair."""
    mesh_name  = mesh_obj.name
    collection = bpy.context.collection
    for (lm_a, lm_b, seg_label) in SEGMENTS:
        line_name = segment_line_name(mesh_name, seg_label)
        # Remove any existing line object for this segment
        existing = bpy.data.objects.get(line_name)
        if existing:
            me_old = existing.data if isinstance(existing.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(existing, do_unlink=True)
            if me_old is not None and me_old.users == 0:
                bpy.data.meshes.remove(me_old)

        loc_a = find_locator(mesh_name, lm_a)
        loc_b = find_locator(mesh_name, lm_b)
        if loc_a is None or loc_b is None:
            continue

        pa    = tuple(loc_a.matrix_world.translation)
        pb    = tuple(loc_b.matrix_world.translation)
        color = SEGMENT_COLORS.get(seg_label, (0.8, 0.8, 0.8, 1.0))

        # Build a 2-vert / 1-edge mesh
        me = bpy.data.meshes.new(line_name)
        me.from_pydata([pa, pb], [(0, 1)], [])
        me.update()

        # Create or reuse a per-segment material
        mat_name = f"FP_SEG_{seg_label}"
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)
            mat.use_nodes = False
        mat.diffuse_color = color
        me.materials.append(mat)

        # Create the object
        line_obj               = bpy.data.objects.new(line_name, me)
        line_obj.display_type  = 'WIRE'
        line_obj.show_in_front = True
        line_obj.color         = color
        collection.objects.link(line_obj)

        # Parent to mesh so lines move / scale with the footprint
        line_obj.parent                = mesh_obj
        line_obj.matrix_parent_inverse = mesh_obj.matrix_world.inverted()


def remove_segment_lines(mesh_name: str):
    """Remove the four segment line objects for a given mesh, if they exist."""
    for _, _, seg_label in SEGMENTS:
        name = segment_line_name(mesh_name, seg_label)
        obj  = bpy.data.objects.get(name)
        if obj:
            me = obj.data if isinstance(obj.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)


def load_results_from_scene():
    """Restore analysis results from mesh custom properties after a file load."""
    _results.clear()
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        data_str = obj.get("fp_track_data")
        if data_str is None:
            continue
        try:
            _results[obj.name] = json.loads(str(data_str))
        except (json.JSONDecodeError, TypeError):
            pass  # corrupt or missing data — skip silently


def raycast_z(mesh_obj, x_world: float, y_world: float):
    """
    Fire a ray straight down (−Z) at world (x, y) position and return
    the world-space Z where it hits the mesh, or None on a miss.
    """
    mat_inv  = mesh_obj.matrix_world.inverted()
    origin_w = mathutils.Vector((x_world, y_world, 1000.0))
    dir_w    = mathutils.Vector((0.0, 0.0, -1.0))
    origin_l = mat_inv @ origin_w
    dir_l    = (mat_inv.to_3x3() @ dir_w).normalized()
    hit, loc_l, _, _ = mesh_obj.ray_cast(origin_l, dir_l)
    if hit:
        return (mesh_obj.matrix_world @ loc_l).z
    return None


def sample_transect(mesh_obj, loc_a, loc_b, n: int = SAMPLES_PER_SEGMENT):
    """
    Sample n evenly-spaced points along the XY projection of loc_a → loc_b,
    raycasting vertically onto the mesh at each step.

    Returns:
        rows       – list of (point_index, distance_along_transect, depth)
        total_len  – XY distance between the two locators (scene units)
    """
    pa = loc_a.matrix_world.translation
    pb = loc_b.matrix_world.translation
    dx, dy   = pb.x - pa.x, pb.y - pa.y
    total_len = (dx ** 2 + dy ** 2) ** 0.5

    rows = []
    for i in range(n):
        t     = i / (n - 1) if n > 1 else 0.0
        x     = pa.x + t * dx
        y     = pa.y + t * dy
        depth = raycast_z(mesh_obj, x, y)
        rows.append((i, t * total_len, depth))
    return rows, total_len


# ── Operators ─────────────────────────────────────────────────────────────────

class FOOTPRINT_OT_Initialize(Operator):
    """
    Start interactive landmark picking for the active mesh.
    Click four times on the mesh surface in order: Hallux, MT1 Head, MT5 Head, Heel.
    Press ESC or RMB to cancel (partially placed locators are removed).
    Running Initialize again on the same mesh starts a new session; any previously
    placed empties for that mesh remain in the scene until overwritten by new clicks.
    """
    bl_idname  = "footprint.initialize"
    bl_label   = "Initialize Landmarks"
    bl_options = {'REGISTER', 'UNDO'}

    # Instance state (re-initialised in invoke)
    _mesh_obj: bpy.types.Object = None
    _click_count: int           = 0
    _session_empties: list      = []

    @classmethod
    def poll(cls, context):
        ao = context.active_object
        return ao is not None and ao.type == 'MESH' and context.mode == 'OBJECT'

    def invoke(self, context, event):
        self._mesh_obj        = context.active_object
        self._click_count     = 0
        self._session_empties = []
        remove_segment_lines(self._mesh_obj.name)
        self._set_status(context, 0)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # If the user selects a different object, abort
        if context.active_object is not self._mesh_obj:
            self._discard()
            self._clear_status(context)
            self.report({'WARNING'}, "Active object changed — landmark picking cancelled")
            return {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._discard()
            self._clear_status(context)
            self.report({'INFO'}, "Landmark picking cancelled")
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            region = context.region
            rv3d   = context.region_data
            if region is None or rv3d is None:
                return {'PASS_THROUGH'}

            coord = (event.mouse_region_x, event.mouse_region_y)
            ray_o = bpy_extras.view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
            ray_d = bpy_extras.view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
            if ray_o is None or ray_d is None:
                return {'RUNNING_MODAL'}

            mat_inv  = self._mesh_obj.matrix_world.inverted()
            origin_l = mat_inv @ mathutils.Vector(ray_o)
            dir_l    = (mat_inv.to_3x3() @ mathutils.Vector(ray_d)).normalized()
            hit, loc_l, _, _ = self._mesh_obj.ray_cast(origin_l, dir_l)

            if not hit:
                return {'RUNNING_MODAL'}   # missed mesh — keep waiting

            loc_w = self._mesh_obj.matrix_world @ loc_l
            lm    = LANDMARK_NAMES[self._click_count]
            name  = locator_name(self._mesh_obj.name, lm)

            # Remove any existing locator with this name
            existing = bpy.data.objects.get(name)
            if existing:
                bpy.data.objects.remove(existing, do_unlink=True)

            # Create sphere empty at hit position
            empty = bpy.data.objects.new(name, None)
            empty.empty_display_type = 'SPHERE'
            empty.empty_display_size = LOCATOR_DISPLAY_SIZE
            empty.location           = loc_w
            context.collection.objects.link(empty)

            # Parent to mesh so it moves/scales with it
            empty.parent                = self._mesh_obj
            empty.matrix_parent_inverse = self._mesh_obj.matrix_world.inverted()

            self._session_empties.append(empty)
            self._click_count += 1

            if self._click_count == 4:
                self.report({'INFO'}, f"All landmarks placed for '{self._mesh_obj.name}' — running analysis")
                bpy.ops.footprint.analyse()
                return {'FINISHED'}

            self._set_status(context, self._click_count)
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    # ── Helpers ──

    def _set_status(self, context, idx: int):
        context.scene.footprint_status = (
            f"Picking landmarks for '{self._mesh_obj.name}' — "
            f"click {LANDMARK_LABELS[idx]}  ({idx + 1} / 4)"
        )

    def _clear_status(self, context):
        context.scene.footprint_status = ""

    def _discard(self):
        """Remove only empties that were placed in this session."""
        for emp in self._session_empties:
            if emp and emp.name in bpy.data.objects:
                bpy.data.objects.remove(emp, do_unlink=True)
        self._session_empties = []


class FOOTPRINT_OT_Analyse(Operator):
    """
    Sample depth profiles along the four transects for the active mesh and
    store results in memory. All four landmark empties must be present.
    Re-running this will overwrite any previous results for the same mesh.
    """
    bl_idname  = "footprint.analyse"
    bl_label   = "Analyse"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        ao = context.active_object
        return (
            ao is not None
            and ao.type == 'MESH'
            and context.mode == 'OBJECT'
            and all_locators_present(ao.name)
        )

    def execute(self, context):
        from . import graph

        mesh_obj  = context.active_object
        mesh_name = mesh_obj.name

        all_rows    = []
        seg_lengths = {}
        lm_coords   = {}

        for lm in LANDMARK_NAMES:
            loc = find_locator(mesh_name, lm)
            lm_coords[lm] = tuple(loc.matrix_world.translation)

        for (lm_a, lm_b, seg_label) in SEGMENTS:
            loc_a = find_locator(mesh_name, lm_a)
            loc_b = find_locator(mesh_name, lm_b)
            rows, length = sample_transect(mesh_obj, loc_a, loc_b)
            seg_lengths[seg_label] = length

            for (pt_idx, dist, depth) in rows:
                all_rows.append({
                    "mesh":                       mesh_name,
                    "segment":                    seg_label,
                    "point_index":                pt_idx,
                    "distance_along_transect_mm": round(dist,  4) if dist  is not None else "",
                    "depth_mm":                   round(depth, 4) if depth is not None else "",
                })

        _results[mesh_name] = {
            "rows":         all_rows,
            "seg_lengths":  seg_lengths,
            "lm_coords":    lm_coords,
        }
        create_segment_lines(mesh_obj)
        mesh_obj["fp_track_data"] = json.dumps(_results[mesh_name])
        graph.notify_results_changed(context.scene)

        context.scene.footprint_status = (
            f"'{mesh_name}' analysed. {len(_results)} track(s) ready to export."
        )
        self.report({'INFO'}, f"Analysis complete for '{mesh_name}'")
        return {'FINISHED'}


class FOOTPRINT_OT_ExportCSV(Operator):
    """
    Export all analysed tracks to a single CSV file.
    Metadata rows (landmark coordinates, segment lengths) are written first,
    followed by the depth-profile data rows.
    """
    bl_idname = "footprint.export_csv"
    bl_label  = "Export CSV"

    filepath: StringProperty(subtype='FILE_PATH', default="footprint_profiles.csv")

    @classmethod
    def poll(cls, context):
        return len(_results) > 0

    def invoke(self, context, event):
        blend_dir    = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
        self.filepath = os.path.join(blend_dir, "footprint_profiles.csv")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        fp = self.filepath
        if not fp.lower().endswith(".csv"):
            fp += ".csv"

        track_names = list(_results.keys())

        # Build fieldnames: fixed cols + one group of 3 cols per track
        data_cols = []
        for tn in track_names:
            data_cols.extend([
                f"{tn}_distance_mm",
                f"{tn}_depth_mm",
                f"{tn}_cumulative_distance_mm",
            ])
        fieldnames = ["segment", "point_index", "cumulative_index"] + data_cols

        # Pre-compute per-track cumulative segment offsets
        # seg_offsets[mesh_name][seg_label] = sum of preceding segment lengths
        seg_offsets: dict = {}
        for mesh_name, result in _results.items():
            offsets: dict = {}
            running = 0.0
            for _, _, seg_label in SEGMENTS:
                offsets[seg_label] = running
                running += result["seg_lengths"].get(seg_label, 0.0)
            seg_offsets[mesh_name] = offsets

        # Build fast lookup: track_data[mesh_name][seg_label][point_index] = row
        track_data: dict = {}
        for mesh_name, result in _results.items():
            by_seg: dict = {}
            for row in result["rows"]:
                by_seg.setdefault(row["segment"], {})[row["point_index"]] = row
            track_data[mesh_name] = by_seg

        with open(fp, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()

            # ── Metadata block ──
            for mesh_name, result in _results.items():
                for lm, xyz in result["lm_coords"].items():
                    writer.writerow({
                        "segment":          f"META_LANDMARK_{lm}",
                        "point_index":      mesh_name,
                        "cumulative_index": f"{xyz[0]:.4f},{xyz[1]:.4f},{xyz[2]:.4f}",
                    })
                for seg_label, length in result["seg_lengths"].items():
                    writer.writerow({
                        "segment":          f"META_LENGTH_{seg_label}",
                        "point_index":      mesh_name,
                        "cumulative_index": f"{length:.4f}",
                    })

            # ── Data block: one row per (segment, sample_point) ──
            cumulative_index = 0
            for _, _, seg_label in SEGMENTS:
                for pt_idx in range(SAMPLES_PER_SEGMENT):
                    out_row: dict = {
                        "segment":          seg_label,
                        "point_index":      pt_idx,
                        "cumulative_index": cumulative_index,
                    }
                    for mesh_name in track_names:
                        row = track_data.get(mesh_name, {}).get(seg_label, {}).get(pt_idx)
                        if row is not None:
                            dist  = row.get("distance_along_transect_mm", "")
                            depth = row.get("depth_mm", "")
                            if dist != "" and dist is not None:
                                cum_dist = round(
                                    seg_offsets[mesh_name][seg_label] + float(dist), 4
                                )
                            else:
                                cum_dist = ""
                            out_row[f"{mesh_name}_distance_mm"]            = dist
                            out_row[f"{mesh_name}_depth_mm"]               = depth
                            out_row[f"{mesh_name}_cumulative_distance_mm"] = cum_dist
                    writer.writerow(out_row)
                    cumulative_index += 1

        total_points = len(SEGMENTS) * SAMPLES_PER_SEGMENT
        self.report(
            {'INFO'},
            f"Exported {total_points} data rows × {len(track_names)} track(s) → {fp}"
        )
        return {'FINISHED'}


class FOOTPRINT_OT_DeleteTrack(Operator):
    """Delete one track's data, segment lines, and landmark locators."""
    bl_idname  = "footprint.delete_track"
    bl_label   = "Delete Track"
    bl_options = {'REGISTER', 'UNDO'}

    mesh_name: StringProperty()

    def execute(self, context):
        from . import graph

        name = self.mesh_name
        remove_segment_lines(name)

        # Remove landmark locators
        for lm in LANDMARK_NAMES:
            loc = find_locator(name, lm)
            if loc:
                bpy.data.objects.remove(loc, do_unlink=True)

        # Remove persisted custom property
        obj = bpy.data.objects.get(name)
        if obj and "fp_track_data" in obj:
            del obj["fp_track_data"]

        _results.pop(name, None)
        graph.notify_results_changed(context.scene)
        context.scene.footprint_status = f"'{name}' deleted."
        return {'FINISHED'}


class FOOTPRINT_OT_ClearResults(Operator):
    """Remove all stored analysis results from memory (does not affect mesh or locators)."""
    bl_idname = "footprint.clear_results"
    bl_label  = "Clear Results"

    def execute(self, context):
        from . import graph

        for mesh_name in list(_results.keys()):
            remove_segment_lines(mesh_name)
            obj = bpy.data.objects.get(mesh_name)
            if obj and "fp_track_data" in obj:
                del obj["fp_track_data"]
        _results.clear()
        graph.notify_results_changed(context.scene)
        context.scene.footprint_status = "Results cleared."
        return {'FINISHED'}
