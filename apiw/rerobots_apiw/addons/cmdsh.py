"""
Assume DASH
http://gondor.apana.org.au/~herbert/dash/
https://git.kernel.org/pub/scm/utils/dash/dash.git

Future work: user declares shell to use during add-on activation in POST request


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import asyncio
import base64
import json
import logging
import uuid

import aiohttp
import redis
from aiohttp import web

from .. import db as rrdb
from .. import settings
from ..requestproc import process_headers
from .tasks import start_cmdsh

logger = logging.getLogger(__name__)


async def apply_addon(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    wdeployment = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == row.deploymentid)
        .one()
    )
    if 'cmdsh' not in wdeployment.supported_addons:
        return web.json_response(
            {'error_message': 'this instance does not support the `cmdsh` add-on'},
            status=503,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    if settings.RUNTIME_ENVIRON.endswith('mock'):
        initial_status = 'active'
    else:
        initial_status = 'starting'

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    addon = query.one_or_none()
    if addon is not None:
        config = json.loads(addon.config)
        if host_id not in config['hosts']:
            if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
                return web.json_response(
                    {
                        'error_message': 'token scope does not permit modifying this add-on'
                    },
                    status=400,
                    headers=data['response_headers'],
                )

            config['hosts'].append(host_id)
            config['hstatus'][host_id] = initial_status
            addon.config = json.dumps(config)

            if settings.RUNTIME_ENVIRON.endswith('mock'):
                return web.Response(status=200, headers=data['response_headers'])

            # TODO: give user option to provide a different API token than
            # that in the request headers.
            token = request.headers['AUTHORIZATION'].split()[1]
            start_cmdsh.delay(
                user=data['user'], instance_id=instance_id, host_id=host_id, token=token
            )

        return web.Response(status=200, headers=data['response_headers'])

    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit modifying this add-on'},
            status=400,
            headers=data['response_headers'],
        )

    config = {
        'user': tunneling_user,
        'status': 'active',  # status \in {active, starting, stopping}
        'hosts': [host_id],
        'hstatus': {host_id: initial_status},
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon=f'{instance_id}:cmdsh',
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)

    if settings.RUNTIME_ENVIRON.endswith('mock'):
        return web.Response(status=200, headers=data['response_headers'])

    # TODO: give user option to provide a different API token than
    # that in the request headers.
    token = request.headers['AUTHORIZATION'].split()[1]

    start_cmdsh.delay(
        user=data['user'], instance_id=instance_id, host_id=host_id, token=token
    )
    return web.Response(status=200, headers=data['response_headers'])


async def get_status(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {
                'error_message': f'add-on `cmdsh` not active on host {host_id} of this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if host_id not in addon_config['hosts']:
        return web.json_response(
            {
                'error_message': f'add-on `cmdsh` not active on host {host_id} of this instance'
            },
            status=404,
            headers=data['response_headers'],
        )
    payload = {
        'status': addon_config['hstatus'][host_id],
        'remote_user': addon_config['user'],
    }

    return web.json_response(payload, headers=data['response_headers'])


async def remove(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh',
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'add-on `cmdsh` not active on this instance'},
            status=404,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = None

    if host_id is None:
        request.app.loop.create_task(
            stop_job(
                user=data['user'],
                instance_id=instance_id,
                eacommand=request.app['eacommand'],
            )
        )
    else:
        request.app.loop.create_task(
            stop_job(
                user=data['user'],
                instance_id=instance_id,
                host_id=host_id,
                eacommand=request.app['eacommand'],
            )
        )

    return web.Response(status=200, headers=data['response_headers'])


async def forwarder(handle, ws_send):
    try:
        red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )
        while True:
            while red.llen(handle) > 0:
                await ws_send(red.lpop(handle))
            await asyncio.sleep(0.01)

    except asyncio.CancelledError:
        pass

    except Exception as err:
        # TODO: remove this in favor of Sentry capture?
        logger.warning(f'caught {type(err)}: {err}')


async def attach_sh(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh',
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    addon_config = json.loads(row.config)

    if host_id not in addon_config['hosts']:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if addon_config['hstatus'][host_id] != 'active':
        return web.json_response(
            {'error_message': 'the add-on `cmdsh` on this instance is not active yet'},
            status=503,
            headers=data['response_headers'],
        )

    sh_id = request.match_info['shid']

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)

    base_handle = f'addon:cmdsh:{instance_id}:{host_id}:{sh_id}'
    stdout_handle = base_handle + ':stdout'
    stdin_handle = base_handle + ':stdin'
    sender = request.app.loop.create_task(forwarder(stdin_handle, ws.send_bytes))
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.CLOSED:
                logger.warning('connection closed')
                break

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error('error on main cmdsh WebSocket connection')
                # TODO: restart
                break

            else:
                request.app['red'].rpush(stdout_handle, msg.data)

    except asyncio.CancelledError:
        # TODO: restart
        pass

    sender.cancel()
    return ws


async def newshell_starter(handle, ws_send):
    try:
        red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )
        while True:
            if red.llen(handle) > 0:
                new_id = red.lpop(handle).decode('utf-8')
                await ws_send(
                    json.dumps(
                        {
                            'cmd': 'new',
                            'sid': new_id,
                        }
                    )
                )
            await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass

    except Exception as err:
        # TODO: remove this in favor of Sentry capture?
        logger.warning(f'caught {type(err)}: {err}')


async def attach_main(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh',
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    addon_config = json.loads(row.config)

    if host_id not in addon_config['hosts']:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if addon_config['hstatus'][host_id] != 'active':
        addon_config['hstatus'][host_id] = 'active'
        row.config = json.dumps(addon_config)

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)

    handle = f'addon:cmdsh:{instance_id}:{host_id}'
    sender = request.app.loop.create_task(newshell_starter(handle, ws.send_str))
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.CLOSED:
                logger.warning('connection closed')
                break

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error('error on main cmdsh WebSocket connection')
                # TODO: restart
                break

            else:
                # TODO: ACK from client?
                pass

    except asyncio.CancelledError:
        # TODO: restart
        pass

    sender.cancel()

    return ws


async def newshell(request):
    # TODO: add rate-limiting, via requestproc.process_headers()
    if 'tok' in request.match_info:
        try:
            should_handle, data = process_headers(
                request, token=request.match_info['tok']
            )
            if data['user'] is None:
                # Support legacy clients that base64 encode
                token = str(
                    base64.urlsafe_b64decode(
                        bytes(request.match_info['tok'], encoding='utf-8')
                    ),
                    encoding='utf-8',
                )
                should_handle, data = process_headers(request, token=token)
                if data['user'] is None:
                    return web.json_response(
                        {'error_message': 'wrong authorization token'},
                        status=400,
                        headers=data['response_headers'],
                    )
            if not should_handle:
                return web.Response(status=403, headers=data['response_headers'])

        except Exception as err:
            logger.info(f'wrong auth token: {err}')
            return web.json_response(
                {'error_message': 'wrong authorization token'}, status=400
            )

    else:
        should_handle, data = process_headers(request)
        if data['user'] is None:
            return web.json_response(
                {'error_message': 'wrong authorization token'},
                status=400,
                headers=data['response_headers'],
            )
        if not should_handle:
            return web.Response(status=403, headers=data['response_headers'])

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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    addon_config = json.loads(row.config)

    if host_id not in addon_config['hosts']:
        return web.json_response(
            {
                'error_message': 'add-on `cmdsh` not active nor starting on this instance'
            },
            status=404,
            headers=data['response_headers'],
        )

    if addon_config['hstatus'][host_id] != 'active':
        return web.json_response(
            {'error_message': 'the add-on `cmdsh` on this instance is not active yet'},
            status=403,
            headers=data['response_headers'],
        )

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)

    sh_id = str(uuid.uuid4())
    request.app['red'].rpush(f'addon:cmdsh:{instance_id}:{host_id}', sh_id)

    base_handle = f'addon:cmdsh:{instance_id}:{host_id}:{sh_id}'
    stdout_handle = base_handle + ':stdout'
    stdin_handle = base_handle + ':stdin'
    if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
        sender = None
    else:
        sender = request.app.loop.create_task(forwarder(stdout_handle, ws.send_bytes))
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.CLOSED:
                logger.warning('connection closed')
                break

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error('error on main cmdsh WebSocket connection')
                # TODO: restart
                break

            else:
                request.app['red'].rpush(stdin_handle, msg.data)
                if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
                    await ws.send_bytes(msg.data.encode())

    except asyncio.CancelledError:
        # TODO: restart
        pass

    if sender is not None:
        sender.cancel()
    return ws


async def stop_job(user, instance_id, host_id=None, eacommand=None):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh',
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
            f'instance {instance_status}, so cannot kill remote device-sharing client if it exists'
        )

    elif instance_status == 'READY':
        if eacommand is None:
            logger.error(
                f'called on instance {instance_id} with eacommand None when status READY'
            )
            return

        red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )

        if host_id is None:
            target_hosts = addon_config['hosts']
        else:
            target_hosts = [host_id]

        for hid in target_hosts:
            if addon_config['hstatus'][hid] != 'active':
                continue

            argv = ['pkill', '-f', 'cmdshr']
            msg_id = str(uuid.uuid4())
            eacommand.send_to_wd(
                wdeployment_id,
                {
                    'command': 'EXEC INSIDE',
                    'iid': instance_id,
                    'hid': hid,
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
                    f'no ACK of `EXEC INSIDE` {argv} from workspace deployment'
                )
                return

    if host_id is None:
        with rrdb.create_session_context() as session:
            session.delete(
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh',
                )
                .one()
            )


# TODO: duplication of code with cmd_send_file in addons.py; how to consolidate?
async def send_file(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
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
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:cmdsh')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'add-on `cmdsh` not active on this instance'},
            status=404,
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'active':
        return web.json_response(
            {
                'error_message': 'the add-on `cmdsh` on this instance is not streaming yet'
            },
            status=503,
            headers=data['response_headers'],
        )

    if not request.can_read_body:
        return web.Response(status=400, headers=data['response_headers'])

    given = await request.json()
    if 'data' not in given or 'path' not in given:
        return web.Response(status=400, headers=data['response_headers'])

    if 'hid' in request.match_info:
        host_id = request.match_info['hid']
        # TODO: determine valid range via wdeployment configuration from database
    else:
        host_id = '0'

    b64_encoded = given.get('b64', False)

    red = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0)

    msg_id = str(uuid.uuid4())
    request.app['eacommand'].send_to_wd(
        instance.deploymentid,
        {
            'command': 'PUT FILE',
            'iid': instance.instanceid,
            'hid': host_id,
            'did': instance.deploymentid,
            'message_id': msg_id,
            'path': given['path'],
            'content': given['data'],
            'b64': b64_encoded,
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
            f'no ACK of `PUT FILE` from workspace deployment {instance.deploymentid}'
        )
        return

    return web.json_response({'success': True})
