"""Scripted scenarios. Each step runs real engine calls and captures a snapshot."""

from __future__ import annotations

from typing import Callable

from . import data
from .data import DA, ME, OPR, PGM, QA
from .engine import SYSTEM, DigitalThread, ThreadError

Step = tuple[str, str, Callable[[DigitalThread], object]]


def _run(title: str, intro: str, steps: list[tuple[str, str, str, Callable[[DigitalThread], object]]],
         actions: list[str] | None = None) -> dict:
    t = data.build()
    out = {"title": title, "intro": intro, "baseline": t.snapshot(), "steps": []}
    for n, (label, who, narrative, fn) in enumerate(steps):
        mark = len(t.audit)
        try:
            result = fn(t)
            outcome, message = "ok", _describe(result)
        except ThreadError as exc:
            outcome, message = "blocked", str(exc)
        out["steps"].append({
            "label": label, "action": (actions or [])[n] if actions and n < len(actions) else label, "who": who, "narrative": narrative, "outcome": outcome, "message": message,
            "events": [a.__dict__ for a in t.audit[mark:]], "snapshot": t.snapshot(),
        })
    return out


def _describe(result: object) -> str:
    if isinstance(result, list):
        return f"{len(result)} item(s)"
    if hasattr(result, "seq") and hasattr(result, "rev"):
        return f"Op {result.seq} {result.name} recorded at Rev {result.rev}" + (" (rework)" if result.rework else "")
    if hasattr(result, "id"):
        return f"{result.id}: {getattr(result, 'status', '')}".strip(": ")
    if isinstance(result, str):
        return result
    return "done"


def _many(*fns):
    def go(t):
        res = None
        for f in fns:
            res = f(t)
        return res
    return go


def _dsp(t: DigitalThread, serial: str | None, impact: str | None = None) -> str:
    return next(d.id for d in t.dispositions.values()
                if d.status == "PENDING" and d.serial == serial and (impact is None or d.impact == impact))


def _guardrails(t: DigitalThread) -> str:
    msgs = []
    target = _dsp(t, "HA410-0002")
    for who in (SYSTEM, QA):
        try:
            t.decide(target, who, "REWORK_TO_NEW_REV")
        except ThreadError as exc:
            msgs.append(str(exc))
    raise ThreadError(" | ".join(msgs))


def _impact_summary(t: DigitalThread) -> str:
    items = t.analyze_impact("ECO-0042")
    return "; ".join(f"{i['serial']}: {i['impact'].replace('_', ' ').lower()}" for i in items)


def rev_d() -> dict:
    return _run(
        "ECO-0042: Rev D released while Rev C work is in process",
        "A design change lands mid-build. The thread finds every affected serial, holds the right work, routes each decision to the authority that owns it, and only then synchronizes travelers.",
        [
            ("Release Rev D", "R. Okafor / Design Authority",
             "Design Authority releases ECO-0042. Rev C work orders are still on the floor.",
             lambda t: t.release_eco("ECO-0042", DA)),
            ("Effectivity and serial impact", "thread-engine / System",
             "Effectivity starts at serial 0002. Each work order is classified by where it is relative to the changed operations (20, 30).",
             _impact_summary),
            ("Controlled holds + routing", "thread-engine / System",
             "Impacted work is held. Each decision is routed to the authority that owns it: Design Authority for built hardware, Manufacturing Engineering for travelers, Program for schedule.",
             lambda t: t.apply_controlled_holds("ECO-0042")),
            ("Stale revision blocked", "J. Brooks / Production",
             "The floor tries to start op 20 on HA410-0004 from the Rev C traveler.",
             lambda t: t.dispatch("WO-5104", OPR, 6.01)),
            ("No auto-approval", "thread-engine, M. Chen",
             "The engine tries to approve its own recommendation, then Quality tries to sign a Design Authority decision. Both are refused and logged.",
             _guardrails),
            ("Design Authority: HA410-0002", "R. Okafor / Design Authority",
             "Ops 20-30 were built at Rev C. Design Authority chooses rework to Rev D over use-as-is.",
             lambda t: t.decide(_dsp(t, "HA410-0002"), DA, "REWORK_TO_NEW_REV", "Seal margin finding applies; no deviation")),
            ("Design Authority: HA410-0003", "R. Okafor / Design Authority",
             "Op 20 was cut to the old groove depth. Rework it before the new gasket goes in.",
             lambda t: t.decide(_dsp(t, "HA410-0003"), DA, "REWORK_TO_NEW_REV", "Re-cut groove to 6.40")),
            ("Mfg Engineering: re-issue travelers", "S. Patel / Manufacturing Engineering",
             "HA410-0004 and 0005 have not reached the changed operations. Their travelers are re-issued at Rev D.",
             _many(lambda t: t.decide(_dsp(t, "HA410-0004"), ME, "REISSUE_TRAVELER"),
                   lambda t: t.decide(_dsp(t, "HA410-0005"), ME, "REISSUE_TRAVELER"))),
            ("Program: accept schedule impact", "D. Alvarez / Program",
             "Program accepts the rework days. With every decision signed, travelers sync to Rev D, the GS-14 gasket is allocated, and holds release.",
             lambda t: t.decide(_dsp(t, None, "SCHEDULE"), PGM, "ACCEPT_SCHEDULE_IMPACT", "Absorbed in float")),
            ("Execute at Rev D", "J. Brooks / Production",
             "HA410-0004 op 20 now runs from the Rev D traveler; groove depth is inspected against the new limits.",
             lambda t: t.dispatch("WO-5104", OPR, 6.41)),
            ("Acceptance gate", "M. Chen / Quality",
             "Quality tries to accept HA410-0002 before its rework is done. The thread blocks it.",
             lambda t: t.accept("HA410-0002", QA)),
            ("Rework, test, accept", "J. Brooks, M. Chen",
             "HA410-0002 is reworked at Rev D (ops 20, 30), seal tested, packaged, then accepted with a complete thread.",
             _many(lambda t: t.dispatch("WO-5102", OPR, 6.39), lambda t: t.dispatch("WO-5102", OPR),
                   lambda t: t.dispatch("WO-5102", OPR, 3.31), lambda t: t.dispatch("WO-5102", OPR),
                   lambda t: t.accept("HA410-0002", QA))),
        ],
        actions=['Release Rev D as R. Okafor (Design Authority)', 'Run effectivity and serial impact', 'Apply controlled holds and route decisions', 'Try to start op 20 from the Rev C traveler', 'Try auto-approval, then a wrong-role signature', 'Sign rework for HA410-0002 as R. Okafor (DA)', 'Sign rework for HA410-0003 as R. Okafor (DA)', 'Re-issue travelers as S. Patel (Mfg Eng)', 'Accept schedule impact as D. Alvarez (Program)', 'Run op 20 on HA410-0004 at Rev D', 'Try to accept HA410-0002 now', 'Rework, test and accept HA410-0002'],
    )


def ncr() -> dict:
    return _run(
        "Inspection out of tolerance -> NCR",
        "A measured characteristic fails. The unit is held, a nonconformance is opened and routed to MRB, and acceptance stays blocked until re-inspection passes.",
        [
            ("Bore out of tolerance", "J. Brooks / Production",
             "SV220-0011 op 20: bore runout measures 0.08 mm against a 0.05 mm limit.",
             lambda t: t.dispatch("WO-5201", OPR, 0.08)),
            ("Work held", "J. Brooks / Production",
             "The floor tries to move the unit to leak test.",
             lambda t: t.dispatch("WO-5201", OPR)),
            ("Use-as-is needs Design Authority", "M. Chen / Quality",
             "Quality tries to disposition use-as-is. Only Design Authority can accept a departure from design.",
             lambda t: t.decide(_dsp(t, "SV220-0011"), QA, "USE_AS_IS")),
            ("MRB: rework", "M. Chen / Quality",
             "Quality dispositions rework. The hold releases and the traveler returns to op 20.",
             lambda t: t.decide(_dsp(t, "SV220-0011"), QA, "REWORK", "Re-bore and re-inspect")),
            ("Rework and re-inspect", "J. Brooks / Production",
             "Re-bore measures 0.03 mm. The NCR closes on passing evidence.",
             lambda t: t.dispatch("WO-5201", OPR, 0.03)),
            ("Finish and accept", "J. Brooks, M. Chen",
             "Leak test, package, accept.",
             _many(lambda t: t.dispatch("WO-5201", OPR), lambda t: t.dispatch("WO-5201", OPR),
                   lambda t: t.accept("SV220-0011", QA))),
        ],
        actions=['Record bore measurement 0.08 mm', 'Try to move unit to leak test', 'Try use-as-is as M. Chen (Quality)', 'Disposition rework as M. Chen (Quality)', 'Re-bore and re-inspect', 'Finish, test and accept'],
    )


def supplier() -> dict:
    return _run(
        "Supplier delay -> replan",
        "An inbound lot slips. The thread finds which work orders need it, which miss their need date, and whether a qualified, certified alternate is on hand. Program decides.",
        [
            ("Supplier slip", "thread-engine / System",
             "Pacific Fastener Co reports lot L-FS08-314 five days late.",
             lambda t: t.supplier_delay("L-FS08-314", 5)),
            ("Floor blocked on missing material", "J. Brooks / Production",
             "Nothing changes on its own: HA410-0004 still points at the late lot.",
             _many(lambda t: setattr(t, "today", 2) or "day 2", lambda t: t.dispatch("WO-5104", OPR, 6.01),
                   lambda t: t.dispatch("WO-5104", OPR), lambda t: t.dispatch("WO-5104", OPR))),
            ("Program: reallocate", "D. Alvarez / Program",
             "Program chooses the qualified alternate lot (Anchor Bolt Supply, cert on file). Allocations update and the work can flow.",
             lambda t: t.decide(_dsp(t, None, "SUPPLY_DELAY"), PGM, "REALLOCATE_ALTERNATE_LOT", "Second source qualified")),
        ],
        actions=['Receive supplier slip notice', 'Try to keep building HA410-0004', 'Reallocate alternate lot as D. Alvarez (Program)'],
    )


def mismatch() -> dict:
    return _run(
        "Physical-to-record mismatch -> hold",
        "What is on the bench must match what the record says. A wrong lot scan holds the unit; a missing cert blocks acceptance.",
        [
            ("Wrong lot scanned", "J. Brooks / Production",
             "CP105-0021 op 30: the operator scans insert lot L-IN05-015; the MBOM allocated L-IN05-016.",
             lambda t: t.dispatch("WO-5301", OPR, scanned={"IN-05": "L-IN05-015"})),
            ("Quality: replace part", "M. Chen / Quality",
             "Quality directs replacement with the allocated, certified lot.",
             lambda t: t.decide(_dsp(t, "CP105-0021"), QA, "REPLACE_WITH_ALLOCATED_LOT")),
            ("Correct lot installed", "J. Brooks / Production",
             "Re-scan matches the record; the operation is recorded against lot L-IN05-016.",
             lambda t: t.dispatch("WO-5301", OPR, scanned={"IN-05": "L-IN05-016"})),
            ("Missing cert blocks acceptance", "J. Brooks, M. Chen",
             "CP105-0022 finishes test, but its insert lot has no material cert. Acceptance is refused.",
             _many(lambda t: t.dispatch("WO-5302", OPR, 4.22), lambda t: t.dispatch("WO-5302", OPR),
                   lambda t: t.accept("CP105-0022", QA))),
        ],
        actions=['Scan insert lot at op 30', 'Direct replacement as M. Chen (Quality)', 'Re-scan the correct lot', 'Finish CP105-0022 and try acceptance'],
    )


ALL = {"eco": rev_d, "ncr": ncr, "supplier": supplier, "mismatch": mismatch}
