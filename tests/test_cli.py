import argparse
import contextlib
import io
import unittest

from hevc_lab import __version__
from hevc_lab.cli import build_parser


class CliContractTests(unittest.TestCase):
    def test_only_realtime_commands_are_registered(self):
        parser = build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(subparsers.choices), {"check-env", "web"})
        self.assertEqual(__version__, "2.2.0")

    def test_removed_commands_do_not_parse(self):
        parser = build_parser()
        removed = [
            "multi" + "-encode",
            "compare",
            "search" + "-crf",
            "r" + "oi-study",
            "de" + "noise-study",
            "aq-study",
        ]
        for command in removed:
            with self.subTest(command=command):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        parser.parse_args([command])
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
