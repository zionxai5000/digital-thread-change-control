import pytest

from dthread import data, scenarios, site
from dthread.data import DA, OPR, PGM, QA
from dthread.engine import AcceptanceBlocked, AuthorityError, DispatchBlocked


def codes(t, serial):
    return {f["code"] for f in t.trace(serial)["findings"]}


def test_trace_walks_full_thread():
    t = data.build()
    tr = t.trace("SV220-0012")
    assert [c["stage"] for c in tr["chain"]] == [
        "Requirements baseline", "Released design + EBOM", "MBOM / routing / traveler",
        "Qualified material lots", "As-built genealogy", "Inspection / test evidence", "Acceptance package"]
    assert tr["acceptable"] and all(v["verified"] for v in tr["verification"])


def test_missing_cert_blocks_acceptance():
    t = data.build()
    assert "MISSING_CERT" in codes(t, "CP105-0022")
    t.dispatch("WO-5302", OPR, 4.2)
    t.dispatch("WO-5302", OPR)
    with pytest.raises(AcceptanceBlocked, match="MISSING_CERT"):
        t.accept("CP105-0022", QA)


def test_as_built_rev_mismatch_flagged_after_release():
    t = data.build()
    t.release_eco("ECO-0042", DA)
    assert {"REV_MISMATCH", "STALE_TRAVELER"} <= codes(t, "HA410-0002")
    assert "REV_MISMATCH" not in codes(t, "HA410-0001")  # accepted, out of effectivity


def test_failed_inspection_opens_ncr_and_blocks():
    t = data.build()
    t.dispatch("WO-5201", OPR, 0.08)
    assert "FAILED_INSPECTION_NO_DISPOSITION" in codes(t, "SV220-0011")
    ncr = next(iter(t.ncrs.values()))
    with pytest.raises(DispatchBlocked):
        t.dispatch("WO-5201", OPR)
    with pytest.raises(AuthorityError):
        t.decide(ncr.disposition_id, QA, "USE_AS_IS")  # needs Design Authority
    t.decide(ncr.disposition_id, QA, "REWORK")
    t.dispatch("WO-5201", OPR, 0.03)
    assert ncr.status == "CLOSED"
    t.dispatch("WO-5201", OPR)
    t.dispatch("WO-5201", OPR)
    assert t.accept("SV220-0011", QA).status == "ACCEPTED"


def test_physical_to_record_mismatch_holds():
    t = data.build()
    with pytest.raises(DispatchBlocked, match="mismatch"):
        t.dispatch("WO-5301", OPR, scanned={"IN-05": "L-IN05-015"})
    assert t.work_orders["WO-5301"].status == "HOLD"
    assert "OPEN_HOLD" in codes(t, "CP105-0021")


def test_supplier_delay_routes_replan_to_program():
    t = data.build()
    d = t.supplier_delay("L-FS08-314", 5)
    assert d.required_role.value == "Program" and d.status == "PENDING"
    assert set(t._last_supply["late"]) == {"HA410-0004", "HA410-0005"}
    assert t.work_orders["WO-5104"].allocated["FS-08"] == "L-FS08-314"  # nothing changes on its own
    t.decide(d.id, PGM, "REALLOCATE_ALTERNATE_LOT")
    assert t.work_orders["WO-5104"].allocated["FS-08"] == "L-FS08-A22"


@pytest.mark.parametrize("key", list(scenarios.ALL))
def test_scenarios_run(key):
    sc = scenarios.ALL[key]()
    assert sc["steps"] and all(s["snapshot"]["kpis"] for s in sc["steps"])


def test_rev_d_scenario_ends_clean():
    sc = scenarios.rev_d()
    last = sc["steps"][-1]["snapshot"]
    assert last["kpis"]["on_hold"] == 0 and last["kpis"]["pending_decisions"] == 0
    assert last["traces"]["HA410-0002"]["status"] == "ACCEPTED"
    outcomes = [s["outcome"] for s in sc["steps"]]
    assert outcomes.count("blocked") == 3  # stale dispatch, auto-approval, early acceptance


def test_site_builds(tmp_path):
    html = site.build(tmp_path).read_text()
    assert "Synthetic reference data" in html and "/*__DATA__*/null" not in html
