"""Standalone animated depth-profile baker for TrackProfiler.

Paste this into Blender's Text Editor and run it after enabling the
TrackProfiler addon. It bakes one profile per frame for the active mesh,
draws every baked frame as a line in the viewport, colors the lines from red
at the first frame to blue at the last frame, and highlights the current frame
in white while you scrub.

This script does not modify the addon itself.
"""

import json

import blf
import bpy
import gpu
import mathutils

from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import Operator, Panel

try:
    from TrackProfiler import operators as tp_ops
except Exception:
    tp_ops = None


GRAPH_MARGIN = 20
GRAPH_WIDTH = 760
GRAPH_HEIGHT = 360
HEADER_HEIGHT = 40
BOTTOM_PADDING = 30
LEFT_PADDING = 54
RIGHT_PADDING = 18
TITLE = "Animated Depth Profiles"
CACHE_PROP = "tp_animated_profile_cache"

RED = (0.92, 0.22, 0.18, 1.0)
BLUE = (0.18, 0.46, 0.94, 1.0)
WHITE = (1.0, 1.0, 1.0, 1.0)
BACKGROUND = (0.08, 0.08, 0.08, 0.84)
OUTLINE = (0.80, 0.80, 0.80, 0.32)
GUIDE = (0.86, 0.86, 0.86, 0.24)
TEXT = (0.96, 0.96, 0.96, 1.0)

_draw_handle = None
_frame_handler = None
_state = {
    "enabled": True,
    "cache": {},
}


def _require_addon():
    if tp_ops is None:
        raise RuntimeError("TrackProfiler addon is not available. Enable it first.")


def _lerp(a, b, t):
    return a + ((b - a) * t)


def _gradient_color(frame, first_frame, last_frame):
    if last_frame <= first_frame:
        return RED
    t = max(0.0, min(1.0, (frame - first_frame) / (last_frame - first_frame)))
    return (
        _lerp(RED[0], BLUE[0], t),
        _lerp(RED[1], BLUE[1], t),
        _lerp(RED[2], BLUE[2], t),
        0.92,
    )


def _mesh_cache(mesh_name):
    return _state["cache"].get(mesh_name)


def _normalize_loaded_cache(data):
    if not isinstance(data, dict):
        return None

    frames = data.get("frames")
    if isinstance(frames, dict):
        normalized = {}
        for key, value in frames.items():
            try:
                normalized[int(key)] = value
            except (TypeError, ValueError):
                continue
        data["frames"] = normalized
    return data


def _load_cache_from_scene():
    _state["cache"].clear()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        raw = obj.get(CACHE_PROP)
        if raw is None:
            continue
        try:
            data = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            continue
        data = _normalize_loaded_cache(data)
        if data is not None:
            _state["cache"][obj.name] = data


def _save_cache_to_object(mesh_obj, cache):
    mesh_obj[CACHE_PROP] = json.dumps(cache)


def _clear_cache_property(mesh_name):
    obj = bpy.data.objects.get(mesh_name)
    if obj and CACHE_PROP in obj:
        del obj[CACHE_PROP]


def _raycast_z_eval(eval_obj, x_world, y_world):
    mat_inv = eval_obj.matrix_world.inverted()
    origin_w = mathutils.Vector((x_world, y_world, 1000.0))
    dir_w = mathutils.Vector((0.0, 0.0, -1.0))
    origin_l = mat_inv @ origin_w
    dir_l = (mat_inv.to_3x3() @ dir_w).normalized()
    hit, loc_l, _, _ = eval_obj.ray_cast(origin_l, dir_l)
    if hit:
        return (eval_obj.matrix_world @ loc_l).z
    return None


def _sample_transect_eval(mesh_obj, loc_a, loc_b, depsgraph, sample_count):
    eval_obj = mesh_obj.evaluated_get(depsgraph)
    pa = loc_a.matrix_world.translation
    pb = loc_b.matrix_world.translation
    dx = pb.x - pa.x
    dy = pb.y - pa.y
    total_len = (dx * dx + dy * dy) ** 0.5

    rows = []
    for index in range(sample_count):
        t = index / (sample_count - 1) if sample_count > 1 else 0.0
        x = pa.x + (t * dx)
        y = pa.y + (t * dy)
        depth = _raycast_z_eval(eval_obj, x, y)
        rows.append((index, t * total_len, depth))
    return rows, total_len


def _build_frame_result(mesh_obj, context):
    _require_addon()
    depsgraph = context.evaluated_depsgraph_get()
    mesh_name = mesh_obj.name
    sample_count = getattr(tp_ops, "SAMPLES_PER_SEGMENT", 50)

    lm_coords = {}
    for lm in tp_ops.LANDMARK_NAMES:
        locator = tp_ops.find_locator(mesh_name, lm)
        if locator is None:
            raise RuntimeError(f"Missing locator: {mesh_name}_{lm}")
        lm_coords[lm] = tuple(locator.matrix_world.translation)

    rows = []
    seg_lengths = {}

    for lm_a, lm_b, seg_label in tp_ops.SEGMENTS:
        loc_a = tp_ops.find_locator(mesh_name, lm_a)
        loc_b = tp_ops.find_locator(mesh_name, lm_b)
        if loc_a is None or loc_b is None:
            raise RuntimeError(f"Missing locators for segment {seg_label}")

        seg_rows, seg_length = _sample_transect_eval(mesh_obj, loc_a, loc_b, depsgraph, sample_count)
        seg_lengths[seg_label] = seg_length

        for point_index, dist, depth in seg_rows:
            rows.append({
                "mesh": mesh_name,
                "segment": seg_label,
                "point_index": point_index,
                "distance_along_transect_mm": round(dist, 4) if dist is not None else "",
                "depth_mm": round(depth, 4) if depth is not None else "",
            })

    return {
        "frame": context.scene.frame_current,
        "rows": rows,
        "seg_lengths": seg_lengths,
        "lm_coords": lm_coords,
    }


def bake_active_mesh_frames(context):
    _require_addon()

    mesh_obj = context.active_object
    if mesh_obj is None or mesh_obj.type != "MESH":
        raise RuntimeError("Select a mesh object first.")

    if not all(tp_ops.find_locator(mesh_obj.name, lm) is not None for lm in tp_ops.LANDMARK_NAMES):
        raise RuntimeError("All four TrackProfiler landmarks must exist before baking.")

    scene = context.scene
    original_frame = scene.frame_current
    frame_start = scene.frame_start
    frame_end = scene.frame_end

    cache = {
        "mesh_name": mesh_obj.name,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frames": {},
    }

    try:
        for frame in range(frame_start, frame_end + 1):
            scene.frame_set(frame)
            context.view_layer.update()
            cache["frames"][frame] = _build_frame_result(mesh_obj, context)

        _state["cache"][mesh_obj.name] = cache
        _save_cache_to_object(mesh_obj, cache)
        tag_redraw_all_view3d()
        return len(cache["frames"])
    finally:
        scene.frame_set(original_frame)
        context.view_layer.update()


def _graph_rect(region_width, region_height):
    width = min(GRAPH_WIDTH, region_width - GRAPH_MARGIN)
    height = min(GRAPH_HEIGHT, region_height - GRAPH_MARGIN)
    if width < 240 or height < 170:
        return None
    return (GRAPH_MARGIN, GRAPH_MARGIN, width, height)


def _plot_rect(graph_rect):
    x, y, width, height = graph_rect
    return (
        x + LEFT_PADDING,
        y + BOTTOM_PADDING,
        width - LEFT_PADDING - RIGHT_PADDING,
        height - HEADER_HEIGHT - BOTTOM_PADDING,
    )


def _text_size(text, size):
    font_id = 0
    blf.size(font_id, size)
    return blf.dimensions(font_id, text)


def _draw_text(x_pos, y_pos, text, size, color):
    font_id = 0
    blf.position(font_id, x_pos, y_pos, 0)
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _norm_to_x(value, plot_rect):
    return plot_rect[0] + (plot_rect[2] * value)


def _depth_to_y(depth, depth_min, depth_max, plot_rect):
    depth_span = depth_max - depth_min
    if depth_span <= 0.0:
        return plot_rect[1] + (plot_rect[3] * 0.5)
    normalized = (depth - depth_min) / depth_span
    return plot_rect[1] + (plot_rect[3] * normalized)


def _depth_range(track_cache):
    depths = []
    for frame_result in track_cache["frames"].values():
        for row in frame_result["rows"]:
            depth = row.get("depth_mm")
            if depth == "" or depth is None:
                continue
            depths.append(float(depth))

    if not depths:
        return None

    depth_min = min(depths)
    depth_max = max(depths)
    if abs(depth_max - depth_min) < 1e-6:
        depth_min -= 0.5
        depth_max += 0.5
    else:
        padding = (depth_max - depth_min) * 0.08
        depth_min -= padding
        depth_max += padding
    return depth_min, depth_max


def _segment_layout():
    _require_addon()
    widths = [1.0 / len(tp_ops.SEGMENTS)] * len(tp_ops.SEGMENTS)
    boundaries = [0.0]
    running = 0.0
    for width in widths:
        running += width
        boundaries.append(running)
    boundaries[-1] = 1.0
    return {"widths": widths, "boundaries": boundaries}


def _build_frame_strips(frame_result, segment_layout, plot_rect, depth_min, depth_max):
    _require_addon()

    segment_rows = {segment_key: [] for _, _, segment_key in tp_ops.SEGMENTS}
    for row in frame_result["rows"]:
        segment_rows.setdefault(row["segment"], []).append(row)

    strips = []
    current_strip = []
    boundaries = segment_layout["boundaries"]
    widths = segment_layout["widths"]

    for index, (_, _, segment_key) in enumerate(tp_ops.SEGMENTS):
        rows = sorted(segment_rows.get(segment_key, []), key=lambda item: item["point_index"])
        segment_length = frame_result["seg_lengths"].get(segment_key, 0.0)
        start = boundaries[index]
        width = widths[index]

        for row_index, row in enumerate(rows):
            depth = row.get("depth_mm")
            if depth == "" or depth is None:
                if len(current_strip) >= 2:
                    strips.append(current_strip)
                current_strip = []
                continue

            local_distance = row.get("distance_along_transect_mm")
            if local_distance == "" or local_distance is None:
                t_value = row_index / (len(rows) - 1) if len(rows) > 1 else 0.0
            elif segment_length > 0.0:
                t_value = float(local_distance) / segment_length
            else:
                t_value = row_index / (len(rows) - 1) if len(rows) > 1 else 0.0

            x_value = _norm_to_x(start + (max(0.0, min(1.0, t_value)) * width), plot_rect)
            y_value = _depth_to_y(float(depth), depth_min, depth_max, plot_rect)
            current_strip.append((x_value, y_value))

    if len(current_strip) >= 2:
        strips.append(current_strip)

    return strips


def _draw_batch(batch_type, coords, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    format_obj = gpu.types.GPUVertFormat()
    format_obj.attr_add(id="pos", comp_type="F32", len=2, fetch_mode="FLOAT")
    vertex_buffer = gpu.types.GPUVertBuf(format=format_obj, len=len(coords))
    vertex_buffer.attr_fill(id="pos", data=coords)
    batch = gpu.types.GPUBatch(type=batch_type, buf=vertex_buffer)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line_strip(coords, color, line_width):
    if len(coords) < 2:
        return
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(line_width)
    _draw_batch("LINE_STRIP", coords, color)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_rect(rect, color):
    x_pos, y_pos, width, height = rect
    coords = [
        (x_pos, y_pos),
        (x_pos + width, y_pos),
        (x_pos + width, y_pos + height),
        (x_pos, y_pos + height),
    ]
    _draw_batch("TRI_FAN", coords, color)


def _draw_rect_outline(rect, color):
    x_pos, y_pos, width, height = rect
    coords = [
        (x_pos, y_pos),
        (x_pos + width, y_pos),
        (x_pos + width, y_pos + height),
        (x_pos, y_pos + height),
        (x_pos, y_pos),
    ]
    _draw_line_strip(coords, color, 1.0)


def _draw_guides(plot_rect, segment_layout):
    _require_addon()
    boundaries = segment_layout["boundaries"]
    top = plot_rect[1]
    bottom = plot_rect[1] + plot_rect[3]

    for boundary in boundaries:
        x_value = _norm_to_x(boundary, plot_rect)
        _draw_line_strip([(x_value, top), (x_value, bottom)], GUIDE, 1.0)

    for index, (_, _, segment_key) in enumerate(tp_ops.SEGMENTS):
        left = _norm_to_x(boundaries[index], plot_rect)
        right = _norm_to_x(boundaries[index + 1], plot_rect)
        text = segment_key
        text_width, _ = _text_size(text, 11)
        center_x = left + ((right - left - text_width) * 0.5)
        _draw_text(center_x, plot_rect[1] + plot_rect[3] + 8, text, 11, TEXT)


def _draw_depth_labels(plot_rect, depth_min, depth_max):
    _draw_text(plot_rect[0] - 46, plot_rect[1] + plot_rect[3] - 6, f"{depth_max:.2f}", 11, TEXT)
    _draw_text(plot_rect[0] - 46, plot_rect[1] - 4, f"{depth_min:.2f}", 11, TEXT)


def _draw_frame_series(track_cache, graph_rect):
    scene = bpy.context.scene
    plot_rect = _plot_rect(graph_rect)
    depth_limits = _depth_range(track_cache)
    if depth_limits is None:
        return

    depth_min, depth_max = depth_limits
    segment_layout = _segment_layout()
    current_frame = scene.frame_current
    frame_items = sorted(track_cache["frames"].items(), key=lambda item: item[0])
    if not frame_items:
        return

    first_frame = frame_items[0][0]
    last_frame = frame_items[-1][0]

    _draw_rect(graph_rect, BACKGROUND)
    _draw_rect_outline(graph_rect, OUTLINE)
    _draw_text(graph_rect[0] + 14, graph_rect[1] + graph_rect[3] - 22, TITLE, 15, TEXT)
    _draw_guides(plot_rect, segment_layout)
    _draw_depth_labels(plot_rect, depth_min, depth_max)

    zero_y = _depth_to_y(0.0, depth_min, depth_max, plot_rect)
    if plot_rect[1] <= zero_y <= plot_rect[1] + plot_rect[3]:
        _draw_line_strip([(plot_rect[0], zero_y), (plot_rect[0] + plot_rect[2], zero_y)], (0.75, 0.75, 0.75, 0.18), 1.0)

    for frame, frame_result in frame_items:
        color = WHITE if frame == current_frame else _gradient_color(frame, first_frame, last_frame)
        line_width = 2.6 if frame == current_frame else 1.25
        for strip in _build_frame_strips(frame_result, segment_layout, plot_rect, depth_min, depth_max):
            _draw_line_strip(strip, color, line_width)


def _active_cached_track():
    scene = bpy.context.scene
    active = bpy.context.active_object
    if active is not None and active.type == "MESH":
        cache = _mesh_cache(active.name)
        if cache is not None:
            return active.name, cache

    for mesh_name, cache in _state["cache"].items():
        obj = bpy.data.objects.get(mesh_name)
        if obj is not None and obj.type == "MESH":
            return mesh_name, cache

    return None, None


def _draw_callback():
    scene = bpy.context.scene
    area = bpy.context.area
    region = bpy.context.region

    if scene is None or area is None or region is None:
        return
    if area.type != "VIEW_3D" or not scene.tp_animated_profiles_enabled:
        return

    _, track_cache = _active_cached_track()
    if track_cache is None:
        return

    graph_rect = _graph_rect(region.width, region.height)
    if graph_rect is None:
        return

    _draw_frame_series(track_cache, graph_rect)


def tag_redraw_all_view3d():
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return

    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


@persistent
def _on_load_post(*args):
    _load_cache_from_scene()
    tag_redraw_all_view3d()


@persistent
def _on_frame_change_post(*args):
    tag_redraw_all_view3d()


class TRACKPROFILER_OT_BakeAnimatedProfiles(Operator):
    bl_idname = "trackprofiler.bake_animated_profiles"
    bl_label = "Bake Animated Profiles"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            count = bake_active_mesh_frames(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Baked {count} frame(s)")
        return {"FINISHED"}


class TRACKPROFILER_OT_ClearAnimatedProfiles(Operator):
    bl_idname = "trackprofiler.clear_animated_profiles"
    bl_label = "Clear Animated Cache"

    def execute(self, context):
        for mesh_name in list(_state["cache"].keys()):
            _clear_cache_property(mesh_name)
        _state["cache"].clear()
        tag_redraw_all_view3d()
        self.report({"INFO"}, "Animated cache cleared")
        return {"FINISHED"}


class TRACKPROFILER_OT_ToggleAnimatedProfiles(Operator):
    bl_idname = "trackprofiler.toggle_animated_profiles"
    bl_label = "Toggle Animated Graph"

    def execute(self, context):
        context.scene.tp_animated_profiles_enabled = not context.scene.tp_animated_profiles_enabled
        tag_redraw_all_view3d()
        return {"FINISHED"}


class TRACKPROFILER_PT_AnimatedProfiles(Panel):
    bl_label = "TrackProfiler Animated"
    bl_idname = "TRACKPROFILER_PT_animated_profiles"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TrackProfiler"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active = context.active_object

        layout.prop(scene, "tp_animated_profiles_enabled", text="Show Animated Graph")

        if active is not None and active.type == "MESH":
            layout.label(text=f"Active mesh: {active.name}", icon="MESH_DATA")
            layout.operator("trackprofiler.bake_animated_profiles", icon="PLAY")
        else:
            layout.label(text="Select a mesh object", icon="INFO")

        layout.operator("trackprofiler.clear_animated_profiles", icon="TRASH")
        layout.separator()
        layout.label(text=f"Cached meshes: {len(_state['cache'])}", icon="PRESET")


_classes = [
    TRACKPROFILER_OT_BakeAnimatedProfiles,
    TRACKPROFILER_OT_ClearAnimatedProfiles,
    TRACKPROFILER_OT_ToggleAnimatedProfiles,
    TRACKPROFILER_PT_AnimatedProfiles,
]


def register():
    global _draw_handle

    if tp_ops is None:
        raise RuntimeError("TrackProfiler addon is not available. Enable it before running this script.")

    _load_cache_from_scene()

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.tp_animated_profiles_enabled = BoolProperty(
        name="Show Animated Graph",
        default=True,
    )

    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), "WINDOW", "POST_PIXEL")

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if _on_frame_change_post not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change_post)

    tag_redraw_all_view3d()


def unregister():
    global _draw_handle

    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change_post)

    if hasattr(bpy.types.Scene, "tp_animated_profiles_enabled"):
        del bpy.types.Scene.tp_animated_profiles_enabled

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()