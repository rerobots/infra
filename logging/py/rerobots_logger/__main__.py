#!/usr/bin/env python
"""logger daemon

The main purpose of this program is to collect log messages from
infrastructure agents via RabbitMQ (AMQP) and aggregate them into
local storage, which can be monitored, retrieved remotely, etc.


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys

from . import __version__

from .loggers import RerobotsLogger


logger = logging.getLogger('rerobots_logger')
logger.setLevel(logging.DEBUG)
loghandler = logging.handlers.WatchedFileHandler(
    filename='rerobots_logger.{}.log'.format(os.getpid()), mode='a'
)
loghandler.setFormatter(
    logging.Formatter(
        '%(asctime)s ; %(name)s (%(levelname)s) (pid: {}); %(message)s'.format(
            os.getpid()
        )
    )
)
loghandler.setLevel(logging.DEBUG)
logger.addHandler(loghandler)
logging.getLogger('rerobots_infra').addHandler(loghandler)


def main_cli(argv=None):
    if argv is None:
        argv = sys.argv
    argparser = argparse.ArgumentParser(prog='rerobots_logger')
    argparser.add_argument(
        '-V', '--version', action='store_true', dest='print_version', default=False
    )
    args = argparser.parse_args(argv)
    if args.print_version:
        print(__version__)
        return 0

    loop = asyncio.get_event_loop()
    lr = RerobotsLogger(event_loop=loop)
    lr.start()

    try:
        loop.run_forever()
    finally:
        loop.close()

    return 0


if __name__ == '__main__':
    sys.exit(main_cli(sys.argv[1:]))
