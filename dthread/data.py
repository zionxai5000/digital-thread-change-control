"""Synthetic reference data.

Generic marine hardware, invented part numbers, serials, lots, suppliers and
people. Nothing here describes a real program, product or organisation.
"""

from __future__ import annotations

from .engine import DigitalThread
from .model import (
    ECO,
    Characteristic,
    DesignRevision,
    MaterialLot,
    OpRecord,
    Operation,
    Person,
    Requirement,
    Role,
    Supplier,
    WorkOrder,
)

DA = Person("R. Okafor", Role.DESIGN_AUTHORITY)
QA = Person("M. Chen", Role.QUALITY)
ME = Person("S. Patel", Role.MFG_ENG)
PGM = Person("D. Alvarez", Role.PROGRAM)
OPR = Person("J. Brooks", Role.OPERATOR)


def _hatch(rev: str, status: str) -> DesignRevision:
    depth = 6.00 if rev == "C" else 6.40
    gasket = "GS-12" if rev == "C" else "GS-14"
    return DesignRevision(
        part="HA-410", rev=rev, status=status, title="Access Hatch Assembly",
        requirements=["REQ-101", "REQ-102", "REQ-103", "REQ-104"],
        ebom={"PL-90": 1, gasket: 1, "FS-08": 1},
        routing=[
            Operation(10, "Kit and verify material", ["PL-90"]),
            Operation(20, "Machine seal groove", [], "GROOVE_DEPTH"),
            Operation(30, "Install seal gasket", [gasket]),
            Operation(40, "Assemble hinge and fasteners", ["FS-08"], "HINGE_TORQUE"),
            Operation(50, "Hydrostatic seal test", [], "SEAL_HOLD"),
            Operation(60, "Final inspection and package"),
        ],
        characteristics={
            "GROOVE_DEPTH": Characteristic("GROOVE_DEPTH", depth, depth - 0.05, depth + 0.05, "mm"),
            "HINGE_TORQUE": Characteristic("HINGE_TORQUE", 85.0, 80.0, 90.0, "N*m"),
            "SEAL_HOLD": Characteristic("SEAL_HOLD", 3.3, 3.0, 3.6, "bar"),
        },
    )


def build() -> DigitalThread:
    t = DigitalThread()
    for r in [
        Requirement("REQ-101", "Hatch seal holds 3.0 bar differential for 10 min", "SEAL_HOLD"),
        Requirement("REQ-102", "Seal groove geometry per released design", "GROOVE_DEPTH"),
        Requirement("REQ-103", "Hinge fasteners torqued to 85 N*m +/- 5", "HINGE_TORQUE"),
        Requirement("REQ-104", "Hull plate material traceable to certified mill lot", "cert:PL-90"),
        Requirement("REQ-201", "Valve bore concentricity <= 0.05 mm", "BORE_RUNOUT"),
        Requirement("REQ-202", "Valve body leak-tight at 4.0 bar", "BODY_LEAK"),
        Requirement("REQ-203", "Casting material traceable to certified heat", "cert:CB-30"),
        Requirement("REQ-301", "Penetrator thread pitch diameter in tolerance", "THREAD_PD"),
        Requirement("REQ-302", "Insert material traceable to certified lot", "cert:IN-05"),
        Requirement("REQ-303", "Penetrator leak-tight at 4.0 bar", "PEN_LEAK"),
    ]:
        t.requirements[r.id] = r

    t.revisions[("HA-410", "C")] = _hatch("C", "RELEASED")
    t.revisions[("HA-410", "D")] = _hatch("D", "PENDING")
    t.revisions[("SV-220", "B")] = DesignRevision(
        "SV-220", "B", "RELEASED", "Seawater Valve Body", ["REQ-201", "REQ-202", "REQ-203"], {"CB-30": 1},
        [Operation(10, "Kit casting", ["CB-30"]), Operation(20, "Machine bore", [], "BORE_RUNOUT"),
         Operation(30, "Body leak test", [], "BODY_LEAK"), Operation(40, "Final inspection and package")],
        {"BORE_RUNOUT": Characteristic("BORE_RUNOUT", 0.02, 0.0, 0.05, "mm"),
         "BODY_LEAK": Characteristic("BODY_LEAK", 4.2, 4.0, 4.6, "bar")})
    t.revisions[("CP-105", "A")] = DesignRevision(
        "CP-105", "A", "RELEASED", "Cable Penetrator Fitting", ["REQ-301", "REQ-302", "REQ-303"], {"BS-40": 1, "IN-05": 1},
        [Operation(10, "Kit bar stock", ["BS-40"]), Operation(20, "Machine thread", [], "THREAD_PD"),
         Operation(30, "Install insert", ["IN-05"]), Operation(40, "Leak test", [], "PEN_LEAK"),
         Operation(50, "Final inspection and package")],
        {"THREAD_PD": Characteristic("THREAD_PD", 22.05, 22.00, 22.10, "mm"),
         "PEN_LEAK": Characteristic("PEN_LEAK", 4.2, 4.0, 4.6, "bar")})

    for s in [Supplier("SUP-01", "Northshore Alloys"), Supplier("SUP-02", "Harborline Seals"),
              Supplier("SUP-03", "Pacific Fastener Co"), Supplier("SUP-04", "Anchor Bolt Supply"),
              Supplier("SUP-05", "Keel Foundry"), Supplier("SUP-06", "Coastline Components")]:
        t.suppliers[s.id] = s
    for lot in [
        MaterialLot("L-PL90-118", "PL-90", "SUP-01", "MTR-118", 2),
        MaterialLot("L-PL90-121", "PL-90", "SUP-01", "MTR-121", 6),
        MaterialLot("L-GS12-044", "GS-12", "SUP-02", "COC-044", 4),
        MaterialLot("L-GS14-007", "GS-14", "SUP-02", "COC-007", 6),
        MaterialLot("L-FS08-310", "FS-08", "SUP-03", "COC-310", 0),
        MaterialLot("L-FS08-314", "FS-08", "SUP-03", "COC-314", 4, eta_day=3),
        MaterialLot("L-FS08-A22", "FS-08", "SUP-04", "COC-A22", 3),
        MaterialLot("L-CB30-052", "CB-30", "SUP-05", "MTR-052", 3),
        MaterialLot("L-BS40-077", "BS-40", "SUP-01", "MTR-077", 4),
        MaterialLot("L-IN05-015", "IN-05", "SUP-06", None, 2),
        MaterialLot("L-IN05-016", "IN-05", "SUP-06", "COC-016", 3),
    ]:
        t.lots[lot.id] = lot

    def wo(id_, serial, part, rev, done, status, need, alloc, nxt):
        w = WorkOrder(id_, serial, part, rev, rev, nxt, status, need, alloc)
        t.work_orders[id_] = w
        return w, done

    hatch = {"PL-90": "L-PL90-121", "GS-12": "L-GS12-044"}
    specs = [
        wo("WO-5101", "HA410-0001", "HA-410", "C", 6, "COMPLETE", -6, {**hatch, "PL-90": "L-PL90-118", "FS-08": "L-FS08-310"}, None),
        wo("WO-5102", "HA410-0002", "HA-410", "C", 4, "ACTIVE", 2, {**hatch, "PL-90": "L-PL90-118", "FS-08": "L-FS08-310"}, 50),
        wo("WO-5103", "HA410-0003", "HA-410", "C", 2, "ACTIVE", 4, {**hatch, "FS-08": "L-FS08-310"}, 30),
        wo("WO-5104", "HA410-0004", "HA-410", "C", 1, "ACTIVE", 6, {**hatch, "FS-08": "L-FS08-314"}, 20),
        wo("WO-5105", "HA410-0005", "HA-410", "C", 0, "PLANNED", 8, {**hatch, "FS-08": "L-FS08-314"}, 10),
        wo("WO-5201", "SV220-0011", "SV-220", "B", 1, "ACTIVE", 3, {"CB-30": "L-CB30-052"}, 20),
        wo("WO-5202", "SV220-0012", "SV-220", "B", 4, "COMPLETE", 1, {"CB-30": "L-CB30-052"}, None),
        wo("WO-5301", "CP105-0021", "CP-105", "A", 2, "ACTIVE", 4, {"BS-40": "L-BS40-077", "IN-05": "L-IN05-016"}, 30),
        wo("WO-5302", "CP105-0022", "CP-105", "A", 3, "ACTIVE", 3, {"BS-40": "L-BS40-077", "IN-05": "L-IN05-015"}, 40),
    ]
    # Replay history so as-built records and inspections are real engine output.
    t.today = -8
    measured = {"GROOVE_DEPTH": 6.01, "HINGE_TORQUE": 85.5, "SEAL_HOLD": 3.32, "BORE_RUNOUT": 0.02, "BODY_LEAK": 4.25, "THREAD_PD": 22.04, "PEN_LEAK": 4.21}
    for w, done in specs:
        rev = t.revisions[(w.part, w.traveler_rev)]
        target_status, target_next = w.status, w.next_seq
        w.status, w.next_seq = "PLANNED", rev.routing[0].seq
        for op in rev.routing[:done]:
            t.dispatch(w.id, OPR, measured.get(op.characteristic or ""))
        w.status, w.next_seq = target_status, target_next
    t.today = 0
    t.accept("HA410-0001", QA)
    t.ecos["ECO-0042"] = ECO(
        "ECO-0042", "HA-410", "C", "D", "Deepen seal groove, change gasket GS-12 -> GS-14",
        "Qualification test showed low seal compression margin; groove 6.00 -> 6.40 mm with thicker gasket",
        affected_ops=[20, 30], effectivity_from=2)
    t.alerts.clear()
    t.alert("warn", "ECO-0042", "ECO-0042 approved by change board; Rev D ready for release with Rev C work in process", "eco")
    t.alert("warn", "L-IN05-015", "Lot L-IN05-015 consumed on CP105-0022 has no material cert on file", None)
    t.alert("info", "L-FS08-314", "Pacific Fastener Co lot L-FS08-314 inbound, ETA day 3", "supplier")
    t.alert("ok", "HA410-0001", "HA410-0001 accepted with complete thread", None)
    t._clock = 0
    t.audit.clear()
    t.log(t_system(), "BASELINE_LOADED", "dataset", "Synthetic reference data loaded")
    return t


def t_system():
    from .engine import SYSTEM
    return SYSTEM
