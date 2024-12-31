"""
SCL <scott@rerobots>
Copyright (C) 2021 rerobots, Inc.
"""

from celery import Celery
from celery import signals
from celery.utils.log import get_task_logger

from .init import init_sentry
from . import __version__
from . import settings


logger = get_task_logger(__name__)


CELERY_BROKER_URL = f'amqp://{settings.AMQP_HOST}:{settings.AMQP_PORT}'
if settings.RUNTIME_ENVIRON in ['development', 'mock']:
    # Facilitate using the same RabbitMQ server as WebUI in development
    CELERY_BROKER_URL += '/core'

celeryconfig = {
    'broker_url': CELERY_BROKER_URL,
    'broker_transport_options': {
        'max_retries': 2,
    },
    'enable_utc': True,
    'timezone': 'UTC',
    'beat_scheduler': 'celery.beat:PersistentScheduler',
    'beat_schedule': {
        'monitor-heartbeats': {
            'task': 'rerobots_apiw.monitor_tasks.check_heartbeats',
            'schedule': 30.0,
            'args': (),
        },
        'monitor-instance-keepalives': {
            'task': 'rerobots_apiw.monitor_tasks.check_instance_keepalives',
            'schedule': 15.0,
            'args': (),
        },
    },
    'task_routes': {
        'rerobots_apiw.proxy_tasks.*': {'queue': 'proxy'},
        'rerobots_apiw.tunnel_hub_tasks.*': {'queue': 'tunnel'},
    },
}

app = Celery(
    'apiw',
    broker='amqp://',
    include=[
        'rerobots_apiw.tasks',
        'rerobots_apiw.monitor_tasks',
        'rerobots_apiw.notify',
        'rerobots_apiw.proxy_tasks',
        'rerobots_apiw.tunnel_hub_tasks',
        'rerobots_apiw.addons.tasks',
    ],
)
app.config_from_object(celeryconfig)


@signals.after_setup_logger.connect
def after_setup_logger(sender=None, conf=None, **kwargs):
    logger.info('this is version {}'.format(__version__))


@signals.worker_ready.connect
def when_ready(**kwargs):
    init_sentry()
