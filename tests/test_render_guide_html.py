"""notely.pipeline.export.render_guide_html -- the web UI's guide-preview
renderer (Phase 7 of the cleanup plan). Replaces app.js's client-side
renderGuideMarkdown (marked.js + a JS regex doing the same math-stash and
path-rewrite job), so the on-screen preview and the exported PDF
(markdown_to_html) can no longer silently drift apart -- both now share
_render_math_protected_markdown."""

from notely.pipeline.export import render_guide_html


def test_math_is_protected_from_the_markdown_parser():
    html = render_guide_html("- The formula is $E = mc^2$\n- A display equation:\n\n$$\\frac{1}{3}$$\n")
    assert "$E = mc^2$" in html
    assert "$$\\frac{1}{3}$$" in html


def test_relative_image_src_becomes_files_relative():
    html = render_guide_html('<img src="slides_extracted/lecture01_images/slide_001.png">')
    assert 'src="/files/slides_extracted/lecture01_images/slide_001.png"' in html


def test_already_absolute_src_is_untouched():
    html = render_guide_html('<img src="/files/already/absolute.png">')
    assert 'src="/files/already/absolute.png"' in html
    assert html.count("/files//files/") == 0


def test_data_uri_src_is_untouched():
    html = render_guide_html('<img src="data:image/png;base64,abc123">')
    assert 'src="data:image/png;base64,abc123"' in html


def test_https_src_is_untouched():
    html = render_guide_html('<img src="https://example.com/pic.png">')
    assert 'src="https://example.com/pic.png"' in html


def test_toc_anchor_hrefs_are_not_rewritten():
    # The bug renderGuideMarkdown had: its regex rewrote href= too, turning
    # the table of contents' `#lectureNN` same-page anchors into
    # `/files/#lectureNN`, breaking in-page navigation. render_guide_html
    # never touches href= at all.
    html = render_guide_html("# Table of Contents\n\n- [lecture01](#lecture01)\n")
    assert 'href="#lecture01"' in html
    assert "/files/#lecture01" not in html


def test_heading_and_list_render_as_expected_html():
    html = render_guide_html("# lecture01\n\n## Introduction\n\n- point one\n")
    assert "<h1>lecture01</h1>" in html
    assert "<h2>Introduction</h2>" in html
    assert "<li>point one</li>" in html


def test_fenced_code_block_renders_as_pre_code():
    # Found live: a real generated exam answer key's Python code fence
    # (```python ... ```) rendered as plain paragraph text with literal
    # backticks instead of a <pre><code> block -- the markdown extension
    # list was missing "fenced_code". VIDEO_SYSTEM_PROMPT (deckless-lecture
    # notes) and EXAM_SYSTEM_PROMPT (answer keys) both explicitly ask the
    # model for fenced code blocks, so this affects real output, not just
    # a hypothetical.
    html = render_guide_html('```python\nprint("hi")\n```\n')
    assert "<pre>" in html and "<code" in html
    assert "<p>```python" not in html
