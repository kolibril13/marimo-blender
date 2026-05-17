import bpy

from .preferences import (
    MarimoAddonPreferences,
    InstallPythonModules,
    InstallPythonModule,
    UninstallPythonModules,
    ListPythonModules,
    StartMarimoServer,
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
    StopMarimoServer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(marimo_header_btn)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(marimo_header_btn)
    for cls in classes:
        bpy.utils.unregister_class(cls)
    from . import main_thread
    main_thread.unregister()
