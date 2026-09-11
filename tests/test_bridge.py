"""Validation tests for untrusted browser bridge payloads."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("bridge_test_plugin")
PACKAGE.__path__ = [str(ROOT)]
sys.modules["bridge_test_plugin"] = PACKAGE
for module_name in ("core", "bridge"):
    spec = importlib.util.spec_from_file_location(
        f"bridge_test_plugin.{module_name}", ROOT / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

validate_payload = sys.modules["bridge_test_plugin.bridge"].validate_payload


class BridgeValidationTests(unittest.TestCase):
    def payload(self):
        return {
            "account": "Example.User",
            "sources": {
                "posts": [{
                    "id": "123456789",
                    "time": 1700000000,
                    "shortcode": "ABC_123",
                    "caption": "hello",
                    "media": [{
                        "video": False,
                        "url": "https://scontent.cdninstagram.com/image.jpg?sig=one",
                    }],
                }],
                "stories": [],
            },
        }

    def test_normalizes_payload_for_store(self):
        account, sources = validate_payload(self.payload())
        self.assertEqual(account, "example.user")
        self.assertEqual(sources["posts"][0]["key"], "post:123456789")
        self.assertEqual(sources["posts"][0]["url"],
                         "https://www.instagram.com/p/ABC_123/")
        self.assertTrue(sources["posts"][0]["media"][0]["url"].startswith("https://"))

    def test_rejects_non_instagram_content_link(self):
        payload = self.payload()
        payload["sources"]["posts"][0]["url"] = "https://example.com/fake"
        with self.assertRaisesRegex(ValueError, "Instagram"):
            validate_payload(payload)

    def test_rejects_untrusted_media_host(self):
        payload = self.payload()
        payload["sources"]["posts"][0]["media"][0]["url"] = \
            "https://cdn.example.com/image.jpg"
        with self.assertRaisesRegex(ValueError, "来源"):
            validate_payload(payload)

    def test_rejects_unknown_source_and_oversized_batch(self):
        payload = self.payload()
        payload["sources"]["messages"] = []
        with self.assertRaisesRegex(ValueError, "未知"):
            validate_payload(payload)
        payload = self.payload()
        payload["sources"]["posts"] = payload["sources"]["posts"] * 101
        with self.assertRaisesRegex(ValueError, "数量"):
            validate_payload(payload)


if __name__ == "__main__":
    unittest.main()
