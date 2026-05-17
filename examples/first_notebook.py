import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import bpy

    return (bpy,)


@app.cell
def _(bpy):
    bpy.ops.mesh.primitive_monkey_add(size=2.5, location=(0, 0, 0))
    return


@app.cell
def _():
    import marimo as mo
    mo.__version__
    return (mo,)


@app.cell
def _(mo):
    slider = mo.ui.slider(
        start=0,
        stop=100,
        step=1,
        value=10,
        label="generate random data",
    )

    slider
    return (slider,)


@app.cell
def _(bpy, slider):
    import math
    import random

    slider.value

    nx = 80
    ny = 80
    spacing = 0.15
    height_scale = slider.value / 20

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    vertices = []
    faces = []

    for y in range(ny):
        for x in range(nx):
            z = (
                math.sin(x * 0.25)
                * math.cos(y * 0.25)
                * height_scale
            )

            vertices.append((
                (x - nx / 2) * spacing,
                (y - ny / 2) * spacing,
                z,
            ))

    for y in range(ny - 1):
        for x in range(nx - 1):
            i = y * nx + x
            faces.append((i, i + 1, i + nx + 1, i + nx))

    mesh = bpy.data.meshes.new("Wave_Grid_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    obj = bpy.data.objects.new("Wave_Grid", mesh)
    bpy.context.collection.objects.link(obj)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
