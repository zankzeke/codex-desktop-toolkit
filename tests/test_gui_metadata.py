import ui_theme


def test_themes_and_version_are_exposed_without_starting_tk():
    assert ui_theme.APP_VERSION == '0.2.0'
    assert {'午夜蓝', '石墨黑', '深海蓝', '明亮'} <= set(ui_theme.THEMES)
    for theme in ui_theme.THEMES.values():
        for key in ('bg', 'panel', 'surface', 'fg', 'accent', 'success', 'warn', 'danger'):
            assert key in theme
