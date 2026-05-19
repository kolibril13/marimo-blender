import bpy

from .preferences import (
    MarimoAddonPreferences,
    InstallPythonModules,
    InstallPythonModule,
    UninstallPythonModules,
    ListPythonModules,
    StartMarimoServer,
    StartWithExample,
    StopMarimoServer,
    draw_preferences,
)


def marimo_header_btn(self: bpy.types.Menu, context):
    self.layout.operator(StartMarimoServer.bl_idname, icon='CONSOLE', text="")


class MARIMO_PT_main_panel(bpy.types.Panel):
    bl_label = "Notebook"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Marimo'

    def draw(self, context):
        prefs = context.preferences.addons[__package__].preferences
        draw_preferences(self.layout, prefs)


classes = (
    MARIMO_PT_main_panel,
    MarimoAddonPreferences,
    InstallPythonModules,
    InstallPythonModule,
    UninstallPythonModules,
    ListPythonModules,
    StartMarimoServer,
    StartWithExample,
    StopMarimoServer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(marimo_header_btn)


def unregister():
    import time
    from . import addon_setup, main_thread

    bpy.types.VIEW3D_HT_header.remove(marimo_header_btn)
    for cls in reversed(classes):
        if hasattr(cls, 'bl_rna'):
            bpy.utils.unregister_class(cls)

    # Signal the server to stop and wait up to 3 s for it to finish.
    # We must drain the queue manually here because the bpy timer cannot fire
    # while this function is blocking the main thread.
    addon_setup.server.stop()
    deadline = time.monotonic() + 3.0
    while addon_setup.server.is_running and time.monotonic() < deadline:
        main_thread.drain_sync()
        time.sleep(0.01)

    main_thread.unregister()
