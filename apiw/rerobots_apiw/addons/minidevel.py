"""commands for add-ons involving mini development environments


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import hashlib
import json
import logging
import os

from aiohttp import web

from .. import db as rrdb
from .. import proxy_tasks
from ..requestproc import process_headers


logger = logging.getLogger(__name__)


async def apply_addon_py(request):
    return await apply_addon(request, kind='py')


async def status_addon_py(request):
    return await status_addon(request, kind='py')


async def remove_addon_py(request):
    return await remove_addon(request, kind='py')


async def apply_addon_java(request):
    return await apply_addon(request, kind='java')


async def status_addon_java(request):
    return await status_addon(request, kind='java')


async def remove_addon_java(request):
    return await remove_addon(request, kind='java')


async def apply_addon(request, kind):
    r"""

    kind \in {py, java}
    """
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
    if kind not in wdeployment.supported_addons:
        # TODO: after creating more add-ons, this quick substring
        # should be changed to the general case of casting to a list
        # from comma-separated values.
        return web.json_response(
            {'error_message': f'this instance does not support the `{kind}` add-on'},
            status=503,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    if query.count() > 0:
        return web.json_response(
            {
                'error_message': 'add-on `{}` already applied to instance {}'.format(
                    kind, instance_id
                )
            },
            status=503,  # Service Unavailable
            headers=data['response_headers'],
        )

    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit modifying this add-on'},
            status=400,
            headers=data['response_headers'],
        )

    config = {
        'ptoken': hashlib.sha256(os.urandom(128)).hexdigest(),
        'port': 0,  # 0 => undefined
        'user': tunneling_user,
        'status': 'starting',  # status \in {active, starting, stopping}
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon='{}:{}'.format(instance_id, kind),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)
    request['dbsession'].commit()

    if kind == 'py':
        proxy_tasks.start_minidevel_py.delay(user=data['user'], instance_id=instance_id)
    else:  # kind == 'java':
        proxy_tasks.start_minidevel_java.delay(
            user=data['user'], instance_id=instance_id
        )

    return web.Response(status=200, headers=data['response_headers'])


async def status_addon(request, kind):
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

    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}')
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': f'add-on `{kind}` not active on this instance'},
            status=404,
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    payload = {
        'status': addon_config['status'],
    }
    if addon_config['status'] == 'active':
        payload['url'] = addon_config['url']

    return web.json_response(payload, headers=data['response_headers'])


async def remove_addon(request, kind):
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

    row = query.one_or_none()
    if row is None:
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:{}'.format(instance_id, kind),
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'add-on `{}` not active on this instance'.format(kind)},
            status=404,
            headers=data['response_headers'],
        )

    if kind == 'py':
        proxy_tasks.stop_minidevel_py.delay(user=data['user'], instance_id=instance_id)
    else:  # kind == 'java':
        proxy_tasks.stop_minidevel_java.delay(
            user=data['user'], instance_id=instance_id
        )

    return web.Response(status=200, headers=data['response_headers'])
