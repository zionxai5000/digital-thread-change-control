"""Domain model for the digital thread.

Plain dataclasses, no external dependencies. Every record carries the
identifiers needed to walk the thread from requirement to acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    DESIGN_AUTHORITY = "Design Authority"
    QUALITY = "Quality"
    MFG_ENG = "Manufacturing Engineering"
    PROGRAM = "Program"
    OPERATOR = "Production"
    SYSTEM = "System"


@dataclass(frozen=True)
class Person:
    name: str
    role: Role


@dataclass
class Requirement:
    id: str
    text: str
    verified_by: str  # characteristic name, or "cert:<component>" for material evidence


@dataclass
class Characteristic:
    name: str
    nominal: float
    lo: float
    hi: float
    unit: str


@dataclass
class Operation:
    seq: int
    name: str
    consumes: list[str] = field(default_factory=list)  # component part numbers
    characteristic: str | None = None


@dataclass
class DesignRevision:
    part: str
    rev: str
    status: str  # RELEASED | PENDING | SUPERSEDED
    title: str
    requirements: list[str]
    ebom: dict[str, int]  # component -> qty
    routing: list[Operation]  # MBOM / routing / traveler template
    characteristics: dict[str, Characteristic]

    def op(self, seq: int) -> Operation:
        return next(o for o in self.routing if o.seq == seq)


@dataclass
class Supplier:
    id: str
    name: str
    qualified: bool = True


@dataclass
class MaterialLot:
    id: str
    component: str
    supplier_id: str
    cert_id: str | None
    qty: int
    qualified: bool = True
    eta_day: int | None = None  # None = on hand; otherwise day it arrives


@dataclass
class OpRecord:
    seq: int
    name: str
    rev: str
    lots: dict[str, str]
    by: str
    at: str
    rework: bool = False


@dataclass
class Inspection:
    id: str
    serial: str
    seq: int
    characteristic: str
    value: float
    lo: float
    hi: float
    unit: str
    rev: str
    passed: bool
    at: str
    by: str


@dataclass
class WorkOrder:
    id: str
    serial: str
    part: str
    traveler_rev: str
    planned_rev: str
    next_seq: int | None  # None when all operations are complete
    status: str  # PLANNED | ACTIVE | HOLD | COMPLETE | ACCEPTED
    need_day: int
    allocated: dict[str, str]  # component -> lot id
    as_built: list[OpRecord] = field(default_factory=list)
    deviations: list[str] = field(default_factory=list)
    rework_seqs: list[int] = field(default_factory=list)


@dataclass
class ECO:
    id: str
    part: str
    from_rev: str
    to_rev: str
    title: str
    reason: str
    affected_ops: list[int]
    effectivity_from: int  # first serial number (numeric suffix) the new rev applies to
    status: str = "APPROVED - PENDING RELEASE"
    released_by: str | None = None


@dataclass
class Hold:
    id: str
    wo_id: str
    source: str  # ECO / NCR / record id
    reason: str
    status: str = "OPEN"


@dataclass
class Disposition:
    id: str
    source: str
    wo_id: str | None
    serial: str | None
    impact: str
    summary: str
    options: dict[str, Role]  # option -> authority allowed to choose it
    recommended: str
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED
    decision: str | None = None
    decided_by: str | None = None
    note: str = ""
    context: str = ""  # machine-readable payload set by the engine

    @property
    def required_role(self) -> Role:
        return self.options[self.recommended]


@dataclass
class NCR:
    id: str
    serial: str
    inspection_id: str
    characteristic: str
    detail: str
    status: str = "OPEN"  # OPEN | DISPOSITIONED | CLOSED
    disposition_id: str | None = None


@dataclass
class AuditEvent:
    seq: int
    at: str
    actor: str
    role: str
    action: str
    ref: str
    detail: str
