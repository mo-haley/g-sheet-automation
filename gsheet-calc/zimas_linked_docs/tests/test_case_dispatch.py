"""Tests for case-document subtype dispatch (Pass D).

Verifies that:
1. _case_subtype() routes ZA/AA → case_za, CPC/CF → case_cpc,
   DIR → case_dir, ENV → case_env, unknown → case_document
2. Classifier produces correct doc_type for each recognized prefix
3. Interrupt scope differs conservatively by subtype:
   - ZA:  setback, parking, height, FAR → PROVISIONAL; density → NONE
   - CPC: all 5 topics → PROVISIONAL
   - DIR: all 5 topics → PROVISIONAL (conservative)
   - ENV: FAR, density → PROVISIONAL; parking, setback, height → NONE
   - unknown: all 5 topics → PROVISIONAL (unchanged fallback)
4. Prefix routing is a scope hint only — no case interpretation claimed
5. No regression on CPIO, Q/D, specific plan, SUD subtype handling

NOTE: Subtype recognition routes interrupt scope only. Case entitlement terms
have not been interpreted. None of the new doc types produce INTERRUPT_UNRESOLVED.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_CASE_ZA,
    DOC_TYPE_CASE_CPC,
    DOC_TYPE_CASE_DIR,
    DOC_TYPE_CASE_ENV,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_SPECIFIC_PLAN,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_NONE,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasLinkedDocInput,
)
from zimas_linked_docs.doc_classifier import (
    classify_candidates,
    _case_subtype,
)
from zimas_linked_docs.gatekeeper import evaluate_interrupts
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline
from zimas_linked_docs.models import LinkedDocCandidate, PATTERN_CASE_NUMBER


# ── Helpers ───────────────────────────────────────────────────────────────────

def _case_candidate(raw: str) -> LinkedDocCandidate:
    return LinkedDocCandidate(
        candidate_id=f"test-{raw.lower().replace(' ', '-')}",
        source_field="zimas_layer_10:CASE_NUMBER",
        raw_value=raw,
        detected_pattern=PATTERN_CASE_NUMBER,
    )


def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _registry_with_record(record: LinkedDocRecord) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=[record])


def _case_record(doc_type: str, label: str = "CASE-2020-001") -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-case-001",
        doc_type=doc_type,
        doc_label=label,
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    )


def _decisions(doc_type: str, label: str = "CASE-2020-001") -> dict[str, str]:
    record = _case_record(doc_type, label)
    decisions, _ = evaluate_interrupts(
        _registry_with_record(record),
        topics=["FAR", "density", "height", "parking", "setback"],
    )
    return {d.topic: d.interrupt_level for d in decisions}


# ── Section 1: _case_subtype() helper — unit tests ────────────────────────────

class TestCaseSubtypeHelper:
    def test_za_prefix(self):
        assert _case_subtype("ZA-2014-123") == DOC_TYPE_CASE_ZA

    def test_za_lowercase(self):
        assert _case_subtype("za-2014-123") == DOC_TYPE_CASE_ZA

    def test_aa_maps_to_case_za(self):
        """Administrative Appeals share ZA's interrupt scope."""
        assert _case_subtype("AA-2019-456") == DOC_TYPE_CASE_ZA

    def test_cpc_prefix(self):
        assert _case_subtype("CPC-2006-5568") == DOC_TYPE_CASE_CPC

    def test_cf_maps_to_case_cpc(self):
        """Council Files share CPC's interrupt scope."""
        assert _case_subtype("CF-2021-012") == DOC_TYPE_CASE_CPC

    def test_dir_prefix(self):
        assert _case_subtype("DIR-2019-456") == DOC_TYPE_CASE_DIR

    def test_env_prefix(self):
        assert _case_subtype("ENV-2018-345") == DOC_TYPE_CASE_ENV

    def test_unknown_prefix_falls_to_case_document(self):
        assert _case_subtype("ABC-2020-001") == DOC_TYPE_CASE_DOCUMENT

    def test_empty_string_falls_to_case_document(self):
        assert _case_subtype("") == DOC_TYPE_CASE_DOCUMENT

    def test_no_hyphen_falls_to_case_document(self):
        assert _case_subtype("NOHYPHEN") == DOC_TYPE_CASE_DOCUMENT


# ── Section 2: Classifier dispatch ────────────────────────────────────────────

class TestClassifierCaseDispatch:
    def test_za_classifies_as_case_za(self):
        records, _ = classify_candidates([_case_candidate("ZA-2014-123-4567")])
        assert records[0].doc_type == DOC_TYPE_CASE_ZA

    def test_cpc_classifies_as_case_cpc(self):
        records, _ = classify_candidates([_case_candidate("CPC-2006-5568")])
        assert records[0].doc_type == DOC_TYPE_CASE_CPC

    def test_dir_classifies_as_case_dir(self):
        records, _ = classify_candidates([_case_candidate("DIR-2019-456-1")])
        assert records[0].doc_type == DOC_TYPE_CASE_DIR

    def test_env_classifies_as_case_env(self):
        records, _ = classify_candidates([_case_candidate("ENV-2018-345-EIR")])
        assert records[0].doc_type == DOC_TYPE_CASE_ENV

    def test_aa_classifies_as_case_za(self):
        records, _ = classify_candidates([_case_candidate("AA-2020-789")])
        assert records[0].doc_type == DOC_TYPE_CASE_ZA

    def test_cf_classifies_as_case_cpc(self):
        records, _ = classify_candidates([_case_candidate("CF-2021-012")])
        assert records[0].doc_type == DOC_TYPE_CASE_CPC

    def test_unknown_prefix_classifies_as_case_document(self):
        """Unrecognized prefix is conservatively treated as generic case_document."""
        records, _ = classify_candidates([_case_candidate("ZBA-2020-001")])
        assert records[0].doc_type == DOC_TYPE_CASE_DOCUMENT

    def test_usability_posture_is_confidence_interrupter_only(self):
        """All case subtypes remain interrupter-only — not fetched or interpreted."""
        for raw in ("ZA-2014-1", "CPC-2006-1", "DIR-2019-1", "ENV-2018-1"):
            records, _ = classify_candidates([_case_candidate(raw)])
            assert records[0].usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY, raw

    def test_za_doc_type_notes_mention_scope_hint(self):
        records, _ = classify_candidates([_case_candidate("ZA-2014-123")])
        assert "scope hint" in records[0].doc_type_notes.lower()

    def test_env_doc_type_notes_mention_scope_hint(self):
        records, _ = classify_candidates([_case_candidate("ENV-2018-345")])
        assert "scope hint" in records[0].doc_type_notes.lower()

    def test_doc_label_preserves_case_number(self):
        """doc_label carries the original case number for provenance."""
        records, _ = classify_candidates([_case_candidate("ZA-2014-123")])
        assert "ZA-2014-123" in records[0].doc_label


# ── Section 3: Gatekeeper interrupt scope by subtype ─────────────────────────

class TestGatekeeperCaseSubtypeInterrupts:
    def test_za_interrupts_setback_parking_height_far(self):
        levels = _decisions(DOC_TYPE_CASE_ZA)
        assert levels["setback"] == INTERRUPT_PROVISIONAL
        assert levels["parking"] == INTERRUPT_PROVISIONAL
        assert levels["height"] == INTERRUPT_PROVISIONAL
        assert levels["FAR"] == INTERRUPT_PROVISIONAL

    def test_za_does_not_interrupt_density(self):
        """ZA authority does not extend to density increases."""
        levels = _decisions(DOC_TYPE_CASE_ZA)
        assert levels["density"] == INTERRUPT_NONE

    def test_cpc_interrupts_all_five_topics(self):
        levels = _decisions(DOC_TYPE_CASE_CPC)
        for topic in ("FAR", "density", "height", "parking", "setback"):
            assert levels[topic] == INTERRUPT_PROVISIONAL, topic

    def test_dir_interrupts_all_five_topics(self):
        """DIR scope is unpredictable; conservative all-topics fallback."""
        levels = _decisions(DOC_TYPE_CASE_DIR)
        for topic in ("FAR", "density", "height", "parking", "setback"):
            assert levels[topic] == INTERRUPT_PROVISIONAL, topic

    def test_env_interrupts_far_and_density(self):
        levels = _decisions(DOC_TYPE_CASE_ENV)
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL

    def test_env_does_not_interrupt_parking_setback_height(self):
        """ENV review does not directly impose dimensional standards."""
        levels = _decisions(DOC_TYPE_CASE_ENV)
        assert levels["parking"] == INTERRUPT_NONE
        assert levels["setback"] == INTERRUPT_NONE
        assert levels["height"] == INTERRUPT_NONE

    def test_unknown_case_interrupts_all_five_topics(self):
        """Unknown prefix uses conservative all-topics fallback."""
        levels = _decisions(DOC_TYPE_CASE_DOCUMENT)
        for topic in ("FAR", "density", "height", "parking", "setback"):
            assert levels[topic] == INTERRUPT_PROVISIONAL, topic

    def test_no_case_subtype_is_blocking(self):
        """PROVISIONAL is not blocking — only UNRESOLVED/REFUSE are."""
        for dt in (DOC_TYPE_CASE_ZA, DOC_TYPE_CASE_CPC, DOC_TYPE_CASE_DIR,
                   DOC_TYPE_CASE_ENV, DOC_TYPE_CASE_DOCUMENT):
            record = _case_record(dt)
            decisions, _ = evaluate_interrupts(_registry_with_record(record), topics=["FAR"])
            assert not any(d.blocking for d in decisions), f"{dt} should not be blocking"


# ── Section 4: End-to-end pipeline ───────────────────────────────────────────

class TestPipelineEndToEnd:
    def _raw_zimas_with_case(self, case_number: str) -> dict:
        return {
            "results": [
                {
                    "layerId": 10,
                    "layerName": "Planning Cases",
                    "attributes": {"CASE_NUMBER": case_number},
                }
            ]
        }

    def test_za_case_in_raw_zimas_classified_and_density_not_interrupted(self):
        inp = _inp(raw_zimas_identify=self._raw_zimas_with_case("ZA-2014-123-4567"))
        output = run_zimas_linked_doc_pipeline(inp)
        za_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_CASE_ZA]
        assert za_records, "ZA case should be classified as case_za"
        density_decision = next(d for d in output.interrupt_decisions if d.topic == "density")
        assert density_decision.interrupt_level == INTERRUPT_NONE

    def test_env_case_does_not_interrupt_parking_in_pipeline(self):
        inp = _inp(raw_zimas_identify=self._raw_zimas_with_case("ENV-2018-345-EIR"))
        output = run_zimas_linked_doc_pipeline(inp)
        env_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_CASE_ENV]
        assert env_records, "ENV case should be classified as case_env"
        parking_decision = next(d for d in output.interrupt_decisions if d.topic == "parking")
        assert parking_decision.interrupt_level == INTERRUPT_NONE

    def test_cpc_case_interrupts_all_topics_in_pipeline(self):
        inp = _inp(raw_zimas_identify=self._raw_zimas_with_case("CPC-2006-5568"))
        output = run_zimas_linked_doc_pipeline(inp)
        cpc_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_CASE_CPC]
        assert cpc_records
        for topic in ("FAR", "density", "height", "parking", "setback"):
            decision = next(d for d in output.interrupt_decisions if d.topic == topic)
            assert decision.interrupt_level == INTERRUPT_PROVISIONAL, topic


# ── Section 5: No regression on other authority classes ──────────────────────

class TestNoRegressionOtherDocTypes:
    def test_specific_plan_still_unresolved(self):
        from zimas_linked_docs.models import INTERRUPT_UNRESOLVED
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert sp_records
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_UNRESOLVED

    def test_q_condition_still_interrupts_provisionally(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL

    def test_cpio_still_classifies_correctly(self):
        inp = _inp(overlay_zones=["San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records

    def test_za_and_env_coexist(self):
        """A parcel can have both a ZA case and an ENV review without collision."""
        raw_zimas = {
            "results": [
                {
                    "layerId": 10,
                    "layerName": "Planning Cases",
                    "attributes": {
                        "ZA_CASE": "ZA-2014-123-4567",
                        "ENV_CASE": "ENV-2018-345-EIR",
                    },
                }
            ]
        }
        inp = _inp(raw_zimas_identify=raw_zimas)
        output = run_zimas_linked_doc_pipeline(inp)
        types = {r.doc_type for r in output.registry.records}
        assert DOC_TYPE_CASE_ZA in types
        assert DOC_TYPE_CASE_ENV in types

    def test_za_and_cpc_coexist_density_comes_from_cpc(self):
        """When ZA (density=NONE) and CPC (density=PROVISIONAL) coexist,
        worst-level aggregation means density is still PROVISIONAL."""
        za_record = _case_record(DOC_TYPE_CASE_ZA, "ZA-2014-001")
        cpc_record = LinkedDocRecord(
            record_id="test-case-002",
            doc_type=DOC_TYPE_CASE_CPC,
            doc_label="CPC-2006-001",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        )
        registry = LinkedDocRegistry(apn="1234-567-890", records=[za_record, cpc_record])
        decisions, _ = evaluate_interrupts(
            registry,
            topics=["FAR", "density", "height", "parking", "setback"],
        )
        levels = {d.topic: d.interrupt_level for d in decisions}
        assert levels["density"] == INTERRUPT_PROVISIONAL  # CPC contributes density
        assert levels["setback"] == INTERRUPT_PROVISIONAL  # ZA contributes setback
