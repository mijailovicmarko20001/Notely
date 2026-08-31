"""Shared package for the Notely pipeline: ports (external-world seams),
adapters (real implementations of those ports), and -- from Phase 4 onward
-- the core path/IO/text/env helpers and the stage registry.

Dependency rule (enforced by tests/test_import_graph.py): this package
never imports webui/ or scripts/. Stage scripts and the web UI both import
*from* notely/, never the other way around.
"""
