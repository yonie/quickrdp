#!/usr/bin/env python3
"""QuickRDP: a minimal GNOME/Linux launcher for sdl-freerdp.

Opens with the last host prefilled. Enter connects. That is the whole idea.
"""
import json
import os
import shlex
import shutil
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
CLIENT = "sdl-freerdp"
CONFIG = Path(os.environ.get("QUICKRDP_CONFIG") or GLib.get_user_config_dir() + "/quickrdp/hosts.json")
LOGDIR = Path(GLib.get_user_cache_dir()) / "quickrdp"
DEFAULT_ARGS = "/dynamic-resolution /gfx:AVC444 /network:lan +clipboard +auto-reconnect"
SCHEMA = Secret.Schema.new(
    "org.yonie.quickrdp", Secret.SchemaFlags.NONE, {"host": Secret.SchemaAttributeType.STRING}
)
ERRORS = {
    "ERRCONNECT_LOGON_FAILURE": "The password was rejected.",
    "ERRCONNECT_CONNECT_TRANSPORT_FAILED": "Could not reach the host.",
    "ERRCONNECT_CONNECT_FAILED": "Could not connect to the host.",
    "ERRCONNECT_CONNECT_CANCELLED": "Connection cancelled.",
    "ERRCONNECT_DNS_NAME_NOT_FOUND": "The host name could not be resolved.",
    "ERRCONNECT_SECURITY_NEGO_CONNECT_FAILED": "Security negotiation failed.",
    "ERRCONNECT_TLS_CONNECT_FAILED": "TLS connection failed.",
    "ERRCONNECT_AUTHENTICATION_FAILED": "Authentication failed.",
}


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
    """A saved host by name or address (case-insensitive), or None."""
    text = text.lower()
    for name in cfg["hosts"]:
        if name.lower() == text:
            return name
    for name, host in cfg["hosts"].items():
        if host.get("address", "").lower() == text:
            return name
    return None


def password_get(name):
    try:
        return Secret.password_lookup_sync(SCHEMA, {"host": name}, None)
    except GLib.Error:
        return None


def password_set(name, pw):
    """Store (non-empty pw) or clear (empty pw). Returns an error string or None."""
    try:
        if pw:
            Secret.password_store_sync(
                SCHEMA, {"host": name}, Secret.COLLECTION_DEFAULT,
                f"{APP_NAME}: {name}", pw, None)
        else:
            Secret.password_clear_sync(SCHEMA, {"host": name}, None)
        return None
    except GLib.Error as e:
        return f"Keyring error: {e.message}"


# ---------------------------------------------------------------- launching

def build_argv(host):
    """May raise ValueError for malformed options (unmatched quote)."""
    argv = [CLIENT, f"/v:{host['address']}"]
    if host.get("user"):
        argv.append(f"/u:{host['user']}")
    if host.get("domain"):
        argv.append(f"/d:{host['domain']}")
    argv += shlex.split(host.get("args") or DEFAULT_ARGS)
    return argv


def launch(name, host, on_exit=None):
    """Spawn sdl-freerdp detached. A stored password goes in over stdin.

    Raises FileNotFoundError when the client is not installed and ValueError
    for malformed options."""
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
    """(token, human message) for a failed session; token is '' when unknown."""
    try:
        text = Path(log).read_text(errors="replace")
    except OSError:
        text = ""
    for token in sorted(ERRORS, key=len, reverse=True):
        if token in text:
            return token, ERRORS[token]
    for prefix in ("ERRCONNECT_", "ERRINFO_"):
        i = text.rfind(prefix)
        if i >= 0:
            token = text[i:].split()[0].strip("[]:,")
            return token, f"Connection failed ({token})."
    return "", f"Connection failed (exit code {code})."


# ---------------------------------------------------------------- app

def password_entry(**kwargs):
    """A masked entry with the usual reveal toggle."""
    entry = Gtk.Entry(visibility=False, activates_default=True, hexpand=True, **kwargs)
    entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
    entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Show password")

    def toggle(e, *_):
        e.set_visibility(not e.get_visibility())
        e.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY,
            "view-conceal-symbolic" if e.get_visibility() else "view-reveal-symbolic")
    entry.connect("icon-press", toggle)
    return entry


def form_dialog(title, parent, ok_label):
    """Modal dialog with a labelled grid, an inline error label and a default OK."""
    dialog = Gtk.Dialog(title=title, parent=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button(ok_label, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=6)
    for side in ("top", "bottom", "start", "end"):
        getattr(grid, f"set_margin_{side}")(12)
    dialog.get_content_area().add(grid)
    error = Gtk.Label(xalign=0)
    error.get_style_context().add_class("error")
    error.set_no_show_all(True)
    return dialog, grid, error


class QuickRDP:
    def __init__(self):
        self.cfg = load_config()
        self.sessions = {}  # name -> Popen

        self.window = Gtk.Window(title=APP_NAME)
        self.window.set_default_size(560, 380)
        self.window.connect("destroy", Gtk.main_quit)
        self.window.connect("key-press-event", self.on_key_press)

        connect_btn = Gtk.Button(label="🖥️ Connect", tooltip_text="Connect (Enter)")
        connect_btn.connect("clicked", lambda *_: self.connect_entry())
        add_btn = Gtk.Button(label="➕ Add", tooltip_text="Add host (Ctrl+N)")
        add_btn.connect("clicked", lambda *_: self.edit_host(None))
        self.edit_btn = Gtk.Button(label="✏️ Edit", tooltip_text="Edit selected host (Ctrl+E)")
        self.edit_btn.connect("clicked", lambda *_: self.edit_host(self.selected_name()))
        self.del_btn = Gtk.Button(label="🗑️ Delete", tooltip_text="Delete selected host (Delete)")
        self.del_btn.connect("clicked", lambda *_: self.delete_host(self.selected_name()))
        help_btn = Gtk.Button(label="❓ Help", tooltip_text="Help (F1)")
        help_btn.connect("clicked", self.show_help)
        note_label = Gtk.Label(label="(Enter connects)")

        hbox = Gtk.Box(spacing=6)
        hbox.pack_start(connect_btn, False, False, 0)
        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        hbox.pack_start(add_btn, False, False, 0)
        hbox.pack_start(self.edit_btn, False, False, 0)
        hbox.pack_start(self.del_btn, False, False, 0)
        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        hbox.pack_start(help_btn, False, False, 0)
        hbox.pack_start(note_label, False, False, 6)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Host name or address, Enter to connect")
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
        self.toast_label.set_line_wrap(True)
        self.toast_label.set_max_width_chars(60)
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
        self.on_selection(None)

        if shutil.which(CLIENT) is None:
            GLib.idle_add(self.show_error, f"{CLIENT} is not installed",
                          "QuickRDP only launches sessions; FreeRDP 3 does the work.\n"
                          "Fedora: sudo dnf install freerdp")

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

    def on_selection(self, _selection):
        name = self.selected_name()
        self.edit_btn.set_sensitive(bool(name))
        self.del_btn.set_sensitive(bool(name))
        if name:
            self.entry.set_text(name)

    # ------------------------------------------------------------ actions

    def connect_entry(self):
        text = self.entry.get_text().strip()
        if not text:
            self.show_toast("Enter a host name or address")
            return
        name = resolve_host(self.cfg, text)
        if name is None:
            # Unknown address: collect credentials here, save, then connect.
            name = self.edit_host(None, address=text)
        if name:
            self.connect_host(name)

    def connect_host(self, name):
        if not name:
            return
        proc = self.sessions.get(name)
        if proc is not None and proc.poll() is None:
            self.show_toast(f"{name} is already open")
            return
        host = self.cfg["hosts"][name]
        self.cfg["last"] = name
        save_config(self.cfg)
        self.entry.set_text(name)
        if not password_get(name):
            creds = self.ask_password(name, host)
            if not creds:
                return
            host["user"], pw = creds
            save_config(self.cfg)
            self.refresh()
            self.select_name(name)
            err = password_set(name, pw)
            if err:
                self.show_toast(err, 6000)
        try:
            self.sessions[name] = launch(name, host, self.on_exit)
        except FileNotFoundError:
            self.show_error(f"{CLIENT} is not installed",
                            "Fedora: sudo dnf install freerdp")
            return
        except ValueError as e:
            self.show_error(f"Bad options for {name}", f"{e}. Edit the host to fix them.")
            return
        self.show_toast(f"Connecting to {name}")

    def on_exit(self, name, code, log):
        self.sessions.pop(name, None)
        if code == 0:
            self.show_toast(f"{name}: disconnected")
            return False
        token, message = explain_exit(code, log)
        if token == "ERRCONNECT_LOGON_FAILURE":
            # Drop the bad password and ask again straight away.
            password_set(name, None)
            self.show_toast(f"{name}: {message}", 4000)
            self.connect_host(name)
        elif token == "ERRCONNECT_CONNECT_CANCELLED":
            self.show_toast(f"{name}: {message}")
        else:
            self.show_error(f"{name}: {message}", f"Details are in {log}")
        return False

    def ask_password(self, name, host):
        """Credentials for a saved host without a stored password.

        Returns (user, password) or None on cancel."""
        dialog, grid, error = form_dialog(f"Connect to {name}", self.window, "Connect")
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<b>Enter your credentials for "
                           f"{GLib.markup_escape_text(host['address'])}</b>")
        grid.attach(heading, 0, 0, 2, 1)
        grid.attach(Gtk.Label(label="User", xalign=1), 0, 1, 1, 1)
        user = Gtk.Entry(text=host.get("user", ""), activates_default=True, hexpand=True,
                         width_chars=32)
        grid.attach(user, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Password", xalign=1), 0, 2, 1, 1)
        password = password_entry()
        grid.attach(password, 1, 2, 1, 1)
        note = Gtk.Label(label="Saved in your keyring, so you are not asked again.", xalign=0)
        note.get_style_context().add_class("dim-label")
        grid.attach(note, 1, 3, 1, 1)
        grid.attach(error, 1, 4, 1, 1)
        dialog.show_all()
        (password if host.get("user") else user).grab_focus()

        result = None
        while dialog.run() == Gtk.ResponseType.OK:
            if not user.get_text().strip():
                error.set_text("A user name is required.")
                error.show()
                user.grab_focus()
            elif not password.get_text():
                error.set_text("A password is required.")
                error.show()
                password.grab_focus()
            else:
                result = (user.get_text().strip(), password.get_text())
                break
        dialog.destroy()
        return result

    def edit_host(self, name, address=None):
        """Add or edit a host. Returns the saved name, or None on cancel."""
        host = self.cfg["hosts"].get(name, {}) if name else {"address": address or ""}
        has_password = bool(name) and password_get(name) is not None
        dialog, grid, error = form_dialog("Edit host" if name else "Add host",
                                          self.window, "Save")
        fields = {}
        rows = [
            ("address", "Address", host.get("address", ""), ""),
            ("user", "User", host.get("user", ""), ""),
            ("password", "Password", "", "unchanged" if has_password else "asked on first connect"),
            ("domain", "Domain", host.get("domain", ""), "optional"),
            ("name", "Name", name or "", "defaults to the address"),
            ("args", "Extra FreeRDP options", host.get("args") or DEFAULT_ARGS, ""),
        ]
        for i, (key, label, value, placeholder) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            if key == "password":
                entry = password_entry()
            else:
                entry = Gtk.Entry(text=value, activates_default=True, hexpand=True,
                                  width_chars=40)
            entry.set_placeholder_text(placeholder)
            grid.attach(entry, 1, i, 1, 1)
            fields[key] = entry
        grid.attach(error, 1, len(rows), 1, 1)
        dialog.show_all()
        fields["user" if address else "address"].grab_focus()

        def fail(message, key):
            error.set_text(message)
            error.show()
            fields[key].grab_focus()

        saved = None
        while dialog.run() == Gtk.ResponseType.OK:
            address = fields["address"].get_text().strip()
            new_name = fields["name"].get_text().strip() or address
            args = fields["args"].get_text().strip() or DEFAULT_ARGS
            if not address:
                fail("An address is required.", "address")
                continue
            clash = resolve_host({"hosts": {k: {} for k in self.cfg["hosts"]}}, new_name)
            if clash and clash != name:
                fail(f"A host named {clash} already exists.", "name")
                continue
            try:
                shlex.split(args)
            except ValueError as e:
                fail(f"Options: {e}.", "args")
                continue
            hosts = self.cfg["hosts"]
            pw = fields["password"].get_text()
            if name and name != new_name:
                hosts.pop(name, None)
                if not pw:
                    pw = password_get(name)  # carry it over to the new name
                password_set(name, None)
                if self.cfg["last"] == name:
                    self.cfg["last"] = new_name
            hosts[new_name] = {
                "address": address,
                "user": fields["user"].get_text().strip(),
                "domain": fields["domain"].get_text().strip(),
                "args": args,
            }
            if pw:
                err = password_set(new_name, pw)
                if err:
                    fail(err, "password")
                    continue
            save_config(self.cfg)
            self.refresh()
            self.select_name(new_name)
            saved = new_name
            break
        dialog.destroy()
        return saved

    def delete_host(self, name):
        if not name:
            return
        dialog = Gtk.MessageDialog(parent=self.window, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE,
                                   text=f"Delete {name}?")
        dialog.format_secondary_text("Its stored password is removed from the keyring too.")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        delete = dialog.add_button("Delete", Gtk.ResponseType.OK)
        delete.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
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
        if (ctrl and event.keyval == Gdk.KEY_e) or event.keyval == Gdk.KEY_F2:
            self.edit_host(self.selected_name())
            return True
        if event.keyval == Gdk.KEY_F1:
            self.show_help(None)
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

    def show_error(self, text, secondary=""):
        """Non-modal error dialog that stays until closed (a toast would vanish)."""
        dialog = Gtk.MessageDialog(parent=self.window, message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=text)
        if secondary:
            dialog.format_secondary_text(secondary)
        dialog.connect("response", lambda d, *_: d.destroy())
        dialog.show()
        return False

    def show_help(self, widget):
        dialog = Gtk.Dialog(title="Help", parent=self.window, flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        text = Gtk.Label(
            label=f"""<b>{APP_NAME} v{VERSION}</b>

A minimal launcher for sdl-freerdp (FreeRDP's own client).

<b>How to use:</b>
1. Type an address and press Enter
2. Fill in user and password once (kept in the keyring)
3. Enter connects; the session opens in its own window
4. Next time: pick the host, or just press Enter

<b>Shortcuts:</b>
• Enter — Connect
• Ctrl+N — Add host
• Ctrl+E or F2 — Edit selected host
• Delete — Delete selected host (list focused)
• F1 — This help
• Esc or Ctrl+Q — Close (sessions keep running)

A rejected password is forgotten and asked again.
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
        try:
            launch(name, host)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(f"quickrdp: {e}")
        return
    QuickRDP()
    Gtk.main()


if __name__ == "__main__":
    main()
