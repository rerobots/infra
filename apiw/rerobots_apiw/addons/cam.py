import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid

import aiohttp
from aiohttp import web
import jwt
import redis

from .. import db as rrdb
from ..requestproc import process_headers
from ..util import create_subprocess_exec
from .. import settings


logger = logging.getLogger(__name__)


async def addon_hscam_mon(instance_id, ws):
    with rrdb.create_session_context() as session:
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        while True:
            await asyncio.sleep(5)
            session.expire(instance)
            if instance.status != 'INIT' and instance.status != 'READY':
                break
    await ws.close()


async def addon_cam_sender_job(handle, red, send):
    prev = None
    try:
        while True:
            if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
                thisimg = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQIAJQAlAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/wAALCAFoAeABAREA/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AK1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k='
                await send(thisimg)
                await asyncio.sleep(0.1)
            else:
                if not red.exists(handle):
                    await asyncio.sleep(0.1)
                    continue
                thisimg = str(red.get(handle), encoding='utf-8')
                if thisimg != prev:
                    await send(thisimg)
                    await asyncio.sleep(0.06)
                else:
                    await asyncio.sleep(0.02)
                prev = thisimg

    except asyncio.CancelledError:
        pass


async def addon_cam_snapshot(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    camera_id = request.match_info['cameraid']
    try:
        camera_id = int(camera_id)
        assert camera_id in [0, 1, 2, 3]  # TODO: check against addons_config
        camera_id = str(camera_id)
    except:
        logger.error('received bad camera id')
        return web.Response(
            body=json.dumps({'error_message': 'not valid camera identifier'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    instance = query.one_or_none()
    if instance is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(instance.deploymentid)
            )
        )
        row = query.one_or_none()
        if row is None:
            return web.Response(
                body=json.dumps(
                    {'error_message': 'add-on `cam` not active on this instance'}
                ),
                status=404,
                content_type='application/json',
            )

        hscam = True

    else:
        hscam = False

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'the add-on `cam` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    if hscam:
        handle = '{}:hscam:0'.format(addon_config['hscamid'])
    else:
        handle = '{}:cam:{}'.format(instance_id, camera_id)
    if request.app['red'].exists(handle):
        img = str(request.app['red'].get(handle), encoding='utf-8')
        index = img.find(',')
        if index < 0:
            payload = {
                'success': False,
                'message': 'error: unexpected coding server-side; try again soon, or contact rerobots.',
            }
        else:
            payload = {
                'success': True,
                'format': 'JPEG',
                'coding': 'base64',
                'data': img[(index + 1) :],
            }
    else:
        payload = {
            'success': False,
            'message': 'no image available; try again soon.',
        }
    return web.json_response(payload, headers=data['response_headers'])


async def restart_cam_job(eacommand, user, instance_id, token):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
            )
            .one_or_none()
        )
        if activeaddon is None:
            logger.warning(
                'called when instance {} does ' 'not have cam add-on applied'.format(
                    instance_id
                )
            )
            return
        addon_config = json.loads(activeaddon.config)
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        if instance.status != 'READY':
            logger.warning('called when instance {} is not READY'.format(instance_id))
            return
        ssh_privatekey = str(instance.ssh_privatekey)
        ipv4 = instance.listening_ipaddr
        port = instance.listening_port
        wdeployment_id = instance.deploymentid
        wdeployment = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        try:
            cam = json.loads(wdeployment.addons_config)['cam']
        except:
            logger.error('error parsing addons_config.cam')
            return

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    if wdeployment_id in [
        '5e9de205-2208-4022-8bd6-b104b740c270',
        'f74470ba-e486-474c-aceb-050ac2d82ef9',
        '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
    ]:
        msg_id = str(uuid.uuid4())
        eacommand.send_to_wd(
            wdeployment_id,
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/jwt.txt',
                'content': token,
            },
        )
        max_tries = 30
        count = 0
        while (count < max_tries) and (not red.exists(msg_id)):
            count += 1
            await asyncio.sleep(1)
        blob = red.get(msg_id)
        if blob is None or blob == b'NACK':
            logger.warning('no ACK of `PUT FILE` jwt.txt from workspace deployment')
            return

        msg_id = str(uuid.uuid4())
        eacommand.send_to_wd(
            wdeployment_id,
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/camerasend.py',
                'content': open('addons/cam/camerasend.py', 'rt').read(),
            },
        )
        max_tries = 30
        count = 0
        while (count < max_tries) and (not red.exists(msg_id)):
            count += 1
            await asyncio.sleep(1)
        blob = red.get(msg_id)
        if blob is None or blob == b'NACK':
            logger.warning(
                'no ACK of `PUT FILE` camerasend.py from workspace deployment'
            )
            return

    else:
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

        # TODO: run these processes in a Docker container? mainly intended as security
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
            'addons/cam/camerasend.py',
            '{}@{}:~/'.format(addon_config['user'], ipv4),
        ]
        for scp_cmd in [scp_token_cmd, scp_up_cmd]:
            logger.info('run: {}'.format(scp_cmd))
            scp_p = await create_subprocess_exec(*scp_cmd)
            rc = await scp_p.wait()
            if rc != 0:
                logger.warning(
                    'returncode of subprocess `{}` is {}'.format(' '.join(scp_cmd), rc)
                )

    if wdeployment_id in [
        '5e9de205-2208-4022-8bd6-b104b740c270',
        'f74470ba-e486-474c-aceb-050ac2d82ef9',
        '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
    ]:
        for k, v in cam.items():
            argv = ['python3', 'camerasend.py', instance_id, 'jwt.txt', str(k)]
            if 'rotate' in v:
                argv.append(v['rotate'])
            if 'w' in v and 'h' in v:
                if 'rotate' not in v:
                    argv.append("0")
                argv.append(v['w'])
                argv.append(v['h'])
            msg_id = str(uuid.uuid4())
            eacommand.send_to_wd(
                wdeployment_id,
                {
                    'command': 'EXEC INSIDE',
                    'iid': instance_id,
                    'did': wdeployment_id,
                    'message_id': msg_id,
                    'argv': argv,
                },
            )
            max_tries = 30
            count = 0
            while (count < max_tries) and (not red.exists(msg_id)):
                count += 1
                await asyncio.sleep(1)
            blob = red.get(msg_id)
            if blob is None or blob == b'NACK':
                logger.warning(
                    'no ACK of `EXEC INSIDE` {} from workspace deployment'.format(argv)
                )
                return

    else:
        camerasend_cmd_prefix = [
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
        camerasend_cmds = []
        for k, v in cam.items():
            camerasend_cmds.append(
                camerasend_cmd_prefix
                + ['python3', 'camerasend.py', instance_id, 'jwt.txt', str(k)]
            )
            if 'rotate' in v:
                camerasend_cmds[-1].append(v['rotate'])
            if 'w' in v and 'h' in v:
                if 'rotate' not in v:
                    camerasend_cmds[-1].append("0")
                camerasend_cmds[-1].append(v['w'])
                camerasend_cmds[-1].append(v['h'])
        for camerasend_cmd in camerasend_cmds:
            logger.info('run: {}'.format(camerasend_cmd))
            camerasend_p = await create_subprocess_exec(*camerasend_cmd)
            rc = await camerasend_p.wait()
            if rc != 0:
                logger.warning(
                    'returncode of subprocess `{}` is {}'.format(
                        ' '.join(camerasend_cmd), rc
                    )
                )

        os.unlink(privatekey_path)
        os.unlink(tmp_token_path)


async def addon_cam_start_job(eacommand, user, instance_id, token):
    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to DELETE while status=`starting`
    while True:
        logger.debug('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == '{}:cam'.format(instance_id),
                )
                .one_or_none()
            )
            if activeaddon is None:
                addon_config = None
            else:
                addon_config = json.loads(activeaddon.config)
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            ssh_privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port
            wdeployment_id = instance.deploymentid

        if (addon_config is not None) and instance_status == 'READY' and len(ipv4) > 0:
            logger.info(
                'instance READY with IPv4 addr {} and port {}'.format(ipv4, port)
            )
            break
        await asyncio.sleep(1)

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    with rrdb.create_session_context() as session:
        wdeployment = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        try:
            cam = json.loads(wdeployment.addons_config)['cam']
        except:
            logger.error('error parsing addons_config.cam')
            return

    if wdeployment_id in [
        '5e9de205-2208-4022-8bd6-b104b740c270',
        'f74470ba-e486-474c-aceb-050ac2d82ef9',
        '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
    ]:
        msg_id = str(uuid.uuid4())
        eacommand.send_to_wd(
            wdeployment_id,
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/jwt.txt',
                'content': token,
            },
        )
        max_tries = 30
        count = 0
        while (count < max_tries) and (not red.exists(msg_id)):
            count += 1
            await asyncio.sleep(1)
        blob = red.get(msg_id)
        if blob is None or blob == b'NACK':
            logger.warning('no ACK of `PUT FILE` jwt.txt from workspace deployment')
            return

        msg_id = str(uuid.uuid4())
        eacommand.send_to_wd(
            wdeployment_id,
            {
                'command': 'PUT FILE',
                'iid': instance_id,
                'did': wdeployment_id,
                'message_id': msg_id,
                'path': '/root/camerasend.py',
                'content': open('addons/cam/camerasend.py', 'rt').read(),
            },
        )
        max_tries = 30
        count = 0
        while (count < max_tries) and (not red.exists(msg_id)):
            count += 1
            await asyncio.sleep(1)
        blob = red.get(msg_id)
        if blob is None or blob == b'NACK':
            logger.warning(
                'no ACK of `PUT FILE` camerasend.py from workspace deployment'
            )
            return

    else:
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

        # TODO: run these processes in a Docker container? mainly intended as security
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
            'addons/cam/camerasend.py',
            '{}@{}:~/'.format(addon_config['user'], ipv4),
        ]
        for scp_cmd in [scp_token_cmd, scp_up_cmd]:
            logger.info('run: {}'.format(scp_cmd))
            scp_p = await create_subprocess_exec(*scp_cmd)
            rc = await scp_p.wait()
            if rc != 0:
                logger.warning(
                    'returncode of subprocess `{}` is {}'.format(' '.join(scp_cmd), rc)
                )

    addon_config['status'] = 'active'
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)

    if wdeployment_id in [
        '5e9de205-2208-4022-8bd6-b104b740c270',
        'f74470ba-e486-474c-aceb-050ac2d82ef9',
        '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
    ]:
        for k, v in cam.items():
            argv = ['python3', 'camerasend.py', instance_id, 'jwt.txt', str(k)]
            if 'rotate' in v:
                argv.append(v['rotate'])
            if 'w' in v and 'h' in v:
                if 'rotate' not in v:
                    argv.append("0")
                argv.append(v['w'])
                argv.append(v['h'])
            msg_id = str(uuid.uuid4())
            eacommand.send_to_wd(
                wdeployment_id,
                {
                    'command': 'EXEC INSIDE',
                    'iid': instance_id,
                    'did': wdeployment_id,
                    'message_id': msg_id,
                    'argv': argv,
                },
            )
            max_tries = 30
            count = 0
            while (count < max_tries) and (not red.exists(msg_id)):
                count += 1
                await asyncio.sleep(1)
            blob = red.get(msg_id)
            if blob is None or blob == b'NACK':
                logger.warning(
                    'no ACK of `EXEC INSIDE` {} from workspace deployment'.format(argv)
                )
                return

    else:
        camerasend_cmd_prefix = [
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
        camerasend_cmds = []
        for k, v in cam.items():
            camerasend_cmds.append(
                camerasend_cmd_prefix
                + ['python3', 'camerasend.py', instance_id, 'jwt.txt', str(k)]
            )
            if 'rotate' in v:
                camerasend_cmds[-1].append(v['rotate'])
            if 'w' in v and 'h' in v:
                if 'rotate' not in v:
                    camerasend_cmds[-1].append("0")
                camerasend_cmds[-1].append(v['w'])
                camerasend_cmds[-1].append(v['h'])
        for camerasend_cmd in camerasend_cmds:
            logger.info('run: {}'.format(camerasend_cmd))
            camerasend_p = await create_subprocess_exec(*camerasend_cmd)
            rc = await camerasend_p.wait()
            if rc != 0:
                logger.warning(
                    'returncode of subprocess `{}` is {}'.format(
                        ' '.join(camerasend_cmd), rc
                    )
                )

        os.unlink(privatekey_path)
        os.unlink(tmp_token_path)


async def addon_cam_stop_job(user, instance_id, eacommand=None):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        wdeployment_id = instance.deploymentid
        instance_status = instance.status
        ssh_privatekey = str(instance.ssh_privatekey)
        ipv4 = instance.listening_ipaddr
        port = instance.listening_port

    if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
        logger.warning(
            f'running with as {settings.RUNTIME_ENVIRON}, so no remote device to stop'
        )

    elif instance_status not in ['READY', 'TERMINATED']:
        logger.warning(
            'instance {}, so cannot kill remote device-sharing client if it exists'.format(
                instance_status
            )
        )

    elif instance_status == 'READY':
        if eacommand is None:
            logger.error(
                'called on instance {} with eacommand None when status READY'.format(
                    instance_id
                )
            )
            return

        red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )

        if wdeployment_id in [
            '5e9de205-2208-4022-8bd6-b104b740c270',
            'f74470ba-e486-474c-aceb-050ac2d82ef9',
            '9ed24591-5020-4bc7-a1d6-3f2490e3afa7',
        ]:
            argv = ['pkill', '-f', 'camerasend.py']
            msg_id = str(uuid.uuid4())
            eacommand.send_to_wd(
                wdeployment_id,
                {
                    'command': 'EXEC INSIDE',
                    'iid': instance_id,
                    'did': wdeployment_id,
                    'message_id': msg_id,
                    'argv': argv,
                },
            )
            max_tries = 30
            count = 0
            while (count < max_tries) and (not red.exists(msg_id)):
                count += 1
                await asyncio.sleep(1)
            blob = red.get(msg_id)
            if blob is None or blob == b'NACK':
                logger.warning(
                    'no ACK of `EXEC INSIDE` {} from workspace deployment'.format(argv)
                )
                return

        else:
            # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
            # provide key text via stdin of child process.
            tmp_fd, privatekey_path = tempfile.mkstemp()
            privatekey_file = os.fdopen(tmp_fd, 'w')
            privatekey_file.write(ssh_privatekey)
            privatekey_file.close()

            kill_cmd = [
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
                'pkill',
                '-f',
                'camerasend.py',
            ]
            logger.info('run: {}'.format(kill_cmd))
            kill_p = await create_subprocess_exec(*kill_cmd)
            rc = await kill_p.wait()
            os.unlink(privatekey_path)
            if rc != 0:
                logger.warning(
                    f'on instance {instance_id}, command exitcode {rc}: {" ".join(kill_cmd)}'
                )

    with rrdb.create_session_context() as session:
        session.delete(
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
            )
            .one()
        )


async def status_addon_cam(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    if 'in' in data['payload'] and instance_id != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instance_id)
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.Instance.rootuser == data['user'])

    instance = query.one_or_none()
    if instance is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id))
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])

    row = query.one_or_none()
    if row is None:
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(instance.deploymentid)
            )
        )
        row = query.one_or_none()
        if row is not None:
            addon_config = json.loads(row.config)
            payload = {
                'status': addon_config['status'],
            }
            return web.json_response(payload, headers=data['response_headers'])

        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cam` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    payload = {
        'status': addon_config['status'],
        'remote_user': addon_config['user'],
    }

    return web.json_response(payload, headers=data['response_headers'])


async def remove_addon_cam(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    if 'in' in data['payload'] and instance_id != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )
    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit modifying this add-on'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    instance = query.one_or_none()
    if instance is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
        )
    )

    row = query.one_or_none()
    if row is None:
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(instance.deploymentid)
            )
        )
        row = query.one_or_none()
        if row is not None:
            return web.Response(status=200, headers=data['response_headers'])

        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cam` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if settings.RUNTIME_ENVIRON.endswith('mock'):
        return web.Response(status=200, headers=data['response_headers'])

    request.app.loop.create_task(
        addon_cam_stop_job(
            user=data['user'],
            instance_id=instance_id,
            eacommand=request.app['eacommand'],
        )
    )

    return web.Response(status=200, headers=data['response_headers'])


async def addon_cam_upload(request):
    # TODO: add rate-limiting, via requestproc.process_headers()
    try:
        token = str(
            base64.urlsafe_b64decode(
                bytes(request.match_info['tok'], encoding='utf-8')
            ),
            encoding='utf-8',
        )
        payload = jwt.decode(
            token,
            issuer='rerobots.net',
            audience='rerobots.net',
            key=settings.WEBUI_PUBLIC_KEY,
            algorithms=['RS256'],
        )
    except:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
        )

    camera_id = request.match_info['cameraid']
    try:
        camera_id = int(camera_id)
        assert camera_id in [0, 1, 2, 3]  # TODO: check against addons_config
        camera_id = str(camera_id)
    except:
        logger.error('received bad camera id')
        return web.Response(
            body=json.dumps({'error_message': 'not valid camera identifier'}),
            status=400,
            content_type='application/json',
        )

    instance_id = request.match_info['inid']
    user = payload['sub']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == payload['sub'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == payload['sub'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cam` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'the add-on `cam` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)
    logger.info('opened WebSocket connection')

    handle = '{}:cam:{}'.format(instance_id, camera_id)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                request.app['red'].set(handle, msg.data)
                request.app['red'].expire(handle, 30)

            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.warning('connection closed')
                break

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error('error on upload WebSocket connection; restarting...')
                request.app.loop.create_task(
                    restart_cam_job(request.app['eacommand'], user, instance_id, token)
                )
                break

    except asyncio.CancelledError:
        logger.info(
            'this coroutine is returning after receiving CancelledError; trying to restart...'
        )
        request.app.loop.create_task(
            restart_cam_job(request.app['eacommand'], user, instance_id, token)
        )
        return ws
    logger.info('this coroutine is returning')
    return ws


async def addon_cam_stream(request):
    # TODO: add rate-limiting, via requestproc.process_headers()
    try:
        token = str(
            base64.urlsafe_b64decode(
                bytes(request.match_info['tok'], encoding='utf-8')
            ),
            encoding='utf-8',
        )
        payload = jwt.decode(
            token,
            issuer='rerobots.net',
            audience='rerobots.net',
            key=settings.WEBUI_PUBLIC_KEY,
            algorithms=['RS256'],
        )
    except:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
        )

    camera_id = request.match_info['cameraid']
    try:
        camera_id = int(camera_id)
        assert camera_id in [0, 1, 2, 3]  # TODO: check against addons_config
        camera_id = str(camera_id)
    except:
        logger.error('received bad camera id')
        return web.Response(
            body=json.dumps({'error_message': 'not valid camera identifier'}),
            status=400,
            content_type='application/json',
        )

    instance_id = request.match_info['inid']

    if 'in' in payload and instance_id != payload['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instance_id)
    )
    if 'in' not in payload:
        query = query.filter(rrdb.Instance.rootuser == payload['sub'])

    instance = query.one_or_none()
    if instance is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id))
    )
    if 'in' not in payload:
        query = query.filter(rrdb.ActiveAddon.user == payload['sub'])
    row = query.one_or_none()
    if row is None:
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(instance.deploymentid)
            )
        )
        row = query.one_or_none()
        if row is None:
            return web.Response(
                body=json.dumps(
                    {'error_message': 'add-on `cam` not active on this instance'}
                ),
                status=404,
                content_type='application/json',
            )

        hscam = True

    else:
        hscam = False

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'the add-on `cam` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)
    logger.info('opened WebSocket connection')

    if hscam:
        if addon_config.get('crop', None) is None:
            handle = '{}:hscam:0'.format(addon_config['hscamid'])
        else:
            handle = '{}:hscam:0:{}'.format(
                addon_config['hscamid'], instance.deploymentid
            )

    else:
        handle = '{}:cam:{}'.format(instance_id, camera_id)

    sender_task = request.app.loop.create_task(
        addon_cam_sender_job(handle, request.app['red'], ws.send_str)
    )
    if hscam:
        request.app.loop.create_task(addon_hscam_mon(instance_id, ws))
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.CLOSED:
            break

        elif msg.type == aiohttp.WSMsgType.ERROR:
            break

    sender_task.cancel()
    return ws


async def apply_addon_cam(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    if 'in' in data['payload'] and instance_id != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()

    # TODO:
    # if 'privkey' not in given:
    #    return web.Response(body=json.dumps({'error_message': '`privkey` parameter is required'}),
    #                        status=403,
    #                        content_type='application/json',
    #                        headers=data['response_headers'])
    # tunneling_private_key = given['privkey']
    if 'user' in given:
        tunneling_user = given['user']
    else:
        tunneling_user = 'root'

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instance_id)
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.Instance.rootuser == data['user'])

    instance = query.one_or_none()
    if instance is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    wdeployment = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == instance.deploymentid)
        .one()
    )
    if 'cam' not in wdeployment.supported_addons:
        if (
            settings.RUNTIME_ENVIRON.endswith('mock')
            and wdeployment.wtype == 'user_provided'
        ):
            return web.Response(status=200, headers=data['response_headers'])

        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(instance.deploymentid)
            )
        )
        row = query.one_or_none()
        if row is not None:
            return web.Response(status=200, headers=data['response_headers'])

        # TODO: after creating more add-ons, this quick substring
        # should be changed to the general case of casting to a list
        # from comma-separated values.
        return web.Response(
            body=json.dumps(
                {'error_message': 'this instance does not support the `cam` add-on'}
            ),
            status=503,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cam'.format(instance_id))
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    if query.count() > 0:
        return web.Response(status=200, headers=data['response_headers'])

    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit modifying this add-on'},
            status=400,
            headers=data['response_headers'],
        )

    if settings.RUNTIME_ENVIRON.endswith('mock'):
        initial_status = 'active'
    else:
        initial_status = 'starting'

    config = {
        'user': tunneling_user,
        #'key': tunneling_private_key,  # TODO
        'status': initial_status,  # status \in {active, starting, stopping}
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon='{}:cam'.format(instance_id),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)

    # TODO: give user option to provide a different API token than
    # that in the request headers.
    token = request.headers['AUTHORIZATION'].split()[1]

    if settings.RUNTIME_ENVIRON.endswith('mock'):
        return web.Response(status=200, headers=data['response_headers'])

    request.app.loop.create_task(
        addon_cam_start_job(
            eacommand=request.app['eacommand'],
            user=data['user'],
            instance_id=instance_id,
            token=token,
        )
    )
    return web.Response(status=200, headers=data['response_headers'])
