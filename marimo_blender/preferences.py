import bpy

from . import addon_setup

_LINES: list[str] = []


def _lines_append(line: str):
    if line.startswith('\r') and len(_LINES) > 0:
        del _LINES[-1]
        line = line[1:]
    _LINES.append(line)


class InstallPythonModules(bpy.types.Operator):
    """Install Python Module marimo dependencies"""
    bl_idname = 'marimo.install_python_modules'
    bl_label = 'Install Python Modules'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not addon_setup.installer.is_running

    def execute(self, context):
        _LINES.clear()
        region = context.region
        addon_setup.installer.install_python_modules(
            line_callback=lambda line: _lines_append(line) or region.tag_redraw(),
            finally_callback=lambda e: region.tag_redraw(),
        )
        return {'FINISHED'}


class InstallPythonModule(bpy.types.Operator):
    """Install Python Module """
    bl_idname = 'marimo.install_python_module'
    bl_label = 'Install Python Module'
    bl_options = {'REGISTER', 'INTERNAL'}

    module_name: bpy.props.StringProperty(name="Module Name", default="")

    @classmethod
    def poll(cls, context):
        return not addon_setup.installer.is_running

    def execute(self, context):
        _LINES.clear()
        region = context.region
        addon_setup.installer.install_python_module(
            self.module_name,
            line_callback=lambda line: _lines_append(line) or region.tag_redraw(),
            finally_callback=lambda e: region.tag_redraw(),
        )
        return {'FINISHED'}


class UninstallPythonModules(bpy.types.Operator):
    """Uninstall Python Module marimo dependencies"""
    bl_idname = 'marimo.uninstall_python_modules'
    bl_label = 'Uninstall Python Modules'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not addon_setup.installer.is_running

    def execute(self, context):
        _LINES.clear()
        region = context.region
        addon_setup.installer.uninstall_python_modules(
            line_callback=lambda line: _lines_append(line) or region.tag_redraw(),
            finally_callback=lambda e: region.tag_redraw(),
        )
        return {'FINISHED'}


class ListPythonModules(bpy.types.Operator):
    """List Python Modules"""
    bl_idname = 'marimo.list_python_modules'
    bl_label = 'List Python Modules'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not addon_setup.installer.is_running

    def execute(self, context):
        _LINES.clear()
        region = context.region
        addon_setup.installer.list_python_modules(
            line_callback=lambda line: _lines_append(line) or region.tag_redraw(),
            finally_callback=lambda e: region.tag_redraw(),
        )
        return {'FINISHED'}


class StartMarimoServer(bpy.types.Operator):
    """Start Marimo Server if not exist then open Browser"""
    bl_idname = 'marimo.start_server_or_open_browser'
    bl_label = 'Start Notebook Server'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return not addon_setup.installer.is_running

    def execute(self, context):
        if not addon_setup.server.is_running:
            _LINES.clear()
            region = context.region
            prefs = context.preferences.addons[__package__].preferences
            addon_setup.server.start(
                prefs.port,
                prefs.filename,
                line_callback=lambda line: _lines_append(line) or region.tag_redraw(),
                finally_callback=lambda e: region.tag_redraw(),
            )
        else:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{addon_setup.server.port}")
        return {'FINISHED'}


class StopMarimoServer(bpy.types.Operator):
    """Stop Marimo Server if exist"""
    bl_idname = 'marimo.stop_server'
    bl_label = 'Stop Notebook Server'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return addon_setup.server.is_running

    def execute(self, context):
        addon_setup.server.stop()
        return {'FINISHED'}


def draw_preferences(layout: bpy.types.UILayout, prefs: "MarimoAddonPreferences"):
    modules = addon_setup.installer.get_required_modules()
    all_installed = all(modules.values())

    # ── Launch ─────────────────────────────────────────────────
    is_running = addon_setup.server.is_running
    launch_header, launch_body = layout.panel("marimo_launch", default_closed=False)
    launch_header.label(text="Launch", icon='PLAY')
    if launch_body is not None:
        row = launch_body.row(align=True)
        split = row.split(factor=0.33)
        split.prop(prefs, 'port')
        split.prop(prefs, 'filename', text="", icon='FILE_SCRIPT')

        if is_running:
            launch_body.label(
                text=f"Server running on http://127.0.0.1:{addon_setup.server.port}",
                icon='RADIOBUT_ON',
            )
            actions = launch_body.row(align=True)
            actions.scale_y = 1.4
            actions.operator(StartMarimoServer.bl_idname, icon='URL', text="Open in Browser")
            actions.operator(StopMarimoServer.bl_idname, icon='X', text="Stop")
        else:
            big = launch_body.row()
            big.scale_y = 1.4
            big.enabled = all_installed
            big.operator(StartMarimoServer.bl_idname, icon='URL', text="Start Notebook Server")
            if not all_installed:
                launch_body.label(text="Install dependencies first ↓", icon='INFO')

    # ── Dependencies ───────────────────────────────────────────
    deps_header, deps_body = layout.panel("marimo_dependencies", default_closed=all_installed)
    deps_header.label(
        text="Dependencies",
        icon='CHECKMARK' if all_installed else 'ERROR',
    )
    if deps_body is not None:
        deps_body.operator(InstallPythonModules.bl_idname, icon="PREFERENCES")

        deps_body.label(text="Required Python Modules:")
        flow = deps_body.row(align=True).grid_flow(align=True)
        for name, is_installed in modules.items():
            flow.row().label(text=name, icon='CHECKMARK' if is_installed else 'ERROR')

        row = deps_body.row()
        row.operator(UninstallPythonModules.bl_idname)
        row.operator(ListPythonModules.bl_idname)

        row = deps_body.row(align=True)
        row.operator(InstallPythonModule.bl_idname, icon='PLUS', text='pip install').module_name = prefs.module_name
        row.prop(prefs, 'module_name', text='')

        # Logs (collapsible within Dependencies)
        col = deps_body.column(align=False)
        log_row = col.row(align=True)
        log_row.prop(
            prefs, 'show_logs',
            icon='TRIA_DOWN' if prefs.show_logs else 'TRIA_RIGHT',
            icon_only=True,
            emboss=False,
        )
        log_row.label(text='Logs')
        exit_code = addon_setup.installer.exit_code
        if addon_setup.installer.is_running:
            log_row.label(text="Processing ...", icon='SORTTIME')
        elif exit_code >= 0:
            log_row.label(
                text=f"Done with code: {exit_code}",
                icon='CHECKMARK' if exit_code == 0 else 'ERROR',
            )

        if prefs.show_logs:
            box = col.box().column(align=True)
            for line in _LINES:
                box.label(text=line)


class MarimoAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: bpy.props.IntProperty(
        name="Port",
        default=2718,
    )
    filename: bpy.props.StringProperty(name="Notebook File Path", description="Leave empty to edit a new file", default="", subtype='FILE_PATH')
    show_logs: bpy.props.BoolProperty(default=False)
    module_name: bpy.props.StringProperty(name="Module Name", default="")

    def draw(self, context: bpy.types.Context):
        self.layout.label(text="Settings available in 3D View > Sidebar (N) > Marimo", icon='INFO')
