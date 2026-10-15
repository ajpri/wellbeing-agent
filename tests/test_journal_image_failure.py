"""Regression tests: optional image failures must not lose journal entries.

Run with: uv run python -m unittest discover -s tests -p test_journal_image_failure.py
"""

import builtins
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.genai.errors import ClientError, ServerError

from wellbeing_agent.tools import journal_store


class JournalImageFailureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict(os.environ, {"JOURNAL_DIR": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.image = patch("wellbeing_agent.tools.image_gen.generate_day_cartoon")
        self.generate = self.image.start()
        self.addCleanup(self.image.stop)
        journal_store.invalidate_index()
        self.addCleanup(journal_store.invalidate_index)
        self.entry = "# A good day\n\nI walked by the lake."

    def saved_file(self):
        paths = list(self.root.glob("*.md"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_text_exists_before_image_request_and_success_appends_image(self):
        def generate(title):
            self.assertEqual(self.saved_file().read_text(), self.entry)
            self.assertIn("lake", journal_store.search_journals("lake"))
            return "Cartoon saved.\n![Day Cartoon](cartoon.png)"

        self.generate.side_effect = generate
        result = journal_store.save_journal(self.entry, "Lake walk")
        self.assertIn("Journal entry saved", result)
        self.assertNotIn("not generated", result)
        self.assertEqual(self.saved_file().read_text(), self.entry + "\n\n---\n![Day Cartoon](cartoon.png)\n")
        self.assertIsNone(journal_store._index_cache)
        self.generate.assert_called_once_with("Lake walk")

    def test_api_failures_keep_journal_and_return_actionable_safe_message(self):
        for code, error_class, explanation in (
            (401, ClientError, "authenticate"),
            (403, ClientError, "permissions and billing"),
            (404, ClientError, "model was not found"),
            (429, ClientError, "quota or rate limit"),
            (500, ServerError, "temporarily unavailable"),
            (400, ClientError, "Image generation failed"),
        ):
            with self.subTest(code=code):
                for path in self.root.glob("*.md"):
                    path.unlink()
                self.generate.reset_mock()
                self.generate.side_effect = error_class(code, {"error": {"message": "secret-provider-response"}})
                with self.assertLogs(journal_store.logger, level="WARNING") as logs:
                    result = journal_store.save_journal(self.entry, "Lake walk")
                self.assertEqual(self.saved_file().read_text(), self.entry)
                self.assertIn("Journal entry saved", result)
                self.assertIn(explanation, result)
                self.assertNotIn("secret-provider-response", result + str(logs.output))
                self.assertIn("lake", journal_store.search_journals("lake"))
                self.generate.assert_called_once()

    def test_network_or_unexpected_image_error_keeps_journal(self):
        for error in (TimeoutError("sensitive-url"), RuntimeError("sensitive-url")):
            with self.subTest(error=type(error).__name__):
                for path in self.root.glob("*.md"):
                    path.unlink()
                self.generate.side_effect = error
                with self.assertLogs(journal_store.logger, level="WARNING") as logs:
                    result = journal_store.save_journal(self.entry, "Lake walk")
                self.assertEqual(self.saved_file().read_text(), self.entry)
                self.assertIn("Cartoon not generated", result)
                self.assertNotIn("sensitive-url", result + str(logs.output))

    def test_no_image_returned_is_reported(self):
        self.generate.return_value = "Could not generate an image this time."
        result = journal_store.save_journal(self.entry, "Lake walk")
        self.assertIn("Google returned no usable image", result)
        self.assertEqual(self.saved_file().read_text(), self.entry)

    def test_no_title_does_not_request_cartoon(self):
        result = journal_store.save_journal(self.entry)
        self.generate.assert_not_called()
        self.assertEqual(self.saved_file().read_text(), self.entry)
        self.assertNotIn("Cartoon", result)

    def test_failed_text_save_is_not_reported_as_success(self):
        with patch("wellbeing_agent.tools.journal_store.open", side_effect=PermissionError("Cannot write"), create=True):
            with self.assertRaises(PermissionError):
                journal_store.save_journal(self.entry, "Lake walk")
        self.generate.assert_not_called()
        self.assertEqual(list(self.root.glob("*.md")), [])

    def test_attachment_write_failure_preserves_saved_text(self):
        self.generate.return_value = "![Day Cartoon](cartoon.png)"

        def open_file(path, mode, **kwargs):
            if mode == "a":
                raise OSError("Disk full")
            return builtins.open(path, mode, **kwargs)

        with patch("wellbeing_agent.tools.journal_store.open", side_effect=open_file, create=True), \
                self.assertLogs(journal_store.logger, level="WARNING"):
            result = journal_store.save_journal(self.entry, "Lake walk")
        self.assertIn("could not be attached", result)
        self.assertEqual(self.saved_file().read_text(), self.entry)

    def test_existing_entry_is_preserved_on_image_failure(self):
        self.generate.side_effect = TimeoutError()
        with self.assertLogs(journal_store.logger, level="WARNING"):
            first = journal_store.save_journal(self.entry, "Lake walk")
            second = journal_store.save_journal("# Another entry", "Later")
        paths = list(self.root.glob("*.md"))
        self.assertEqual(len(paths), 2)
        self.assertEqual({path.read_text() for path in paths}, {self.entry, "# Another entry"})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
