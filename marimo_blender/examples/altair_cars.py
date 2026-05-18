import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")

with app.setup:
    import marimo as mo
    import altair as alt
    from altair.datasets import data


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
def _(bpy, chart):
    import math

    # Get the selected cars from the chart
    selected_data = chart.value

    # If nothing selected, use all cars
    if selected_data is None or len(selected_data) == 0:
        from altair.datasets import data
        cars_df = data.cars()
        # Convert to list of dicts
        selected_cars = [cars_df.iloc[i].to_dict() for i in range(len(cars_df))]
    else:
        selected_cars = selected_data

    # Clear existing mesh objects
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.select_by_type(type='MESH')
    bpy.ops.object.delete()

    # Create a car for each data point
    created = 0
    for i, car in enumerate(selected_cars[:50]):  # Limit to 50 for performance
        # Get car stats
        hp = car.get('Horsepower')
        mpg = car.get('Miles_per_Gallon')
        weight = car.get('Weight_in_lbs')
        origin = car.get('Origin', 'USA')
    
        # Skip if missing data
        if hp is None or mpg is None or weight is None:
            continue
    
        # Create car body (stretched cube)
        bpy.ops.mesh.primitive_cube_add(
            size=1,
            location=(hp / 50, mpg, weight / 1000)
        )
        body = bpy.context.active_object
        body.scale = (0.8, 0.4, 0.3)
        body.name = f"Car_{i}"
    
        # Add material based on origin
        mat = bpy.data.materials.new(name=f"Mat_{i}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
    
        # Color by origin
        if origin == 'USA':
            bsdf.inputs['Base Color'].default_value = (0.8, 0.2, 0.2, 1)
        elif origin == 'Japan':
            bsdf.inputs['Base Color'].default_value = (1.0, 0.6, 0.2, 1)
        else:  # Europe
            bsdf.inputs['Base Color'].default_value = (0.2, 0.4, 0.8, 1)
    
        bsdf.inputs['Metallic'].default_value = 0.7
        bsdf.inputs['Roughness'].default_value = 0.3
    
        body.data.materials.append(mat)
    
        # Add wheels
        for wx, wy in [(-0.3, -0.15), (-0.3, 0.15), (0.3, -0.15), (0.3, 0.15)]:
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.15,
                depth=0.1,
                location=(hp / 50 + wx, mpg + wy, weight / 1000 - 0.3),
                rotation=(0, math.pi/2, 0)
            )
            wheel = bpy.context.active_object
            wheel.data.materials.append(mat)
            wheel.parent = body
    
        created += 1

    # Add camera
    bpy.ops.object.camera_add(location=(10, 10, 8))
    camera = bpy.context.active_object
    camera.rotation_euler = (math.radians(60), 0, math.radians(45))
    bpy.context.scene.camera = camera

    # Add lighting
    bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))
    light = bpy.context.active_object
    light.data.energy = 2

    # Add ground plane
    bpy.ops.mesh.primitive_plane_add(size=30, location=(5, 20, 0))
    ground = bpy.context.active_object
    ground_mat = bpy.data.materials.new(name="Ground")
    ground_mat.use_nodes = True
    ground_mat.node_tree.nodes["Principled BSDF"].inputs['Base Color'].default_value = (0.1, 0.1, 0.1, 1)
    ground.data.materials.append(ground_mat)

    mo.md(f"**Created {created} 3D cars in Blender!**\n\n*Position: X=Horsepower, Y=MPG, Z=Weight*\n\n🔴 Red = USA | 🟠 Orange = Japan | 🔵 Blue = Europe")
    return (data,)


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
