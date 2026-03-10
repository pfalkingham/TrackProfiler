import blf
import bpy
import gpu

from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from .operators import SEGMENTS, SEGMENT_COLORS, _results


GRAPH_MARGIN = 20
GRAPH_WIDTH = 600
GRAPH_HEIGHT = 300
RESIZE_GRIP = 16  # px square in bottom-right corner used as resize handle

DEFAULT_GRAPH_POS = (GRAPH_MARGIN, GRAPH_MARGIN)
DEFAULT_GRAPH_SIZE = (GRAPH_WIDTH, GRAPH_HEIGHT)
HEADER_HEIGHT = 42
BOTTOM_PADDING = 28
LEFT_PADDING = 50
RIGHT_PADDING = 18
TITLE = "Depth Profile"
PALETTE = [
    (0.90, 0.29, 0.25, 1.0),
    (0.18, 0.55, 0.86, 1.0),
    (0.22, 0.68, 0.47, 1.0),
    (0.96, 0.69, 0.21, 1.0),
    (0.63, 0.40, 0.87, 1.0),
    (0.93, 0.46, 0.60, 1.0),
]
SEGMENT_TITLES = {
    "Hallux_MT1": "Hallux->MT1",
    "MT1_MT5": "MT1->MT5",
    "MT5_Heel": "MT5->Heel",
    "Heel_MT1": "Heel->MT1",
}

_draw_handler = None
_graph_keymaps = []  # (keymap, keymap_item) pairs registered by this addon


def _on_graph_setting_changed(self, context):
    tag_redraw_all_view3d()


class FOOTPRINT_PG_TrackDisplay(PropertyGroup):
    mesh_name: StringProperty(name="Mesh Name")
    visible: BoolProperty(name="Visible", default=True, update=_on_graph_setting_changed)
    color: FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=PALETTE[0],
        update=_on_graph_setting_changed,
    )
    expand: BoolProperty(name="Show Segment Lengths", default=False)


class FOOTPRINT_OT_ToggleGraph(Operator):
    bl_idname = "footprint.toggle_graph"
    bl_label = "Toggle Graph"

    def execute(self, context):
        context.scene.footprint_graph_enabled = not context.scene.footprint_graph_enabled
        tag_redraw_all_view3d()
        return {'FINISHED'}


class FOOTPRINT_OT_GraphTransform(Operator):
    """Click-drag graph to move; drag bottom-right corner to resize. ESC/RMB cancels."""
    bl_idname = "footprint.graph_transform"
    bl_label = "Move / Resize Graph"

    _mode = None
    _start_mouse = None
    _start_pos = None
    _start_size = None

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (
            context.area is not None
            and context.area.type == 'VIEW_3D'
            and getattr(scene, 'footprint_graph_enabled', False)
            and bool(_results)
        )

    def invoke(self, context, event):
        region = context.region
        if region is None or region.type != 'WINDOW':
            return {'PASS_THROUGH'}

        scene = context.scene
        mx, my = event.mouse_region_x, event.mouse_region_y
        rect = _graph_rect(region.width, region.height)
        if rect is None:
            return {'PASS_THROUGH'}

        rx, ry, rw, rh = rect
        in_resize = (rx + rw - RESIZE_GRIP <= mx <= rx + rw
                     and ry <= my <= ry + RESIZE_GRIP)
        in_rect = (rx <= mx <= rx + rw and ry <= my <= ry + rh)

        if not in_rect:
            return {'PASS_THROUGH'}

        self._mode = 'RESIZE' if in_resize else 'MOVE'
        self._start_mouse = (mx, my)
        self._start_pos = tuple(scene.footprint_graph_pos)
        self._start_size = tuple(scene.footprint_graph_size)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        scene = context.scene
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            scene.footprint_graph_pos = self._start_pos
            scene.footprint_graph_size = self._start_size
            tag_redraw_all_view3d()
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return {'FINISHED'}

        if event.type == 'MOUSEMOVE':
            dx = event.mouse_region_x - self._start_mouse[0]
            dy = event.mouse_region_y - self._start_mouse[1]
            if self._mode == 'MOVE':
                scene.footprint_graph_pos = (
                    self._start_pos[0] + dx,
                    self._start_pos[1] + dy,
                )
            else:
                scene.footprint_graph_size = (
                    max(220, self._start_size[0] + dx),
                    max(160, self._start_size[1] - dy),
                )
            tag_redraw_all_view3d()

        return {'RUNNING_MODAL'}


_classes = [
    FOOTPRINT_PG_TrackDisplay,
    FOOTPRINT_OT_ToggleGraph,
    FOOTPRINT_OT_GraphTransform,
]


def tag_redraw_all_view3d():
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return

    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def get_track_display(scene, mesh_name):
    for track in scene.footprint_graph_tracks:
        if track.mesh_name == mesh_name:
            return track
    return None


def sync_track_settings(scene):
    if scene is None:
        return

    wanted_names = list(_results.keys())
    for index in range(len(scene.footprint_graph_tracks) - 1, -1, -1):
        if scene.footprint_graph_tracks[index].mesh_name not in wanted_names:
            scene.footprint_graph_tracks.remove(index)

    for mesh_name in wanted_names:
        if get_track_display(scene, mesh_name) is not None:
            continue

        track = scene.footprint_graph_tracks.add()
        track.mesh_name = mesh_name
        track.visible = True
        track.color = PALETTE[(len(scene.footprint_graph_tracks) - 1) % len(PALETTE)]


def notify_results_changed(scene):
    sync_track_settings(scene)
    tag_redraw_all_view3d()


@bpy.app.handlers.persistent
def _on_load_post(*args):
    """Called after a .blend file loads. Sync track display state."""
    from . import operators
    operators.load_results_from_scene()
    for scene in bpy.data.scenes:
        sync_track_settings(scene)
    tag_redraw_all_view3d()


def _draw_callback():
    context = bpy.context
    scene = context.scene
    area = context.area
    region = context.region

    if scene is None or area is None or region is None:
        return
    if area.type != 'VIEW_3D' or not scene.footprint_graph_enabled or not _results:
        return

    visible_tracks = []
    for mesh_name in _results:
        track = get_track_display(scene, mesh_name)
        if track is not None and track.visible:
            visible_tracks.append((mesh_name, track))

    if not visible_tracks:
        return

    graph_rect = _graph_rect(region.width, region.height)
    if graph_rect is None:
        return

    segment_layout = _segment_layout(scene.footprint_graph_x_mode, [name for name, _ in visible_tracks])
    plot_rect = _plot_rect(graph_rect)
    depth_range = _depth_range([name for name, _ in visible_tracks])
    if depth_range is None:
        return

    depth_min, depth_max = depth_range
    _draw_rect(graph_rect, (0.08, 0.08, 0.08, 0.84))
    _draw_rect_outline(graph_rect, (0.78, 0.78, 0.78, 0.35))
    _draw_text(graph_rect[0] + 14, graph_rect[1] + graph_rect[3] - 22, TITLE, 15, (0.96, 0.96, 0.96, 1.0))
    _draw_segment_guides(plot_rect, graph_rect, segment_layout)
    _draw_depth_labels(plot_rect, depth_min, depth_max)

    zero_y = _depth_to_y(0.0, depth_min, depth_max, plot_rect)
    if plot_rect[1] <= zero_y <= plot_rect[1] + plot_rect[3]:
        _draw_line((plot_rect[0], zero_y), (plot_rect[0] + plot_rect[2], zero_y), (0.75, 0.75, 0.75, 0.20), 1.0)

    for mesh_name, track in visible_tracks:
        for strip in _build_track_strips(mesh_name, segment_layout, plot_rect, depth_min, depth_max):
            if len(strip) >= 2:
                _draw_line_strip(strip, tuple(track.color), 2.0)

    # Resize grip — small triangle in bottom-right corner
    gx = graph_rect[0] + graph_rect[2]
    gy = graph_rect[1]
    g = RESIZE_GRIP
    grip_coords = [(gx - g, gy), (gx, gy), (gx, gy + g)]
    _draw_batch('TRIS', grip_coords, (0.65, 0.65, 0.65, 0.45))


def _graph_rect(region_width, region_height):
    scene = bpy.context.scene
    pos = tuple(getattr(scene, 'footprint_graph_pos', DEFAULT_GRAPH_POS))
    size = tuple(getattr(scene, 'footprint_graph_size', DEFAULT_GRAPH_SIZE))
    width = min(size[0], region_width - GRAPH_MARGIN)
    height = min(size[1], region_height - GRAPH_MARGIN)
    if width < 220 or height < 160:
        return None
    pos_x = max(GRAPH_MARGIN, min(pos[0], region_width - width - GRAPH_MARGIN))
    pos_y = max(GRAPH_MARGIN, min(pos[1], region_height - height - GRAPH_MARGIN))
    return (pos_x, pos_y, width, height)


def _plot_rect(graph_rect):
    x, y, width, height = graph_rect
    return (
        x + LEFT_PADDING,
        y + BOTTOM_PADDING,
        width - LEFT_PADDING - RIGHT_PADDING,
        height - HEADER_HEIGHT - BOTTOM_PADDING,
    )


def _segment_layout(mode, mesh_names):
    if mode == 'UNIFORM':
        widths = [1.0 / len(SEGMENTS)] * len(SEGMENTS)
    else:
        raw_widths = []
        for _, _, segment_key in SEGMENTS:
            lengths = [max(_results[name]["seg_lengths"].get(segment_key, 0.0), 0.0) for name in mesh_names]
            raw_widths.append(sum(lengths) / len(lengths) if lengths else 0.0)

        total = sum(raw_widths)
        if total <= 0.0:
            widths = [1.0 / len(SEGMENTS)] * len(SEGMENTS)
        else:
            widths = [value / total for value in raw_widths]

    boundaries = [0.0]
    total_width = 0.0
    for width in widths:
        total_width += width
        boundaries.append(total_width)
    boundaries[-1] = 1.0

    return {
        "widths": widths,
        "boundaries": boundaries,
    }


def _depth_range(mesh_names):
    depths = []
    for mesh_name in mesh_names:
        for row in _results[mesh_name]["rows"]:
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


def _build_track_strips(mesh_name, segment_layout, plot_rect, depth_min, depth_max):
    track_rows = _results[mesh_name]["rows"]
    segment_rows = {segment_key: [] for _, _, segment_key in SEGMENTS}
    for row in track_rows:
        segment_rows.setdefault(row["segment"], []).append(row)

    strips = []
    current_strip = []
    boundaries = segment_layout["boundaries"]
    widths = segment_layout["widths"]

    for index, (_, _, segment_key) in enumerate(SEGMENTS):
        rows = sorted(segment_rows.get(segment_key, []), key=lambda row: row["point_index"])
        segment_length = _results[mesh_name]["seg_lengths"].get(segment_key, 0.0)
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


def _draw_segment_guides(plot_rect, graph_rect, segment_layout):
    boundaries = segment_layout["boundaries"]
    top = plot_rect[1]
    bottom = plot_rect[1] + plot_rect[3]

    for boundary in boundaries:
        x_value = _norm_to_x(boundary, plot_rect)
        _draw_line((x_value, top), (x_value, bottom), (0.85, 0.85, 0.85, 0.28), 1.0)

    for index, (_, _, segment_key) in enumerate(SEGMENTS):
        left = _norm_to_x(boundaries[index], plot_rect)
        right = _norm_to_x(boundaries[index + 1], plot_rect)
        text = SEGMENT_TITLES.get(segment_key, segment_key)
        text_width, _ = _text_size(text, 11)
        center_x = left + ((right - left - text_width) * 0.5)
        label_y = graph_rect[1] + graph_rect[3] - 38
        title_color = SEGMENT_COLORS.get(segment_key, (0.93, 0.93, 0.93, 1.0))
        _draw_text(center_x, label_y, text, 11, title_color)


def _draw_depth_labels(plot_rect, depth_min, depth_max):
    # Graph Y-axis increases upward: top shows the shallowest (least
    # negative) depth value, bottom shows the deepest (most negative).
    _draw_text(plot_rect[0] - 42, plot_rect[1] + plot_rect[3] - 6, f"{depth_max:.2f}", 11, (0.92, 0.92, 0.92, 1.0))
    _draw_text(plot_rect[0] - 42, plot_rect[1] - 4, f"{depth_min:.2f}", 11, (0.92, 0.92, 0.92, 1.0))


def _norm_to_x(value, plot_rect):
    return plot_rect[0] + (plot_rect[2] * value)


def _depth_to_y(depth, depth_min, depth_max, plot_rect):
    """Map a depth value to the vertical coordinate of the plot.

    Depths are negative Z values (deeper = more negative). The graph should
    display deeper points lower on the screen, so normalise with respect to the
    minimum depth and project downward.
    """
    depth_span = depth_max - depth_min
    if depth_span <= 0.0:
        return plot_rect[1] + (plot_rect[3] * 0.5)
    # normalised range 0..1 where 0 corresponds to depth_min (most negative)
    # and 1 corresponds to depth_max (least negative)
    normalized = (depth - depth_min) / depth_span
    return plot_rect[1] + (plot_rect[3] * normalized)


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


def _draw_rect(rect, color):
    x_pos, y_pos, width, height = rect
    coords = [
        (x_pos, y_pos),
        (x_pos + width, y_pos),
        (x_pos + width, y_pos + height),
        (x_pos, y_pos + height),
    ]
    _draw_batch('TRI_FAN', coords, color)


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


def _draw_line(start, end, color, line_width):
    _draw_line_strip([start, end], color, line_width)


def _draw_line_strip(coords, color, line_width):
    if len(coords) < 2:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(line_width)
    _draw_batch('LINE_STRIP', coords, color)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_batch(batch_type, coords, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    format_obj = gpu.types.GPUVertFormat()
    format_obj.attr_add(id="pos", comp_type='F32', len=2, fetch_mode='FLOAT')
    vertex_buffer = gpu.types.GPUVertBuf(format=format_obj, len=len(coords))
    vertex_buffer.attr_fill(id="pos", data=coords)
    batch = gpu.types.GPUBatch(type=batch_type, buf=vertex_buffer)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def register():
    global _draw_handler

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.footprint_graph_enabled = BoolProperty(
        name="Show Graph",
        default=False,
        update=_on_graph_setting_changed,
    )
    bpy.types.Scene.footprint_graph_x_mode = EnumProperty(
        name="Graph X Mode",
        items=[
            ('UNIFORM', "Uniform", "Each segment uses equal width"),
            ('RELATIVE', "Relative", "Segment widths follow average scene length"),
        ],
        default='UNIFORM',
        update=_on_graph_setting_changed,
    )
    bpy.types.Scene.footprint_graph_tracks = CollectionProperty(type=FOOTPRINT_PG_TrackDisplay)
    bpy.types.Scene.footprint_graph_pos = FloatVectorProperty(
        name="Graph Position",
        size=2,
        default=DEFAULT_GRAPH_POS,
    )
    bpy.types.Scene.footprint_graph_size = FloatVectorProperty(
        name="Graph Size",
        size=2,
        default=DEFAULT_GRAPH_SIZE,
    )

    if _draw_handler is None:
        _draw_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    # Keymap: LEFT_MOUSE in VIEW_3D window invokes graph_transform;
    # the operator itself passes through if cursor is not on the graph.
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new(
            'footprint.graph_transform', 'LEFTMOUSE', 'PRESS'
        )
        _graph_keymaps.append((km, kmi))


def unregister():
    global _draw_handler

    for km, kmi in _graph_keymaps:
        km.keymap_items.remove(kmi)
    _graph_keymaps.clear()

    if _draw_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handler, 'WINDOW')
        _draw_handler = None

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

    for prop in (
        'footprint_graph_tracks', 'footprint_graph_x_mode',
        'footprint_graph_enabled', 'footprint_graph_pos',
        'footprint_graph_size',
    ):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)