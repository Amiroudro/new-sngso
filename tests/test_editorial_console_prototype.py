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

    assert html.count("<button disabled") >= 7


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


def test_story_detail_exposes_claims_provenance_and_separate_language_states():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    for story_id in ("S-024", "S-023", "S-022", "S-019"):
        assert f'data-story-id="{story_id}"' in html
        assert f'"{story_id}": {{' in script
    assert 'id="story-claim-list"' in html
    assert 'id="story-source-note"' in html
    assert 'id="story-en-state"' in html
    assert 'id="story-bn-state"' in html
    assert 'dialog.showModal()' in script
    assert "no approval can be recorded" in html


def test_navigation_has_aria_current_and_keyboard_focus_style():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "studio.css").read_text(encoding="utf-8")
    assert 'aria-current="page"' in html
    assert ":focus-visible" in css


def test_evidence_workspace_maps_observations_to_claims_and_history():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    assert 'id="story-observation-rows"' in html
    assert 'id="story-decision-history"' in html
    assert "const observations =" in script
    assert "const decisionHistory =" in script
    assert "observationRows.replaceChildren" in script
    assert "history.replaceChildren" in script
    assert "fictional" in html.lower()
    for story_id in ("S-024", "S-023", "S-022", "S-019"):
        assert f'"{story_id}": [' in script


def test_evidence_workspace_keeps_conflicts_visible_and_non_actionable():
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '"kind": "conflict"' in script
    assert '"kind": "warning"' in script
    assert "no approval can be recorded" in html
    assert "Illustrative event sequence, not a persisted audit log" in html


def test_edition_composition_exposes_ordering_and_local_only_boundary():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    assert 'id="edition-story-list"' in html
    assert 'id="reset-edition-order"' in html
    assert "Local demo ordering only" in html
    assert "let editionOrder" in script
    assert "renderEditionComposition()" in script
    assert 'data-move' in script
    assert "has not been saved" in script


def test_edition_readiness_keeps_language_rights_and_approval_gates_separate():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    assert 'id="edition-readiness"' in html
    match = re.search(r'<button[^>]*id="publish-edition"[^>]*>', html)
    assert match is not None and "disabled" in match.group(0)
    for field in ("claimsResolved", "languagesReviewed", "rightsCleared"):
        assert field in script
    assert "atomic state transition and audit guarantees" in script
    assert "English and Bangla variants independently reviewed" in script


def test_release_control_has_independent_preflight_gates_and_no_live_actions():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "studio.js").read_text(encoding="utf-8")
    assert 'data-view="release"' in html
    assert 'id="view-release"' in html
    assert 'id="release-gate-list"' in html
    assert 'id="release-overall-status"' in html
    assert "function renderReleasePreflight()" in script
    for gate in (
        "Evidence and claim resolution",
        "English and Bangla review",
        "Operation-specific source rights",
        "Approved immutable edition revision",
        "Release authorization and audit readiness",
    ):
        assert gate in script
    assert "fixture check passed; this is not a live verification" in script
    for control_id in ("authorize-release", "publish-release"):
        match = re.search(rf'<button[^>]*id="{re.escape(control_id)}"[^>]*>', html)
        assert match is not None and "disabled" in match.group(0)


def test_release_control_separates_approval_authorization_and_publication():
    html = (ROOT / "apps" / "editorial-console" / "index.html").read_text(encoding="utf-8")
    for label in ("Editorial approval", "Release authorization", "Publication", "Publisher service"):
        assert label in html
    assert "Role names and permissions are design examples" in html
