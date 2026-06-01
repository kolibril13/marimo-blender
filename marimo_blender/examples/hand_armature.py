import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    # Make deps + the gesture_widget package importable in Blender's Python.
    import subprocess
    import sys

    import bpy
    import marimo as mo

    REPO = "/Users/jan-hendrik/projects/caputre_motion"  # <-- adjust if moved

    # anywidget etc. must live in BLENDER's bundled Python (this kernel).
    for pkg in ("anywidget", "traitlets", "ipywidgets"):
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

    src = f"{REPO}/src_widget"
    if src not in sys.path:
        sys.path.insert(0, src)

    from gesture_widget import GestureRecognizerWidget

    return GestureRecognizerWidget, bpy, mo


@app.cell
def _():
    SCALE = 5.0       # world units spanned by the camera frame
    BONE_REST = 0.4   # placeholder length for the bones at creation time;
    #                   the timer overwrites head/tail with real positions.
    SMOOTHING = 0.4   # EMA factor for de-jitter: 1.0 = raw (no smoothing),
    #                   lower = smoother but laggier. ~0.3-0.5 is a good range.

    # Traitlet names on the widget for all 21 MediaPipe hand landmarks.
    LANDMARKS = (
        "wrist",
        "thumb_cmc",
        "thumb_mcp",
        "thumb_ip",
        "thumb_tip",
        "index_finger_mcp",
        "index_finger_pip",
        "index_finger_dip",
        "index_finger_tip",
        "middle_finger_mcp",
        "middle_finger_pip",
        "middle_finger_dip",
        "middle_finger_tip",
        "ring_finger_mcp",
        "ring_finger_pip",
        "ring_finger_dip",
        "ring_finger_tip",
        "pinky_mcp",
        "pinky_pip",
        "pinky_dip",
        "pinky_tip",
    )

    # (parent, child) edges of the MediaPipe hand skeleton. Each edge is one
    # bone: head at the parent landmark, tail at the child landmark.
    CONNECTIONS = [
        ("wrist", "thumb_cmc"),
        ("thumb_cmc", "thumb_mcp"),
        ("thumb_mcp", "thumb_ip"),
        ("thumb_ip", "thumb_tip"),
        ("wrist", "index_finger_mcp"),
        ("index_finger_mcp", "index_finger_pip"),
        ("index_finger_pip", "index_finger_dip"),
        ("index_finger_dip", "index_finger_tip"),
        ("wrist", "middle_finger_mcp"),
        ("middle_finger_mcp", "middle_finger_pip"),
        ("middle_finger_pip", "middle_finger_dip"),
        ("middle_finger_dip", "middle_finger_tip"),
        ("wrist", "ring_finger_mcp"),
        ("ring_finger_mcp", "ring_finger_pip"),
        ("ring_finger_pip", "ring_finger_dip"),
        ("ring_finger_dip", "ring_finger_tip"),
        ("wrist", "pinky_mcp"),
        ("pinky_mcp", "pinky_pip"),
        ("pinky_pip", "pinky_dip"),
        ("pinky_dip", "pinky_tip"),
    ]
    return BONE_REST, CONNECTIONS, LANDMARKS, SCALE, SMOOTHING


@app.cell
def _(GestureRecognizerWidget, mo):
    # Show the widget, then click "Start Camera" in the output and allow webcam.
    # `gw` is the live widget whose traits sync from the browser; the timer in
    # the next cell polls it. (Kept separate from the UI wrapper so referencing
    # it does not make the next cell re-run on every frame.)
    gw = GestureRecognizerWidget()
    gw.sync_interval_ms = 33  # push landmarks to Python at ~30 Hz
    w_ui = mo.ui.anywidget(gw)
    w_ui
    return (gw,)


@app.cell
def _(BONE_REST, CONNECTIONS, LANDMARKS, SCALE, SMOOTHING, bpy, gw):
    from mathutils import Vector

    # Armature object holding one bone per connection.
    ARM_NAME = "HandArmature"
    arm_obj = bpy.data.objects.get(ARM_NAME)
    if arm_obj is None:
        arm_obj = bpy.data.objects.new(ARM_NAME, bpy.data.armatures.new(ARM_NAME))
        bpy.context.scene.collection.objects.link(arm_obj)
    arm_obj.show_in_front = False          # shaded octahedral look, like a rig
    arm_obj.data.display_type = "OCTAHEDRAL"

    def _bone_name(child_trait):
        return f"Bone_{child_trait}"

    # The bone whose TAIL sits on landmark X is the parent of the bone whose
    # HEAD sits on X -> turns the flat edge list into proper finger chains.
    _bone_ending_at = {child: _bone_name(child) for _p, child in CONNECTIONS}

    # Create the bones once + parent them into finger chains. The timer drives
    # the real head/tail each frame; Blender draws each as a proper octahedral
    # bone whose width is auto-proportional to its length (no scaling involved).
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    _eb = arm_obj.data.edit_bones
    for _i, (_parent, _child) in enumerate(CONNECTIONS):
        _bone = _eb.get(_bone_name(_child)) or _eb.new(_bone_name(_child))
        _bone.head = (_i * BONE_REST, 0.0, 0.0)
        _bone.tail = (_i * BONE_REST, 0.0, BONE_REST)
    for _parent_lm, _child in CONNECTIONS:
        _bone = _eb[_bone_name(_child)]
        _parent_name = _bone_ending_at.get(_parent_lm)  # None for wrist roots
        _bone.parent = _eb[_parent_name] if _parent_name else None
        _bone.use_connect = False  # heads are driven explicitly each frame
    bpy.ops.object.mode_set(mode="OBJECT")

    def _img_to_world(p):
        # image space (x right, y DOWN) -> Blender (x right, z UP, y depth)
        x, y, z = p
        return Vector(((x - 0.5) * SCALE, z * SCALE, (0.5 - y) * SCALE))

    # Per-landmark smoothing state (exponential moving average) to kill jitter.
    _smoothed = {}

    def _update_landmarks():
        # Read every landmark; bail until the whole hand is visible.
        pos = {}
        for _trait in LANDMARKS:
            p = getattr(gw, _trait)  # [x, y, z] normalized, or [] when no hand
            if not p:
                return 0.033
            _raw = _img_to_world(p)
            _prev = _smoothed.get(_trait)
            pos[_trait] = _raw if _prev is None else _prev.lerp(_raw, SMOOTHING)
            _smoothed[_trait] = pos[_trait]

        # bpy.app.timers always fire on Blender's main thread, so entering Edit
        # mode and writing edit bones here is safe. head/tail direct = no scale.
        try:
            bpy.context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode="EDIT")
            eb = arm_obj.data.edit_bones
            for parent_lm, child in CONNECTIONS:
                head, tail = pos[parent_lm], pos[child]
                if (tail - head).length < 1e-4:  # avoid zero-length bones
                    tail = head + Vector((0.0, 0.0, 1e-3))
                bone = eb[_bone_name(child)]
                bone.head = head
                bone.tail = tail
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass  # context not ready this tick; retry next frame
        return 0.033  # seconds until next call (~30 Hz). Return None to stop.

    # Dedupe across cell re-runs: stash the active timer in driver_namespace.
    _ns = bpy.app.driver_namespace
    _old = _ns.get("_hand_timer")
    if _old is not None and bpy.app.timers.is_registered(_old):
        bpy.app.timers.unregister(_old)
    _ns["_hand_timer"] = _update_landmarks
    bpy.app.timers.register(_update_landmarks)
    print(f"Timer running — {len(CONNECTIONS)} bones now track your hand.")
    return


@app.cell
def _(bpy, gw, mo):
    # Run this cell to stop driving Blender and turn the camera off.
    def _stop():
        _t = bpy.app.driver_namespace.get("_hand_timer")
        if _t is not None and bpy.app.timers.is_registered(_t):
            bpy.app.timers.unregister(_t)
        gw.stop()

    _stop_btn = mo.ui.button(label="Stop tracking", on_click=lambda _: _stop())
    _stop_btn
    return


if __name__ == "__main__":
    app.run()
