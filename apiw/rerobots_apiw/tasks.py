"""
SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import asyncio
from datetime import datetime, timedelta
import json
import logging
import os
import random
import subprocess
import tempfile
import time
import uuid

from celery.utils.log import get_task_logger
import jwt
import pika
import requests
import sqlalchemy

import redis

from .addons import addon_vnc_stop_job, addon_vnc_waitdelete_job, addon_cam_stop_job
from .addons import addon_drive_stop_job, addon_cmd_stop_job
from .addons.cmdsh import stop_job as addon_cmdsh_stop_job
from .addons.wstcp import addon_wstcp_stop_job, addon_wstcp_waitdelete_job
from . import db as rrdb
from .celery import app as capp
from . import proxy_tasks
from . import notify
from .tunnel_hub_tasks import destroy_sshtun
from . import settings


logger = get_task_logger(__name__)


class Error(Exception):
    """Error not otherwise specified"""

    pass


async def cleanup_terminated_instance(instance_id):
    logger.debug('enter cleanup_terminated_instance({})'.format(instance_id))
    addons_to_deactivate = []
    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        wdeployment_id = instance.deploymentid
        rootuser = instance.rootuser
        if not instance.has_vpn:
            destroy_sshtun.delay(instance_id)

        query = session.query(rrdb.ActiveAddon).filter(
            rrdb.ActiveAddon.instanceid_with_addon.like(instance_id + '%')
        )
        for row in query:
            logger.info('found active addon: {}'.format(row.instanceid_with_addon))
            config = json.loads(row.config)
            if row.instanceid_with_addon.endswith(':vnc'):
                addons_to_deactivate.append(
                    {
                        'addon': 'vnc',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':wstcp'):
                addons_to_deactivate.append(
                    {
                        'addon': 'wstcp',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':cam'):
                addons_to_deactivate.append(
                    {
                        'addon': 'cam',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':mistyproxy'):
                addons_to_deactivate.append(
                    {
                        'addon': 'mistyproxy',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':py'):
                addons_to_deactivate.append(
                    {
                        'addon': 'py',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':java'):
                addons_to_deactivate.append(
                    {
                        'addon': 'java',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':vscode'):
                addons_to_deactivate.append(
                    {
                        'addon': 'vscode',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':drive'):
                addons_to_deactivate.append(
                    {
                        'addon': 'drive',
                        'status': config['status'],
                    }
                )
            elif row.instanceid_with_addon.endswith(':cmd'):
                addons_to_deactivate.append(
                    {
                        'addon': 'cmd',
                        'status': config['status'],
                    }
                )
            elif ':cmdsh' in row.instanceid_with_addon:
                addons_to_deactivate.append(
                    {
                        'addon': 'cmdsh',
                        'status': config['status'],
                    }
                )

    for addon_to_deactivate in addons_to_deactivate:
        if addon_to_deactivate['addon'] == 'vnc':
            if (
                addon_to_deactivate['status'] == 'active'
            ):  # TODO: case of status = `starting`
                logger.info('await addon_vnc_stop_job({})'.format(instance_id))
                await addon_vnc_stop_job(user=rootuser, instance_id=instance_id)
            logger.info('await addon_vnc_waitdelete_job({})'.format(instance_id))
            await addon_vnc_waitdelete_job(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'wstcp':
            if (
                addon_to_deactivate['status'] == 'active'
            ):  # TODO: case of status = `starting`
                logger.info('await addon_wstcp_stop_job({})'.format(instance_id))
                await addon_wstcp_stop_job(user=rootuser, instance_id=instance_id)
            logger.info('await addon_wstcp_waitdelete_job({})'.format(instance_id))
            await addon_wstcp_waitdelete_job(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'cam':
            logger.info('await addon_cam_stop_job({})'.format(instance_id))
            await addon_cam_stop_job(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'mistyproxy':
            logger.info(f'stop_mistyproxy({rootuser}, {instance_id})')
            proxy_tasks.stop_mistyproxy.delay(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'java':
            logger.info(f'stop_minidevel_java({rootuser}, {instance_id})')
            proxy_tasks.stop_minidevel_java.delay(
                user=rootuser, instance_id=instance_id
            )
        elif addon_to_deactivate['addon'] == 'py':
            logger.info(f'stop_minidevel_py({rootuser}, {instance_id})')
            proxy_tasks.stop_minidevel_py.delay(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'vscode':
            logger.info(f'stop_vscode({rootuser}, {instance_id})')
            proxy_tasks.stop_vscode.delay(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'drive':
            logger.info('await addon_drive_stop_job({})'.format(instance_id))
            await addon_drive_stop_job(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'cmd':
            logger.info('await addon_cmd_stop_job({})'.format(instance_id))
            await addon_cmd_stop_job(user=rootuser, instance_id=instance_id)
        elif addon_to_deactivate['addon'] == 'cmdsh':
            logger.info('await .cmdsh.stop_job({})'.format(instance_id))
            await addon_cmdsh_stop_job(user=rootuser, instance_id=instance_id)
        else:
            logger.error('unexpected addon: {}'.format(addon_to_deactivate))

    wait_queue_available.delay(wdeployment_id)


@capp.task
def terminate_instance(instance_id, wdeployment_id):
    with rrdb.create_session_context() as session:
        query = session.query(rrdb.Instance).filter(
            rrdb.Instance.instanceid == instance_id
        )
        inst = query.one_or_none()
        if inst is None:
            logger.warning('called with unknown instance {}'.format(instance_id))
            return
        if inst.status == 'TERMINATED':
            # No-op if already terminated
            return
        if inst.status != 'TERMINATING':
            inst.terminating_started_at = datetime.utcnow()
            inst.status = 'TERMINATING'

    if_stuck_terminating.apply_async((instance_id,), countdown=70)

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    param = pika.ConnectionParameters(
        host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
    )
    eacommand_conn = pika.BlockingConnection(param)
    eacommand_chan = eacommand_conn.channel()

    param = pika.ConnectionParameters(
        host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
    )
    pam_conn = pika.BlockingConnection(param)
    pam_chan = pam_conn.channel()
    pam_chan.exchange_declare('portaccess.th', exchange_type='fanout')

    logger.info('sending `INSTANCE DESTROY` to wdeployment {}'.format(wdeployment_id))
    msg_id = str(uuid.uuid4())
    eacommand_chan.basic_publish(
        exchange='eacommand.{}'.format(wdeployment_id),
        routing_key='',
        body=json.dumps(
            {
                'command': 'INSTANCE DESTROY',
                'did': wdeployment_id,
                'iid': instance_id,
                'message_id': msg_id,
            }
        ),
    )
    max_tries = 10
    count = 0
    while (count < max_tries) and (not red.exists(msg_id)):
        count += 1
        time.sleep(6)
    done = False
    nack_err = 'received NACK response to `INSTANCE DESTROY` from {}'.format(
        wdeployment_id
    )
    if red.type(msg_id) != b'hash':
        blob = red.get(msg_id)
        if blob == b'NACK':
            logger.error(nack_err)
            return
        if blob is None:
            m = 'timed out waiting for response to `INSTANCE DESTROY` from {}, current instance {}'.format(
                wdeployment_id, instance_id
            )
            notify.to_admins.delay(m)
            logger.error(m)
            return
    else:
        result = red.hget(msg_id, 'result')
        if result == b'NACK':
            logger.error(nack_err)
            return
        if red.hexists(msg_id, 'st') and red.hget(msg_id, 'st') == b'DONE':
            done = True
    red.delete(msg_id)

    firewall_end = {'command': 'END', 'wdID': wdeployment_id}
    pam_chan.basic_publish(
        exchange='portaccess.th', routing_key='', body=json.dumps(firewall_end)
    )

    if not done:
        max_tries = 10
        count = 0
        while (count < max_tries) and (not red.exists(msg_id)):
            count += 1
            time.sleep(6)
        # If another arrives, it must be `st: DONE`

    with rrdb.create_session_context() as session:
        row = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        row.endtime = datetime.utcnow()
        row.status = 'TERMINATED'
        event_url = row.event_url
        user = row.rootuser
        wdeployment = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wdeployment.instance_counter = rrdb.Deployment.instance_counter + 1
        session.commit()

        exp = (
            session.query(rrdb.InstanceExpiration)
            .filter(rrdb.InstanceExpiration.instance_id == instance_id)
            .one_or_none()
        )
        if exp is not None:
            session.delete(exp)

    if len(event_url) > 0:
        payload = {
            'id': instance_id,
            'wd': wdeployment_id,
            'kind': 'instance status',
            'val': 'TERMINATED',
            'user': user,
        }
        notify_user(event_url=event_url, payload=payload)

    loop = asyncio.new_event_loop()

    success = False
    for attempts in range(5):
        try:
            loop.run_until_complete(
                cleanup_terminated_instance(instance_id=instance_id)
            )
            success = True
            break
        except Exception as err:
            logger.warning(f'caught {type(err)}: {err}; will try again...')
            loop.run_until_complete(asyncio.sleep(5))

    if not success:
        logger.error(
            'failed to call cleanup routines for instance {}'.format(instance_id)
        )
        return

    loop.stop()
    loop.run_forever()
    loop.close()

    eacommand_chan.close()
    eacommand_conn.close()
    pam_chan.close()
    pam_conn.close()


def do_periodic():
    with rrdb.create_session_context() as session:
        for row in session.query(rrdb.InstanceExpiration):
            inst = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == row.instance_id)
                .one()
            )
            if inst.status in ['TERMINATING', 'TERMINATED', 'INIT_FAIL']:
                session.delete(row)
            elif (datetime.utcnow() - inst.starttime).seconds >= row.target_duration:
                # TODO: send notification if event url nonempty
                logger.debug(
                    'detected that instance {} should expire!'.format(inst.instanceid)
                )
                inst.terminating_started_at = datetime.utcnow()
                inst.status = 'TERMINATING'
                terminate_instance.delay(inst.instanceid, inst.deploymentid)


@capp.task
def expire_instance(instance_id):
    with rrdb.create_session_context() as session:
        exp = (
            session.query(rrdb.InstanceExpiration)
            .filter(rrdb.InstanceExpiration.instance_id == instance_id)
            .one_or_none()
        )
        if exp is None:
            return
        inst = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        if inst.status in ['TERMINATING', 'TERMINATED', 'INIT_FAIL']:
            session.delete(exp)
            return
        tdiff = (datetime.utcnow() - inst.starttime).seconds
        if tdiff < exp.target_duration:
            logger.warning('called too early; scheduling again to later')
            expire_instance.apply_async(
                (instance_id,), countdown=(exp.target_duration - tdiff + 5)
            )
            return
        session.delete(exp)
        # TODO: send notification if event url nonempty
        inst.terminating_started_at = datetime.utcnow()
        inst.status = 'TERMINATING'
        terminate_instance.delay(inst.instanceid, inst.deploymentid)


@capp.task(bind=True)
def if_stuck_init(self, instance_id):
    now = datetime.utcnow()
    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one_or_none()
        )
        if instance is None:
            self.retry(countdown=60, max_retries=1)
        if instance.status != 'INIT':
            return
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == instance.deploymentid)
            .one()
        )
        if instance.status == 'INIT' and now - instance.starttime > timedelta(
            seconds=60
        ):
            wd.locked_out = True
            instance.status = 'INIT_FAIL'


@capp.task(bind=True)
def if_stuck_terminating(self, instance_id):
    now = datetime.utcnow()
    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        if instance.status != 'TERMINATING':
            return
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == instance.deploymentid)
            .one()
        )
        if (
            instance.status == 'TERMINATING'
            and now - instance.terminating_started_at > timedelta(seconds=60)
        ):
            wd.locked_out = True
            instance.status = 'TERMINATED'


@capp.task(bind=True)
def launch_instance(
    self,
    wdeployment_id,
    instance_id,
    user,
    ssh_publickey=None,
    has_vpn=False,
    expire_d=0,
    eurl=None,
    repo_args=None,
    start_keepalive=False,
):
    if eurl is None:
        eurl = ''

    pathname = tempfile.mkdtemp(dir=os.getcwd())
    subprocess.check_call(
        ['ssh-keygen', '-f', os.path.join(pathname, 'generatedkey'), '-N', '']
    )
    with open(os.path.join(pathname, 'generatedkey.pub'), 'rt') as fp:
        generated_ssh_publickey = fp.read()
    with open(os.path.join(pathname, 'generatedkey'), 'rt') as fp:
        ssh_privatekey = fp.read()
    os.unlink(os.path.join(pathname, 'generatedkey'))
    os.unlink(os.path.join(pathname, 'generatedkey.pub'))
    os.rmdir(pathname)

    user_provided_publickey = ssh_publickey is not None
    if ssh_publickey is None:
        ssh_publickey = generated_ssh_publickey
    else:
        ssh_publickey = '\n'.join([generated_ssh_publickey, ssh_publickey])

    # Immediately fail if other active instances
    with rrdb.create_session_context() as session:
        current_count = (
            session.query(rrdb.Instance)
            .filter(
                sqlalchemy.and_(
                    rrdb.Instance.deploymentid == wdeployment_id,
                    rrdb.Instance.instanceid != instance_id,
                    rrdb.Instance.status != 'INIT_FAIL',
                    rrdb.Instance.status != 'TERMINATING',
                    rrdb.Instance.status != 'TERMINATED',
                )
            )
            .count()
        )
        if current_count > 0:
            try:
                session.add(
                    rrdb.Instance(
                        deploymentid=wdeployment_id,
                        instanceid=instance_id,
                        rootuser=user,
                        starttime=datetime.utcnow(),
                        status='INIT_FAIL',
                        has_vpn=has_vpn,
                        ssh_privatekey=ssh_privatekey,
                        event_url=eurl,
                    )
                )
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                inst = (
                    session.query(rrdb.Instance)
                    .filter(rrdb.Instance.instanceid == instance_id)
                    .one()
                )
                if inst.status == 'INIT':
                    inst.status = 'INIT_FAIL'
                    session.commit()
            finally:
                return

        # Try to get from the table, else insert
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one_or_none()
        )
        if instance is None:
            instance = rrdb.Instance(
                deploymentid=wdeployment_id,
                instanceid=instance_id,
                rootuser=user,
                starttime=datetime.utcnow(),
                status='INIT',
                has_vpn=has_vpn,
                ssh_privatekey=ssh_privatekey,
                event_url=eurl,
            )
            session.add(instance)
            if start_keepalive:
                session.add(rrdb.InstanceKeepAlive(instanceid=instance_id))

        elif instance.status != 'INIT':
            logger.error(
                'instance {} not INIT at start of launch job'.format(instance_id)
            )
            return

        if expire_d > 0:
            exp = (
                session.query(rrdb.InstanceExpiration)
                .filter(rrdb.InstanceExpiration.instance_id == instance_id)
                .one_or_none()
            )
            if exp is None:
                exp = rrdb.InstanceExpiration(
                    instance_id=instance_id, target_duration=expire_d, event_url=eurl
                )
                session.add(exp)

        session.commit()

    # At this point, it is possible that there are other instances in
    # status INIT or active for this wdeployment. Therefore, check for
    # this in a loop doing a random INIT_FAIL until this is the only
    # INIT or if some other instance progessed to active status. If
    # timeout occurs, INIT_FAIL.
    BACKOFF_MAX_DURATION = 10
    start_t = time.monotonic()
    while time.monotonic() - start_t < BACKOFF_MAX_DURATION:
        with rrdb.create_session_context() as session:
            current_count = (
                session.query(rrdb.Instance)
                .filter(
                    sqlalchemy.and_(
                        rrdb.Instance.deploymentid == wdeployment_id,
                        rrdb.Instance.instanceid != instance_id,
                        rrdb.Instance.status == 'INIT',
                    )
                )
                .count()
            )
        if current_count == 0:
            break
        if current_count > 0 and random.random() < 1.0 / (current_count + 2):
            try:
                notify.to_admins.delay(
                    'INIT_FAIL instance {} on wd {}\nfor user {}'.format(
                        instance_id, wdeployment_id, user
                    )
                )
                with rrdb.create_session_context() as session:
                    inst = (
                        session.query(rrdb.Instance)
                        .filter(rrdb.Instance.instanceid == instance_id)
                        .one()
                    )
                    inst.status = 'INIT_FAIL'
                if len(eurl) > 0:
                    payload = {
                        'id': instance_id,
                        'wd': wdeployment_id,
                        'kind': 'instance status',
                        'val': 'INIT_FAIL',
                        'user': user,
                    }
                    notify_user(event_url=eurl, payload=payload)
            except sqlalchemy.exc.IntegrityError:
                with rrdb.create_session_context() as session:
                    inst = (
                        session.query(rrdb.Instance)
                        .filter(rrdb.Instance.instanceid == instance_id)
                        .one()
                    )
                    inst.status = 'INIT_FAIL'
            return

    # Past initial checks, send notification of INIT
    if len(eurl) > 0:
        payload = {
            'id': instance_id,
            'wd': wdeployment_id,
            'kind': 'instance status',
            'val': 'INIT',
            'user': user,
        }
        if not user_provided_publickey:
            payload['sshkey'] = ssh_privatekey
        notify_user(event_url=eurl, payload=payload)

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    param = pika.ConnectionParameters(
        host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
    )
    eacommand_conn = pika.BlockingConnection(param)
    eacommand_chan = eacommand_conn.channel()
    eax_name = 'eacommand.{}'.format(wdeployment_id)

    param = pika.ConnectionParameters(
        host=settings.AMQP_HOST, port=settings.AMQP_PORT, heartbeat=20
    )
    pam_conn = pika.BlockingConnection(param)
    pam_chan = pam_conn.channel()
    pam_chan.exchange_declare('portaccess.th', exchange_type='fanout')

    msg_id = str(uuid.uuid4())
    try:
        msg = {
            'command': 'INSTANCE LAUNCH',
            'iid': instance_id,
            'did': wdeployment_id,
            'publickey': ssh_publickey,
            'vpn': has_vpn,
            'message_id': msg_id,
            'user': user,
        }
        if repo_args:
            msg['repo_url'] = repo_args['url']
            if repo_args['path']:
                msg['repo_path'] = repo_args['path']
        eacommand_chan.basic_publish(
            exchange=eax_name, routing_key='', body=json.dumps(msg)
        )
    except Exception as err:
        logger.warning(f'caught {type(err)}: {err}')
        notify.to_admins.delay(
            'INIT_FAIL instance {} on wd {}\nfor user {}'.format(
                instance_id, wdeployment_id, user
            )
        )
        with rrdb.create_session_context() as session:
            inst = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            inst.status = 'INIT_FAIL'
        raise

    max_tries = 6
    count = 0
    while (count < max_tries) and (not red.exists(msg_id)):
        count += 1
        time.sleep(5)
    if red.type(msg_id) != b'hash':
        blob = red.get(msg_id)
        if blob is None:
            notify.to_admins.delay(
                'INIT_FAIL instance {} on wd {}\nfor user {}'.format(
                    instance_id, wdeployment_id, user
                )
            )
            with rrdb.create_session_context() as session:
                inst = (
                    session.query(rrdb.Instance)
                    .filter(rrdb.Instance.instanceid == instance_id)
                    .one()
                )
                inst.status = 'INIT_FAIL'
            logger.error(
                'timeout waiting for wdeployment {} response for instance {}'.format(
                    wdeployment_id, instance_id
                )
            )
            return
        if blob == b'NACK':
            notify.to_admins.delay(
                'INIT_FAIL instance {} on wd {}\nfor user {}'.format(
                    instance_id, wdeployment_id, user
                )
            )
            with rrdb.create_session_context() as session:
                inst = (
                    session.query(rrdb.Instance)
                    .filter(rrdb.Instance.instanceid == instance_id)
                    .one()
                )
                inst.status = 'INIT_FAIL'
            logger.error(
                'wdeployment {} denied launch of instance {}'.format(
                    wdeployment_id, instance_id
                )
            )
            return
    else:
        result = red.hget(msg_id, 'result')
        if result != b'ACK':
            st = red.hget(msg_id, 'st')
            if st == b'TERMINATING':
                logger.warning(
                    'wdeployment {} terminating; retrying in 30 s'.format(
                        wdeployment_id
                    )
                )
                self.retry(countdown=30, max_retries=2)
            else:
                notify.to_admins.delay(
                    'INIT_FAIL instance {} on wd {}\nfor user {}'.format(
                        instance_id, wdeployment_id, user
                    )
                )
                with rrdb.create_session_context() as session:
                    inst = (
                        session.query(rrdb.Instance)
                        .filter(rrdb.Instance.instanceid == instance_id)
                        .one()
                    )
                    inst.status = 'INIT_FAIL'
                logger.error(
                    'wdeployment {} denied launch of instance {}, requires manual check'.format(
                        wdeployment_id, instance_id
                    )
                )
                return

    # blob == b'ACK', so send final ACK of ACK.
    # Endpoint arbiter does not complete instantiation until this arrives.
    logger.info('sending final ACK')
    eacommand_chan.basic_publish(
        exchange=eax_name,
        routing_key='',
        body=json.dumps(
            {
                'command': 'ACK',
                'iid': instance_id,
                'did': wdeployment_id,
                'message_id': msg_id,
            }
        ),
    )
    firewall_start = {'command': 'START', 'wdID': wdeployment_id}
    logger.info('sending notification to th...')
    pam_chan.basic_publish(
        exchange='portaccess.th', routing_key='', body=json.dumps(firewall_start)
    )
    logger.info('done')
    expire_instance.apply_async((instance_id,), countdown=(expire_d + 5))
    if_stuck_init.apply_async((instance_id,), countdown=70)
    notify.new_instance.delay(user, instance_id, wdeployment_id)

    eacommand_chan.close()
    eacommand_conn.close()
    pam_chan.close()
    pam_conn.close()


@capp.task
def wait_queue_available(wdeployment_id):
    """Try to use given workspace deployment to satisfy a reservation

    This function is intended to be called when an instance
    terminates and, thus, the underlying workspace deployment
    becomes available.
    """
    with rrdb.create_session_context() as session:
        query = session.query(rrdb.Reservation).filter(
            rrdb.Reservation.rfilter == 'wd:{}'.format(wdeployment_id),
            rrdb.Reservation.handler == 0,
        )
        reservation = query.first()
        if reservation is None:
            logger.info(
                'wd {} is available, but no existing reservations match it'.format(
                    wdeployment_id
                )
            )
            return
        logger.info(
            'available wd {} satisfies reservation {}; trying to obtain lock...'.format(
                wdeployment_id, reservation.reservationid
            )
        )
        if len(reservation.ssh_publickey) > 0:
            ssh_publickey = reservation.ssh_publickey
        else:
            ssh_publickey = None
        reservationid = reservation.reservationid
        user = reservation.user
        has_vpn = reservation.has_vpn
        event_url = reservation.event_url
        expire_d = reservation.expire_d

    launch_instance.delay(
        wdeployment_id=wdeployment_id,
        instance_id=reservationid,
        user=user,
        ssh_publickey=ssh_publickey,
        has_vpn=has_vpn,
        expire_d=expire_d,
        eurl=event_url,
    )

    with rrdb.create_session_context() as session:
        reservation = (
            session.query(rrdb.Reservation)
            .filter(rrdb.Reservation.reservationid == reservationid)
            .one()
        )
        session.delete(reservation)


def _create_new_wdeployment_main(wdeployment_id, config):
    if 'supported_addons' not in config:
        supported_addons = []
    else:
        supported_addons = config['supported_addons']
    if len(supported_addons) > 0:
        if 'addons_config' in config:
            addons_config = config[
                'addons_config'
            ]  # TODO: should check JSON syntax here?
        else:
            addons_config = '{}'  # JSON, empty
    else:
        addons_config = ''  # no need to parse JSON if no add-ons supported

    with rrdb.create_session_context() as session:
        is_known = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .count()
            > 0
        )
        if not is_known:
            logging.info('adding deployment {}'.format(wdeployment_id))
            if 'desc_yaml' in config:
                desc_yaml = config['desc_yaml']
            else:
                desc_yaml = ''
            if 'wversion' not in config:
                wversion = 1  # temporary support for not-yet-updated wdeployments
            else:
                wversion = config['wversion']
            deployment = rrdb.Deployment(
                deploymentid=wdeployment_id,
                wtype=config['type'],
                wversion=wversion,
                supported_addons=','.join(supported_addons),
                addons_config=addons_config,
                region=config['region'],
                description=desc_yaml,
            )
            session.add(deployment)
            logging.info(
                'registered new workspace deployment {}'.format(wdeployment_id)
            )
            if 'hs' in config:
                userprovided = rrdb.UserProvidedSupp(
                    deploymentid=deployment.deploymentid,
                    owner=config['hs']['owner'],
                    registration_origin=config['hs'].get('origin', None),
                )
                session.add(userprovided)
                dacl = rrdb.DeploymentACL(
                    date_created=datetime.utcnow(),
                    user=config['hs']['owner'],
                    capability='CAP_INSTANTIATE',
                    wdeployment_id=wdeployment_id,
                )
                session.add(dacl)
                deployment.supported_addons = 'cmd,cmdsh'
                deployment.addons_config = '{}'  # JSON, empty
                logging.info(
                    'registered hardshare data for wdeployment {}'.format(
                        deployment.deploymentid
                    )
                )
        else:
            logging.info(
                'received NEW notification from known deployment {}'.format(
                    wdeployment_id
                )
            )


@capp.task
def register_new_user_provided(wdeployment_id, owner, origin):
    _create_new_wdeployment_main(
        wdeployment_id,
        {
            'type': 'user_provided',
            'wversion': 1,
            'region': '',
            'hs': {
                'owner': owner,
                'origin': origin,
            },
        },
    )


@capp.task
def create_new_wdeployment(wdeployment_id, config):
    _create_new_wdeployment_main(wdeployment_id, config)


def _update_wdeployment_main(wdeployment_id, config):
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        if 'supported_addons' in config:
            wd.supported_addons = ','.join(config['supported_addons'])
        if len(wd.supported_addons) > 0:
            if 'addons_config' in config:
                if isinstance(config['addons_config'], str):
                    wd.addons_config = config[
                        'addons_config'
                    ]  # TODO: should check JSON syntax here?
                else:
                    wd.addons_config = json.dumps(config['addons_config'])
        else:
            wd.addons_config = '{}'  # JSON, empty


@capp.task
def update_user_provided(wdeployment_id, supported_addons, addons_config):
    _update_wdeployment_main(
        wdeployment_id,
        {
            'supported_addons': supported_addons,
            'addons_config': addons_config,
        },
    )


@capp.task
def update_wdeployment(wdeployment_id, config):
    _update_wdeployment_main(wdeployment_id, config)


@capp.task
def dissolve_user_provided(wdeployment_id, owner, date_dissolved):
    logger.info(
        'dissolving user_provided wdeployment {} (owner: {}), timestamp {}'.format(
            wdeployment_id, owner, date_dissolved
        )
    )
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment, rrdb.UserProvidedSupp)
            .filter(rrdb.UserProvidedSupp.owner == owner)
            .filter(rrdb.UserProvidedSupp.deploymentid == wdeployment_id)
            .filter(rrdb.Deployment.deploymentid == rrdb.UserProvidedSupp.deploymentid)
            .one()[0]
        )
        if wd.date_dissolved is None:
            wd.date_dissolved = date_dissolved
            logger.info('marked wdeployment {} as dissolved'.format(wdeployment_id))
        else:
            logger.warning(
                'ignoring task with timestamp {} to dissolve user_provided wd {} that was already dissolved at {}'.format(
                    date_dissolved, wdeployment_id, str(wd.date_dissolved)
                )
            )


# TODO: filter localhost and rerobots-internal targets when not DEBUG
def notify_user(event_url, payload):
    logger.info('sending event notification to {}'.format(event_url))
    now = time.time()
    tok = jwt.encode(
        {'exp': int(now) + 10, 'nbf': int(now) - 1},
        key=settings.PRIVATE_KEY,
        algorithm='RS256',
    )
    headers = {
        'Authorization': 'Bearer {}'.format(str(tok, encoding='utf-8')),
    }
    if event_url.startswith('https://') or (
        settings.DEBUG and event_url.startswith('http://')
    ):
        # TODO: use aiohttp ClientSession instead of requests.post() ?
        res = requests.post(event_url, json=payload, headers=headers)
        if not res.ok:
            logger.warning('POST request failed to event URL: {}'.format(event_url))

    elif event_url.startswith('mailto://'):
        addr = event_url[len('mailto://') :]
        try:
            notify.send_raw_email(
                to=addr,
                subject='notification from rerobots',
                body=json.dumps(payload, indent=2, sort_keys=True),
            )
        except:
            logger.error('exception while attempting to send notification email')

    else:
        logger.warning('unrecognized event URL: {}'.format(event_url))


@capp.task
def notify_hardshare_owners(deployment_id, msg):
    with rrdb.create_session_context() as session:
        upattr = (
            session.query(rrdb.UserProvidedAttr)
            .filter(
                rrdb.UserProvidedAttr.deploymentid == deployment_id,
            )
            .one_or_none()
        )
        if upattr is None or upattr.alert_emails is None:
            logger.warning(
                f'cannot alert because no email addresses for wd {deployment_id}'
            )
            return
        notify.send_raw_email(
            to=upattr.alert_emails.split(','),
            subject=f'alert for hardshare deployment {deployment_id}',
            body=msg,
        )


@capp.task
def record_instance_event(instance_id, event_kind, event_data):
    with rrdb.create_session_context() as session:
        session.add(
            rrdb.InstanceEvent(
                instanceid=instance_id, kind=event_kind, data=json.dumps(event_data)
            )
        )
