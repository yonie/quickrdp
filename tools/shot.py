#!/usr/bin/env python3
"""Screenshot of the QuickRDP window with invented hosts.

Usage: python3 tools/shot.py [out.png] [width] [height]
"""
import json
import os
import sys
import tempfile

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, root)

cfg = os.path.join(tempfile.mkdtemp(), "hosts.json")
with open(cfg, "w") as f:
    json.dump({
        "last": "workstation",
        "hosts": {
            "workstation": {"address": "10.0.0.12", "user": "ronald", "domain": "", "args": ""},
            "gaming-pc": {"address": "10.0.0.20", "user": "ronald", "domain": "", "args": ""},
            "office": {"address": "rdp.example.com", "user": "r.klarenbeek", "domain": "CORP", "args": ""},
        },
    }, f)
os.environ["QUICKRDP_CONFIG"] = cfg

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import quickrdp  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "screenshot.png"
W = int(sys.argv[2]) if len(sys.argv) > 2 else 560
H = int(sys.argv[3]) if len(sys.argv) > 3 else 380

app = quickrdp.QuickRDP()
child = app.window.get_child()
app.window.remove(child)
app.window.hide()
off = Gtk.OffscreenWindow()
off.add(child)
off.set_size_request(W, H)
off.show_all()
app.entry.grab_focus()


def take():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    pb = off.get_pixbuf()
    if pb is None:
        print("[shot] FAILED (no pixbuf)")
    else:
        pb.savev(out, "png", [], [])
        print(f"[shot] saved {out} ({pb.get_width()}x{pb.get_height()})")
    Gtk.main_quit()
    return False


GLib.timeout_add(800, take)
Gtk.main()
