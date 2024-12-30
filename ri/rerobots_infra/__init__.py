#!/usr/bin/env python
"""common classes used across parts of the rerobots infrastructure

SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

import logging

from .chantempl import RerobotsChannel, RerobotsQChannel
from .loggers import RLogSenderHandler


__all__ = [
    'RLogSenderHandler',
    'RerobotsChannel',
    'RerobotsQChannel',
]


logger = logging.getLogger('rerobots_infra')
logger.setLevel(logging.INFO)


try:
    from ._version import __version__
except ImportError:
    __version__ = '0.0.0.dev0+Unknown'
