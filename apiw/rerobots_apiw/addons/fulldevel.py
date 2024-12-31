"""commands for add-ons involving fully featured development environments

e.g., VS Code (https://code.visualstudio.com/) via https://github.com/cdr/code-server.git


SCL <scott@rerobots>
Copyright (C) 2021 rerobots, Inc.
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


async def apply_addon_vscode(request):
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
    if 'vscode' not in wdeployment.supported_addons:
        return web.json_response(
            {'error_message': 'this instance does not support the `vscode` add-on'},
            status=503,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vscode'.format(instance_id)
        )
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    if query.count() > 0:
        return web.json_response(
            {
                'error_message': 'add-on `vscode` already applied to instance {}'.format(
                    instance_id
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
        instanceid_with_addon='{}:vscode'.format(instance_id),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)
    request['dbsession'].commit()
    proxy_tasks.start_vscode.delay(user=data['user'], instance_id=instance_id)

    return web.Response(status=200, headers=data['response_headers'])


async def status_addon_vscode(request):
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
        .filter(
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vscode'.format(instance_id)
        )
    )
    if 'in' not in data['payload']:
        query = query.filter(rrdb.ActiveAddon.user == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'add-on `vscode` not active on this instance'},
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


async def remove_addon_vscode(request):
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
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vscode'.format(instance_id),
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'add-on `vscode` not active on this instance'},
            status=404,
            headers=data['response_headers'],
        )

    proxy_tasks.stop_vscode.delay(user=data['user'], instance_id=instance_id)

    return web.Response(status=200, headers=data['response_headers'])
