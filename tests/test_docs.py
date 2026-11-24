"""Docs that must agree with the code (N15, N16)."""
import importlib.util
import unittest

from support import ROOT


class GeneratedDocsTest(unittest.TestCase):
    def test_generated_pages_are_up_to_date(self):
        spec = importlib.util.spec_from_file_location("gen_docs", ROOT / "scripts" / "gen_docs.py")
        gen_docs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen_docs)
        for filename, make in gen_docs.PAGES.items():
            with self.subTest(page=filename):
                current = (ROOT / "docs" / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(current, make().rstrip("\n") + "\n",
                                 f"docs/{filename} is out of date: run `python scripts/gen_docs.py`")


if __name__ == "__main__":
    unittest.main()
