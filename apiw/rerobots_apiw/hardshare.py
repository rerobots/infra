"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import asyncio
from datetime import datetime
import json
import logging
import math
import os
import time
import uuid

import aiohttp
from aiohttp import web
import sqlalchemy

from . import db as rrdb
from .requestproc import process_headers
from . import tasks, tunnel_hub_tasks
from .hschannels import CommandChannel, ConnectionChannel
from .util import create_subprocess_exec


logger = logging.getLogger(__name__)


async def status_cam(request):
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

    owner = data['user'] if data['org'] is None else data['org']

    payload = dict()
    for wd in (
        request['dbsession']
        .query(rrdb.UserProvidedSupp)
        .filter(rrdb.UserProvidedSupp.owner == owner)
    ):
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == owner,
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(wd.deploymentid),
            )
        )
        row = query.one_or_none()
        if row is not None:
            addon_config = json.loads(row.config)
            if addon_config['hscamid'] not in payload:
                payload[addon_config['hscamid']] = []
            payload[addon_config['hscamid']].append(wd.deploymentid)

    return web.json_response(payload, headers=data['response_headers'])


async def apply_cam(request):
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

    if not request.can_read_body:
        return web.Response(
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    given = await request.json()
    if 'wds' not in given:
        return web.Response(
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    wds = list(given['wds'])
    if len(wds) != len(set(wds)):
        return web.Response(
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    owner = data['user'] if data['org'] is None else data['org']

    for wd in wds:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.owner == owner,
                rrdb.UserProvidedSupp.deploymentid == wd,
            )
            .one_or_none()
        )
        if usupp is None:
            return web.Response(status=400, headers=data['response_headers'])

    if 'crop' in given:
        if set(wds) != set(given['crop'].keys()):
            return web.Response(status=400, headers=data['response_headers'])
        crop = given['crop']
    else:
        crop = None

    hscam_id = str(uuid.uuid4())
    config = {
        'hscamid': hscam_id,
        'wds': list(wds),
        'status': 'active',
        'crop': crop,
    }
    active_addons = []
    for wd in wds:
        prior = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == owner,
                rrdb.ActiveAddon.instanceid_with_addon == f'{wd}:hscam',
            )
            .one_or_none()
        )
        if prior is not None:
            prior_hscam_id = json.loads(prior.config)['hscamid']
            return web.json_response(
                {
                    'error_message': 'deployment already has associated camera',
                    'wd': wd,
                    'id': prior_hscam_id,
                },
                status=400,
                headers=data['response_headers'],
            )

        active_addons.append(
            rrdb.ActiveAddon(
                instanceid_with_addon=f'{wd}:hscam',
                user=owner,
                config=json.dumps(config),
            )
        )

    for active_addon in active_addons:
        request['dbsession'].add(active_addon)

    return web.json_response({'id': hscam_id}, headers=data['response_headers'])


async def remove_cam(request):
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

    owner = data['user'] if data['org'] is None else data['org']

    hscamera_id = request.match_info['hscamid']
    for wd in (
        request['dbsession']
        .query(rrdb.UserProvidedSupp)
        .filter(rrdb.UserProvidedSupp.owner == owner)
    ):
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == owner,
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(wd.deploymentid),
            )
        )
        row = query.one_or_none()
        if row is not None:
            addon_config = json.loads(row.config)
            if addon_config['hscamid'] == hscamera_id:
                request['dbsession'].delete(row)

    # TODO: close corresponding WebSocket

    return web.Response(status=200, headers=data['response_headers'])


async def cam_upload(request):
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

    owner = data['user'] if data['org'] is None else data['org']

    hscamera_id = request.match_info['hscamid']
    matched = False
    for wd in (
        request['dbsession']
        .query(rrdb.UserProvidedSupp)
        .filter(rrdb.UserProvidedSupp.owner == owner)
    ):
        query = (
            request['dbsession']
            .query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == owner,
                rrdb.ActiveAddon.instanceid_with_addon
                == '{}:hscam'.format(wd.deploymentid),
            )
        )
        row = query.one_or_none()
        if row is not None:
            addon_config = json.loads(row.config)
            if addon_config['hscamid'] == hscamera_id:
                matched = True
                break
    if not matched:
        return web.json_response(
            {'error_message': 'hardshare camera id not found'}, status=404
        )

    ws = web.WebSocketResponse(timeout=90.0, heartbeat=30.0, autoping=True)
    await ws.prepare(request)

    handle = '{}:hscam:0'.format(hscamera_id)
    crop_config = addon_config.get('crop', None)
    wds = addon_config['wds']
    sender_task = request.app.loop.create_task(
        cam_start_stop_sender(handle, crop_config, ws.send_str, wds=wds)
    )
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
                # TODO: restart
                break

    except asyncio.CancelledError:
        logger.debug(
            'this coroutine is returning after receiving CancelledError; trying to restart...'
        )
        # TODO: restart
        sender_task.cancel()
        return ws

    logger.debug('this coroutine is returning')
    sender_task.cancel()
    return ws


async def list_my_wdeployments(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    if 'with_dissolved' in request.query:
        if request.query['with_dissolved'] == '':
            with_dissolved = True  # for GET /hardshare/list?with_dissolved
        elif request.query['with_dissolved'].lower() in ['f', 'false', '0']:
            with_dissolved = False
        else:
            with_dissolved = True
    else:
        with_dissolved = False

    if data['org']:
        owner = data['org']
    else:
        owner = data['user']

    query = (
        request['dbsession']
        .query(rrdb.Deployment, rrdb.UserProvidedSupp)
        .filter(rrdb.UserProvidedSupp.owner == owner)
        .filter(rrdb.Deployment.deploymentid == rrdb.UserProvidedSupp.deploymentid)
    )
    if not with_dissolved:
        query = query.filter(rrdb.Deployment.date_dissolved == None)
    wds = [
        {
            'id': row[0].deploymentid,
            'date_created': row[0].date_created.strftime('%Y-%m-%d %H:%M UTC'),
            'origin': row[1].registration_origin,
            'dissolved': row[0].date_dissolved.strftime('%Y-%m-%d %H:%M UTC')
            if row[0].date_dissolved is not None
            else None,
            'heartbeat': row[0].last_heartbeat.strftime('%Y-%m-%d %H:%M UTC')
            if row[0].last_heartbeat is not None
            else None,
            'lockout': row[0].locked_out,
        }
        for row in query
    ]
    for j, wd in enumerate(wds):
        upattr = (
            request['dbsession']
            .query(rrdb.UserProvidedAttr)
            .filter(rrdb.UserProvidedAttr.deploymentid == wd['id'])
            .one_or_none()
        )
        if upattr is None:
            wds[j]['desc'] = None
        else:
            wds[j]['desc'] = upattr.description
    return web.json_response(
        {'wdeployments': wds, 'owner': owner}, headers=data['response_headers']
    )


async def list_owners(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if not data['su']:
        return web.Response(status=404, headers=data['response_headers'])
    owners = set(
        [
            row[0]
            for row in request['dbsession'].query(rrdb.UserProvidedSupp.owner).all()
        ]
    )
    return web.json_response({'owners': list(owners)}, headers=data['response_headers'])


async def admin_list_wdeployments(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if not data['su']:
        return web.Response(status=404, headers=data['response_headers'])
    if 'with_dissolved' in request.query:
        if request.query['with_dissolved'] == '':
            with_dissolved = True  # for GET /hardshare/list/<owner>?with_dissolved
        elif request.query['with_dissolved'].lower() in ['f', 'false', '0']:
            with_dissolved = False
        else:
            with_dissolved = True
    else:
        with_dissolved = False
    query = (
        request['dbsession']
        .query(rrdb.Deployment, rrdb.UserProvidedSupp)
        .filter(rrdb.Deployment.deploymentid == rrdb.UserProvidedSupp.deploymentid)
    )
    query = query.filter(rrdb.UserProvidedSupp.owner == request.match_info['owner'])
    if not with_dissolved:
        query = query.filter(rrdb.Deployment.date_dissolved == None)
    wds = [row[1].deploymentid for row in query]
    return web.json_response({'wdeployments': wds}, headers=data['response_headers'])


async def instances_on_mine(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wds = [
        row[0]
        for row in request['dbsession']
        .query(rrdb.UserProvidedSupp.deploymentid)
        .filter(rrdb.UserProvidedSupp.owner == data['user'])
    ]
    if len(wds) == 0:
        return web.json_response([], headers=data['response_headers'])

    # Parse query parameters
    if 'include_terminated' in request.query:
        include_terminated = True
    else:
        include_terminated = False
    include_init_fail = 'include_init_fail' in request.query
    if 'sort_by' in request.query and request.query['sort_by'] in [
        'dec_start_date',
        'inc_start_date',
    ]:
        sort_by = request.query['sort_by']
    else:
        sort_by = 'dec_start_date'
    if 'max_per_page' in request.query:
        try:
            max_per_page = int(request.query['max_per_page'])
            if max_per_page < 0:
                logger.warning(
                    'received negative max_per_page parameter to GET /instances'
                )
                max_per_page = 0
        except:
            max_per_page = 0
    else:
        max_per_page = 0
    if 'page' in request.query:
        try:
            page = int(request.query['page'])
            if page < 1:
                logger.warning('received non-positive page parameter to GET /instances')
                page = 1
        except:
            page = 1
    else:
        page = 1

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(sqlalchemy.or_(*[rrdb.Instance.deploymentid == wd for wd in wds]))
    )
    if not include_terminated:
        query = query.filter(rrdb.Instance.status != 'TERMINATED')
    if not include_init_fail:
        query = query.filter(rrdb.Instance.status != 'INIT_FAIL')
    if sort_by == 'dec_start_date':
        query = query.order_by(sqlalchemy.desc(rrdb.Instance.starttime))
    elif sort_by == 'inc_start_date':
        query = query.order_by(sqlalchemy.asc(rrdb.Instance.starttime))
    else:
        logger.error('unrecognized sort_by parameter to GET /hardshare/instances')

    if max_per_page == 0:
        page_count = 1
    else:
        page_count = math.ceil(query.count() / max_per_page)
    if page > page_count:
        page = page_count

    # TODO: return page_count in header?

    instances = []
    for ii, row in enumerate(query):
        if (max_per_page == 0) or (ii // max_per_page == page - 1):
            instances.append(
                {
                    'id': row.instanceid,
                    'starttime': str(row.starttime),
                    'status': row.status,
                    'wdid': row.deploymentid,
                    'user': row.rootuser,
                }
            )
            if row.endtime is not None:
                instances[-1]['endtime'] = str(row.endtime)
        elif ii >= page * max_per_page:
            break

    return web.json_response(instances, headers=data['response_headers'])


async def instance_on_mine(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wds = [
        row[0]
        for row in request['dbsession']
        .query(rrdb.UserProvidedSupp.deploymentid)
        .filter(rrdb.UserProvidedSupp.owner == data['user'])
    ]
    if len(wds) == 0:
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instance_id)
    )
    query = query.filter(
        sqlalchemy.or_(*[rrdb.Instance.deploymentid == wd for wd in wds])
    )
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    instance = {
        'id': row.instanceid,
        'starttime': str(row.starttime),
        'status': row.status,
        'wdid': row.deploymentid,
        'user': row.rootuser,
    }
    if row.endtime is not None:
        instance['endtime'] = str(row.endtime)

    return web.json_response(instance, headers=data['response_headers'])


async def terminate_instance_on_mine(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wds = [
        row[0]
        for row in request['dbsession']
        .query(rrdb.UserProvidedSupp.deploymentid)
        .filter(rrdb.UserProvidedSupp.owner == data['user'])
    ]
    if len(wds) == 0:
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instance_id)
    )
    query = query.filter(
        sqlalchemy.or_(*[rrdb.Instance.deploymentid == wd for wd in wds])
    )
    row = query.one_or_none()
    if row is None:
        return web.json_response(
            {'error_message': 'instance not found'},
            status=404,
            headers=data['response_headers'],
        )

    if row.status in ['INIT', 'INIT_FAIL']:
        return web.json_response(
            {
                'error_message': 'cannot terminate instance that is '
                + row.status.lower()
            },
            status=400,
            headers=data['response_headers'],
        )
    if row.status in ['TERMINATED', 'TERMINATING']:
        return web.json_response(
            {'error_message': 'instance already ' + row.status.lower()},
            status=400,
            headers=data['response_headers'],
        )

    row.terminating_started_at = datetime.utcnow()
    row.status = 'TERMINATING'
    tasks.terminate_instance.delay(row.instanceid, row.deploymentid)

    return web.Response(headers=data['response_headers'])


async def edit_attr_wd(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    if request.can_read_body:
        given = await request.json()
    else:
        return web.json_response(
            {'error_message': 'arguments required but not given'},
            status=400,
            headers=data['response_headers'],
        )

    if (
        'desc' not in given
        or not isinstance(given['desc'], str)
        or len(given['desc']) > 80
    ):
        return web.json_response(
            {'error_message': 'argument `desc` not given or wrong form'},
            status=400,
            headers=data['response_headers'],
        )

    wdeployment_id = request.match_info['wdid']

    query = (
        request['dbsession']
        .query(rrdb.UserProvidedSupp.deploymentid)
        .filter(rrdb.UserProvidedSupp.owner == data['user'])
    )
    query = query.filter(rrdb.UserProvidedSupp.deploymentid == wdeployment_id)
    upwd = query.one_or_none()
    if upwd is None:
        return web.json_response(
            {'error_message': 'workspace deployment not found'},
            status=404,
            headers=data['response_headers'],
        )

    upattr = (
        request['dbsession']
        .query(rrdb.UserProvidedAttr)
        .filter(rrdb.UserProvidedAttr.deploymentid == upwd.deploymentid)
        .one_or_none()
    )
    if upattr is None:
        upattr = rrdb.UserProvidedAttr(
            deploymentid=upwd.deploymentid, description=given['desc']
        )
        request['dbsession'].add(upattr)
    else:
        upattr.description = given['desc']

    return web.Response(headers=data['response_headers'])


async def cam_start_stop_sender(handle, crop_config, send, wds=None):
    croptask = None
    sent_start = False
    try:
        while True:
            await asyncio.sleep(5)

            if wds:
                with rrdb.create_session_context() as session:
                    query = session.query(rrdb.Instance).filter(
                        sqlalchemy.and_(
                            rrdb.Instance.status != 'TERMINATING',
                            rrdb.Instance.status != 'TERMINATED',
                            rrdb.Instance.status != 'INIT_FAIL',
                            rrdb.Instance.status != 'NONE',
                        )
                    )
                    query = query.filter(
                        sqlalchemy.or_(
                            *[rrdb.Instance.deploymentid == wd for wd in wds]
                        )
                    )
                    if session.query(query.exists()).one()[0]:
                        if not sent_start:
                            await send('START')
                            sent_start = True
                            if crop_config:
                                croptask = await create_subprocess_exec(
                                    './crop', handle, json.dumps(crop_config)
                                )
                    else:
                        if sent_start:
                            await send('STOP')
                            sent_start = False
                            if croptask is not None:
                                croptask.kill()
                                croptask = None

    except asyncio.CancelledError:
        try:
            if croptask is not None:
                croptask.kill()
        except Exception as err:
            logger.warning('{}: {}'.format(type(err), err))


async def register_new_wdeployment(request):
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

    if data['org']:
        owner = data['org']
    else:
        owner = data['user']

    if 'X-Forwarded-For' in request.headers:
        peername = request.headers['X-Forwarded-For']
    else:
        peername = str(request.transport.get_extra_info('peername')[0])
    new_wdeployment_id = str(uuid.uuid4())
    result = {
        'id': new_wdeployment_id,
        'owner': owner,
    }
    tasks.register_new_user_provided.delay(
        wdeployment_id=new_wdeployment_id, owner=owner, origin=peername
    )
    return web.json_response(result, headers=data['response_headers'])


async def check_wdeployment_data(request):
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
    wdeployment_id_prefix = request.match_info['did']
    try:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.deploymentid.startswith(wdeployment_id_prefix),
                rrdb.UserProvidedSupp.owner == data['user'],
            )
            .one_or_none()
        )
        if usupp is None:
            return web.Response(status=404, headers=data['response_headers'])
        wd = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == usupp.deploymentid)
            .one()
        )
    except:
        return web.Response(status=404, headers=data['response_headers'])
    payload = {
        'id': wd.deploymentid,
        'date_created': wd.date_created.strftime('%Y-%m-%d %H:%M UTC'),
        'origin': usupp.registration_origin,
    }
    if wd.date_dissolved is not None:
        payload['date_dissolved'] = wd.date_dissolved.strftime('%Y-%m-%d %H:%M UTC')
    return web.json_response(payload, headers=data['response_headers'])


async def dissolve_wdeployment(request):
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
    wdeployment_id_prefix = request.match_info['did']
    try:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.deploymentid.startswith(wdeployment_id_prefix),
                rrdb.UserProvidedSupp.owner == data['user'],
            )
            .one_or_none()
        )
        if usupp is None:
            return web.Response(status=404, headers=data['response_headers'])
        wd = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == usupp.deploymentid)
            .one()
        )
    except:
        return web.Response(status=404, headers=data['response_headers'])
    if wd.date_dissolved is not None:
        return web.json_response(
            {'error_message': 'already dissolved'},
            status=400,
            headers=data['response_headers'],
        )
    wd.date_dissolved = datetime.utcnow()
    tasks.dissolve_user_provided.delay(
        wdeployment_id=wd.deploymentid,
        owner=usupp.owner,
        date_dissolved=wd.date_dissolved,
    )
    return web.json_response(
        {
            'id': wd.deploymentid,
            'date_created': wd.date_created.strftime('%Y-%m-%d %H:%M UTC'),
            'date_dissolved': wd.date_dissolved.strftime('%Y-%m-%d %H:%M UTC'),
            'origin': usupp.registration_origin,
        },
        headers=data['response_headers'],
    )


async def sshtun_wait(ws_send, instance_id, message_id):
    while True:
        await asyncio.sleep(2)
        with rrdb.create_session_context() as session:
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            if (
                len(instance.associated_th) > 0
                and len(instance.listening_ipaddr) > 0
                and instance.listening_port > 0
                and instance.th_infra_port > 0
            ):
                associated_th = instance.associated_th
                listening_ipaddr = instance.listening_ipaddr
                listening_port = instance.listening_port
                th_infra_port = instance.th_infra_port
                break

    await ws_send(
        json.dumps(
            {
                'v': 0,
                'cmd': 'CREATE_SSHTUN_DONE',
                'mi': message_id,
                'id': instance_id,
                'thid': associated_th,
                'ipv4': listening_ipaddr,
                'hostkey': 'nil',
                'port': listening_port,
                'thport': th_infra_port,
                'thuser': 'root',
            }
        )
    )


async def advertise_wdeployment(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )

    wdeployment_id_prefix = request.match_info['did']
    try:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.deploymentid.startswith(wdeployment_id_prefix),
                rrdb.UserProvidedSupp.owner == data['user'],
            )
            .one_or_none()
        )
        if usupp is None:
            if data['org'] is None:
                return web.Response(status=404, headers=data['response_headers'])
            else:
                usupp = (
                    request['dbsession']
                    .query(rrdb.UserProvidedSupp)
                    .filter(
                        rrdb.UserProvidedSupp.deploymentid.startswith(
                            wdeployment_id_prefix
                        ),
                        rrdb.UserProvidedSupp.owner == data['org'],
                    )
                    .one_or_none()
                )
                if usupp is None:
                    return web.Response(status=404, headers=data['response_headers'])
        wd = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == usupp.deploymentid)
            .one()
        )
    except:
        return web.Response(status=404, headers=data['response_headers'])

    if wd.date_dissolved is not None:
        return web.json_response(
            {'error_message': 'cannot advertise dissolved workspace deployment'},
            status=400,
            headers=data['response_headers'],
        )

    pid = os.urandom(4)
    rkey = 'hs:' + wd.deploymentid

    request.app['red'].hset(rkey, 'ad', pid)
    if request.app['red'].hget(rkey, 'ad') != pid:
        return web.json_response(
            {'error_message': 'busy'}, status=400, headers=data['response_headers']
        )

    sshtun_tasks = dict()
    portaccess = None
    eacommand = None
    heartbeat_task = None
    try:
        ws = web.WebSocketResponse(timeout=90.0, heartbeat=30.0, autoping=True)
        await ws.prepare(request)
        logger.debug('WebSocket opened for {}'.format(wd.deploymentid))

        portaccess = ConnectionChannel(
            wd.deploymentid,
            ws.send_str,
            pid=pid,
            rkey=rkey,
            red=request.app['red'],
            event_loop=request.app.loop,
        )
        eacommand = CommandChannel(
            wd.deploymentid,
            ws.send_str,
            pid=pid,
            rkey=rkey,
            red=request.app['red'],
            event_loop=request.app.loop,
        )
        portaccess.start()
        eacommand.start()
        while not eacommand.is_open() or not portaccess.is_open():
            logger.debug('waiting for AMQP channels to open...')
            await asyncio.sleep(1)
        logger.debug('all AMQP channels opened')

        heartbeat_task = request.app.loop.create_task(eacommand.heartbeat())

        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), 5)
            except asyncio.TimeoutError:
                msg = None
            if request.app['red'].hget(rkey, 'ad') != pid:
                if msg is not None:
                    logger.warning(
                        'do not have semaphore, but received via WebSocket: {}'.format(
                            msg
                        )
                    )
                return ws
            if msg is None:
                continue
            if msg.type == aiohttp.WSMsgType.TEXT:
                logger.debug('received ws message: {}'.format(msg.data))
                try:
                    payload = json.loads(msg.data)
                    assert 'v' in payload and payload['v'] == 0
                    assert 'cmd' in payload
                except:
                    print('ERROR: failed to parse message payload.')
                    await ws.close()
                    break

                if payload['cmd'] in ['ACK', 'NACK']:
                    if 'mi' not in payload:
                        if 'req' in payload:
                            if (
                                payload['req'] == 'INSTANCE_DESTROY'
                                and payload.get('st', None) == 'DONE'
                            ):
                                if (
                                    eacommand.prior_instance is not None
                                    and time.monotonic() - eacommand.prior_instance[1]
                                    < 300.0
                                ):
                                    with rrdb.create_session_context() as session:
                                        inst = (
                                            session.query(rrdb.Instance)
                                            .filter(
                                                rrdb.Instance.deploymentid
                                                == wd.deploymentid,
                                                rrdb.Instance.instanceid
                                                == eacommand.prior_instance[0],
                                            )
                                            .one_or_none()
                                        )
                                    if inst is not None:
                                        eacommand.send_to_apiw(
                                            {
                                                'req': 'INSTANCE DESTROY',
                                                'message_id': eacommand.prior_instance[
                                                    2
                                                ],
                                                'command': 'ACK',
                                                'st': 'DONE',
                                            }
                                        )
                                    else:
                                        logger.warning(
                                            'INSTANCE_DESTROY but prior instance {} not found'.format(
                                                eacommand.prior_instance[0]
                                            )
                                        )
                                else:
                                    logger.warning(
                                        'INSTANCE_DESTROY but prior instance {} destroyed too long ago'.format(
                                            eacommand.prior_instance[0]
                                        )
                                    )
                            else:
                                logger.warning(
                                    'received {} message with unknown req'.format(
                                        payload['cmd']
                                    )
                                )
                        else:
                            logger.error(
                                'received message that lacks required ID or req'
                            )
                    elif payload['mi'] in eacommand.expected_resp:
                        logger.debug(
                            'start async eacommand.handle_response(payload={}), for expected_resp: {}'.format(
                                payload, eacommand.expected_resp[payload['mi']]
                            )
                        )
                        request.app.loop.create_task(eacommand.handle_response(payload))
                    elif payload['mi'] in portaccess.expected_resp:
                        logger.debug(
                            'start async ConnectionChannel.handle_response(payload={}), for expected_resp: {}'.format(
                                payload, portaccess.expected_resp[payload['mi']]
                            )
                        )
                        request.app.loop.create_task(
                            portaccess.handle_response(payload)
                        )
                    else:
                        logger.warning(
                            'received unexpected response message ' 'with ID {}'.format(
                                payload['mi']
                            )
                        )

                elif payload['cmd'] == 'INSTANCE_STATUS':
                    if 's' not in payload:
                        logger.warning(
                            'received status update message that '
                            'is missing `s` entry. ignoring...'
                        )
                    elif payload['s'] != 'NONE':
                        with rrdb.create_session_context() as session:
                            try:
                                inst = (
                                    session.query(rrdb.Instance)
                                    .filter(
                                        rrdb.Instance.deploymentid == wd.deploymentid,
                                        rrdb.Instance.instanceid
                                        == eacommand.current_instance_id,
                                    )
                                    .one()
                                )
                                instance_id = inst.instanceid
                            except:
                                inst = None
                                instance_id = None
                            if inst is None:
                                logger.warning(
                                    'received status update message'
                                    ' when there is no instance'
                                )
                            elif payload['s'] not in [
                                'INIT',
                                'NONE',
                                'READY',
                                'INIT_FAIL',
                            ]:
                                logger.warning(
                                    'received unknown status string' ' from client'
                                )
                            else:
                                inst.status = payload['s']
                                if inst.status == 'READY' and inst.ready_at is None:
                                    inst.ready_at = datetime.utcnow()
                                if 'h' in payload:
                                    inst.hostkey = payload['h']
                                    logger.debug(
                                        'received instance hostkey '
                                        '{} (status: {})'.format(
                                            inst.hostkey, inst.status
                                        )
                                    )
                        if instance_id is not None:
                            eacommand.send_status(instance_id)

                elif payload['cmd'] == 'CREATE_SSHTUN':
                    if 'mi' not in payload:
                        logger.warning(
                            'received message that lacks ID with CREATE_SSHTUN'
                        )
                        message_id = None
                    else:
                        message_id = payload['mi']

                    if eacommand.current_instance_id is not None:
                        with rrdb.create_session_context() as session:
                            instance = (
                                session.query(rrdb.Instance)
                                .filter(
                                    rrdb.Instance.deploymentid == wd.deploymentid,
                                    rrdb.Instance.instanceid
                                    == eacommand.current_instance_id,
                                )
                                .one_or_none()
                            )
                            if instance is None:
                                instance_id = None
                            else:
                                instance_id = instance.instanceid
                    else:
                        instance_id = None

                    if instance_id is None:
                        logger.warning(
                            f'received CREATE_SSHTUN from wdeployment {wd.deploymentid} that does not have instance yet. (current_instance_id: {eacommand.current_instance_id})'
                        )
                    else:
                        tunnelkey = payload.get('key', '')
                        proxy_mode = payload.get('proxy', False)
                        tunnel_hub_tasks.create_sshtun.delay(
                            instance_id=instance_id,
                            pubkey=tunnelkey,
                            proxy_mode=proxy_mode,
                        )
                        if instance_id in sshtun_tasks:
                            sshtun_tasks[instance_id].cancel()
                        sshtun_tasks[instance_id] = request.app.loop.create_task(
                            sshtun_wait(
                                ws_send=ws.send_str,
                                instance_id=instance_id,
                                message_id=message_id,
                            )
                        )

                elif payload['cmd'] == 'TH_SEARCH':
                    # TODO: deprecated!
                    if 'mi' not in payload:
                        logger.warning('received message that lacks ID with TH_SEARCH')
                        message_id = None
                    else:
                        message_id = payload['mi']

                    if 'mo' not in payload:
                        conntype = 'sshtun'
                    elif payload['mo'] not in ['sshtun', 'vpn']:
                        logger.warning(
                            'received request for unknown connection mode; defaulting to sshtun'
                        )
                        conntype = 'sshtun'
                    else:
                        conntype = payload['mo']

                    tunnelkey = payload.get('key', '')

                    try:
                        inst = (
                            session.query(rrdb.Instance)
                            .filter(
                                rrdb.Instance.deploymentid == wd.deploymentid,
                                rrdb.Instance.instanceid
                                == eacommand.current_instance_id,
                            )
                            .one()
                        )
                    except:
                        inst = None
                        logger.warning(
                            'received TH_SEARCH from wdeployment {} that does not have instance yet. (current_instance_id: {})'.format(
                                wd.deploymentid, eacommand.current_instance_id
                            )
                        )

                    if inst is not None:
                        if inst.instanceid in portaccess.associate_tasks:
                            portaccess.associate_tasks[inst.instanceid].cancel()
                        portaccess.associate_tasks[inst.instanceid] = (
                            request.app.loop.create_task(
                                portaccess.associate_tunnelhub(
                                    ws_send=ws.send_str,
                                    instance_id=inst.instanceid,
                                    message_id=message_id,
                                    conntype=conntype,
                                    publickey=tunnelkey,
                                )
                            )
                        )

                elif payload['cmd'] == 'VPN_CREATE':
                    pass  # TODO

                elif payload['cmd'] == 'VPN_NEWCLIENT':
                    pass  # TODO

                elif payload['cmd'] == 'SSHTUN_DELETE':
                    if (
                        eacommand.current_instance_id is None
                        and eacommand.prior_instance is not None
                        and time.monotonic() - eacommand.prior_instance[1] < 300.0
                    ):
                        instance_id = eacommand.prior_instance[0]
                    else:
                        instance_id = eacommand.current_instance_id
                    with rrdb.create_session_context() as session:
                        inst = (
                            session.query(rrdb.Instance)
                            .filter(
                                rrdb.Instance.deploymentid == wd.deploymentid,
                                rrdb.Instance.instanceid == instance_id,
                            )
                            .one_or_none()
                        )
                        if inst is None:
                            logger.warning(
                                'received SSHTUN_DELETE' ' when there is no instance'
                            )
                        elif len(inst.associated_th) == 0:
                            logger.warning(
                                'received SSHTUN_DELETE'
                                ' when there is no associated hub'
                            )
                        else:
                            portaccess.send_to_thportal(
                                {
                                    'command': 'SSHTUN DELETE',
                                    'id': inst.deploymentid,
                                    'inid': inst.instanceid,
                                    'thid': inst.associated_th,
                                }
                            )

                else:
                    logger.warning(
                        'received unknown ws command from client. ignoring...'
                    )

            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.debug('WebSocket CLOSED for {}'.format(wd.deploymentid))
                break

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.debug(
                    'error message in WebSocket session ' 'for {}'.format(
                        wd.deploymentid
                    )
                )
                break

            else:
                logger.debug(
                    'unexpected message type {} in WebSocket session' ' for {}'.format(
                        msg.type, wd.deploymentid
                    )
                )
                break

    except asyncio.CancelledError:
        pass

    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if request.app['red'].hget(rkey, 'ad') == pid:
            request.app['red'].hdel(rkey, 'ad')
        if portaccess:
            for instance_id, atask in portaccess.associate_tasks.items():
                atask.cancel()
            portaccess.stop()
        for instance_id, task in sshtun_tasks.items():
            task.cancel()
        if eacommand:
            eacommand.stop()

    return ws


async def update_wdeployment(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )

    wdeployment_id = request.match_info['did']
    try:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.deploymentid == wdeployment_id,
                rrdb.UserProvidedSupp.owner == data['user'],
            )
            .one_or_none()
        )
    except:
        return web.Response(status=404, headers=data['response_headers'])
    if usupp is None:
        return web.Response(status=404, headers=data['response_headers'])

    try:
        given = await request.json()
        assert 'supported_addons' in given
        if 'addons_config' not in given:
            given['addons_config'] = dict()
        assert len(given) == 2
        for addon in given['supported_addons']:
            assert addon in ('cam', 'cmd', 'cmdsh', 'mistyproxy', 'py', 'java', 'vnc')
            if addon == 'mistyproxy':
                assert addon in given['addons_config']
            elif addon == 'cam':
                assert addon not in given['addons_config']
        for k in given['addons_config']:
            assert k in given['supported_addons']
            assert k in ('mistyproxy',)
            if k == 'mistyproxy':
                assert len(given['addons_config']['mistyproxy']) == 1
                assert 'ip' in given['addons_config']['mistyproxy']
    except:
        return web.Response(status=400, headers=data['response_headers'])

    tasks.update_user_provided.delay(
        wdeployment_id=usupp.deploymentid,
        supported_addons=given['supported_addons'],
        addons_config=given['addons_config'],
    )

    return web.Response(status=200)
