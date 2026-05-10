from src.visualization.types import VenuePreset, FigureSize


def test_venue_preset():
    pvldb = VenuePreset(
        name="pvldb",
        single_col_width_in=3.33,
        double_col_width_in=7.00,
        base_font_size_pt=9,
        font_family="serif",
        use_latex=True,
    )
    assert pvldb.single_col_width_in == 3.33


def test_figure_size():
    pvldb = VenuePreset(
        name="pvldb",
        single_col_width_in=3.33,
        double_col_width_in=7.00,
        base_font_size_pt=9,
        font_family="serif",
        use_latex=True,
    )
    single = FigureSize.single_column(pvldb, aspect=1.0)
    assert single.width_in == 3.33
    assert single.height_in == 3.33

    double = FigureSize.double_column(pvldb, aspect=0.5)
    assert double.width_in == 7.00
    assert double.height_in == 3.50
