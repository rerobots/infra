import asyncio
import base64
import logging
import json
import os
import tempfile
import uuid

import aiohttp
from aiohttp import web
import jwt

from .. import db as rrdb
from ..requestproc import process_headers
from ..util import create_subprocess_exec
from ..settings import WEBUI_PUBLIC_KEY


logger = logging.getLogger(__name__)


async def addon_cmd_start_job(
    user, instance_id, token, base_uri=None, verify_certs=True
):
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
                    == '{}:cmd'.format(instance_id),
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
        'addons/cmd/cmdr',
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
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)

    cmdr_call_prefix = [
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
    cmdr_call = cmdr_call_prefix + ['python3', 'cmdr', instance_id, 'jwt.txt']
    if base_uri:
        cmdr_call.append(base_uri)
    if not verify_certs:
        cmdr_call.append('0')
    logger.info('exec: {}'.format(cmdr_call))
    cmdr_p = await create_subprocess_exec(*cmdr_call)
    rc = await cmdr_p.wait()
    if rc != 0:
        logger.warning(
            'returncode of subprocess `{}` is {}'.format(' '.join(cmdr_call), rc)
        )

    os.unlink(privatekey_path)
    os.unlink(tmp_token_path)


async def addon_cmd_stop_job(user, instance_id):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
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
            'cmdr',
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
                rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
            )
            .one()
        )


async def apply_addon_cmd(request):
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
    if 'cmd' not in wdeployment.supported_addons:
        return web.Response(
            body=json.dumps(
                {'error_message': 'this instance does not support the `cmd` add-on'}
            ),
            status=503,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id))
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

    config = {
        'user': tunneling_user,
        #'key': tunneling_private_key,  # TODO
        'status': 'starting',  # status \in {active, starting, stopping}
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon='{}:cmd'.format(instance_id),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)

    # TODO: give user option to provide a different API token than
    # that in the request headers.
    token = request.headers['AUTHORIZATION'].split()[1]

    base_uri = given.get('base_uri', None)
    verify_certs = given.get('verify_certs', True)

    request.app.loop.create_task(
        addon_cmd_start_job(
            user=data['user'],
            instance_id=instance_id,
            token=token,
            base_uri=base_uri,
            verify_certs=verify_certs,
        )
    )
    return web.Response(status=200, headers=data['response_headers'])


async def status_addon_cmd(request):
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
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id))
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
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


async def remove_addon_cmd(request):
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    request.app.loop.create_task(
        addon_cmd_stop_job(user=data['user'], instance_id=instance_id)
    )

    return web.Response(status=200, headers=data['response_headers'])


async def cmd_sender_job(red, instance_id, ws_send):
    handle = 'cmd:{}'.format(instance_id)
    try:
        while True:
            await asyncio.sleep(0.5)
            x = red.lpop(handle)
            if x:
                logger.debug('detected cmd: {}'.format(x))
                x = str(x, encoding='utf-8')
                await ws_send(x)
                logger.info('sent: {}'.format(x))
    except asyncio.CancelledError:
        pass


async def cmd_rx_commands(request):
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` on this instance is not streaming yet'}
            ),
            status=503,
            content_type='application/json',
        )

    ws = web.WebSocketResponse(autoping=True, heartbeat=30.0)
    await ws.prepare(request)
    logger.info('opened WebSocket connection')

    sender_task = request.app.loop.create_task(
        cmd_sender_job(
            red=request.app['red'], instance_id=instance_id, ws_send=ws.send_str
        )
    )
    base_handle = 'cmd:stdout:{}'.format(instance_id)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                request.app['red'].rpush(base_handle, msg.data)
                request.app['red'].expire(base_handle, 30)
                m = json.loads(msg.data)
                if 'i' in m:
                    handle = base_handle + ':{}'.format(m['i'])
                    request.app['red'].rpush(handle, msg.data)
                    request.app['red'].expire(handle, 30)
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                break
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    except asyncio.CancelledError:
        pass
    finally:
        sender_task.cancel()
    return ws


async def cmd_send_command(request):
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
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id))
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
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
                    'error_message': 'the add-on `cmd` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    if request.can_read_body:
        try:
            body = await request.read()
            given = json.loads(str(body, encoding='utf-8'))
        except json.decoder.JSONDecodeError:
            return web.json_response(
                {'error_message': 'body not valid JSON'},
                status=400,
                headers=data['response_headers'],
            )
        if 'cmd' not in given:
            return web.json_response(
                {'error_message': 'body missing required `cmd`'},
                status=400,
                headers=data['response_headers'],
            )
    else:
        return web.json_response(
            {'error_message': 'body required'},
            status=400,
            headers=data['response_headers'],
        )

    cmd_id = str(uuid.uuid4())
    payload = {
        'id': cmd_id,
        'cmd': given['cmd'],
    }
    request.app['red'].rpush('cmd:{}'.format(instance_id), json.dumps(payload))
    return web.json_response({'success': True, 'id': cmd_id})


async def cmd_readline_stdout(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
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

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if 'cmdid' in request.match_info:
        cmd_id = request.match_info['cmdid']
        handle = 'cmd:stdout:{}:{}'.format(instance_id, cmd_id)
    else:
        cmd_id = None
        handle = 'cmd:stdout:{}'.format(instance_id)

    lines = []
    terminated = False
    for li in range(80):
        x = request.app['red'].lpop(handle)
        if x is None:
            break
        m = json.loads(str(x, encoding='utf-8'))
        if 't' in m and m['t']:
            terminated = True
        else:
            lines.append(m['l'])

    return web.json_response(
        {'lines': lines, 'terminated': terminated, 'id': cmd_id},
        headers=data['response_headers'],
    )


async def cmd_send_file(request):
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
        .filter(rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id))
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
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
                    'error_message': 'the add-on `cmd` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    if not request.can_read_body:
        return web.Response(
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    given = await request.json()
    if 'data' not in given or 'path' not in given:
        return web.Response(
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    b64_encoded = given.get('b64', False)
    # TODO: check type?

    # TODO: only for anonymous
    cft = rrdb.CommandFileTrace(
        instance_id=instance.instanceid, path=given['path'], data=given['data']
    )
    request['dbsession'].add(cft)

    ssh_privatekey = str(instance.ssh_privatekey)
    ipv4 = instance.listening_ipaddr
    port = instance.listening_port

    # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
    # provide key text via stdin of child process.
    tmp_fd, privatekey_path = tempfile.mkstemp()
    privatekey_file = os.fdopen(tmp_fd, 'w')
    privatekey_file.write(ssh_privatekey)
    privatekey_file.close()

    if b64_encoded:
        try:
            given_data = base64.b64decode(given['data'])
        except Exception as err:
            logger.warning('caught {}: {}'.format(type(err), err))
            return web.Response(
                status=400,
                content_type='application/json',
                headers=data['response_headers'],
            )
        tmp_fd, tmp_path = tempfile.mkstemp()
        tmp_file = os.fdopen(tmp_fd, 'wb')
        tmp_file.write(given_data)
        tmp_file.close()
    else:
        tmp_fd, tmp_path = tempfile.mkstemp()
        tmp_file = os.fdopen(tmp_fd, 'wt')
        tmp_file.write(given['data'])
        tmp_file.close()

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
    scp_cmd = scp_cmd_prefix + [
        tmp_path,
        '{}@{}:{}'.format(addon_config['user'], ipv4, given['path']),
    ]
    logger.info('exec: {}'.format(scp_cmd))
    scp_p = await create_subprocess_exec(*scp_cmd)
    rc = await scp_p.wait()
    if rc != 0:
        logger.warning(
            'returncode of subprocess `{}` is {}'.format(' '.join(scp_cmd), rc)
        )

    os.unlink(privatekey_path)
    os.unlink(tmp_path)

    return web.json_response({'success': True})


async def cmd_cancel_job(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
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

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    cmd_id = request.match_info['cmdid']

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:cmd'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `cmd` not active on this instance'}
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
                    'error_message': 'the add-on `cmd` on this instance is not streaming yet'
                }
            ),
            status=503,
            content_type='application/json',
        )

    payload = {
        'id': cmd_id,
        'do': 'cancel',
    }
    request.app['red'].rpush('cmd:{}'.format(instance_id), json.dumps(payload))
    return web.json_response({'success': True, 'id': cmd_id})
