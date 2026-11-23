from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1] / "apps" / "editorial-console"


def test_console_assets_are_self_contained_and_linked():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'href="./studio.css"' in html
    assert 'src="./studio.js"' in html
    assert (ROOT / "studio.css").is_file()
    assert (ROOT / "studio.js").is_file()


def test_prototype_discloses_demo_data_and_no_backend_connection():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "UI prototype" in html
    assert "illustrative sample records" in html
    assert "No live system connected" in html
    assert "No publishers are contacted" in readme
    assert "Backend not connected" in html


def test_backend_dependent_controls_are_disabled():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for control_id in ("new-edition",):
        match = re.search(
            rf'<button[^>]*id="{re.escape(control_id)}"[^>]*>',
            html,
        )
        assert match is not None
        assert "disabled" in match.group(0)

    assert html.count("<button disabled") >= 8


def test_navigation_and_all_primary_views_exist():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for view in ("overview", "editions", "stories", "review", "sources", "operations"):
        assert f'data-view="{view}"' in html
        assert f'id="view-{view}"' in html


def test_story_search_and_filter_are_wired():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    assert 'id="story-search"' in html
    assert 'id="story-filter"' in html
    assert 'addEventListener("input", filterStories)' in script
    assert 'addEventListener("change", filterStories)' in script
