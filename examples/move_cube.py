import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import bpy
    import marimo as mo

    return bpy, mo


@app.cell
def _(mo):
    x = mo.ui.slider(start=0, stop=3, step=0.1, label="x")
    y = mo.ui.slider(start=0, stop=3, step=0.1, label="y")
    z = mo.ui.slider(start=0, stop=3, step=0.1, label="z")
    mo.vstack([x, y, z])
    return x, y, z


@app.cell
def _(bpy, x, y, z):
    bpy.data.objects["Cube"].location = (x.value, y.value, z.value)
    return


if __name__ == "__main__":
    app.run()
