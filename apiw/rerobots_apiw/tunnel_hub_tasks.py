"""
SCL <scott@rerobots>
Copyright (C) 2022 rerobots, Inc.
"""

import json
import os
import subprocess
import tempfile

from celery.utils.log import get_task_logger
import pika

from .celery import app as capp
from . import db as rrdb
from .util import get_container_addr
from . import settings


logger = get_task_logger(__name__)


@capp.task
def create_sshtun(instance_id, pubkey, request_id=None, proxy_mode=False):
    if settings.TH_CONTAINER_PROVIDER == 'podman':
        caps_add = ['--cap-add=CAP_SYS_CHROOT']
    else:
        caps_add = None

    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        wdeployment_id = instance.deploymentid

        if instance.associated_th != '':
            sshtun_container_id = instance.associated_th
            matches = subprocess.check_output(
                [
                    settings.TH_CONTAINER_PROVIDER,
                    'ps',
                    '-f',
                    'id=' + sshtun_container_id,
                ],
                universal_newlines=True,
            ).strip()
            if len(matches) > 0:
                subprocess.check_call(
                    [settings.TH_CONTAINER_PROVIDER, 'rm', '-f', sshtun_container_id]
                )
                instance.associated_th = ''
                session.commit()
            else:
                logger.warning(
                    'expected Docker container {} not found'.format(sshtun_container_id)
                )

        sshtun_container_id = None
        try:
            if proxy_mode:
                listening_part_option = '127.0.0.1::2210'
            else:
                listening_part_option = '2210'

            run_command = [
                settings.TH_CONTAINER_PROVIDER,
                'run',
                '-d',
                '--rm',
                '-p',
                '22/tcp',
                '-p',
                listening_part_option,
            ]
            if caps_add:
                run_command.extend(caps_add)
            run_command.append('rerobots-infra/th-sshtunnel')
            cp = subprocess.run(
                run_command, stdout=subprocess.PIPE, universal_newlines=True
            )
            cp.check_returncode()
            sshtun_container_id = cp.stdout.strip()

            if proxy_mode:
                th_hostname = get_container_addr(sshtun_container_id) or '127.0.0.1'
            else:
                th_hostname = 'api.rerobots.net'  # TODO: support other hosts

            cp = subprocess.run(
                [settings.TH_CONTAINER_PROVIDER, 'port', sshtun_container_id, '22'],
                stdout=subprocess.PIPE,
            )
            if cp.returncode != 0:
                raise Exception('failed to get port number')
            _, th_infra_port = (
                cp.stdout.decode('utf-8').split('\n')[0].strip().split(':')
            )
            cp = subprocess.run(
                [settings.TH_CONTAINER_PROVIDER, 'port', sshtun_container_id, '2210'],
                stdout=subprocess.PIPE,
            )
            if cp.returncode != 0:
                raise Exception('failed to get port number')
            _, port = cp.stdout.decode('utf-8').split('\n')[0].strip().split(':')

            fd, fname = tempfile.mkstemp()
            fp = os.fdopen(fd, 'wt')
            fp.write(pubkey + '\n')
            fp.close()
            cp = subprocess.run(
                [
                    settings.TH_CONTAINER_PROVIDER,
                    'cp',
                    fname,
                    sshtun_container_id + ':/root/.ssh/authorized_keys',
                ],
                universal_newlines=True,
            )
            os.unlink(fname)
            cp.check_returncode()

            instance.associated_th = sshtun_container_id
            instance.listening_ipaddr = settings.TH_ADDR
            instance.listening_port = port
            instance.th_infra_port = th_infra_port
            instance.th_hostname = th_hostname

        except Exception as err:
            logger.error('caught {}: {}'.format(type(err), err))
            if sshtun_container_id is not None:
                subprocess.check_call(
                    [settings.TH_CONTAINER_PROVIDER, 'rm', '-f', sshtun_container_id]
                )
            raise

    if request_id is not None:
        param = pika.ConnectionParameters(
            host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
        )
        eacommand_conn = pika.BlockingConnection(param)
        eacommand_chan = eacommand_conn.channel()
        eax_name = 'eacommand.{}'.format(wdeployment_id)

        result = {
            'command': 'RES',
            'rid': request_id,
            'iid': instance_id,
            'ipv4': settings.TH_ADDR,
            'hostkey': settings.TH_HOSTKEY,
            'port': port,
            'thport': th_infra_port,
        }
        eacommand_chan.basic_publish(
            exchange=eax_name, routing_key='', body=json.dumps(result)
        )


@capp.task
def destroy_sshtun(instance_id):
    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        # For backwards-compatibility, if '-' in associated_th, assume it is not Docker container ID
        if instance.associated_th != '' and '-' not in instance.associated_th:
            sshtun_container_id = instance.associated_th
            matches = subprocess.check_output(
                [
                    settings.TH_CONTAINER_PROVIDER,
                    'ps',
                    '-f',
                    'id=' + sshtun_container_id,
                ],
                universal_newlines=True,
            ).strip()
            if len(matches) > 0:
                subprocess.check_call(
                    [settings.TH_CONTAINER_PROVIDER, 'rm', '-f', sshtun_container_id]
                )
            else:
                logger.warning(
                    'expected Docker container {} not found'.format(sshtun_container_id)
                )
