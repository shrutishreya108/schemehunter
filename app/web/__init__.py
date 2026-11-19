"""Official web pages as a fallback, used only when a scheme has no guideline PDF text.

The model never supplies a URL and has no fetch tool. Code picks pages from rulebook/sources/<id>/web.yaml.
Fetching is in fetch.py, caching and snapshots in cache.py, the CLI in warm.py. This package never imports
anthropic or app.brain.
"""
