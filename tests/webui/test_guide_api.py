"""GET /api/guide: previously zero coverage. Phase 7 of the cleanup plan
moved markdown->HTML rendering server-side (notely.pipeline.export.
render_guide_html) so the response carries ready-to-display `html` instead
of raw `markdown` -- app.js used to duplicate the rendering client-side."""


def test_guide_reports_not_exists_when_nothing_assembled(client):
    resp = client.get("/api/guide")
    assert resp.status_code == 200
    assert resp.json() == {"exists": False}


def test_guide_returns_rendered_html_when_assembled(project_root, client):
    (project_root / "output").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "study_guide.md").write_text(
        "# Table of Contents\n\n- [lecture01](#lecture01)\n\n"
        "# lecture01\n\n## Introduction\n\n- The formula is $E = mc^2$\n"
        '<img src="slides_extracted/lecture01_images/slide_001.png">\n',
        encoding="utf-8",
    )

    resp = client.get("/api/guide")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["download"] == "/files/study_guide.md"
    html = body["html"]
    assert "<h1>lecture01</h1>" in html
    assert "$E = mc^2$" in html  # math passed through untouched for MathJax
    assert 'href="#lecture01"' in html  # TOC anchor untouched
    assert 'src="/files/slides_extracted/lecture01_images/slide_001.png"' in html
