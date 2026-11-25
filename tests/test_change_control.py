import pytest

from dthread import data
from dthread.data import DA, ME, OPR, PGM, QA
from dthread.engine import SYSTEM, AcceptanceBlocked, AuthorityError, DispatchBlocked


def released():
    t = data.build()
    t.release_eco("ECO-0042", DA)
    return t


def pending(t, serial=None, impact=None):
    return next(d for d in t.dispositions.values() if d.status == "PENDING" and d.serial == serial and (impact is None or d.impact == impact))


def test_only_design_authority_releases():
    t = data.build()
    with pytest.raises(AuthorityError):
        t.release_eco("ECO-0042", QA)
    assert t.ecos["ECO-0042"].status.startswith("APPROVED")


def test_effectivity_and_serial_impact():
    t = released()
    impact = {i["serial"]: i["impact"] for i in t.analyze_impact("ECO-0042")}
    assert impact == {
        "HA410-0001": "OUT_OF_EFFECTIVITY",       # accepted, delivered config frozen
        "HA410-0002": "BUILT_AT_OLD_REV",         # ops 20, 30 done at Rev C
        "HA410-0003": "BUILT_AT_OLD_REV",         # op 20 done at Rev C
        "HA410-0004": "NOT_YET_AT_AFFECTED_OPS",
        "HA410-0005": "NOT_YET_AT_AFFECTED_OPS",
    }


def test_holds_and_routing_to_right_authority():
    t = released()
    routed = t.apply_controlled_holds("ECO-0042")
    roles = {d.serial: d.required_role.value for d in routed}
    assert roles == {"HA410-0002": "Design Authority", "HA410-0003": "Design Authority",
                     "HA410-0004": "Manufacturing Engineering", "HA410-0005": "Manufacturing Engineering",
                     None: "Program"}
    assert {w.serial for w in t.work_orders.values() if w.status == "HOLD"} == {"HA410-0002", "HA410-0003", "HA410-0004", "HA410-0005"}
    assert t.wo_for("HA410-0001").status == "ACCEPTED"


def test_stale_revision_cannot_be_dispatched_even_without_hold():
    t = released()  # no holds applied yet
    with pytest.raises(DispatchBlocked, match="stale"):
        t.dispatch("WO-5104", OPR)


def test_engine_never_auto_approves_and_roles_are_enforced():
    t = released()
    t.apply_controlled_holds("ECO-0042")
    d = pending(t, "HA410-0002")
    with pytest.raises(AuthorityError, match="never approves"):
        t.decide(d.id, SYSTEM, "REWORK_TO_NEW_REV")
    with pytest.raises(AuthorityError):
        t.decide(d.id, QA, "REWORK_TO_NEW_REV")
    assert d.status == "PENDING"
    assert [a.action for a in t.audit[-2:]] == ["APPROVAL_REFUSED", "APPROVAL_REFUSED"]


def test_travelers_sync_only_after_every_decision():
    t = released()
    t.apply_controlled_holds("ECO-0042")
    t.decide(pending(t, "HA410-0002").id, DA, "REWORK_TO_NEW_REV")
    t.decide(pending(t, "HA410-0003").id, DA, "REWORK_TO_NEW_REV")
    t.decide(pending(t, "HA410-0004").id, ME, "REISSUE_TRAVELER")
    t.decide(pending(t, "HA410-0005").id, ME, "REISSUE_TRAVELER")
    assert t.wo_for("HA410-0004").traveler_rev == "C"  # program still pending
    t.decide(pending(t, None, "SCHEDULE").id, PGM, "ACCEPT_SCHEDULE_IMPACT")
    for s in ("HA410-0002", "HA410-0003", "HA410-0004", "HA410-0005"):
        w = t.wo_for(s)
        assert w.traveler_rev == "D" and w.status != "HOLD"
        assert w.allocated["GS-14"] and "GS-12" not in w.allocated
    assert t.wo_for("HA410-0002").next_seq == 20  # rework from first changed op
    rec = t.dispatch("WO-5104", OPR, 6.41)
    assert rec.rev == "D"
    assert t.ecos["ECO-0042"].status == "RELEASED - IMPLEMENTED"


def test_use_as_is_records_deviation():
    t = released()
    t.apply_controlled_holds("ECO-0042")
    t.decide(pending(t, "HA410-0002").id, DA, "USE_AS_IS_BY_DEVIATION")
    for s in ("HA410-0003",):
        t.decide(pending(t, s).id, DA, "REWORK_TO_NEW_REV")
    for s in ("HA410-0004", "HA410-0005"):
        t.decide(pending(t, s).id, ME, "REISSUE_TRAVELER")
    t.decide(pending(t, None, "SCHEDULE").id, PGM, "ACCEPT_SCHEDULE_IMPACT")
    w = t.wo_for("HA410-0002")
    assert w.deviations and w.next_seq == 50
    assert not [f for f in t.trace("HA410-0002")["findings"] if f["code"] == "REV_MISMATCH"]


def test_audit_log_is_append_only_sequence():
    t = released()
    t.apply_controlled_holds("ECO-0042")
    seqs = [a.seq for a in t.audit]
    assert seqs == list(range(1, len(seqs) + 1))
    assert any(a.action == "ECO_RELEASED" and a.actor == DA.name for a in t.audit)
