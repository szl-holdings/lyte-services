"""Parse the shipped browser program; backend 200 is not frontend execution."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.in_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.parts.append(data)


class FrontendScriptTests(unittest.TestCase):
    def test_shipped_inline_javascript_parses(self):
        document = Path(__file__).resolve().parents[1] / "space/index.html"
        parser = InlineScripts()
        parser.feed(document.read_text(encoding="utf-8"))
        self.assertTrue(parser.parts, "No executable browser program found")
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to verify the browser program")
        for program in parser.parts:
            checked = subprocess.run(
                [node, "--check"], input=program, text=True,
                encoding="utf-8", capture_output=True, timeout=15,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_agent_cards_keep_scenario_truth_label(self):
        source = (Path(__file__).resolve().parents[1] / "space/index.html").read_text(encoding="utf-8")
        agent_renderer = source.split("function renderAgents(data){", 1)[1].split("function renderPlayback", 1)[0]
        self.assertIn('const truth=data.truth_label||"UNAVAILABLE"', agent_renderer)
        self.assertIn('node("span","label",truth)', agent_renderer)
        self.assertIn('select(entry[0],truth,entry[1])', agent_renderer)
        self.assertNotIn('"MEASURED"', agent_renderer)
