import importlib
import json
import os
import sys
import tempfile
import unittest

os.environ["GDK_BACKEND"] = "broadway"
os.environ["QUICKRDP_CONFIG"] = os.path.join(tempfile.mkdtemp(), "hosts.json")
os.environ["QUICKRDP_SDL_PREFS"] = os.path.join(tempfile.mkdtemp(), "freerdp", "sdl-freerdp.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

quickrdp = importlib.import_module("quickrdp")


class TestBasics(unittest.TestCase):
    def test_version_is_set(self):
        self.assertEqual(quickrdp.VERSION, "0.1.0")

    def test_main_exists(self):
        self.assertTrue(callable(quickrdp.main))

    def test_default_args_force_lan(self):
        self.assertIn("/network:lan", quickrdp.DEFAULT_ARGS)


class TestConfig(unittest.TestCase):
    def test_missing_config_gives_defaults(self):
        cfg = quickrdp.load_config()
        self.assertEqual(cfg, {"hosts": {}, "last": ""})

    def test_roundtrip(self):
        cfg = {"last": "a", "hosts": {"a": {"address": "10.0.0.1", "user": "me"}}}
        quickrdp.save_config(cfg)
        self.assertEqual(json.loads(quickrdp.CONFIG.read_text()), cfg)
        self.assertEqual(quickrdp.load_config(), cfg)

    def test_resolve_by_name_or_address(self):
        cfg = {"last": "", "hosts": {"blue": {"address": "10.0.0.2"}}}
        self.assertEqual(quickrdp.resolve_host(cfg, "blue"), "blue")
        self.assertEqual(quickrdp.resolve_host(cfg, "10.0.0.2"), "blue")
        self.assertEqual(quickrdp.resolve_host(cfg, "BLUE"), "blue")
        self.assertIsNone(quickrdp.resolve_host(cfg, "nope"))


class TestLaunch(unittest.TestCase):
    def test_build_argv(self):
        host = {"address": "10.0.0.2", "user": "me", "domain": "D", "args": "/f +clipboard"}
        self.assertEqual(
            quickrdp.build_argv(host),
            ["sdl-freerdp", "/v:10.0.0.2", "/u:me", "/d:D", "/f", "+clipboard"],
        )

    def test_build_argv_defaults(self):
        argv = quickrdp.build_argv({"address": "h"})
        self.assertEqual(argv[:2], ["sdl-freerdp", "/v:h"])
        self.assertIn("/network:lan", argv)
        self.assertNotIn("/u:", " ".join(argv))

    def test_explain_exit(self):
        log = os.path.join(tempfile.mkdtemp(), "x.log")
        with open(log, "w") as f:
            f.write("[ERROR] nla_recv_pdu: ERRCONNECT_LOGON_FAILURE [0x00020014]\n")
        token, msg = quickrdp.explain_exit(134, log)
        self.assertEqual(token, "ERRCONNECT_LOGON_FAILURE")
        self.assertIn("password", msg)
        with open(log, "w") as f:
            f.write("[ERROR] foo: ERRCONNECT_SOMETHING_NEW [0x00020006]\n")
        self.assertEqual(quickrdp.explain_exit(1, log),
                         ("ERRCONNECT_SOMETHING_NEW", "Connection failed (ERRCONNECT_SOMETHING_NEW)."))
        self.assertEqual(quickrdp.explain_exit(3, "/nonexistent"),
                         ("", "Connection failed (exit code 3)."))

    def test_build_argv_rejects_unmatched_quote(self):
        with self.assertRaises(ValueError):
            quickrdp.build_argv({"address": "h", "args": "/title:'oops"})

    def test_on_connect_fires_when_log_shows_gdi_init(self):
        from gi.repository import GLib
        tmp = tempfile.mkdtemp()
        fake = os.path.join(tmp, "fake-freerdp")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho '[INFO][com.freerdp.gdi] - [gdi_init_ex]: Local framebuffer'\n"
                    "sleep 1\nexit 0\n")
        os.chmod(fake, 0o755)
        events = []
        loop = GLib.MainLoop()

        def on_connect(name):
            events.append(("connect", name))
            return False

        def on_exit(name, code, log):
            events.append(("exit", name, code))
            loop.quit()
            return False

        old_client, old_logdir = quickrdp.CLIENT, quickrdp.LOGDIR
        quickrdp.CLIENT, quickrdp.LOGDIR = fake, quickrdp.Path(tmp)
        try:
            GLib.timeout_add(5000, loop.quit)
            quickrdp.launch("", {"address": "h", "args": ""}, on_exit, on_connect)
            loop.run()
        finally:
            quickrdp.CLIENT, quickrdp.LOGDIR = old_client, old_logdir
        self.assertEqual(events, [("connect", ""), ("exit", "", 0)])

    def test_on_connect_not_fired_without_marker(self):
        from gi.repository import GLib
        tmp = tempfile.mkdtemp()
        fake = os.path.join(tmp, "fake-freerdp")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho 'ERRCONNECT_LOGON_FAILURE'\nexit 3\n")
        os.chmod(fake, 0o755)
        events = []
        loop = GLib.MainLoop()
        old_client, old_logdir = quickrdp.CLIENT, quickrdp.LOGDIR
        quickrdp.CLIENT, quickrdp.LOGDIR = fake, quickrdp.Path(tmp)
        try:
            GLib.timeout_add(5000, loop.quit)
            quickrdp.launch("", {"address": "h", "args": ""},
                            lambda *a: (events.append(a), loop.quit()),
                            lambda name: events.append(("connect", name)))
            loop.run()
        finally:
            quickrdp.CLIENT, quickrdp.LOGDIR = old_client, old_logdir
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], ("", 3))


class TestHotkeyPrefs(unittest.TestCase):
    def test_missing_client_raises(self):
        old = quickrdp.CLIENT
        quickrdp.CLIENT = "/nonexistent/sdl-freerdp"
        try:
            with self.assertRaises(FileNotFoundError):
                quickrdp.launch("", {"address": "h"})
        finally:
            quickrdp.CLIENT = old

    def path(self):
        return quickrdp.Path(tempfile.mkdtemp()) / "freerdp" / "sdl-freerdp.json"

    def test_creates_file_with_right_ctrl(self):
        p = self.path()
        self.assertTrue(quickrdp.ensure_hotkey_mask(p))
        self.assertEqual(json.loads(p.read_text()), {"SDL_KeyModMask": ["KMOD_RCTRL"]})

    def test_merges_with_existing_keys(self):
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text('{"SDL_Fullscreen": "SDL_SCANCODE_F"}')
        self.assertTrue(quickrdp.ensure_hotkey_mask(p))
        self.assertEqual(json.loads(p.read_text()),
                         {"SDL_Fullscreen": "SDL_SCANCODE_F", "SDL_KeyModMask": ["KMOD_RCTRL"]})

    def test_respects_explicit_choice(self):
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text('{"SDL_KeyModMask": ["KMOD_RALT"]}')
        self.assertFalse(quickrdp.ensure_hotkey_mask(p))
        self.assertEqual(json.loads(p.read_text()), {"SDL_KeyModMask": ["KMOD_RALT"]})

    def test_leaves_broken_file_alone(self):
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text("{not json")
        self.assertFalse(quickrdp.ensure_hotkey_mask(p))
        self.assertEqual(p.read_text(), "{not json")

    def test_launch_writes_prefs(self):
        p = quickrdp.SDL_PREFS
        if p.exists():
            p.unlink()
        old, old_logdir = quickrdp.CLIENT, quickrdp.LOGDIR
        quickrdp.CLIENT, quickrdp.LOGDIR = "true", quickrdp.Path(tempfile.mkdtemp())
        try:
            quickrdp.launch("", {"address": "h", "args": ""}).wait()
        finally:
            quickrdp.CLIENT, quickrdp.LOGDIR = old, old_logdir
        self.assertEqual(json.loads(p.read_text())["SDL_KeyModMask"], ["KMOD_RCTRL"])


class TestAppClass(unittest.TestCase):
    def test_has_expected_methods(self):
        for m in ("connect_entry", "connect_host", "edit_host", "delete_host",
                  "ask_password", "show_toast", "show_help", "on_key_press"):
            self.assertTrue(hasattr(quickrdp.QuickRDP, m), m)


if __name__ == "__main__":
    unittest.main()
