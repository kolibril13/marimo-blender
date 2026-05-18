import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")

with app.setup:
    import marimo as mo
    import altair as alt
    from altair.datasets import data
    import numpy as np
    import bpy



@app.cell
def _():
    cars = data.cars()

    brush = alt.selection_interval()

    scatter = (
        alt.Chart(cars)
        .mark_point()
        .encode(
            x='Horsepower:Q',
            y='Miles_per_Gallon:Q',
            color=alt.condition(brush, 'Origin:N', alt.value('lightgray'))
        )
        .add_params(brush)
        .properties(width=400, height=300, title="Brush to filter by Origin")
    )

    bars = (
        alt.Chart(cars)
        .mark_bar()
        .encode(
            y='Origin:N',
            x='count():Q',
            color='Origin:N'
        )
        .transform_filter(brush)
        .properties(width=400, height=200)
    )

    chart = mo.ui.altair_chart(scatter & bars)
    chart
    return (chart,)


@app.cell
def _(chart):

    # Get the selected cars from the chart
    selected_cars = chart.value.to_dict('records')

    # Clear existing mesh objects
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.select_by_type(type='MESH')
    bpy.ops.object.delete()

    # Create materials once (reuse them)
    materials = {}
    for origin, color in [('USA', (0.8, 0.2, 0.2, 1)), 
                          ('Japan', (1.0, 0.6, 0.2, 1)), 
                          ('Europe', (0.2, 0.4, 0.8, 1))]:
        mat = bpy.data.materials.new(name=f"Mat_{origin}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Metallic'].default_value = 0.8
        bsdf.inputs['Roughness'].default_value = 0.2
        materials[origin] = mat

    # Use same scale for both axes
    scale = 0.1

    # Create spheres for each car
    for i, car in enumerate(selected_cars):
        hp = car['Horsepower']
        mpg = car['Miles_per_Gallon']
        origin = car['Origin']
    
        # Create sphere - same scale for both axes
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=0.3,
            location=(hp * scale, mpg * scale, 0),
            segments=16,
            ring_count=8
        )
        sphere = bpy.context.active_object
        sphere.name = f"Car_{i}"
    
        # Shade smooth
        bpy.ops.object.shade_smooth()
    
        # Assign material
        sphere.data.materials.append(materials[origin])

    mo.md(f"**Created {len(selected_cars)} spheres!**\n\n*X = Horsepower, Y = Miles per Gallon (equal scale)*\n\n🔴 Red = USA | 🟠 Orange = Japan | 🔵 Blue = Europe")
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
