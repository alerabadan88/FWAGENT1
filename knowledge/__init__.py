"""A knowledge base of hardware families that Zephyr does not cover.

This package is deliberately separate from `codegen/zephyr/`. It imports
nothing from it and nothing in it imports this, so adding a vendor family here
cannot change or break the Zephyr path. The two share only the platform-neutral
core: `core.evidence` for what is known and why, and `agents.uncertainty` for
the shape of a question.

See `knowledge/README.md` for the design and the reasoning behind it.
"""
