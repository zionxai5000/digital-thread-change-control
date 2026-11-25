"""Change-control and traceability engine.

Design rules enforced here (and covered by tests):
  * The engine analyses, holds and routes. It never approves. Every
    disposition needs a named person holding the authority for the chosen option.
  * Effectivity decides which serials a released revision applies to.
  * A work order cannot be dispatched on a stale traveler revision or while a
    hold is open.
  * Acceptance is blocked while any thread gap is open.
  * Every state change is written to an append-only audit log.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta

from .model import (
    ECO,
    NCR,
    AuditEvent,
    DesignRevision,
    Disposition,
    Hold,
    Inspection,
    MaterialLot,
    OpRecord,
    Person,
    Requirement,
    Role,
    Supplier,
    WorkOrder,
)

SYSTEM = Person("thread-engine", Role.SYSTEM)
BASE_TIME = datetime(2026, 11, 24, 7, 0)


class ThreadError(Exception):
    """Base class for refused actions."""


class AuthorityError(ThreadError):
    """The actor does not hold the authority required for the action."""


class DispatchBlocked(ThreadError):
    """Work cannot proceed: open hold or stale revision."""


class AcceptanceBlocked(ThreadError):
    """The digital thread for the unit has open gaps."""


def serial_no(serial: str) -> int:
    return int(serial.rsplit("-", 1)[1])


class DigitalThread:
    def __init__(self) -> None:
        self.requirements: dict[str, Requirement] = {}
        self.revisions: dict[tuple[str, str], DesignRevision] = {}
        self.suppliers: dict[str, Supplier] = {}
        self.lots: dict[str, MaterialLot] = {}
        self.work_orders: dict[str, WorkOrder] = {}
        self.ecos: dict[str, ECO] = {}
        self.holds: dict[str, Hold] = {}
        self.dispositions: dict[str, Disposition] = {}
        self.ncrs: dict[str, NCR] = {}
        self.inspections: dict[str, Inspection] = {}
        self.alerts: list[dict] = []
        self.audit: list[AuditEvent] = []
        self.today = 0
        self._clock = 0
        self._ids: dict[str, int] = {}

    # ------------------------------------------------------------------ utils
    def _next_id(self, prefix: str, start: int = 1) -> str:
        n = self._ids.get(prefix, start - 1) + 1
        self._ids[prefix] = n
        return f"{prefix}-{n:04d}"

    def now(self) -> str:
        self._clock += 1
        return (BASE_TIME + timedelta(minutes=4 * self._clock)).strftime("%Y-%m-%d %H:%M")

    def log(self, actor: Person, action: str, ref: str, detail: str) -> None:
        self.audit.append(
            AuditEvent(len(self.audit) + 1, self.now(), actor.name, actor.role.value, action, ref, detail)
        )

    def alert(self, level: str, ref: str, text: str, scenario: str | None = None) -> None:
        self.alerts.insert(0, {"level": level, "ref": ref, "text": text, "scenario": scenario, "at": self.now()})

    def wo_for(self, serial: str) -> WorkOrder:
        return next(w for w in self.work_orders.values() if w.serial == serial)

    def clone(self) -> "DigitalThread":
        return copy.deepcopy(self)

    # ------------------------------------------------------------- revisions
    def released_revs(self, part: str) -> list[DesignRevision]:
        return [r for (p, _), r in self.revisions.items() if p == part and r.status != "PENDING"]

    def effective_rev(self, serial: str, part: str, planned_rev: str) -> str:
        """Revision that applies to a serial, after effectivity of released ECOs."""
        rev = planned_rev
        for eco in sorted(self.ecos.values(), key=lambda e: e.to_rev):
            if eco.part == part and eco.status.startswith("RELEASED") and eco.from_rev == rev:
                if serial_no(serial) >= eco.effectivity_from:
                    rev = eco.to_rev
        return rev

    def wo_effective_rev(self, wo: WorkOrder) -> str:
        if wo.status == "ACCEPTED":
            return wo.planned_rev  # accepted configuration is frozen
        return self.effective_rev(wo.serial, wo.part, wo.planned_rev)

    def design(self, wo: WorkOrder, rev: str | None = None) -> DesignRevision:
        return self.revisions[(wo.part, rev or wo.traveler_rev)]

    # ------------------------------------------------------------ authority
    @staticmethod
    def _require(person: Person, role: Role, what: str) -> None:
        if person.role == Role.SYSTEM:
            raise AuthorityError(f"{what}: the system never approves; a named {role.value} is required")
        if person.role != role:
            raise AuthorityError(f"{what}: requires {role.value}, {person.name} holds {person.role.value}")

    # ------------------------------------------------------------------ ECO
    def release_eco(self, eco_id: str, person: Person) -> ECO:
        eco = self.ecos[eco_id]
        self._require(person, Role.DESIGN_AUTHORITY, f"Release {eco_id}")
        new = self.revisions[(eco.part, eco.to_rev)]
        new.status = "RELEASED"
        eco.status = "RELEASED - IMPLEMENTING"
        eco.released_by = person.name
        self.log(person, "ECO_RELEASED", eco_id, f"{eco.part} Rev {eco.from_rev} -> Rev {eco.to_rev}; effectivity serial {eco.effectivity_from:04d} and up")
        self.alert("warn", eco_id, f"{eco_id} released: {eco.part} Rev {eco.to_rev} while Rev {eco.from_rev} work is in process", "eco")
        return eco

    def analyze_impact(self, eco_id: str) -> list[dict]:
        """Effectivity and serial impact for every work order on the part."""
        eco = self.ecos[eco_id]
        out = []
        for wo in sorted(self.work_orders.values(), key=lambda w: w.serial):
            if wo.part != eco.part:
                continue
            done_affected = [r.seq for r in wo.as_built if r.seq in eco.affected_ops and r.rev == eco.from_rev]
            if wo.status == "ACCEPTED" or serial_no(wo.serial) < eco.effectivity_from:
                impact, action = "OUT_OF_EFFECTIVITY", "No action. Delivered configuration Rev %s recorded" % wo.traveler_rev
            elif wo.traveler_rev == eco.to_rev:
                impact, action = "ALREADY_CURRENT", "No action"
            elif done_affected:
                impact = "BUILT_AT_OLD_REV"
                action = f"Ops {', '.join(map(str, done_affected))} built at Rev {eco.from_rev}: rework or use-as-is (Design Authority)"
            else:
                impact, action = "NOT_YET_AT_AFFECTED_OPS", f"Re-issue traveler at Rev {eco.to_rev} (Manufacturing Engineering)"
            out.append({
                "wo": wo.id, "serial": wo.serial, "status": wo.status, "traveler_rev": wo.traveler_rev,
                "next_seq": wo.next_seq, "built_affected_ops": done_affected, "impact": impact, "action": action,
            })
        self.log(SYSTEM, "IMPACT_ANALYZED", eco_id, f"{len(out)} serials evaluated; {sum(1 for i in out if i['impact'] in ('BUILT_AT_OLD_REV', 'NOT_YET_AT_AFFECTED_OPS'))} impacted")
        self._last_impact = out
        return out

    def place_hold(self, wo: WorkOrder, source: str, reason: str) -> Hold:
        hold = Hold(self._next_id("HOLD"), wo.id, source, reason)
        self.holds[hold.id] = hold
        if wo.status not in ("COMPLETE", "ACCEPTED"):
            wo.status = "HOLD"
        self.log(SYSTEM, "HOLD_PLACED", wo.id, f"{hold.id} {wo.serial}: {reason}")
        return hold

    def apply_controlled_holds(self, eco_id: str) -> list[Disposition]:
        """Hold impacted work and route each disposition to its authority."""
        eco = self.ecos[eco_id]
        impact = self.analyze_impact(eco_id) if not getattr(self, "_last_impact", None) else self._last_impact
        routed: list[Disposition] = []
        rework_days = 0
        for item in impact:
            wo = self.work_orders[item["wo"]]
            if item["impact"] == "BUILT_AT_OLD_REV":
                self.place_hold(wo, eco_id, f"Ops {item['built_affected_ops']} built at superseded Rev {eco.from_rev}")
                d = Disposition(self._next_id("DSP"), eco_id, wo.id, wo.serial, item["impact"],
                                f"{wo.serial}: rework ops {item['built_affected_ops']} to Rev {eco.to_rev}, or accept Rev {eco.from_rev} features by deviation",
                                {"REWORK_TO_NEW_REV": Role.DESIGN_AUTHORITY, "USE_AS_IS_BY_DEVIATION": Role.DESIGN_AUTHORITY},
                                "REWORK_TO_NEW_REV")
                rework_days += 2
            elif item["impact"] == "NOT_YET_AT_AFFECTED_OPS":
                self.place_hold(wo, eco_id, f"Traveler at superseded Rev {eco.from_rev}; awaiting re-issue")
                d = Disposition(self._next_id("DSP"), eco_id, wo.id, wo.serial, item["impact"],
                                f"{wo.serial}: re-issue traveler at Rev {eco.to_rev} before op {min(eco.affected_ops)}",
                                {"REISSUE_TRAVELER": Role.MFG_ENG}, "REISSUE_TRAVELER")
            else:
                continue
            self.dispositions[d.id] = d
            routed.append(d)
            self.log(SYSTEM, "DISPOSITION_ROUTED", d.id, f"{d.summary} -> {d.required_role.value}")
        if routed:
            d = Disposition(self._next_id("DSP"), eco_id, None, None, "SCHEDULE",
                            f"Accept {rework_days} days of rework across {len(routed)} serials to implement {eco_id}",
                            {"ACCEPT_SCHEDULE_IMPACT": Role.PROGRAM, "DEFER_EFFECTIVITY": Role.PROGRAM},
                            "ACCEPT_SCHEDULE_IMPACT")
            self.dispositions[d.id] = d
            routed.append(d)
            self.log(SYSTEM, "DISPOSITION_ROUTED", d.id, f"{d.summary} -> {d.required_role.value}")
        self.alert("warn", eco_id, f"{len([r for r in routed if r.wo_id])} work orders on controlled hold; {len(routed)} decisions awaiting human authority", "eco")
        return routed

    # --------------------------------------------------------- dispositions
    def decide(self, disp_id: str, person: Person, decision: str, note: str = "") -> Disposition:
        d = self.dispositions[disp_id]
        if d.status != "PENDING":
            raise ThreadError(f"{disp_id} already {d.status}")
        if decision not in d.options:
            raise ThreadError(f"{decision} is not an option for {disp_id}: {sorted(d.options)}")
        try:
            self._require(person, d.options[decision], f"Disposition {disp_id} ({decision})")
        except AuthorityError as exc:
            self.log(person, "APPROVAL_REFUSED", disp_id, str(exc))
            raise
        d.status, d.decision, d.decided_by, d.note = "APPROVED", decision, person.name, note
        self.log(person, "DISPOSITION_APPROVED", disp_id, f"{decision}. {note}".strip())
        self._apply(d, person)
        return d

    def _apply(self, d: Disposition, person: Person) -> None:
        if d.source in self.ecos:
            eco = self.ecos[d.source]
            # Travelers sync only once the whole ECO implementation is authorised.
            pending = [x for x in self.dispositions.values() if x.source == eco.id and x.status == "PENDING"]
            if not pending:
                self._implement_eco(eco)
        elif d.source in self.ncrs:
            self._apply_ncr(self.ncrs[d.source], d)
        else:
            self._apply_record(d)

    def _implement_eco(self, eco: ECO) -> None:
        sched = next(x for x in self.dispositions.values() if x.source == eco.id and x.impact == "SCHEDULE")
        if sched.decision == "DEFER_EFFECTIVITY":
            eco.status = "RELEASED - EFFECTIVITY DEFERRED"
            return
        new = self.revisions[(eco.part, eco.to_rev)]
        for d in self.dispositions.values():
            if d.source != eco.id or not d.wo_id:
                continue
            wo = self.work_orders[d.wo_id]
            old_rev = wo.traveler_rev
            wo.traveler_rev = eco.to_rev
            wo.planned_rev = eco.to_rev
            self._reallocate(wo, new)
            if d.decision == "REWORK_TO_NEW_REV":
                wo.rework_seqs = sorted(eco.affected_ops)
                wo.next_seq = min(eco.affected_ops)
            elif d.decision == "USE_AS_IS_BY_DEVIATION":
                dev = self._next_id("DEV")
                wo.deviations.append(f"{dev}: ops {eco.affected_ops} accepted at Rev {eco.from_rev} ({d.decided_by})")
            self._release_holds(wo, eco.id)
            self.log(SYSTEM, "TRAVELER_SYNCED", wo.id, f"{wo.serial} traveler Rev {old_rev} -> Rev {eco.to_rev}" + (f"; rework from op {wo.next_seq}" if wo.rework_seqs else ""))
        eco.status = "RELEASED - IMPLEMENTED"
        self.revisions[(eco.part, eco.from_rev)].status = "SUPERSEDED"
        self.alert("ok", eco.id, f"{eco.id} implemented: travelers synchronized to Rev {eco.to_rev}, holds released", "eco")

    def _reallocate(self, wo: WorkOrder, rev: DesignRevision) -> None:
        for comp in list(wo.allocated):
            if comp not in rev.ebom:
                del wo.allocated[comp]
        for comp in rev.ebom:
            if comp not in wo.allocated:
                lot = self._pick_lot(comp)
                if lot:
                    wo.allocated[comp] = lot.id
                    lot.qty -= 1

    def _pick_lot(self, comp: str, exclude: str | None = None) -> MaterialLot | None:
        cands = [l for l in self.lots.values() if l.component == comp and l.qualified and l.cert_id and l.qty > 0 and l.id != exclude]
        cands.sort(key=lambda l: (l.eta_day or 0, l.id))
        return cands[0] if cands else None

    def _release_holds(self, wo: WorkOrder, source: str) -> None:
        for h in self.holds.values():
            if h.wo_id == wo.id and h.source == source and h.status == "OPEN":
                h.status = "RELEASED"
                self.log(SYSTEM, "HOLD_RELEASED", h.id, f"{wo.serial} ({source})")
        if not any(h.wo_id == wo.id and h.status == "OPEN" for h in self.holds.values()):
            if wo.status == "HOLD":
                wo.status = "ACTIVE" if wo.as_built or wo.rework_seqs else "PLANNED"

    # -------------------------------------------------------------- dispatch
    def dispatch(self, wo_id: str, person: Person, measured: float | None = None,
                 scanned: dict[str, str] | None = None) -> OpRecord:
        """Perform the next operation on a work order (shop-floor execution)."""
        wo = self.work_orders[wo_id]
        reasons = [f"open hold {h.id} ({h.reason})" for h in self.holds.values() if h.wo_id == wo_id and h.status == "OPEN"]
        eff = self.wo_effective_rev(wo)
        if wo.traveler_rev != eff:
            reasons.append(f"traveler Rev {wo.traveler_rev} is stale; released Rev {eff} applies to {wo.serial}")
        if reasons:
            msg = f"{wo_id} blocked: " + "; ".join(reasons)
            self.log(person, "DISPATCH_BLOCKED", wo_id, msg)
            raise DispatchBlocked(msg)
        if wo.next_seq is None:
            raise DispatchBlocked(f"{wo_id} has no remaining operations")
        rev = self.design(wo)
        op = rev.op(wo.next_seq)
        lots = {c: wo.allocated[c] for c in op.consumes if c in wo.allocated}
        for c in op.consumes:
            lot = self.lots.get(lots.get(c, ""))
            if lot and lot.eta_day is not None and lot.eta_day > self.today:
                msg = f"{wo_id} blocked: {c} lot {lot.id} not received (ETA day {lot.eta_day})"
                self.log(person, "DISPATCH_BLOCKED", wo_id, msg)
                raise DispatchBlocked(msg)
        # physical-to-record check: what the operator scanned must match the MBOM allocation
        for comp, lot_id in (scanned or {}).items():
            if lots.get(comp) != lot_id:
                self.place_hold(wo, f"SCAN-{wo.serial}", f"Scanned {comp} lot {lot_id} does not match allocated lot {lots.get(comp)}")
                d = Disposition(self._next_id("DSP"), f"SCAN-{wo.serial}", wo.id, wo.serial, "PHYSICAL_RECORD_MISMATCH",
                                f"{wo.serial} op {op.seq}: physical lot {lot_id} vs record {lots.get(comp)}. Verify part and certs",
                                {"REPLACE_WITH_ALLOCATED_LOT": Role.QUALITY, "REALLOCATE_AFTER_CERT_REVIEW": Role.QUALITY},
                                "REPLACE_WITH_ALLOCATED_LOT")
                d.context = f"{comp}|{lot_id}"
                self.dispositions[d.id] = d
                self.log(SYSTEM, "DISPOSITION_ROUTED", d.id, f"{d.summary} -> {d.required_role.value}")
                self.alert("bad", wo.id, f"Physical-to-record mismatch on {wo.serial}: lot {lot_id} scanned, {lots.get(comp)} on record", "mismatch")
                raise DispatchBlocked(f"{wo_id} held: physical-to-record mismatch")
        rework = wo.next_seq in wo.rework_seqs
        rec = OpRecord(op.seq, op.name, wo.traveler_rev, lots, person.name, self.now(), rework)
        wo.as_built.append(rec)
        if rework:
            wo.rework_seqs.remove(op.seq)
        self.log(person, "OP_COMPLETED", wo_id, f"{wo.serial} op {op.seq} {op.name} at Rev {wo.traveler_rev}" + (" (rework)" if rework else ""))
        if wo.status == "PLANNED":
            wo.status = "ACTIVE"
        if op.characteristic:
            ch = rev.characteristics[op.characteristic]
            self.record_inspection(wo, op.seq, ch.name, ch.nominal if measured is None else measured, person)
        if wo.status != "HOLD":
            wo.next_seq = self._next_open_op(wo, op.seq)
            if wo.next_seq is None:
                wo.status = "COMPLETE"
        return rec

    def _next_open_op(self, wo: WorkOrder, after: int) -> int | None:
        done = {r.seq for r in wo.as_built}
        for o in self.design(wo).routing:
            if o.seq > after and (o.seq not in done or o.seq in wo.rework_seqs):
                return o.seq
        return None

    # ------------------------------------------------------------ inspection
    def record_inspection(self, wo: WorkOrder, seq: int, char: str, value: float, person: Person) -> Inspection:
        ch = self.design(wo).characteristics[char]
        ok = ch.lo <= value <= ch.hi
        insp = Inspection(self._next_id("INSP"), wo.serial, seq, char, value, ch.lo, ch.hi, ch.unit, wo.traveler_rev, ok, self.now(), person.name)
        self.inspections[insp.id] = insp
        self.log(person, "INSPECTION_RECORDED", insp.id, f"{wo.serial} {char} = {value} {ch.unit} [{ch.lo}..{ch.hi}] {'PASS' if ok else 'FAIL'}")
        if not ok:
            ncr = NCR(self._next_id("NCR"), wo.serial, insp.id, char, f"{char} {value} {ch.unit} outside {ch.lo}..{ch.hi}")
            self.ncrs[ncr.id] = ncr
            self.log(SYSTEM, "NCR_OPENED", ncr.id, ncr.detail)
            self.place_hold(wo, ncr.id, f"Nonconformance {ncr.id}: {ncr.detail}")
            d = Disposition(self._next_id("DSP"), ncr.id, wo.id, wo.serial, "NONCONFORMANCE",
                            f"{wo.serial} op {seq}: {ncr.detail}",
                            {"REWORK": Role.QUALITY, "SCRAP": Role.QUALITY, "USE_AS_IS": Role.DESIGN_AUTHORITY},
                            "REWORK")
            self.dispositions[d.id] = d
            ncr.disposition_id = d.id
            self.log(SYSTEM, "DISPOSITION_ROUTED", d.id, f"MRB: {d.summary} -> Quality (use-as-is requires Design Authority)")
            self.alert("bad", ncr.id, f"{ncr.id} opened on {wo.serial}: {ncr.detail}", "ncr")
        else:
            for n in self.ncrs.values():
                if n.serial == wo.serial and n.characteristic == char and n.status == "DISPOSITIONED":
                    n.status = "CLOSED"
                    self.log(SYSTEM, "NCR_CLOSED", n.id, f"Re-inspection {insp.id} passed")
                    self.alert("ok", n.id, f"{n.id} closed: re-inspection passed on {wo.serial}", "ncr")
        return insp

    def _apply_ncr(self, ncr: NCR, d: Disposition) -> None:
        wo = self.work_orders[d.wo_id]
        insp = self.inspections[ncr.inspection_id]
        ncr.status = "DISPOSITIONED"
        if d.decision == "REWORK":
            wo.rework_seqs = [insp.seq]
            wo.next_seq = insp.seq
        elif d.decision == "USE_AS_IS":
            wo.deviations.append(f"{self._next_id('DEV')}: {ncr.detail} accepted ({d.decided_by})")
            ncr.status = "CLOSED"
            wo.next_seq = self._next_open_op(wo, insp.seq)
        elif d.decision == "SCRAP":
            wo.next_seq = None
            wo.status = "SCRAPPED"
            ncr.status = "CLOSED"
        self._release_holds(wo, ncr.id)

    def _apply_record(self, d: Disposition) -> None:
        wo = self.work_orders[d.wo_id] if d.wo_id else None
        if d.impact == "PHYSICAL_RECORD_MISMATCH" and wo:
            comp, lot_id = d.context.split("|")
            if d.decision == "REALLOCATE_AFTER_CERT_REVIEW":
                wo.allocated[comp] = lot_id
            self._release_holds(wo, d.source)
        elif d.impact == "SUPPLY_DELAY":
            lot_id, alt = d.context.split("|")
            if d.decision == "REALLOCATE_ALTERNATE_LOT" and alt:
                for w in self.work_orders.values():
                    if w.allocated.get(self.lots[lot_id].component) == lot_id and self._needs(w, self.lots[lot_id].component):
                        w.allocated[self.lots[lot_id].component] = alt
                        self.lots[alt].qty -= 1
                        self.log(SYSTEM, "MATERIAL_REALLOCATED", w.id, f"{w.serial}: {lot_id} -> {alt}")
            self.alert("ok", lot_id, f"Supply plan updated: {d.decision.replace('_', ' ').lower()}", "supplier")

    # ----------------------------------------------------------- supply
    def _needs(self, wo: WorkOrder, comp: str) -> bool:
        if wo.status in ("COMPLETE", "ACCEPTED", "SCRAPPED") or wo.next_seq is None:
            return False
        rev = self.design(wo)
        return any(comp in o.consumes for o in rev.routing if o.seq >= wo.next_seq)

    def supplier_delay(self, lot_id: str, days: int) -> Disposition:
        lot = self.lots[lot_id]
        old = lot.eta_day or self.today
        lot.eta_day = old + days
        sup = self.suppliers[lot.supplier_id]
        self.log(SYSTEM, "SUPPLY_SLIP", lot_id, f"{sup.name}: {lot.component} lot ETA day {old} -> day {lot.eta_day}")
        hit = [w for w in self.work_orders.values() if w.allocated.get(lot.component) == lot_id and self._needs(w, lot.component)]
        late = [w for w in hit if lot.eta_day > w.need_day - 2]
        alt = self._pick_lot(lot.component, exclude=lot_id)
        alt_ok = alt is not None and (alt.eta_day or 0) <= self.today and alt.qty >= len(late)
        opts = {"ACCEPT_SLIP": Role.PROGRAM, "RESEQUENCE_WORK": Role.PROGRAM}
        if alt_ok:
            opts = {"REALLOCATE_ALTERNATE_LOT": Role.PROGRAM, **opts}
        d = Disposition(self._next_id("DSP"), lot_id, None, None, "SUPPLY_DELAY",
                        f"{lot.component} lot {lot_id} ({sup.name}) slips {days} days; {len(late)} of {len(hit)} work orders miss need date"
                        + (f"; qualified alternate {alt.id} ({self.suppliers[alt.supplier_id].name}, cert {alt.cert_id}) on hand x{alt.qty}" if alt_ok else ""),
                        opts, next(iter(opts)))
        d.context = f"{lot_id}|{alt.id if alt_ok else ''}"
        self.dispositions[d.id] = d
        self.log(SYSTEM, "DISPOSITION_ROUTED", d.id, f"{d.summary} -> Program")
        self.alert("warn", lot_id, f"Supplier slip: {lot_id} +{days} days, {len(late)} work orders at risk", "supplier")
        self._last_supply = {"lot": lot_id, "impacted": [w.serial for w in hit], "late": [w.serial for w in late], "alternate": alt.id if alt_ok else None}
        return d

    # --------------------------------------------------------------- trace
    def trace(self, serial: str) -> dict:
        """Walk the thread for one serial and flag every gap."""
        wo = self.wo_for(serial)
        eff = self.wo_effective_rev(wo)
        rev = self.design(wo)
        eco = next((e for e in self.ecos.values() if e.part == wo.part and e.status.startswith("RELEASED")), None)
        findings: list[dict] = []

        def flag(code: str, text: str, blocking: bool = True) -> None:
            findings.append({"code": code, "text": text, "blocking": blocking})

        reqs = [asdict(self.requirements[r]) for r in rev.requirements]
        lots = []
        for comp, lot_id in sorted(wo.allocated.items()):
            lot = self.lots[lot_id]
            lots.append({"component": comp, "lot": lot.id, "supplier": self.suppliers[lot.supplier_id].name,
                         "cert": lot.cert_id, "qualified": lot.qualified, "eta_day": lot.eta_day})
        consumed = {lid for r in wo.as_built for lid in r.lots.values()}
        for lid in sorted(consumed):
            lot = self.lots[lid]
            if not lot.cert_id:
                flag("MISSING_CERT", f"Lot {lid} ({lot.component}) consumed without a material certification")
            if not lot.qualified:
                flag("UNQUALIFIED_LOT", f"Lot {lid} from an unqualified source")
        latest: dict[int, OpRecord] = {}
        for r in wo.as_built:
            latest[r.seq] = r
        for seq, r in sorted(latest.items()):
            if r.rev != eff and eco and seq in eco.affected_ops and not wo.deviations and seq not in wo.rework_seqs:
                flag("REV_MISMATCH", f"Op {seq} as-built Rev {r.rev}, released Rev {eff} applies")
            elif r.rev != eff and eco and seq in eco.affected_ops and seq in wo.rework_seqs:
                flag("REWORK_PENDING", f"Op {seq} built at Rev {r.rev}; rework to Rev {eff} authorised, not yet done")
        if wo.status not in ("ACCEPTED",) and wo.traveler_rev != eff:
            flag("STALE_TRAVELER", f"Traveler Rev {wo.traveler_rev}; released Rev {eff} applies")
        insp = [i for i in self.inspections.values() if i.serial == serial]
        last_by_char: dict[str, Inspection] = {}
        for i in insp:
            last_by_char[i.characteristic] = i
        for c, i in last_by_char.items():
            if not i.passed:
                ncr = next((n for n in self.ncrs.values() if n.inspection_id == i.id), None)
                if not ncr or ncr.status == "OPEN":
                    flag("FAILED_INSPECTION_NO_DISPOSITION", f"{c} failed ({i.value} {i.unit}); no approved disposition")
                elif ncr.status == "DISPOSITIONED":
                    flag("NCR_OPEN", f"{ncr.id} dispositioned, re-inspection outstanding")
        for h in self.holds.values():
            if h.wo_id == wo.id and h.status == "OPEN":
                flag("OPEN_HOLD", f"{h.id}: {h.reason}")
        verification = []
        for r in rev.requirements:
            req = self.requirements[r]
            if req.verified_by.startswith("cert:"):
                comp = req.verified_by[5:]
                lot = self.lots.get(wo.allocated.get(comp, ""))
                used = any(comp in rec.lots for rec in wo.as_built)
                ok = bool(used and lot and lot.cert_id)
                evidence = f"{lot.id} / {lot.cert_id or 'NO CERT'}" if used and lot else "not yet consumed"
            else:
                i = last_by_char.get(req.verified_by)
                ok = bool(i and i.passed)
                evidence = f"{i.id} {i.value} {i.unit} {'PASS' if i.passed else 'FAIL'}" if i else "no evidence yet"
            verification.append({"req": r, "text": req.text, "verified": ok, "evidence": evidence})
        for v in verification:
            if not v["verified"]:
                flag("REQUIREMENT_UNVERIFIED", f"{v['req']} not yet verified ({v['evidence']})", blocking=True)
        if wo.status not in ("COMPLETE", "ACCEPTED"):
            flag("NOT_COMPLETE", f"Work order {wo.status.lower()}, next op {wo.next_seq}")
        blocking = [f for f in findings if f["blocking"]]
        return {
            "serial": serial, "wo": wo.id, "part": wo.part, "title": rev.title, "status": wo.status,
            "chain": [
                {"stage": "Requirements baseline", "items": [f"{r['id']}: {r['text']}" for r in reqs]},
                {"stage": "Released design + EBOM", "items": [f"{wo.part} Rev {eff} ({self.revisions[(wo.part, eff)].status})" + (f" via {eco.id}" if eco and eff == eco.to_rev else "")] + [f"EBOM: {c} x{q}" for c, q in self.revisions[(wo.part, eff)].ebom.items()]},
                {"stage": "MBOM / routing / traveler", "items": [f"Traveler Rev {wo.traveler_rev}"] + [f"Op {o.seq} {o.name}" for o in rev.routing]},
                {"stage": "Qualified material lots", "items": [f"{l['component']}: {l['lot']} ({l['supplier']}) cert {l['cert'] or 'MISSING'}" for l in lots]},
                {"stage": "As-built genealogy", "items": [f"Op {r.seq} Rev {r.rev}{' rework' if r.rework else ''} | {', '.join(r.lots.values()) or '-'} | {r.by}" for r in wo.as_built] or ["no operations yet"]},
                {"stage": "Inspection / test evidence", "items": [f"{i.id} op {i.seq} {i.characteristic} {i.value} {i.unit} {'PASS' if i.passed else 'FAIL'} (Rev {i.rev})" for i in insp] or ["no evidence yet"]},
                {"stage": "Acceptance package", "items": ["ACCEPTED"] if wo.status == "ACCEPTED" else (["Ready for acceptance"] if not blocking else [f"Blocked: {len(blocking)} open gap(s)"])},
            ],
            "verification": verification,
            "deviations": wo.deviations,
            "findings": findings,
            "acceptable": not blocking and wo.status in ("COMPLETE", "ACCEPTED"),
        }

    def accept(self, serial: str, person: Person) -> WorkOrder:
        self._require(person, Role.QUALITY, f"Accept {serial}")
        t = self.trace(serial)
        wo = self.wo_for(serial)
        if wo.status == "ACCEPTED":
            raise AcceptanceBlocked(f"{serial} already accepted")
        blocking = [f for f in t["findings"] if f["blocking"]]
        if blocking:
            msg = f"{serial} acceptance blocked: " + "; ".join(f["code"] for f in blocking)
            self.log(person, "ACCEPTANCE_BLOCKED", serial, msg)
            raise AcceptanceBlocked(msg)
        wo.status = "ACCEPTED"
        self.log(person, "UNIT_ACCEPTED", serial, f"Acceptance package complete, Rev {wo.traveler_rev}")
        return wo

    # ------------------------------------------------------------ dashboard
    def kpis(self) -> dict:
        wos = list(self.work_orders.values())
        active = [w for w in wos if w.status in ("ACTIVE", "HOLD", "PLANNED")]
        links = ok = 0
        for w in wos:
            latest = {r.seq: r for r in w.as_built}
            for r in latest.values():
                links += 1
                eco = next((e for e in self.ecos.values() if e.part == w.part and e.status.startswith("RELEASED")), None)
                eff = self.wo_effective_rev(w)
                ok += not (eco and r.seq in eco.affected_ops and r.rev != eff and not w.deviations)
                for lid in r.lots.values():
                    links += 1
                    ok += bool(self.lots[lid].cert_id)
        failed_serials = {i.serial for i in self.inspections.values() if not i.passed}
        inspected = {i.serial for i in self.inspections.values()}
        return {
            "active_wos": len(active),
            "on_hold": sum(1 for w in wos if w.status == "HOLD"),
            "open_ecos": sum(1 for e in self.ecos.values() if not e.status.endswith("IMPLEMENTED")),
            "open_ncrs": sum(1 for n in self.ncrs.values() if n.status != "CLOSED"),
            "trace_coverage": round(100 * ok / links, 1) if links else 100.0,
            "fpy": round(100 * (len(inspected) - len(failed_serials)) / len(inspected), 1) if inspected else 100.0,
            "accepted": sum(1 for w in wos if w.status == "ACCEPTED"),
            "pending_decisions": sum(1 for d in self.dispositions.values() if d.status == "PENDING"),
        }

    def build_chain(self) -> list[dict]:
        wos = list(self.work_orders.values())
        k = self.kpis()
        late_lots = [l for l in self.lots.values() if l.eta_day is not None and any(w.allocated.get(l.component) == l.id and self._needs(w, l.component) and l.eta_day > w.need_day - 2 for w in wos)]
        no_cert = [l for l in self.lots.values() if not l.cert_id]
        blocked_accept = [w for w in wos if w.status == "COMPLETE" and not self.trace(w.serial)["acceptable"]]
        ready = [w for w in wos if w.status == "COMPLETE" and self.trace(w.serial)["acceptable"]]
        failed = [i for i in self.inspections.values() if not i.passed]
        def st(bad: bool, warn: bool) -> str:
            return "bad" if bad else ("warn" if warn else "ok")
        return [
            {"stage": "Define", "cap": "Traceability", "status": "ok", "detail": f"{len(self.requirements)} requirements baselined"},
            {"stage": "Design", "cap": "Configuration", "status": st(False, k["open_ecos"] > 0), "detail": f"{len(self.released_revs_all())} released revs, {k['open_ecos']} open ECO"},
            {"stage": "Source", "cap": "Readiness", "status": st(bool(late_lots), bool(no_cert)), "detail": f"{len(self.lots)} lots, {len(late_lots)} late, {len(no_cert)} missing cert"},
            {"stage": "Assemble", "cap": "Execution", "status": st(False, k["on_hold"] > 0), "detail": f"{k['active_wos']} active WOs, {k['on_hold']} on hold"},
            {"stage": "Test", "cap": "Assurance", "status": st(k["open_ncrs"] > 0, False), "detail": f"{len(self.inspections)} inspections, {len(failed)} failed, {k['open_ncrs']} open NCR"},
            {"stage": "Deploy", "cap": "Acceptance", "status": st(False, bool(blocked_accept)), "detail": f"{k['accepted']} accepted, {len(ready)} ready, {len(blocked_accept)} blocked"},
        ]

    def released_revs_all(self) -> list[DesignRevision]:
        return [r for r in self.revisions.values() if r.status == "RELEASED"]

    def snapshot(self) -> dict:
        wos = []
        for w in sorted(self.work_orders.values(), key=lambda w: w.id):
            eff = self.wo_effective_rev(w)
            t = self.trace(w.serial)
            wos.append({
                "id": w.id, "serial": w.serial, "part": w.part, "title": self.design(w).title,
                "traveler_rev": w.traveler_rev, "effective_rev": eff, "status": w.status,
                "next_op": (f"{w.next_seq} {self.design(w).op(w.next_seq).name}" if w.next_seq else "-"),
                "need": (BASE_TIME + timedelta(days=w.need_day)).strftime("%b %d"),
                "gaps": len([f for f in t["findings"] if f["blocking"] and f["code"] not in ("NOT_COMPLETE", "REQUIREMENT_UNVERIFIED")]),
                "acceptable": t["acceptable"],
            })
        return {
            "kpis": self.kpis(),
            "chain": self.build_chain(),
            "work_orders": wos,
            "ecos": [asdict(e) for e in self.ecos.values()],
            "ncrs": [asdict(n) for n in self.ncrs.values()],
            "holds": [asdict(h) for h in self.holds.values()],
            "dispositions": [{**asdict(d), "options": {k: v.value for k, v in d.options.items()}, "required_role": d.required_role.value} for d in self.dispositions.values()],
            "lots": [{**asdict(l), "supplier": self.suppliers[l.supplier_id].name} for l in self.lots.values()],
            "alerts": self.alerts[:12],
            "audit": [asdict(a) for a in self.audit],
            "traces": {w.serial: self.trace(w.serial) for w in self.work_orders.values()},
        }
