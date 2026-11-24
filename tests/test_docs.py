"""Docs that must agree with each other or with the code (N15, N16, N20)."""
import importlib.util
import re
import unittest

from support import ROOT


def _bullets(text):
    """The '- ' list items (with their continuation lines) of a Markdown text."""
    items, current = [], None
    for line in text.splitlines():
        if line.startswith("- "):
            current = [line[2:].strip()]
            items.append(current)
        elif current is not None and line.startswith("  ") and line.strip():
            current.append(line.strip())
        else:
            current = None
    return [" ".join(item) for item in items]


class AgentInstructionsTest(unittest.TestCase):
    def test_skill_and_paste_block_say_the_same(self):
        block = re.search(r"```markdown\n(.*?)\n```", (ROOT / "docs" / "agents.md").read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(block, "docs/agents.md has no ```markdown block")
        skill = (ROOT / "skills" / "unreal-toolkit" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(_bullets(block.group(1)), _bullets(skill))

    def test_skill_front_matter(self):
        skill = (ROOT / "skills" / "unreal-toolkit" / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"---\nname: unreal-toolkit\ndescription: .+\n---\n", skill)
        self.assertIsNotNone(match, "SKILL.md needs name and description front matter")


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
