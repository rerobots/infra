import asyncio
import base64
import json
import logging
import os
import tempfile

import aiohttp
from aiohttp import web
import jwt

from .. import db as rrdb
from ..requestproc import process_headers
from ..util import create_subprocess_exec
from ..settings import WEBUI_PUBLIC_KEY


logger = logging.getLogger(__name__)


async def addon_drive_start_job(user, instance_id, token):
    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to DELETE while status=`starting`
    while True:
        logger.info('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == '{}:drive'.format(instance_id),
                )
                .one_or_none()
            )
            if activeaddon is None:
                await asyncio.sleep(1)
                continue
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

        if instance_status == 'READY' and len(ipv4) > 0:
            logger.info(
                'instance READY with IPv4 addr {} and port {}'.format(ipv4, port)
            )
            break
        await asyncio.sleep(1)

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
        'addons/drive/misty/drivesend.py',
        '{}@{}:~/'.format(addon_config['user'], ipv4),
    ]
    for scp_cmd in [scp_token_cmd, scp_up_cmd]:
        logger.info('exec: {}'.format(scp_cmd))
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
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:drive'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)

    drivesend_cmd_prefix = [
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
    drivesend_cmd = drivesend_cmd_prefix + [
        'python',
        'drivesend.py',
        instance_id,
        'jwt.txt',
    ]
    logger.info('exec: {}'.format(drivesend_cmd))
    drivesend_p = await create_subprocess_exec(*drivesend_cmd)
    rc = await drivesend_p.wait()
    if rc != 0:
        logger.warning(
            'returncode of subprocess `{}` is {}'.format(' '.join(drivesend_cmd), rc)
        )

    os.unlink(privatekey_path)
    os.unlink(tmp_token_path)


async def addon_drive_stop_job(user, instance_id):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:drive'.format(instance_id),
            )
            .one()
        )
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

    if instance_status != 'READY':
        logger.warning(
            'instance not READY, so cannot kill remote device-sharing client if it exists'
        )
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
            'drivesend.py',
        ]
        logger.info('exec: {}'.format(kill_cmd))
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
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:drive'.format(instance_id),
            )
            .one()
        )


async def apply_addon_drive(request):
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

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    wdeployment = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == row.deploymentid)
        .one()
    )
    if 'drive' not in wdeployment.supported_addons:
        return web.Response(
            body=json.dumps(
                {'error_message': 'this instance does not support the `drive` add-on'}
            ),
            status=503,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:drive'.format(instance_id),
        )
    )
    if query.count() > 0:
        return web.Response(status=200, headers=data['response_headers'])

    config = {
        'user': tunneling_user,
        #'key': tunneling_private_key,  # TODO
        'status': 'starting',  # status \in {active, starting, stopping}
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon='{}:drive'.format(instance_id),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)

    # TODO: give user option to provide a different API token than
    # that in the request headers.
    token = request.headers['AUTHORIZATION'].split()[1]

    request.app.loop.create_task(
        addon_drive_start_job(user=data['user'], instance_id=instance_id, token=token)
    )
    return web.Response(status=200, headers=data['response_headers'])


async def status_addon_drive(request):
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
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:drive'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `drive` not active on this instance'}
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


async def remove_addon_drive(request):
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
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:drive'.format(instance_id),
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `drive` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    request.app.loop.create_task(
        addon_drive_stop_job(user=data['user'], instance_id=instance_id)
    )

    return web.Response(status=200, headers=data['response_headers'])


async def drive_sender_job(red, instance_id, ws_send):
    handle = 'drive:{}'.format(instance_id)
    try:
        while True:
            await asyncio.sleep(0.1)
            x = red.get(handle)
            if x:
                red.delete(handle)
                await ws_send(json.loads(x))
                logger.info('sent: {}'.format(x))
    except asyncio.CancelledError:
        pass


async def drive_rx_commands(request):
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
            key=WEBUI_PUBLIC_KEY,
            algorithms=['RS256'],
        )
    except:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
        )

    instance_id = request.match_info['inid']
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:drive'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `drive` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'add-on `drive` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)
    logger.info('opened WebSocket connection')

    sender_task = request.app.loop.create_task(
        drive_sender_job(
            red=request.app['red'], instance_id=instance_id, ws_send=ws.send_json
        )
    )
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                # TODO
                print(msg.data)
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                break
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    except asyncio.CancelledError:
        pass
    finally:
        sender_task.cancel()
    return ws


async def drive_send_command(request):
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
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:drive'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `drive` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'the add-on `drive` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()
    request.app['red'].set('drive:{}'.format(instance_id), json.dumps(given))
    return web.json_response({'success': True})
