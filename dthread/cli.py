"""Command line: `python -m dthread demo|trace|build-site|serve`."""

from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import tempfile
import textwrap
import webbrowser
from pathlib import Path

from . import data, scenarios, site

_TTY = sys.stdout.isatty()
BOLD, DIM, RED, GRN, YEL, CYN, RST = (("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m")
                                      if _TTY else ("",) * 7)


def _kpis(k: dict) -> str:
    return (f"active {k['active_wos']} | hold {k['on_hold']} | ECO {k['open_ecos']} | NCR {k['open_ncrs']} | "
            f"trace {k['trace_coverage']}% | FPY {k['fpy']}% | accepted {k['accepted']} | decisions {k['pending_decisions']}")


def demo(name: str) -> int:
    keys = list(scenarios.ALL) if name == "all" else [name]
    for key in keys:
        sc = scenarios.ALL[key]()
        print(f"\n{BOLD}{sc['title']}{RST}\n{DIM}{textwrap.fill(sc['intro'], 100)}{RST}")
        print(f"{DIM}baseline: {_kpis(sc['baseline']['kpis'])}{RST}\n")
        for i, st in enumerate(sc["steps"], 1):
            tag = f"{GRN}OK     {RST}" if st["outcome"] == "ok" else f"{YEL}REFUSED{RST}"
            print(f"{BOLD}{i:>2}. {st['label']}{RST}  {CYN}{st['who']}{RST}")
            print(textwrap.indent(textwrap.fill(st["narrative"], 96), "     "))
            for line in st["message"].split(" | "):
                print(f"     {tag} {line}")
            print(f"     {DIM}{_kpis(st['snapshot']['kpis'])}{RST}")
        print()
    return 0


def trace(serial: str) -> int:
    t = data.build().trace(serial)
    print(f"{BOLD}{t['serial']}{RST} {t['title']} ({t['part']}, {t['wo']}) status {t['status']}")
    for node in t["chain"]:
        print(f"  {CYN}{node['stage']}{RST}")
        for item in node["items"]:
            print(f"    - {item}")
    for f in t["findings"]:
        print(f"  {RED if f['blocking'] else DIM}{f['code']}{RST}: {f['text']}")
    print(f"  acceptable: {GRN if t['acceptable'] else RED}{t['acceptable']}{RST}")
    return 0


def serve(port: int, open_browser: bool) -> int:
    out = Path(tempfile.mkdtemp(prefix="dthread-"))
    site.build(out)
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(out), **kw)  # noqa: E731
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"Digital Thread Control dashboard at {url}  (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dthread", description="Digital thread change control reference implementation")
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("demo", help="print a scenario step by step")
    d.add_argument("scenario", nargs="?", default="eco", choices=[*scenarios.ALL, "all"])
    t = sub.add_parser("trace", help="print the digital thread for a serial (baseline data)")
    t.add_argument("serial", nargs="?", default="CP105-0022")
    b = sub.add_parser("build-site", help="write the static dashboard")
    b.add_argument("--out", default="docs")
    s = sub.add_parser("serve", help="serve the dashboard locally")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--open", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "trace":
        return trace(a.serial)
    if a.cmd == "build-site":
        print(site.build(a.out))
        return 0
    if a.cmd == "serve":
        return serve(a.port, a.open)
    return demo(getattr(a, "scenario", "eco"))
