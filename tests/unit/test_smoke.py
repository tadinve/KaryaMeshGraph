"""M0 smoke: the package imports cleanly and the CLI wires up."""

import karyameshgraph
from karyameshgraph.cli import main


def test_version():
    assert karyameshgraph.__version__


def test_cli_requires_subcommand(capsys):
    try:
        main([])
    except SystemExit as e:
        assert e.code == 2
    else:  # pragma: no cover
        raise AssertionError("argparse should reject missing subcommand")
