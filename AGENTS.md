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
- The launcher quits on its own once the session is established (Ronald, 2026-09-27:
  "it's just a launcher"). `launch()` tails the session log in its wait thread and
  fires `on_connect` when `[gdi_init_ex]` appears: sdl-freerdp logs that at INFO from
  `postConnect`, i.e. after NLA accepted the credentials and the RDP connection went
  active. Nothing earlier is reliable: the SDL window and renderer exist before the
  connect (they show the connecting dialog) and the state transitions are only logged
  at DEBUG on the chatty `core.rdp` tag. Consequence: failures before that line
  (logon, TLS, cancelled cert dialog) still reach `on_exit` and its dialogs; anything
  after it is sdl-freerdp's business (`+auto-reconnect`).
  The child runs under `stdbuf -oL`: WLog writes INFO to stdout and ERROR/WARN to
  stderr, and stdout redirected to a file is block-buffered, so without it the marker
  only reached the log when the session ended (first attempt 2026-09-27 failed exactly
  like that). Because `stdbuf` hides a missing client, `launch()` checks
  `shutil.which` itself and raises `FileNotFoundError`.
- The entry resolves a saved host by name or address. An unknown address opens the
  Add dialog prefilled with it (focus on User); saving connects immediately. sdl-freerdp's
  own credential dialog should never appear; only `quickrdp.py <unknown-address>` on the
  command line still leaves credentials to it.
- Connecting to a host without a stored password asks for user and password in the launcher
  first (Enter connects), stores them, then launches. A session that ends with
  `ERRCONNECT_LOGON_FAILURE` clears the keyring entry and reopens that dialog.
- Edit/Delete are insensitive without a selection. Names are unique case-insensitively
  and the dialog refuses duplicates. Options are validated with `shlex.split` before
  saving; the launch path still catches `ValueError` and a missing `sdl-freerdp`
  (`FileNotFoundError`) and shows an error dialog instead of a traceback.
- In the edit dialog the password field is never prefilled: empty means unchanged
  (placeholder says so), non-empty replaces. Renaming carries the password over.
- One session per host: `self.sessions` maps name to Popen, a second connect toasts.
- Errors that need reading (`show_error`) are non-modal MessageDialogs; toasts are only
  for confirmations.
- List rows are saved hosts; double-click or Enter on a row connects, selecting a row
  fills the entry. Edit (F2) and Delete (Delete key, list focused) act on the selection.
  Enter inside the edit dialog saves. Only the address is required; the name defaults to
  it. Validation errors show inside the dialog, not as a toast (the dialog is modal and
  would hide it).
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

## sdl-freerdp hotkeys: Right Shift is the default modifier

sdl-freerdp's built-in shortcuts (Return fullscreen, R resizeable, M minimize, G grab,
D disconnect) hang off `SDL_KeyModMask`, default `KMOD_RSHIFT`, matched as
`(mods & mask) == mask`. That swallows Right Shift+R and friends before they reach
Windows, so capitals typed with the right hand went missing (found 2026-09-27; the log
line is `<KMOD_RSHIFT>+<SDL_SCANCODE_R> pressed, toggling resizeable state`). There is
no command-line switch, only `$XDG_CONFIG_HOME/freerdp/sdl-freerdp.json`, so
`ensure_hotkey_mask()` runs before every launch and writes
`{"SDL_KeyModMask": ["KMOD_RCTRL"]}` into that file, merging with whatever else is there.
It only acts when the key is absent (an explicit value is Ronald's choice) and leaves an
unparseable file untouched. `QUICKRDP_SDL_PREFS` overrides the path for tests. Setting
`XDG_CONFIG_HOME` for the child instead was rejected: it would also move the trusted
certificate store under `~/.config/freerdp/server/`.

## Dependencies (Fedora)

`freerdp` (sdl-freerdp with OpenH264 and GFX built in), `python3-gobject`, `gtk3`,
`libsecret`. All present on magenta.

## UX review (Kimi, 2026-09-26)

The code was reviewed by kimi-k3 via the local Ollama proxy; 12 of its 15 points were
applied (missing client, option validation, rejected-password loop, keyring errors,
persistent error dialogs, disabled Edit/Delete, name collisions, case-insensitive
lookup, duplicate sessions, tooltips with shortcuts, password reveal toggle, destructive
Delete button). Rejected on purpose because they are the quick* family look: emoji
button labels, the "(Enter connects)" toolbar hint, and replacing the toolbar with
symbolic icons.

## GUI testing without touching the desktop

Never run GUI probes on the live Wayland session; they steal focus while Ronald types.
Use broadway: `broadwayd :8 &` then `GDK_BACKEND=broadway BROADWAY_DISPLAY=:8 python3 ...`.
Drive dialogs with `GLib.timeout_add` steps scheduled up front (a step that opens a
modal `dialog.run()` does not return until it closes, so chaining from inside a step
hangs), and locate widgets via the dialog's grid `top-attach` property.

## Checks before committing

```bash
python3 -m unittest discover -s tests -v
python3 tools/shot.py screenshot.png   # only when the UI changed
```
