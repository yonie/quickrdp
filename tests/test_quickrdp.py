import importlib
import json
import os
import sys
import tempfile
import unittest

os.environ["GDK_BACKEND"] = "broadway"
os.environ["QUICKRDP_CONFIG"] = os.path.join(tempfile.mkdtemp(), "hosts.json")
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
        self.assertIn("password", quickrdp.explain_exit(134, log))
        with open(log, "w") as f:
            f.write("[ERROR] foo: ERRCONNECT_CONNECT_FAILED [0x00020006]\n")
        self.assertEqual(quickrdp.explain_exit(1, log), "ERRCONNECT_CONNECT_FAILED")
        self.assertEqual(quickrdp.explain_exit(3, "/nonexistent"), "sdl-freerdp exited with 3")


class TestAppClass(unittest.TestCase):
    def test_has_expected_methods(self):
        for m in ("connect_entry", "connect_host", "edit_host", "delete_host",
                  "ask_password", "show_toast", "show_help", "on_key_press"):
            self.assertTrue(hasattr(quickrdp.QuickRDP, m), m)


if __name__ == "__main__":
    unittest.main()
