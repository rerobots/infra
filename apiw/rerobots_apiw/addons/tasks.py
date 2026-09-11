"""
SCL <scott@rerobots>
Copyright (C) 2023 rerobots, Inc.
"""

import json
import os
import subprocess
import tempfile
import time
import uuid

from celery.utils.log import get_task_logger
import pika
import redis

from ..celery import app as capp
from .. import db as rrdb
from .. import settings


logger = get_task_logger(__name__)


@capp.task(bind=True)
def start_cmdsh(self, user, instance_id, host_id, token):
    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to DELETE while status=`starting`

    while True:
        logger.info('checking instance status...')
        with rrdb.create_session_context() as session:
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            if instance_status in ['INIT_FAIL', 'TERMINATING', 'TERMINATED']:
                logger.warning('instance is not in feasible status')
                return

            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == '{}:cmdsh'.format(instance_id),
                )
                .one_or_none()
            )
            if activeaddon is None:
                time.sleep(1)
                continue
            addon_config = json.loads(activeaddon.config)

            ssh_privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port
            wdeployment_id = instance.deploymentid

        if instance_status == 'READY' and len(ipv4) > 0:
            logger.info(
                'instance READY with IPv4 addr {} and port {}'.format(ipv4, port)
            )
            break

        time.sleep(1)

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    msg_id = str(uuid.uuid4())

    param = pika.ConnectionParameters(
        host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
    )
    eacommand_conn = pika.BlockingConnection(param)
    eacommand_chan = eacommand_conn.channel()
    eax_name = 'eacommand.{}'.format(wdeployment_id)

    eacommand_chan.basic_publish(
        exchange=eax_name,
        routing_key='',
        body=json.dumps(
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'hid': host_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/jwt.txt',
                'content': token,
            }
        ),
    )

    max_tries = 30
    count = 0
    while (count < max_tries) and (not red.exists(msg_id)):
        count += 1
        time.sleep(1)
    blob = red.get(msg_id)
    if blob is None or blob == b'NACK':
        logger.warning('no ACK of `PUT FILE` jwt.txt from workspace deployment')
        return

    msg_id = str(uuid.uuid4())
    eacommand_chan.basic_publish(
        exchange=eax_name,
        routing_key='',
        body=json.dumps(
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'hid': host_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/cmdshr',
                'content': open('addons/cmdsh/cmdshr', 'rt').read(),
            }
        ),
    )
    max_tries = 30
    count = 0
    while (count < max_tries) and (not red.exists(msg_id)):
        count += 1
        time.sleep(1)
    blob = red.get(msg_id)
    if blob is None or blob == b'NACK':
        logger.warning('no ACK of `PUT FILE` cmdshr from workspace deployment')
        return

    argv = [
        'sh',
        '-c',
        'cd ~ && python3 cmdshr {} {} jwt.txt'.format(instance_id, host_id),
    ]
    msg_id = str(uuid.uuid4())
    eacommand_chan.basic_publish(
        exchange=eax_name,
        routing_key='',
        body=json.dumps(
            {
                'command': 'EXEC INSIDE',
                'iid': instance_id,
                'hid': host_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'argv': argv,
            }
        ),
    )
    max_tries = 30
    count = 0
    while (count < max_tries) and (not red.exists(msg_id)):
        count += 1
        time.sleep(1)
    blob = red.get(msg_id)
    if blob is None or blob == b'NACK':
        logger.warning(
            'no ACK of `EXEC INSIDE` {} from workspace deployment'.format(argv)
        )
        return

    addon_config['hstatus'][host_id] = 'active'
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:cmdsh'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)
