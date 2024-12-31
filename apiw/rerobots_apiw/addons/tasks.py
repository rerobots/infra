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

    COMPATIBLE_WITH_NEW_SEND = [
        '5e9de205-2208-4022-8bd6-b104b740c270',
        'f74470ba-e486-474c-aceb-050ac2d82ef9',
        '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
    ]

    while True:
        logger.info('checking instance status...')
        with rrdb.create_session_context() as session:
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            if instance_status in ['TERMINATING', 'TERMINATED']:
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

    if wdeployment_id in COMPATIBLE_WITH_NEW_SEND:
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

    else:
        # NOTE: host_id != 0 is not implemented for the case of SSH-based add-on management, which is deprecated anyway.
        # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
        # provide key text via stdin of child process.
        tmp_fd, privatekey_path = tempfile.mkstemp()
        privatekey_file = os.fdopen(tmp_fd, 'w')
        privatekey_file.write(ssh_privatekey)
        privatekey_file.close()

        tmp_fd, tmp_token_path = tempfile.mkstemp()
        tmp_token_file = os.fdopen(tmp_fd, 'w')
        tmp_token_file.write(token)
        tmp_token_file.close()

        scp_cmd_prefix = [
            'scp',
            '-o',
            'UserKnownHostsFile=/dev/null',
            '-o',
            'StrictHostKeyChecking=no',
            '-i',
            privatekey_path,
            '-P',
            str(port),
        ]
        scp_token_cmd = scp_cmd_prefix + [
            tmp_token_path,
            '{}@{}:~/jwt.txt'.format(addon_config['user'], ipv4),
        ]
        scp_up_cmd = scp_cmd_prefix + [
            'addons/cmdsh/cmdshr',
            '{}@{}:~/'.format(addon_config['user'], ipv4),
        ]
        commands = [scp_token_cmd, scp_up_cmd]
        retry_count = 0
        while retry_count < 3 and len(commands) > 0:
            scp_cmd = commands.pop()
            logger.info('exec: {}'.format(scp_cmd))
            rc = subprocess.call(scp_cmd)
            if rc != 0:
                logger.warning(
                    'returncode of subprocess `{}` is {}'.format(' '.join(scp_cmd), rc)
                )
                commands.append(scp_cmd)
                retry_count += 1
        if retry_count >= 3:
            self.retry(countdown=10, max_retries=1)

    if wdeployment_id in COMPATIBLE_WITH_NEW_SEND:
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

    else:
        # NOTE: host_id != 0 is not implemented for the case of SSH-based add-on management, which is deprecated anyway.
        cmdshr_call_prefix = [
            'ssh',
            '-T',
            '-o',
            'UserKnownHostsFile=/dev/null',
            '-o',
            'StrictHostKeyChecking=no',
            '-i',
            privatekey_path,
            '-p',
            str(port),
            '{}@{}'.format(addon_config['user'], ipv4),
        ]
        cmdshr_call = cmdshr_call_prefix + [
            'python3',
            'cmdshr',
            instance_id,
            host_id,
            'jwt.txt',
        ]
        retry_count = 0
        while retry_count < 3:
            logger.info('exec: {}'.format(cmdshr_call))
            rc = subprocess.call(
                cmdshr_call,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if rc == 0:
                break
            logger.warning(
                'returncode of subprocess `{}` is {}'.format(' '.join(cmdshr_call), rc)
            )
            retry_count += 1
        if retry_count >= 3:
            self.retry(countdown=10, max_retries=1)

        os.unlink(privatekey_path)
        os.unlink(tmp_token_path)

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
