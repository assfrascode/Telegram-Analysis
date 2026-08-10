from pathlib import Path

import pytest


REPORT_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
)


@pytest.mark.parametrize(
    ("template_name", "script_href"),
    [
        ("index.html.j2", "assets/report.js"),
        ("subreport.html.j2", "../assets/report.js"),
        ("media_gallery.html.j2", "assets/report.js"),
    ],
)
def test_every_report_page_exposes_shared_theme_toggle(
    template_name: str,
    script_href: str,
) -> None:
    template = (REPORT_TEMPLATE_DIR / template_name).read_text()

    assert "data-theme-toggle" in template
    assert "data-theme-label" in template
    assert "theme-toggle__sun" in template
    assert "theme-toggle__moon" in template
    assert f'<script defer src="{script_href}"></script>' in template


def test_report_theme_script_persists_and_propagates_the_choice() -> None:
    script = (REPORT_TEMPLATE_DIR / "report.js.j2").read_text()

    assert 'const themeKey = "chat-analysis-report-theme"' in script
    assert "window.localStorage.getItem(themeKey)" in script
    assert "window.localStorage.setItem(themeKey, theme)" in script
    assert 'destination.searchParams.set("theme", theme)' in script
    assert "document.documentElement.dataset.theme = nextTheme" in script
    assert 'window.history.replaceState(null, "", current.href)' in script


def test_light_theme_uses_telegram_toned_chat_colors() -> None:
    css = (REPORT_TEMPLATE_DIR / "report.css.j2").read_text()

    assert ':root[data-theme="light"]' in css
    assert "--accent: #3390ec" in css
    assert "--bg: #dfeaf2" in css
    assert ':root[data-theme="light"] .message-thread' in css
    assert "linear-gradient(145deg, #dce9f1, #e6eff5)" in css
    assert ':root[data-theme="light"] .telegram-bubble' in css
    assert "background: #ffffff" in css
