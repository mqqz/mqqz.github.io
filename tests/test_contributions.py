import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import contributions as c


def message(title, mid, date="2026-03-01T10:00:00Z", parent=None, body=""):
    return {"title": title, "url": f"https://lore.kernel.org/all/{mid}/",
            "date": date, "parent": f"https://lore.kernel.org/all/{parent}/" if parent else None,
            "body": body, "author": c.EMAIL}


class ContributionsTests(unittest.TestCase):
    def test_series_revisions_and_self_replies_are_one_topic(self):
        messages = [message("[PATCH 0/2] rust: add tracepoints", "v1"),
                    message("[PATCH 1/2] rust: first change", "v1-1", parent="v1"),
                    message("[PATCH v2 0/3] rust: add tracepoints", "v2", "2026-04-01T10:00:00Z"),
                    message("[PATCH v2 1/3] rust: new change", "v2-1", parent="v2"),
                    message("Re: [PATCH v2 1/3] rust: new change", "reply", parent="v2-1")]
        result = c.kernel_contributions(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://lore.kernel.org/all/v2/")
        self.assertEqual(result[0]["kind"], "Submitted series")

    def test_resend_replaces_patch_and_uncovered_series_patch_is_retained(self):
        messages = [message("[PATCH] kbuild: fix linker", "first"),
                    message("[PATCH RESEND] kbuild: fix linker", "resend", "2026-04-01T10:00:00Z"),
                    message("[PATCH 1/2] rust: separate change", "orphan", parent="missing")]
        result = c.kernel_contributions(messages)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["url"].endswith("/resend/"))

    def test_exact_sender_and_unquoted_review_trailer(self):
        other = message("[PATCH] not mine", "other")
        other["author"] = "someone@sdhn.cc"
        result = c.kernel_contributions([
            other,
            message("Re: [PATCH] reviewed change", "review", body=f"Reviewed-by: Mo <{c.EMAIL}>"),
            message("Re: [PATCH] another change", "discussion", body=f"> Reviewed-by: Mo <{c.EMAIL}>"),
        ])
        self.assertEqual({entry["kind"] for entry in result}, {"Review", "Discussion"})

    def test_atom_mirror_links_are_canonical_and_html_error_is_rejected(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <author><email>mo@sdhn.cc</email></author><title>[PATCH] fix &amp; test</title>
          <updated>2026-03-01T10:00:00Z</updated>
          <link href="https://lore-kernel.gnuweeb.org/all/test%40sdhn.cc/"/>
          <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">text</div></content>
        </entry></feed>'''
        result = c.parse_atom(atom)
        self.assertEqual(result[0]["url"], "https://lore.kernel.org/all/test%40sdhn.cc/")
        self.assertEqual(result[0]["title"], "[PATCH] fix & test")
        self.assertEqual(c.parse_atom(b"</feed>"), [])
        with self.assertRaises(ValueError):
            c.parse_atom(b"<html><body>Verify you are human</body></html>")

    def test_lore_pagination_and_repeated_page_failure(self):
        first, second = message("[PATCH] first", "1"), message("[PATCH] second", "2")
        with patch.object(c, "fetch", return_value=b"feed"), patch.object(
                c, "parse_atom", side_effect=[[first], [second], []]):
            self.assertEqual(len(c.fetch_lore(c.LORE_HOSTS[0])), 2)
        with patch.object(c, "fetch", return_value=b"feed"), patch.object(
                c, "parse_atom", return_value=[first]):
            with self.assertRaisesRegex(ValueError, "repeated"):
                c.fetch_lore(c.LORE_HOSTS[0])

    def test_github_pagination_and_incomplete_search(self):
        def item(number):
            return {"repository_url": "https://api.github.com/repos/example/project",
                    "title": f"Change {number}", "html_url": f"https://github.com/example/project/pull/{number}",
                    "closed_at": "2026-03-01T10:00:00Z"}
        pages = [{"incomplete_results": False, "total_count": 2, "items": [item(n)]} for n in (1, 2)]
        with patch.object(c, "fetch", side_effect=[json.dumps(p) for p in pages]) as fetch:
            self.assertEqual(len(c.github_contributions()), 2)
            self.assertIn("page=2", fetch.call_args.args[0])
        with patch.object(c, "fetch", return_value=json.dumps({"incomplete_results": True})):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                c.github_contributions()

    def test_render_escapes_external_content_and_rejects_unsafe_links(self):
        entry = {"project": "example/project", "url": "https://github.com/example/project/pull/1", "title": '<script>alert("hi")</script>',
                 "date": "2026-03-01", "kind": "Merged PR"}
        self.assertNotIn("<script>", c.render_entry(entry))
        entry["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            c.render_entry(entry)

    def test_failed_refresh_does_not_modify_existing_page_or_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            page = root / "index.html"
            snapshot = root / "data/contributions.json"
            page.write_text("existing page")
            snapshot.write_text("existing data")
            with patch.object(c, "ROOT", root), patch("sys.argv", ["contributions.py", "--refresh"]), patch.object(
                    c, "github_contributions", return_value=[]), patch.object(
                    c, "fetch_lore", side_effect=ValueError("source unavailable")):
                with self.assertRaises(RuntimeError):
                    c.main()
            self.assertEqual(page.read_text(), "existing page")
            self.assertEqual(snapshot.read_text(), "existing data")

    def test_mirror_fallback_uses_same_canonical_entries(self):
        with patch.object(c, "github_contributions", return_value=[]), patch.object(
                c, "fetch_lore", side_effect=[ValueError("blocked"), [message("[PATCH] fix", "patch")]]):
            data = c.refresh()
        self.assertEqual(data["kernel_archive"], c.LORE_HOSTS[1])
        self.assertEqual(data["kernel"][0]["url"], "https://lore.kernel.org/all/patch/")

    def test_empty_state_and_marker_preservation(self):
        section = c.render({"github": [], "kernel": [], "updated": "2026-10-12"})
        self.assertIn("No merged pull requests", section)
        self.assertIn("All messages on lore", section)
        page = "my about text\n" + c.START + "old section" + c.END + "\nmy footer"
        result = c.replace_section(page, section)
        self.assertTrue(result.startswith("my about text\n"))
        self.assertTrue(result.endswith("\nmy footer"))
        with self.assertRaises(ValueError):
            c.replace_section("missing markers", section)


if __name__ == "__main__":
    unittest.main()
