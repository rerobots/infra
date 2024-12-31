"""
Copyright (C) 2022 rerobots, Inc.
"""

from datetime import datetime, timedelta

from celery.utils.log import get_task_logger

from . import db as rrdb
from .celery import app as capp
from .notify import to_admins
from .tasks import terminate_instance
from .util import now


logger = get_task_logger(__name__)


@capp.task
def check_heartbeats():
    with rrdb.create_session_context() as session:
        for rule in session.query(rrdb.WDMonitorHeartbeat).filter(
            rrdb.WDMonitorHeartbeat.alert_sent == None
        ):
            wd = (
                session.query(rrdb.Deployment)
                .filter(rrdb.Deployment.deploymentid == rule.deploymentid)
                .one()
            )
            if wd.date_dissolved is not None:
                logger.warning(
                    f'skipping heartbeat check of dissolved deployment {rule.deploymentid}'
                )
                continue
            if datetime.utcnow() - wd.last_heartbeat >= timedelta(seconds=30):
                wd.locked_out = True
                session.commit()
                # TODO: support other users
                to_admins(f'lost contact with deployment {rule.deploymentid}')
                rule.alert_sent = now()


@capp.task
def check_instance_keepalives():
    with rrdb.create_session_context() as session:
        for ka in session.query(rrdb.InstanceKeepAlive):
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == ka.instanceid)
                .one_or_none()
            )
            if instance is None:
                logger.error(
                    f'keepalive instance row for missing instance: {ka.instanceid}'
                )
                continue
            if instance.status in ['INIT_FAIL', 'TERMINATING', 'TERMINATED']:
                session.delete(ka)
                continue
            if now() - ka.last_ping >= timedelta(seconds=60):
                session.delete(ka)
                terminate_instance.delay(
                    instance_id=instance.instanceid,
                    wdeployment_id=instance.deploymentid,
                )
