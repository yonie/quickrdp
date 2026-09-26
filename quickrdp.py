#!/usr/bin/env python3
"""QuickRDP: a minimal GNOME/Linux launcher for sdl-freerdp.

Opens with the last host prefilled. Enter connects. That is the whole idea.
"""
import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Secret", "1")
from gi.repository import Gdk, GLib, Gtk, Secret  # noqa: E402

VERSION = "0.1.0"
APP_NAME = "QuickRDP"
APP_ID = "quickrdp"  # matches quickrdp.desktop, see AGENTS.md
CONFIG = Path(os.environ.get("QUICKRDP_CONFIG") or GLib.get_user_config_dir() + "/quickrdp/hosts.json")
LOGDIR = Path(GLib.get_user_cache_dir()) / "quickrdp"
DEFAULT_ARGS = "/dynamic-resolution /gfx:AVC444 /network:lan +clipboard +auto-reconnect"
SCHEMA = Secret.Schema.new(
    "org.yonie.quickrdp", Secret.SchemaFlags.NONE, {"host": Secret.SchemaAttributeType.STRING}
)


# ---------------------------------------------------------------- config

def load_config():
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("hosts", {})
    cfg.setdefault("last", "")
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def resolve_host(cfg, text):
    """A saved host by name or address, or None."""
    if text in cfg["hosts"]:
        return text
    for name, host in cfg["hosts"].items():
        if host.get("address", "").lower() == text.lower():
            return name
    return None


def password_get(name):
    try:
        return Secret.password_lookup_sync(SCHEMA, {"host": name}, None)
    except GLib.Error:
        return None


def password_set(name, pw):
    try:
        if pw:
            Secret.password_store_sync(
                SCHEMA, {"host": name}, Secret.COLLECTION_DEFAULT,
                f"{APP_NAME}: {name}", pw, None)
        else:
            Secret.password_clear_sync(SCHEMA, {"host": name}, None)
    except GLib.Error as e:
        print(f"keyring: {e}", file=sys.stderr)


# ---------------------------------------------------------------- launching

def build_argv(host):
    argv = ["sdl-freerdp", f"/v:{host['address']}"]
    if host.get("user"):
        argv.append(f"/u:{host['user']}")
    if host.get("domain"):
        argv.append(f"/d:{host['domain']}")
    argv += shlex.split(host.get("args") or DEFAULT_ARGS)
    return argv


def launch(name, host, on_exit=None):
    """Spawn sdl-freerdp detached. A stored password goes in over stdin."""
    argv = build_argv(host)
    pw = password_get(name) if name else None
    if pw:
        argv.append("/from-stdin")
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = LOGDIR / f"{name or 'adhoc'}.log"
    # The session window announces our app id so GNOME files it under QuickRDP
    # and remembers the "allow inhibiting shortcuts" answer.
    env = dict(os.environ, SDL_APP_ID=APP_ID)
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if pw else subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )
    if pw:
        proc.stdin.write((pw + "\n").encode())
        proc.stdin.close()
    if on_exit:
        def wait():
            code = proc.wait()
            GLib.idle_add(on_exit, name, code, log)
        threading.Thread(target=wait, daemon=True).start()
    return proc


def explain_exit(code, log):
    try:
        text = Path(log).read_text(errors="replace")
    except OSError:
        text = ""
    if "LOGON_FAILURE" in text:
        return "logon failed, check the password"
    for token in ("ERRCONNECT_", "ERRINFO_"):
        i = text.rfind(token)
        if i >= 0:
            return text[i:].split()[0].strip("[]:,")
    return f"sdl-freerdp exited with {code}"


# ---------------------------------------------------------------- app

class QuickRDP:
    def __init__(self):
        self.cfg = load_config()

        self.window = Gtk.Window(title=APP_NAME)
        self.window.set_default_size(560, 380)
        self.window.connect("destroy", Gtk.main_quit)
        self.window.connect("key-press-event", self.on_key_press)

        connect_btn = Gtk.Button(label="🖥️ Connect")
        connect_btn.connect("clicked", lambda *_: self.connect_entry())
        add_btn = Gtk.Button(label="➕ Add")
        add_btn.connect("clicked", lambda *_: self.edit_host(None))
        edit_btn = Gtk.Button(label="✏️ Edit")
        edit_btn.connect("clicked", lambda *_: self.edit_host(self.selected_name()))
        del_btn = Gtk.Button(label="🗑️ Delete")
        del_btn.connect("clicked", lambda *_: self.delete_host(self.selected_name()))
        help_btn = Gtk.Button(label="❓ Help")
        help_btn.connect("clicked", self.show_help)
        note_label = Gtk.Label(label="(Enter connects)")

        hbox = Gtk.Box(spacing=6)
        hbox.pack_start(connect_btn, False, False, 0)
        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        hbox.pack_start(add_btn, False, False, 0)
        hbox.pack_start(edit_btn, False, False, 0)
        hbox.pack_start(del_btn, False, False, 0)
        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        hbox.pack_start(help_btn, False, False, 0)
        hbox.pack_start(note_label, False, False, 6)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Host name or address")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "go-next-symbolic")
        self.entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Connect")
        self.entry.connect("activate", lambda *_: self.connect_entry())
        self.entry.connect("icon-press", lambda *_: self.connect_entry())

        self.store = Gtk.ListStore(str, str, str)  # name, address, user
        self.tree = Gtk.TreeView(model=self.store)
        for i, title in enumerate(("Name", "Address", "User")):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_expand(i == 1)
            self.tree.append_column(col)
        self.tree.connect("row-activated", lambda *_: self.connect_host(self.selected_name()))
        self.tree.get_selection().connect("changed", self.on_selection)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.tree)

        self.toast_label = Gtk.Label()
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .toast {
                background-color: rgba(0, 0, 0, 0.8);
                color: white;
                padding: 10px 20px;
                border-radius: 5px;
            }
        """
        )
        self.toast_label.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.toast_label.get_style_context().add_class("toast")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.END)
        self.toast_label.set_margin_bottom(16)
        self.toast_label.set_no_show_all(True)
        self._toast_timer_id = None

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(vbox, f"set_margin_{side}")(6)
        vbox.pack_start(hbox, False, False, 0)
        vbox.pack_start(self.entry, False, False, 0)
        vbox.pack_start(scrolled, True, True, 0)

        overlay = Gtk.Overlay()
        overlay.add(vbox)
        overlay.add_overlay(self.toast_label)
        self.window.add(overlay)

        self.refresh()
        self.entry.set_text(self.cfg["last"])
        self.window.show_all()
        self.entry.grab_focus()  # selects the prefilled text

    # ------------------------------------------------------------ list

    def refresh(self):
        self.store.clear()
        for name in sorted(self.cfg["hosts"], key=str.lower):
            h = self.cfg["hosts"][name]
            self.store.append([name, h.get("address", ""), h.get("user", "")])

    def selected_name(self):
        model, it = self.tree.get_selection().get_selected()
        return model[it][0] if it else None

    def select_name(self, name):
        for row in self.store:
            if row[0] == name:
                self.tree.get_selection().select_iter(row.iter)
                return

    def on_selection(self, selection):
        name = self.selected_name()
        if name:
            self.entry.set_text(name)

    # ------------------------------------------------------------ actions

    def connect_entry(self):
        text = self.entry.get_text().strip()
        if not text:
            self.show_toast("Type a host first")
            return
        name = resolve_host(self.cfg, text)
        if name:
            self.connect_host(name)
        else:
            # Ad hoc: sdl-freerdp asks for credentials itself.
            launch("", {"address": text, "args": DEFAULT_ARGS}, self.on_exit)
            self.show_toast(f"Connecting to {text}")

    def connect_host(self, name):
        if not name:
            return
        host = self.cfg["hosts"][name]
        self.cfg["last"] = name
        save_config(self.cfg)
        self.entry.set_text(name)
        if host.get("user") and not password_get(name):
            pw = self.ask_password(name, host)
            if not pw:
                return
            password_set(name, pw)
        launch(name, host, self.on_exit)
        self.show_toast(f"Connecting to {name}")

    def on_exit(self, name, code, log):
        label = name or "session"
        if code == 0:
            self.show_toast(f"{label}: disconnected")
        else:
            self.show_toast(f"{label}: {explain_exit(code, log)}", 6000)
        return False

    def ask_password(self, name, host):
        dialog = Gtk.Dialog(title=f"Password for {host.get('user')}@{name}",
                            parent=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Connect", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(6)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        box.add(Gtk.Label(label="Stored in the keyring for next time.", xalign=0))
        entry = Gtk.Entry(visibility=False, activates_default=True)
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        pw = entry.get_text()
        dialog.destroy()
        return pw if response == Gtk.ResponseType.OK else None

    def edit_host(self, name):
        host = self.cfg["hosts"].get(name, {}) if name else {}
        dialog = Gtk.Dialog(title="Edit host" if name else "Add host",
                            parent=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(grid, f"set_margin_{side}")(12)
        fields = {}
        rows = [
            ("address", "Address", host.get("address", ""), True),
            ("user", "User", host.get("user", ""), True),
            ("password", "Password", password_get(name) or "" if name else "", False),
            ("domain", "Domain", host.get("domain", ""), True),
            ("name", "Name", name or "", True),
            ("args", "sdl-freerdp options", host.get("args") or DEFAULT_ARGS, True),
        ]
        for i, (key, label, value, visible) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            entry = Gtk.Entry(text=value, visibility=visible, activates_default=True,
                              hexpand=True, width_chars=40)
            grid.attach(entry, 1, i, 1, 1)
            fields[key] = entry
        fields["name"].set_placeholder_text("defaults to the address")
        fields["domain"].set_placeholder_text("optional")
        error = Gtk.Label(xalign=0)
        error.get_style_context().add_class("error")
        error.set_no_show_all(True)
        grid.attach(error, 1, len(rows), 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        fields["address"].grab_focus()

        while dialog.run() == Gtk.ResponseType.OK:
            address = fields["address"].get_text().strip()
            new_name = fields["name"].get_text().strip() or address
            if not address:
                error.set_text("An address is required.")
                error.show()
                fields["address"].grab_focus()
                continue
            hosts = self.cfg["hosts"]
            if name and name != new_name:
                hosts.pop(name, None)
                password_set(name, None)
                if self.cfg["last"] == name:
                    self.cfg["last"] = new_name
            hosts[new_name] = {
                "address": address,
                "user": fields["user"].get_text().strip(),
                "domain": fields["domain"].get_text().strip(),
                "args": fields["args"].get_text().strip() or DEFAULT_ARGS,
            }
            password_set(new_name, fields["password"].get_text())
            save_config(self.cfg)
            self.refresh()
            self.select_name(new_name)
            self.show_toast(f"Saved {new_name}")
            break
        dialog.destroy()

    def delete_host(self, name):
        if not name:
            return
        dialog = Gtk.MessageDialog(parent=self.window, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL,
                                   text=f"Delete {name}?")
        dialog.format_secondary_text("Its stored password is removed from the keyring too.")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.cfg["hosts"].pop(name, None)
        password_set(name, None)
        if self.cfg["last"] == name:
            self.cfg["last"] = ""
            self.entry.set_text("")
        save_config(self.cfg)
        self.refresh()
        self.show_toast(f"Deleted {name}")

    # ------------------------------------------------------------ chrome

    def on_key_press(self, widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if event.keyval == Gdk.KEY_Escape or (ctrl and event.keyval == Gdk.KEY_q):
            Gtk.main_quit()
            return True
        if ctrl and event.keyval == Gdk.KEY_n:
            self.edit_host(None)
            return True
        if event.keyval == Gdk.KEY_F2:
            self.edit_host(self.selected_name())
            return True
        if event.keyval == Gdk.KEY_Delete and self.window.get_focus() is self.tree:
            self.delete_host(self.selected_name())
            return True
        return False

    def show_toast(self, message, duration=2000):
        if self._toast_timer_id is not None:
            GLib.source_remove(self._toast_timer_id)
        self.toast_label.set_text(message)
        self.toast_label.show()
        self._toast_timer_id = GLib.timeout_add(duration, self.hide_toast)

    def hide_toast(self):
        self._toast_timer_id = None
        self.toast_label.hide()
        return False

    def show_help(self, widget):
        dialog = Gtk.Dialog(title="Help", parent=self.window, flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        text = Gtk.Label(
            label=f"""<b>{APP_NAME} v{VERSION}</b>

A minimal launcher for sdl-freerdp (FreeRDP's own client).

<b>How to use:</b>
1. Add a host (address, user, password; the name defaults to the address)
2. Type its name or pick it in the list
3. Enter connects; the session opens in its own window

<b>Shortcuts:</b>
• Enter — Connect
• Ctrl+N — Add host
• F2 — Edit selected host
• Delete — Delete selected host (list focused)
• Esc or Ctrl+Q — Close (sessions keep running)

Inside a session, Right Ctrl+G releases the keyboard grab.

<b>GitHub:</b> github.com/yonie/quickrdp

<b>License:</b> MIT"""
        )
        text.set_use_markup(True)
        for side in ("top", "bottom", "start", "end"):
            getattr(text, f"set_margin_{side}")(15)
        dialog.get_content_area().add(text)
        dialog.show_all()
        dialog.run()
        dialog.destroy()


def main():
    GLib.set_prgname(APP_ID)  # Wayland app id for the launcher window itself
    # `quickrdp.py <name>` connects straight away without a window.
    if len(sys.argv) > 1:
        cfg = load_config()
        name = sys.argv[1]
        host = cfg["hosts"].get(name)
        if host is None:
            host = {"address": name, "args": DEFAULT_ARGS}
            name = ""
        else:
            cfg["last"] = name
            save_config(cfg)
        launch(name, host)
        return
    QuickRDP()
    Gtk.main()


if __name__ == "__main__":
    main()
