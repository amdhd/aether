"""RAG evaluation harness for Aether's note-search retrieval + generation.

Measures the three canonical RAGAS metrics — faithfulness, context precision,
and answer relevancy — against a curated golden dataset. See ``README.md`` for
the metric definitions and how to run it, and ``FAILURE_MODES.md`` for the log
of failure modes this harness surfaced.
"""
