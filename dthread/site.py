"""Builds the single-page dashboard (static, no external assets)."""

from __future__ import annotations

import json
from pathlib import Path

from . import data, scenarios

TEMPLATE = Path(__file__).parent / "web" / "dashboard.html"


def payload() -> dict:
    base = data.build().snapshot()
    scen = {}
    for key, fn in scenarios.ALL.items():
        s = fn()
        s.pop("baseline", None)
        scen[key] = s
    return {"baseline": base, "scenarios": scen}


def render() -> str:
    blob = json.dumps(payload(), separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("/*__DATA__*/null", blob)


def build(out: str | Path = "docs") -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(render())
    (out / ".nojekyll").write_text("")
    return path
