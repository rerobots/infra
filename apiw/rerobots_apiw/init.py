"""
SCL <scott@rerobots>
Copyright (C) 2022 rerobots, Inc.
"""

import logging
import logging.handlers
import os
from socket import getfqdn

from rerobots_infra import RLogSenderHandler
from . import __version__
from .settings import DEBUG, SENTRY_DSN


def init_sentry():
    """

    No-op if DEBUG
    """
    if not DEBUG:
        import sentry_sdk
        from sentry_sdk.integrations.aiohttp import AioHttpIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            release=__version__,
            integrations=[
                AioHttpIntegration(),
                CeleryIntegration(),
                RedisIntegration(),
                SqlalchemyIntegration(),
            ],
        )


def init_logging():
    logger = logging.getLogger('rerobots_apiw')
    logger.setLevel(logging.INFO)
    loghandler = logging.handlers.WatchedFileHandler(
        filename='rerobots_apiw.{}.log'.format(os.getpid()), mode='a'
    )
    loghandler.setFormatter(
        logging.Formatter(
            '%(asctime)s ; %(name)s.%(funcName)s (%(levelname)s) (pid: {}); %(message)s'.format(
                os.getpid()
            )
        )
    )
    loghandler.setLevel(logging.DEBUG)
    logger.addHandler(loghandler)
    stdouthandler = logging.StreamHandler()
    stdouthandler.setFormatter(
        logging.Formatter(
            '%(asctime)s ; %(name)s.%(funcName)s (%(levelname)s) (pid: {}); %(message)s'.format(
                os.getpid()
            )
        )
    )
    stdouthandler.setLevel(logging.DEBUG)
    logger.addHandler(stdouthandler)
    logging.getLogger('rerobots_infra').addHandler(loghandler)
    logging.getLogger('aiohttp').addHandler(loghandler)

    if not DEBUG:
        logsendhandler = RLogSenderHandler('aw', '{} {}'.format(getfqdn(), os.getpid()))
        logsendhandler.setFormatter(
            logging.Formatter(
                '%(asctime)s ; %(name)s.%(funcName)s (%(levelname)s); %(message)s'
            )
        )
        logsendhandler.setLevel(logging.INFO)
        logger.addHandler(logsendhandler)
        logging.getLogger('rerobots_infra').addHandler(logsendhandler)

    logger.info('this is version {}'.format(__version__))
