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
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "drawdata"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "polars"])
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
def _(slider):
    slider.value
    return


@app.cell
def _(bpy, slider):
    import random

    # Make this cell depend on the button
    slider.value

    # Delete everything
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    for i in range(100):
        x = random.uniform(-5, 5)
        y = random.uniform(-5, 5)
        z = random.uniform(-1, 1)

        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=0.08,
            location=(x, y, z),
        )

        obj = bpy.data.objects[-1]
        obj.name = f"Random_Sphere_{i}"

        hex_color = random.choice(colors).lstrip("#")
        rgb = tuple(int(hex_color[j:j+2], 16) / 255 for j in (0, 2, 4))

        mat = bpy.data.materials.new(name=f"Mat_{i}")
        mat.diffuse_color = (*rgb, 1.0)
        obj.data.materials.append(mat)
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
