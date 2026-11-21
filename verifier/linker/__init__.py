"""
Linker: connects every record that describes the same order, delivery or lot.

Pipeline: discover (classify files) → adapters (files → records) → rules (records → links and
issues) → graph (save, trace). Everything is rebuilt from the portal data on each run.
"""

from verifier.linker.graph import Graph, build

__all__ = ["Graph", "build"]
