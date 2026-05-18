import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")

with app.setup:
    import marimo as mo
    import altair as alt
    from altair.datasets import data


@app.cell
def _():
    mo.md("""
    # Reactive plots! 🚗 ⚡
    """)
    return


@app.cell
def _():
    mo.md("""
    This plot is **interactive**! Click and drag to select points to get a filtered dataset.
    """)
    return


@app.cell
def _():
    cars = data("cars")

    brush = alt.selection_interval()
    scatter = (
        alt.Chart(cars)
        .mark_point()
        .encode(
            x="Horsepower",
            y="Miles_per_Gallon",
            color="Origin",
        )
        .add_params(brush)
    )
    bars = (
        alt.Chart(cars)
        .mark_bar()
        .encode(y="Origin:N", color="Origin:N", x="count(Origin):Q")
        .transform_filter(brush)
    )
    chart = mo.ui.altair_chart(scatter & bars)
    chart
    return (chart,)


@app.cell
def _():
    mo.md("""
    Select one or more cars from the table.
    """)
    return


@app.cell
def _(chart):
    (filtered_data := mo.ui.table(chart.value))
    return (filtered_data,)


@app.cell
def _(filtered_data):
    mo.stop(not len(filtered_data.value))
    mpg_hist = mo.ui.altair_chart(
        alt.Chart(filtered_data.value)
        .mark_bar()
        .encode(alt.X("Miles_per_Gallon:Q", bin=True), y="count()")
    )
    horsepower_hist = mo.ui.altair_chart(
        alt.Chart(filtered_data.value)
        .mark_bar()
        .encode(alt.X("Horsepower:Q", bin=True), y="count()")
    )
    mo.hstack([mpg_hist, horsepower_hist], justify="space-around", widths="equal")
    return


if __name__ == "__main__":
    app.run()
