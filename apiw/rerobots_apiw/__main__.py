"""
SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

import argparse
import logging
import os
import sys
import time

from aiohttp import web

from .tasks import do_periodic
from .wsgi import application
from . import __version__


def main_cli(argv=None):
    if argv is None:
        argv = sys.argv
    argparser = argparse.ArgumentParser('apiw')
    argparser.add_argument(
        '--host',
        action='store',
        dest='host',
        default='127.0.0.1',
        help='bind to this host address; default is 127.0.0.1',
    )
    argparser.add_argument(
        '--port',
        action='store',
        type=int,
        dest='port',
        default='8080',
        help='bind to this host address; default is 8080',
    )
    argparser.add_argument(
        '-v', '--verbose', action='store_true', dest='verbose', default=False
    )
    argparser.add_argument(
        '-V', '--version', action='store_true', dest='print_version', default=False
    )
    argparser.add_argument(
        '--periodic',
        action='store_true',
        dest='do_periodic_tasks',
        default=False,
        help=(
            'start periodic tasks only; '
            'run this from a cron-like daemon; '
            'switches not relevant (e.g., --port) are ignored'
        ),
    )
    argparser.add_argument(
        '--poll-sleep',
        action='store_true',
        dest='periodic_sleep_poll',
        default=False,
        help='if --periodic, then call do_periodic(), sleep 30 seconds, repeat',
    )
    args = argparser.parse_args(argv)
    if args.print_version:
        print(__version__)
        return 0

    if args.verbose:
        os.environ['PYTHONASYNCIODEBUG'] = '1'
        logging.basicConfig(
            level=logging.DEBUG, filename='rerobots_apiw.log', filemode='w'
        )
        rootlogger = logging.getLogger()
        rootlogger.addHandler(logging.StreamHandler())

    if args.do_periodic_tasks:
        do_periodic()
        if args.periodic_sleep_poll:
            try:
                while True:
                    time.sleep(15)
                    do_periodic()
            except KeyboardInterrupt:
                pass

    else:
        web.run_app(application, host=args.host, port=args.port)

    return 0


if __name__ == '__main__':
    sys.exit(main_cli(sys.argv[1:]))
