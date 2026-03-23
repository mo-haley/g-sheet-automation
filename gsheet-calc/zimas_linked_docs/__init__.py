"""ZIMAS linked-document handling module.

Detects, classifies, logs, and selectively fetches linked zoning authority
materials referenced by ZIMAS parcel data (specific plans, CPIO overlays,
Q/D conditions, ZI documents, ordinances, case documents).

This module is upstream-only. It does not write to or import from any calc
module (FAR, density, parking, setback). Downstream wiring is a future sprint.

Entry point:
    from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline

Posture:
    unknown-first / fail-loud / detected-but-not-interpreted is acceptable.
"""

from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline

__all__ = ["run_zimas_linked_doc_pipeline"]
