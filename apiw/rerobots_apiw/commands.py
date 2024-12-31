#!/usr/bin/env python
"""API worker

Status codes of responses follow common practice and the HTTP/1.1
standard (RFC 2616). However, there can be cases of ambiguity, and
some may eventually be revised.


SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

from ast import literal_eval
import asyncio
import base64
from datetime import datetime, timedelta
import json
import logging
import math
import re
import uuid

import aiocache
from aiohttp import web
import jwt
import sqlalchemy

from . import db as rrdb
from .cap import accept_instantiate
from .requestproc import process_headers, rate_limit
from .settings import CACHE_TTL


logger = logging.getLogger(__name__)


class Error(Exception):
    """Error not otherwise specified"""

    pass


class WDeploymentNotFound(Error):
    """No workspace deployment found with given identifier."""


async def index(request):
    """List various available API routes and indicate parameters"""
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    return web.json_response(
        {
            'workspace_types': 'https://api.rerobots.net/workspaces',
            'workspace_deployments': 'https://api.rerobots.net/deployments{/workspace_type}',
            'deployment_details': 'https://api.rerobots.net/deployment/:deploymentID',
            'create_instance': 'https://api.rerobots.net/new/{:workspace_type,:deploymentID}',
            'vpn': 'https://api.rerobots.net/vpn/:instanceID',
            'firewall': 'https://api.rerobots.net/firewall/:instanceID',
            'terminate_instance': 'https://api.rerobots.net/terminate/:instanceID',
        },
        headers=data['response_headers'],
    )


async def list_workspace_types(request):
    """List known workspace types"""
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if 'with_dissolved' in request.query:
        if request.query['with_dissolved'] == '':
            with_dissolved = True  # for GET /workspaces?with_dissolved
        elif request.query['with_dissolved'].lower() in ['f', 'false', '0']:
            with_dissolved = False
        else:
            with_dissolved = True
    else:
        with_dissolved = False
    query = request['dbsession'].query(rrdb.Deployment.wtype)
    if not with_dissolved:
        query = query.filter(rrdb.Deployment.date_dissolved == None)
    return web.json_response(
        {'workspace_types': [wtype for (wtype,) in query.distinct()]},
        headers=data['response_headers'],
    )


async def list_deployments(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if CACHE_TTL > 0:
        cache = aiocache.caches.get('default')
        res = await cache.get(request.path_qs)
        if res:
            res = json.loads(res)
            return web.json_response(res, headers=data['response_headers'])

    if 'with_dissolved' in request.query:
        if request.query['with_dissolved'] == '':
            with_dissolved = True  # for GET /deployments?with_dissolved
        elif request.query['with_dissolved'].lower() in ['f', 'false', '0']:
            with_dissolved = False
        else:
            with_dissolved = True
    else:
        with_dissolved = False

    if 'info' in request.query:
        if request.query['info'] == '':
            with_info = True  # for GET /deployments?info
        elif request.query['info'].lower() in ['f', 'false', '0']:
            with_info = False
        else:
            with_info = True
    else:
        with_info = False

    # Parse query parameters
    if 'sort_by' in request.query and request.query['sort_by'] in [
        'dec_wtype',
        'inc_wtype',
        'inc_created',
        'dec_created',
        'inc_count',
        'dec_count',
    ]:
        sort_by = request.query['sort_by']
    else:
        sort_by = 'inc_wtype'

    if 'maxlen' in request.query:
        if request.query['maxlen'] == 'u':
            maxlen = 'u'
        else:
            try:
                maxlen = int(request.query['maxlen'])
                if maxlen < 0:
                    logger.warning(
                        'received negative maxlen parameter to GET /deployments'
                    )
                    maxlen = 0
            except:
                maxlen = 'u'
    else:
        maxlen = 'u'

    if 'max_per_page' in request.query:
        try:
            max_per_page = int(request.query['max_per_page'])
            if max_per_page < 0:
                logger.warning(
                    'received negative max_per_page parameter to GET /deployments'
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
                logger.warning(
                    'received non-positive page parameter to GET /deployments'
                )
                page = 1
        except:
            page = 1
    else:
        page = 1

    types = []  # Default is to include all
    if 'types' in request.query:
        if len(request.query['types']) == 0:
            types = []
        else:
            types = [wtype.strip() for wtype in request.query['types'].split(',')]

    elif 'wt' in request.match_info:
        types = [request.match_info['wt']]

    query = request['dbsession'].query(rrdb.Deployment)
    if not with_dissolved:
        query = query.filter(rrdb.Deployment.date_dissolved == None)
    if len(types) > 0:
        base_query = query
        for ii, wtype in enumerate(types):
            if not wtype:
                logger.warning('received empty wtype; skipping')
                continue

            if wtype[0] == '!':
                complement = True
                wtype = wtype[1:]
                if len(wtype) == 1:
                    logger.warning('received empty wtype; skipping')
                    continue
            elif len(wtype) >= 3 and wtype[:3] == '%21':
                complement = True
                wtype = wtype[3:]
                if len(wtype) == 1:
                    logger.warning('received empty wtype; skipping')
                    continue
            else:
                complement = False

            if complement:
                if ii == 0:
                    query = base_query.filter(rrdb.Deployment.wtype != wtype)
                else:
                    query = query.intersection(
                        base_query.filter(rrdb.Deployment.wtype != wtype)
                    )

            else:
                if ii == 0:
                    query = base_query.filter(rrdb.Deployment.wtype == wtype)
                else:
                    query = query.union(
                        base_query.filter(rrdb.Deployment.wtype == wtype)
                    )

    if 'q' in request.query:
        q = request.query['q'].replace('%', r'\%')
        q = q.replace('_', r'\_')
        q = '%' + q + '%'
        query = query.filter(
            sqlalchemy.or_(
                rrdb.Deployment.wtype.ilike(q), rrdb.Deployment.region.ilike(q)
            )
        )

    if sort_by == 'inc_wtype':
        query = query.order_by(sqlalchemy.asc(rrdb.Deployment.wtype))
    elif sort_by == 'dec_wtype':
        query = query.order_by(sqlalchemy.desc(rrdb.Deployment.wtype))
    elif sort_by == 'inc_created':
        query = query.order_by(sqlalchemy.asc(rrdb.Deployment.date_created))
    elif sort_by == 'dec_created':
        query = query.order_by(sqlalchemy.desc(rrdb.Deployment.date_created))
    elif sort_by == 'inc_count':
        query = query.order_by(sqlalchemy.asc(rrdb.Deployment.instance_counter))
    elif sort_by == 'dec_count':
        query = query.order_by(sqlalchemy.desc(rrdb.Deployment.instance_counter))
    else:
        logger.error('unrecognized sort_by parameter to GET /deployments')

    # TODO: compare performance with explicit method (used below) and
    # using SQL JOIN based construction instead.

    matches = []
    if with_info:
        info = dict()
    for row in query:
        if maxlen == 'u':
            matches.append(row.deploymentid)
            if with_info:
                waiting_ub = (
                    request['dbsession']
                    .query(rrdb.Reservation)
                    .filter(
                        rrdb.Reservation.rfilter == 'wd:{}'.format(row.deploymentid)
                    )
                    .count()
                )
                current_count = (
                    request['dbsession']
                    .query(rrdb.Instance)
                    .filter(
                        sqlalchemy.and_(
                            rrdb.Instance.deploymentid == row.deploymentid,
                            rrdb.Instance.status != 'INIT_FAIL',
                            rrdb.Instance.status != 'TERMINATED',
                        )
                    )
                    .count()
                )
                if len(row.supported_addons) == 0:
                    supported_addons = []
                else:
                    supported_addons = row.supported_addons.split(',')
                if 'cam' not in supported_addons:
                    hscam = (
                        request['dbsession']
                        .query(rrdb.ActiveAddon)
                        .filter(
                            rrdb.ActiveAddon.instanceid_with_addon
                            == '{}:hscam'.format(row.deploymentid)
                        )
                        .one_or_none()
                    )
                    if hscam is not None:
                        supported_addons.append('cam')
                info[row.deploymentid] = {
                    'type': row.wtype,
                    'type_version': row.wversion,
                    'supported_addons': supported_addons,
                    'desc': row.description,
                    'region': row.region,
                    'icounter': row.instance_counter,
                    'created': str(row.date_created),
                    'queuelen': current_count + waiting_ub,
                }
        else:
            waiting_ub = (
                request['dbsession']
                .query(rrdb.Reservation)
                .filter(rrdb.Reservation.rfilter == 'wd:{}'.format(row.deploymentid))
                .count()
            )
            if waiting_ub <= maxlen:
                current_count = (
                    request['dbsession']
                    .query(rrdb.Instance)
                    .filter(
                        sqlalchemy.and_(
                            rrdb.Instance.deploymentid == row.deploymentid,
                            rrdb.Instance.status != 'INIT_FAIL',
                            rrdb.Instance.status != 'TERMINATED',
                        )
                    )
                    .count()
                )
                if current_count + waiting_ub <= maxlen:
                    matches.append(row.deploymentid)
                    if with_info:
                        if len(row.supported_addons) == 0:
                            supported_addons = []
                        else:
                            supported_addons = row.supported_addons.split(',')
                        if 'cam' not in supported_addons:
                            hscam = (
                                request['dbsession']
                                .query(rrdb.ActiveAddon)
                                .filter(
                                    rrdb.ActiveAddon.instanceid_with_addon
                                    == '{}:hscam'.format(row.deploymentid)
                                )
                                .one_or_none()
                            )
                            if hscam is not None:
                                supported_addons.append('cam')
                        info[row.deploymentid] = {
                            'type': row.wtype,
                            'type_version': row.wversion,
                            'supported_addons': supported_addons,
                            'desc': row.description,
                            'region': row.region,
                            'icounter': row.instance_counter,
                            'created': str(row.date_created),
                            'queuelen': current_count + waiting_ub,
                        }

    if max_per_page == 0:
        page_count = 1
    else:
        page_count = math.ceil(len(matches) / max_per_page)
    if page > page_count:
        page = page_count

    if max_per_page > 0:
        matches = matches[(page - 1) * max_per_page : page * max_per_page]

    payload = {
        'workspace_deployments': matches,
        'page_count': page_count,
    }
    if with_info:
        payload['info'] = info

    if CACHE_TTL > 0:
        await cache.set(request.path_qs, json.dumps(payload), ttl=CACHE_TTL)
    return web.json_response(payload, headers=data['response_headers'])


def get_wdinfo(dbsession, wdeployment_id, username, is_superuser=False):
    row = (
        dbsession.query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == wdeployment_id)
        .one_or_none()
    )
    if row is None:
        raise WDeploymentNotFound()
    waiting_ub = (
        dbsession.query(rrdb.Reservation)
        .filter(rrdb.Reservation.rfilter == f'wd:{wdeployment_id}')
        .count()
    )
    current_count = (
        dbsession.query(rrdb.Instance)
        .filter(
            sqlalchemy.and_(
                rrdb.Instance.deploymentid == wdeployment_id,
                rrdb.Instance.status != 'INIT_FAIL',
                rrdb.Instance.status != 'TERMINATED',
            )
        )
        .count()
    )
    if len(row.supported_addons) == 0:
        supported_addons = []
    else:
        supported_addons = row.supported_addons.split(',')
    if 'cam' not in supported_addons:
        hscam = (
            dbsession.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.instanceid_with_addon == f'{row.deploymentid}:hscam'
            )
            .one_or_none()
        )
        if hscam is not None:
            supported_addons.append('cam')
    wdinfo = {
        'id': row.deploymentid,
        'type': row.wtype,
        'type_version': row.wversion,
        'supported_addons': supported_addons,
        'desc': row.description,
        'region': row.region,
        'icounter': row.instance_counter,
        'created': str(row.date_created),
        'queuelen': current_count + waiting_ub,
    }
    if row.locked_out:
        wdinfo['lockout'] = row.locked_out
    if row.date_dissolved:
        wdinfo['dissolved'] = str(row.date_dissolved)
    if is_superuser:
        wdinfo['heartbeat'] = str(row.last_heartbeat)
    wdinfo['online'] = row.date_dissolved is None and (
        datetime.utcnow() - row.last_heartbeat < timedelta(seconds=30)
    )
    if username is not None:
        instances = (
            dbsession.query(rrdb.Instance)
            .filter(
                sqlalchemy.and_(
                    rrdb.Instance.deploymentid == wdinfo['id'],
                    rrdb.Instance.rootuser == username,
                    rrdb.Instance.status != 'INIT_FAIL',
                    rrdb.Instance.status != 'TERMINATED',
                )
            )
            .all()
        )
        if instances:
            if len(instances) > 1:
                logger.error(
                    f'more than 1 active instance on same wdeployment {wdinfo["id"]}'
                )
            wdinfo['instance'] = instances[0].instanceid

        if row.wtype == 'user_provided' and len(supported_addons) > 0:
            query = dbsession.query(rrdb.UserProvidedSupp).filter(
                sqlalchemy.and_(
                    rrdb.UserProvidedSupp.deploymentid == row.deploymentid,
                    rrdb.UserProvidedSupp.owner == username,
                )
            )
            if query.count() > 0:
                if row.addons_config:
                    wdinfo['addons_config'] = json.loads(row.addons_config)
                else:
                    wdinfo['addons_config'] = dict()
    return wdinfo


async def get_deployment_info(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    try:
        return web.json_response(
            get_wdinfo(
                request['dbsession'],
                request.match_info['did'],
                username=data['user'],
                is_superuser=data['su'],
            ),
            headers=data['response_headers'],
        )
    except WDeploymentNotFound:
        res_msg = {'error_message': 'no workspace deployment found with given ID'}
        return web.Response(
            body=json.dumps(res_msg),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )


async def get_firewall_rules(request):
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

    instanceid = request.match_info['inid']
    msg_id = str(uuid.uuid4())
    row = (
        request['dbsession']
        .query(rrdb.Instance.deploymentid, rrdb.Instance.status)
        .filter(rrdb.Instance.instanceid == instanceid)
        .one_or_none()
    )
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif row[1] == 'TERMINATED':
        return web.Response(
            body=json.dumps({'error_message': 'instance not active'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    else:
        deploymentid = row[0]
    firewall_list = {'command': 'LIST', 'wdID': deploymentid, 'message_id': msg_id}
    request.app['pam'].send_command(firewall_list)
    max_tries = 50
    count = 0
    while (count < max_tries) and (not request.app['red'].exists(msg_id)):
        count += 1
        await asyncio.sleep(0.1)
    rules = request.app['red'].get(msg_id)
    if rules is not None:
        rules = literal_eval(str(rules, encoding='utf-8'))
    request.app['red'].delete(msg_id)
    return web.json_response({'rules': rules}, headers=data['response_headers'])


async def change_firewall_rules(request):
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
    instanceid = request.match_info['inid']
    if 'src' in given:
        if not isinstance(given['src'], str):
            return web.Response(
                body=json.dumps(
                    {'error_message': 'value for option `src` has wrong type'}
                ),
                status=400,
                content_type='application/json',
                headers=data['response_headers'],
            )
        if not re.match(
            r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$',
            given['src'],
        ):
            return web.Response(
                body=json.dumps(
                    {
                        'error_message': (
                            'value for option `src` must be an '
                            'IPv4 address or subnet in CIDR notation'
                        )
                    }
                ),
                status=400,
                content_type='application/json',
                headers=data['response_headers'],
            )

    else:
        if 'X-Forwarded-For' in request.headers:
            default_addr = request.headers['X-Forwarded-For']
        else:
            default_addr = str(request.transport.get_extra_info('peername')[0])
    if 'action' in given:
        if given['action'] not in ['ACCEPT', 'DROP', 'REJECT']:
            return web.Response(
                body=json.dumps(
                    {'error_message': 'value for option `action` not recognized'}
                ),
                status=400,
                content_type='application/json',
                headers=data['response_headers'],
            )

    row = (
        request['dbsession']
        .query(rrdb.Instance.deploymentid, rrdb.Instance.status)
        .filter(rrdb.Instance.instanceid == instanceid)
        .one_or_none()
    )
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif row[1] == 'TERMINATED':
        return web.Response(
            body=json.dumps({'error_message': 'instance not active'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    else:
        deploymentid = row[0]

    command_msg = {
        'command': given['action'] if 'action' in given else 'ACCEPT',
        'src': given['src'] if 'src' in given else default_addr,
        'wdID': deploymentid,
    }
    request.app['pam'].send_command(command_msg)
    return web.Response(headers=data['response_headers'])


async def clear_firewall_rules(request):
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

    instanceid = request.match_info['inid']
    row = (
        request['dbsession']
        .query(rrdb.Instance.deploymentid, rrdb.Instance.status)
        .filter(rrdb.Instance.instanceid == instanceid)
        .one_or_none()
    )
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif row[1] == 'TERMINATED':
        return web.Response(
            body=json.dumps({'error_message': 'instance not active'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    else:
        deploymentid = row[0]
    firewall_flush = {'command': 'FLUSH', 'wdID': deploymentid}
    request.app['pam'].send_command(firewall_flush)
    return web.Response(headers=data['response_headers'])


async def get_vpn_info(request):
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

    instanceid = request.match_info['inid']
    row = (
        request['dbsession']
        .query(rrdb.Instance.deploymentid, rrdb.Instance.status, rrdb.Instance.has_vpn)
        .filter(rrdb.Instance.instanceid == instanceid)
        .one_or_none()
    )
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif row[1] == 'TERMINATED':
        return web.Response(
            body=json.dumps({'error_message': 'instance not active'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif not row[2]:  # not Instance.has_vpn
        return web.Response(
            body=json.dumps({'error_message': 'instance does not have VPN'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    matches = (
        request['dbsession']
        .query(rrdb.VPNClient)
        .filter(
            rrdb.VPNClient.instanceid == instanceid, rrdb.VPNClient.user == data['user']
        )
        .all()
    )
    client_ids = [match.client_id for match in matches]

    return web.json_response(
        {
            'status': 'ready',
            'clients': client_ids,
        },
        headers=data['response_headers'],
    )


async def make_new_vpnclient(request):
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

    instanceid = request.match_info['inid']
    row = (
        request['dbsession']
        .query(rrdb.Instance.deploymentid, rrdb.Instance.status)
        .filter(rrdb.Instance.instanceid == instanceid)
        .one_or_none()
    )
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif row[1] == 'TERMINATED':
        return web.Response(
            body=json.dumps({'error_message': 'instance not active'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    else:
        deploymentid = row[0]

    msg_id = str(uuid.uuid4())
    request.app['eacommand'].send_to_wd(
        deploymentid,
        {
            'command': 'VPN NEWCLIENT',
            'did': deploymentid,
            'message_id': msg_id,
        },
    )
    max_tries = 10
    count = 0
    blob = None
    while count < max_tries:
        if request.app['red'].exists(msg_id):
            blob = {
                'result': request.app['red'].hget(msg_id, 'result'),
            }
            if blob['result'] == b'ACK':
                blob['client_id'] = str(
                    request.app['red'].hget(msg_id, 'client_id'), encoding='utf-8'
                )
                blob['ovpn_config'] = str(
                    request.app['red'].hget(msg_id, 'ovpn_config'), encoding='utf-8'
                )
            break
        count += 1
        await asyncio.sleep(2)
    if blob is None or blob['result'] == b'NACK':
        res_msg = {'result_message': ('This instance is busy.' ' Try again later.')}
        return web.Response(
            body=json.dumps(res_msg),
            status=503,  # Service Unavailable
            content_type='application/json',
            headers=data['response_headers'],
        )

    request['dbsession'].add(
        rrdb.VPNClient(
            instanceid=instanceid,
            user=data['user'],
            creationtime=datetime.utcnow(),
            client_id=blob['client_id'],
            ovpn=blob['ovpn_config'],
        )
    )

    return web.json_response(
        {'client_id': blob['client_id'], 'ovpn': blob['ovpn_config']},
        headers=data['response_headers'],
    )


def compute_queuelen(dbsession, wdeployment_id, get_current_rem=False):
    waiting_ub = (
        dbsession.query(rrdb.Reservation)
        .filter(rrdb.Reservation.rfilter == 'wd:{}'.format(wdeployment_id))
        .count()
    )
    current_instance = (
        dbsession.query(rrdb.Instance)
        .filter(
            sqlalchemy.and_(
                rrdb.Instance.deploymentid == wdeployment_id,
                rrdb.Instance.status != 'INIT_FAIL',
                rrdb.Instance.status != 'TERMINATED',
            )
        )
        .one_or_none()
    )
    count = waiting_ub + (0 if current_instance is None else 1)
    if get_current_rem:
        if current_instance is None:
            return count, None
        exp = (
            dbsession.query(rrdb.InstanceExpiration)
            .filter(rrdb.InstanceExpiration.instance_id == current_instance.instanceid)
            .one_or_none()
        )
        if exp:
            remaining_duration = (
                exp.target_duration
                - (datetime.utcnow() - current_instance.starttime).seconds
            )
        else:
            remaining_duration = None
        return count, remaining_duration
    return count


async def get_queuelen(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )
    return web.json_response(
        {
            'id': str(request.match_info['did']),
            'len': compute_queuelen(
                request['dbsession'], wdeployment_id=request.match_info['did']
            ),
        },
        headers=data['response_headers'],
    )


async def checkpassword(dbsession, authstr):
    return None  # Placeholder until implementation of linking with accounts from Web UI
    # import hashlib
    # import hmac
    # authstr = str(base64.decodebytes(bytes(authstr, encoding='utf-8')),
    #               encoding='utf-8')
    # name, password = authstr.split(':')
    # row = dbsession.query(rrdb.User).filter(
    #     rrdb.User.name == name
    # ).one_or_none()
    # if row is None:
    #     return None
    # salt, passhash = row.passhash.split(':')
    # salt = base64.decodebytes(bytes(salt, encoding='utf-8'))
    # passhash = base64.decodebytes(bytes(passhash, encoding='utf-8'))
    # password = bytes(password, encoding='utf-8')
    # trial = hashlib.pbkdf2_hmac('sha256', password, salt, 100000)
    # if not hmac.compare_digest(passhash, trial):
    #     return None
    # return row.user


async def signin(request):
    raise Exception('Temporarily not available!')
    import os

    should_handle, headers = rate_limit(request)
    # headers.update(COMMON_HEADERS)
    if not should_handle:
        return web.Response(
            status=403, content_type='application/json', headers=headers
        )

    if 'AUTHORIZATION' not in request.headers:
        return web.Response(
            body=json.dumps({'error_message': 'no signin data provided'}),
            status=400,
            content_type='application/json',
            headers=headers,
        )

    if not request.headers['AUTHORIZATION'].startswith('Basic '):
        return web.Response(
            body=json.dumps({'error_message': 'malformed Authorization header'}),
            status=400,
            content_type='application/json',
            headers=headers,
        )
    authstr = request.headers['AUTHORIZATION'].split()[1]

    user = await checkpassword(request['dbsession'], authstr)
    if user is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong user or password'}),
            status=400,
            content_type='application/json',
            headers=headers,
        )

    if 'X-Forwarded-For' in request.headers:
        peername = request.headers['X-Forwarded-For']
    else:
        peername = str(request.transport.get_extra_info('peername')[0])
    nonce = str(base64.urlsafe_b64encode(os.urandom(16)), encoding='utf-8')
    token = str(
        jwt.encode(
            {'user': user, 'nonce': nonce}, request.app['ephkey'], algorithm='HS256'
        ),
        encoding='utf-8',
    )
    request['dbsession'].add(
        rrdb.APIToken(
            user=user,
            token=token,
            nonce=nonce,
            creationtime=datetime.utcnow(),
            origin=peername,
        )
    )

    return web.json_response({'jwt': token}, headers=headers)


async def revoke_token(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'authentication failed'}),
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

    sha256 = request.match_info['sha256']
    query = (
        request['dbsession'].query(rrdb.APIToken).filter(rrdb.APIToken.sha256 == sha256)
    )
    if not data['su']:
        query = query.filter(rrdb.APIToken.user == data['user'])
    count = query.update({rrdb.APIToken.revoked: True})
    if count == 0:
        # TODO: This prevents first-time use of revoked API
        # tokens. It can be deleted after API token creation is
        # moved to APIWs instead of the Web UI server.
        request['dbsession'].add(
            rrdb.APIToken(user=data['user'], sha256=sha256, revoked=True)
        )
    return web.Response(headers=data['response_headers'])


async def purge_tokens(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'authentication failed'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )

    for usertoken in (
        request['dbsession']
        .query(rrdb.APIToken)
        .filter(rrdb.APIToken.user == data['user'], rrdb.APIToken.revoked == False)
        .all()
    ):
        usertoken.revoked = True
    return web.Response(headers=data['response_headers'])


async def get_reservations_list(request):
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
    query = request['dbsession'].query(rrdb.Reservation)
    if not data['su']:
        query = query.filter(rrdb.Reservation.user == data['user'])
    matches = []
    for reservation in query.all():
        matches.append(
            {
                'id': reservation.reservationid,
                'created': reservation.createdtime.strftime('%Y-%m-%d %H:%M UTC'),
                'desc': reservation.rfilter,
                'user': reservation.user,
            }
        )
    return web.json_response(
        {
            'reservations': matches,
        },
        headers=data['response_headers'],
    )


async def delete_reservation(request):
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
    instanceid = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Reservation)
        .filter(rrdb.Reservation.reservationid == instanceid)
    )
    if not data['su']:
        query = query.filter(rrdb.Reservation.user == data['user'])
    reservation = query.one_or_none()
    if reservation is None:
        return web.Response(
            body=json.dumps({'error_message': 'reservation not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    request['dbsession'].delete(reservation)
    return web.Response(headers=data['response_headers'])


async def get_queue_lengths(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wtype = request.match_info.get('wt', None)
    if wtype is None:
        if 'wds' not in request.query:
            return web.Response(status=404, headers=data['response_headers'])
        try:
            wdeployment_ids = [
                str(uuid.UUID(wd)) for wd in request.query['wds'].split(',')
            ]
        except:
            return web.Response(status=404, headers=data['response_headers'])
    else:
        wdeployment_ids = None

    if wtype is None:
        wds_query = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(
                sqlalchemy.or_(
                    *[rrdb.Deployment.deploymentid == wd for wd in wdeployment_ids]
                )
            )
        )
    else:
        wds_query = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.wtype == wtype)
        )

    if wdeployment_ids is not None and wds_query.count() < len(wdeployment_ids):
        if len(wdeployment_ids) == 1:
            msg = 'no matching wdeployment found'
        else:
            msg = 'some given IDs have no matching wdeployment'
        return web.json_response(
            {'error': msg}, status=404, headers=data['response_headers']
        )
    wds_query = wds_query.filter(rrdb.Deployment.date_dissolved == None)
    if wdeployment_ids is not None and wds_query.count() < len(wdeployment_ids):
        if len(wdeployment_ids) == 1:
            msg = 'workspace deployment is permanently unavailable'
        else:
            msg = 'some of the given workspace deployments are permanently unavailable'
        return web.json_response(
            {'error': msg}, status=400, headers=data['response_headers']
        )

    feasible_wds = dict()
    for row in wds_query:
        if row.locked_out:
            continue

        if (
            row.last_heartbeat is None
            or datetime.utcnow().timestamp() - row.last_heartbeat.timestamp() > 60.0
        ):
            continue

        if not accept_instantiate(
            request['dbsession'], data['user'], row.wtype, row.deploymentid
        ):
            continue

        count, current_rem = compute_queuelen(
            request['dbsession'], wdeployment_id=row.deploymentid, get_current_rem=True
        )
        feasible_wds[row.deploymentid] = {'qlen': count, 'rem': current_rem}

    return web.json_response(feasible_wds, headers=data['response_headers'])


async def unrecognized(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    return web.json_response(
        {'error_message': 'unrecognized command'},
        status=400,
        headers=data['response_headers'],
    )
