"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

from io import StringIO
import sys

from rerobots_apiw import __version__
from rerobots_apiw.__main__ import main_cli


def test_version():
    original_stdout = sys.stdout
    sys.stdout = StringIO()
    rc = main_cli(['-V'])
    assert rc == 0
    res = sys.stdout.getvalue().strip()
    sys.stdout = original_stdout
    assert res == __version__
