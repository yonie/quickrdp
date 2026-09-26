# quickrdp

Own launcher around `sdl-freerdp`, written 2026-09-26 because Thincast (closed source,
Flathub just untars a prebuilt tarball) ignores Enter in its connect dialog, and because
sdl-freerdp turned out faster and freeze-free against blue anyway.

Member of the `quick*` family (quickcast, quicksnip, quickcell) and follows their shape:
one `quickrdp.py`, GTK 3 via PyGObject, emoji toolbar with a Help dialog, toast overlay,
`pyproject.toml` + `requirements.txt`, `tests/` (unittest), `tools/shot.py` for the
README screenshot, MIT. No libadwaita, no GTK4: keep it that way for consistency.

## Behaviour

- Window opens with the last host prefilled and selected in the entry. Enter connects.
  Escape or Ctrl+Q closes the window. Closing it never kills a running session
  (spawned with `start_new_session`).
- The entry resolves a saved host by name or address; anything else is an ad hoc
  connect with the default options, and sdl-freerdp asks for credentials itself.
- Connecting to a host with a user but no stored password asks for it in the launcher
  first (Enter connects), stores it, then launches.
- List rows are saved hosts; double-click or Enter on a row connects, selecting a row
  fills the entry. Edit (F2) and Delete (Delete key, list focused) act on the selection.
  Enter inside the edit dialog saves.
- `quickrdp.py <name>` connects without a window (used by the desktop file's
  "Connect to blue" action).

## Where things live

- Hosts: `~/.config/quickrdp/hosts.json` (`last`, `hosts.<name>.{address,user,domain,args}`).
  `QUICKRDP_CONFIG` overrides the path; tests and `tools/shot.py` use that.
- Passwords: GNOME keyring, libsecret schema `org.yonie.quickrdp`, attribute `host=<name>`.
  Fed to sdl-freerdp over stdin with `/from-stdin`, never on the command line. The
  `tcsetattr ... Inappropriate ioctl` lines that produces in the log are harmless.
- Session logs: `~/.cache/quickrdp/<name>.log`. A non-zero exit is summarised in a toast
  (`explain_exit` looks for `LOGON_FAILURE` and `ERRCONNECT_*`).
- Desktop entry: `quickrdp.desktop`, symlinked into `~/.local/share/applications`.
  Its `Exec` lines are absolute paths into this clone.

## Default sdl-freerdp options

`/dynamic-resolution /gfx:AVC444 /network:lan +clipboard +auto-reconnect`

`/network:lan` matters: it stops the Windows server's own network detection, which on
blue misjudged the link (380 ms / 512 kbit/s) and throttled the encoder. Measured
2026-09-26 with `typeperf "\RemoteFX Network(*)\Current TCP Bandwidth"` on blue: with
the flag the server sees 49 Mbit/s, 30 fps, zero skipped frames. Certificates are
handled by sdl-freerdp's own dialog and stored under `~/.config/freerdp/server/`.

## Keyboard grab and the GNOME "allow inhibiting shortcuts" dialog

sdl-freerdp grabs the keyboard so Super/Alt+Tab reach Windows. On Wayland GNOME asks
permission for that, and it only remembers the answer for windows it can tie to a
desktop file. Bare sdl-freerdp announces app id `com.freerdp.client.sdl3`, which has
none, so it asked on every connect. The launcher therefore spawns it with
`SDL_APP_ID=quickrdp` (verified with `WAYLAND_DEBUG=1`: the toplevel then calls
`set_app_id("quickrdp")`), matching `quickrdp.desktop`; the launcher window itself gets
the same id via `GLib.set_prgname`. Answer Allow once and it is stored in the portal
permission store (`PermissionStore.Lookup gnome shortcuts-inhibitor`). Inside a
session, Right Ctrl+G toggles the grab; `-grab-keyboard` in a host's options disables
it entirely.

## Dependencies (Fedora)

`freerdp` (sdl-freerdp with OpenH264 and GFX built in), `python3-gobject`, `gtk3`,
`libsecret`. All present on magenta.

## Checks before committing

```bash
python3 -m unittest discover -s tests -v
python3 tools/shot.py screenshot.png   # only when the UI changed
```
