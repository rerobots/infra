"""commands for the instance lifecycle


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

from datetime import datetime, timedelta
import json
import logging
import math
import time
import uuid

from aiohttp import web
import sqlalchemy

from .cap import accept_instantiate
from .commands import compute_queuelen, get_wdinfo
from . import db as rrdb
from . import tasks
from .requestproc import process_headers
from .util import now


logger = logging.getLogger(__name__)


async def get_instances_list(request):
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

    # Parse query parameters
    if 'include_terminated' in request.query:
        include_terminated = True
    else:
        include_terminated = False
    include_init_fail = 'include_init_fail' in request.query
    include_details = 'details' in request.query
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

    if 'wt' in request.match_info:
        constrained_wtype = request.match_info['wt']
    else:
        constrained_wtype = False

    if 'user' in request.query:
        if not data['su']:
            return web.Response(status=403, headers=data['response_headers'])
        user = request.query['user']
    else:
        user = None

    # Create response list
    instances = []
    query = request['dbsession'].query(rrdb.Instance)
    if not data['su']:
        query = query.filter(rrdb.Instance.rootuser == data['user'])
    if not include_terminated:
        query = query.filter(
            sqlalchemy.and_(
                rrdb.Instance.status != 'TERMINATED',
                rrdb.Instance.status != 'TERMINATING',
            )
        )
    if not include_init_fail:
        query = query.filter(rrdb.Instance.status != 'INIT_FAIL')
    if sort_by == 'dec_start_date':
        query = query.order_by(sqlalchemy.desc(rrdb.Instance.starttime))
    elif sort_by == 'inc_start_date':
        query = query.order_by(sqlalchemy.asc(rrdb.Instance.starttime))
    else:
        logger.error('unrecognized sort_by parameter to GET /instances')

    if user:
        if user == 'type:anon':
            query = query.filter(
                sqlalchemy.or_(
                    rrdb.Instance.rootuser.like('anon_%'),
                    rrdb.Instance.rootuser.like('sandbox_anon_%'),
                )
            )
        else:
            query = query.filter(rrdb.Instance.rootuser == user)

    if max_per_page == 0:
        page_count = 1
    else:
        page_count = math.ceil(query.count() / max_per_page) if query.count() > 0 else 1
    if page > page_count:
        page = page_count

    wds = []
    for ii, row in enumerate(query):
        if (max_per_page == 0) or (ii // max_per_page == page - 1):
            if constrained_wtype:
                wds_query = (
                    request['dbsession']
                    .query(rrdb.Deployment)
                    .filter(
                        sqlalchemy.and_(
                            rrdb.Deployment.wtype == constrained_wtype,
                            rrdb.Deployment.deploymentid == row.deploymentid,
                        )
                    )
                    .one_or_none()
                )
                if (wds_query is not None) and wds_query.wtype == constrained_wtype:
                    if include_details:
                        drow = (
                            request['dbsession']
                            .query(rrdb.Deployment)
                            .filter(rrdb.Deployment.deploymentid == row.deploymentid)
                            .one()
                        )
                        instances.append(
                            {
                                'id': row.instanceid,
                                'status': row.status,
                                'deployment': row.deploymentid,
                                'type': drow.wtype,
                                'region': drow.region,
                                'starttime': f'{row.starttime.isoformat()}Z',
                                'rootuser': row.rootuser,
                            }
                        )
                        if row.endtime is not None:
                            instances[-1]['endtime'] = str(row.endtime)
                    else:
                        instances.append(row.instanceid)
                    wds.append(row.deploymentid)
            else:
                if include_details:
                    drow = (
                        request['dbsession']
                        .query(rrdb.Deployment)
                        .filter(rrdb.Deployment.deploymentid == row.deploymentid)
                        .one()
                    )
                    instances.append(
                        {
                            'id': row.instanceid,
                            'status': row.status,
                            'deployment': row.deploymentid,
                            'type': drow.wtype,
                            'region': drow.region,
                            'starttime': f'{row.starttime.isoformat()}Z',
                            'rootuser': row.rootuser,
                        }
                    )
                    if row.endtime is not None:
                        instances[-1]['endtime'] = f'{row.endtime.isoformat()}Z'
                else:
                    instances.append(row.instanceid)
                wds.append(row.deploymentid)
        elif ii >= page * max_per_page:
            break

    return web.json_response(
        {
            'workspace_instances': instances,
            'workspace_deployments': wds,
            'page_count': page_count,
        },
        headers=data['response_headers'],
    )


async def get_instance_info(request):
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

    if 'in' in data['payload'] and instanceid != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instanceid)
    )
    if not data['su'] and 'in' not in data['payload']:
        query = query.filter(rrdb.Instance.rootuser == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )
    drow = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == row.deploymentid)
        .one_or_none()
    )
    if drow is None:
        return web.Response(
            body=json.dumps({'error_message': 'internal error'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    payload = {
        'id': instanceid,
        'deployment': row.deploymentid,
        'type': drow.wtype,
        'region': drow.region,
        'starttime': str(row.starttime),
        'rootuser': row.rootuser,
        'wdinfo': get_wdinfo(
            request['dbsession'], row.deploymentid, username=data['user']
        ),
    }
    if row.endtime is not None:
        payload['endtime'] = str(row.endtime)

    if row.status not in ['TERMINATED', 'TERMINATING', 'INIT_FAIL']:
        red = request.app['red']
        if (
            not red.hexists('instance:' + row.instanceid, 'updated')
            or time.time() - int(red.hget('instance:' + row.instanceid, 'updated')) > 2
        ):
            request.app['eacommand'].send_to_wd(
                row.deploymentid, {'command': 'STATUS', 'iid': row.instanceid}
            )

        if red.hexists('instance:' + row.instanceid, 'status'):
            rstatus = red.hget('instance:' + row.instanceid, 'status')
            if rstatus is not None:
                row.status = str(rstatus, encoding='utf-8')
                red.hdel('instance:' + row.instanceid, 'status')
                if row.status == 'READY' and row.ready_at is None:
                    row.ready_at = datetime.utcnow()

        payload['fwd'] = dict()
        if red.hexists('instance:' + row.instanceid, 'ipv4'):
            payload['fwd']['ipv4'] = str(
                red.hget('instance:' + row.instanceid, 'ipv4'), encoding='utf-8'
            )
        if red.hexists('instance:' + row.instanceid, 'port'):
            payload['fwd']['port'] = int(red.hget('instance:' + row.instanceid, 'port'))
        if red.hexists('instance:' + row.instanceid, 'hostkey'):
            payload['hostkeys'] = [
                str(red.hget('instance:' + row.instanceid, 'hostkey'), encoding='utf-8')
            ]
        if red.hexists('instance:' + row.instanceid, 'nbhd'):
            payload['nbhd'] = json.loads(
                str(red.hget('instance:' + row.instanceid, 'nbhd'), encoding='utf-8')
            )

        if row.has_vpn:
            query = (
                request['dbsession']
                .query(rrdb.VPNClient)
                .filter(rrdb.VPNClient.instanceid == row.instanceid)
            )
            if not data['su']:
                query = query.filter(rrdb.VPNClient.user == data['user'])
            matches = query.all()
            client_ids = [match.client_id for match in matches]
            payload['vpn'] = {'status': 'ready', 'clients': client_ids}

    expires = (
        request['dbsession']
        .query(rrdb.InstanceExpiration)
        .filter(rrdb.InstanceExpiration.instance_id == instanceid)
        .one_or_none()
    )
    if expires is not None:
        payload['expires'] = str(
            row.starttime + timedelta(seconds=expires.target_duration)
        )

    payload['status'] = row.status
    return web.json_response(payload, headers=data['response_headers'])


async def request_instance(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    if 'in' in data['payload']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )
    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit instantiation'},
            status=400,
            headers=data['response_headers'],
        )

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()

    wtype = None
    wdeployment_ids = []
    if 'did' in request.match_info:
        wdeployment_ids = [request.match_info['did']]
    elif 'wt' in request.match_info:
        wtype = request.match_info['wt']
    else:
        if 'wds' not in given or not isinstance(given['wds'], list):
            return web.Response(status=404, headers=data['response_headers'])
        try:
            wdeployment_ids = [str(uuid.UUID(wd)) for wd in given['wds']]
        except:
            return web.Response(status=404, headers=data['response_headers'])

    if 'repo' in given:
        if not isinstance(given['repo'], dict) or 'url' not in given['repo']:
            return web.json_response(
                {'error_message': 'value for option `repo` missing url'},
                status=400,
                headers=data['response_headers'],
            )
        repo_url = given['repo']['url']
        repo_path = given['repo'].get('path', None)
    else:
        repo_url = None
        repo_path = None

    if 'reserve' in given:
        if not isinstance(given['reserve'], bool):
            return web.json_response(
                {'error_message': 'value for option `reserve` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )

    if 'expire_d' in given:
        if not isinstance(given['expire_d'], int):
            return web.json_response(
                {'error_message': 'value for option `expire_d` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )
        # 0 indicates that there is no expiration duration
        if given['expire_d'] < 1:
            return web.json_response(
                {'error_message': 'value for option `expire_d` must be positive'},
                status=400,
                headers=data['response_headers'],
            )

    if 'keepalive' in given:
        if not isinstance(given['keepalive'], bool):
            return web.json_response(
                {'error_message': 'value for option `keepalive` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )

    # TODO: filter localhost and rerobots-internal targets when not DEBUG
    if 'eurl' in given:
        if not isinstance(given['eurl'], str):
            return web.json_response(
                {'error_message': 'value for option `eurl` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )
        given['eurl'] = given['eurl'].strip()
        if len(given['eurl']) == 0:
            return web.json_response(
                {'error_message': 'value for option `eurl` must be nonempty'},
                status=400,
                headers=data['response_headers'],
            )

    if 'vpn' in given:
        if not isinstance(given['vpn'], bool):
            return web.json_response(
                {'error_message': 'value for option `vpn` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )

    if 'sshkey' in given:
        if not isinstance(given['sshkey'], str):
            return web.json_response(
                {'error_message': 'value for option `sshkey` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )

    if 'allow_noop' in given:
        if not isinstance(given['allow_noop'], bool):
            return web.json_response(
                {'error_message': 'value for option `allow_noop` has wrong type'},
                status=400,
                headers=data['response_headers'],
            )
        allow_noop = given['allow_noop']
    else:
        allow_noop = False

    opts = {
        'reserve': given['reserve'] if 'reserve' in given else False,
        'eurl': given['eurl'] if 'eurl' in given else '',
        'vpn': given['vpn'] if 'vpn' in given else False,
        'expire_d': given['expire_d'] if 'expire_d' in given else 0,
        'keepalive': given['keepalive'] if 'keepalive' in given else False,
    }

    if len(wdeployment_ids) > 0:
        wds_query = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(
                sqlalchemy.or_(
                    *[rrdb.Deployment.deploymentid == wd for wd in wdeployment_ids]
                )
            )
        )
        if wds_query.count() < len(wdeployment_ids):
            if len(wdeployment_ids) == 1:
                msg = 'no matching wdeployment found'
            else:
                msg = 'some given IDs have no matching wdeployment'
            return web.json_response(
                {'error': msg}, status=404, headers=data['response_headers']
            )
        wds_query = wds_query.filter(rrdb.Deployment.date_dissolved == None)
        if wds_query.count() < len(wdeployment_ids):
            if len(wdeployment_ids) == 1:
                msg = 'workspace deployment is permanently unavailable'
            else:
                msg = 'some of the given workspace deployments are permanently unavailable'
            return web.json_response(
                {'error': msg}, status=400, headers=data['response_headers']
            )

        if allow_noop:
            query = (
                request['dbsession']
                .query(rrdb.Instance)
                .filter(
                    sqlalchemy.or_(
                        *[rrdb.Instance.deploymentid == wd for wd in wdeployment_ids]
                    ),
                    sqlalchemy.or_(
                        rrdb.Instance.status == 'READY', rrdb.Instance.status == 'INIT'
                    ),
                    rrdb.Instance.rootuser == data['user'],
                )
            )
            match = query.first()
            if match is not None:
                ka = (
                    request['dbsession']
                    .query(rrdb.InstanceKeepAlive)
                    .filter(rrdb.InstanceKeepAlive.instanceid == match.instanceid)
                    .one_or_none()
                )
                if ka is not None:
                    ka.last_ping = now()
                return web.json_response(
                    {'success': True, 'id': match.instanceid},
                    headers=data['response_headers'],
                )

        feasible_wds = []
        wtype = None
        for row in wds_query:
            if (
                row.last_heartbeat is None
                or datetime.utcnow().timestamp() - row.last_heartbeat.timestamp() > 60.0
            ):
                error_msg = (
                    'workspace deployment temporarily unavailable, try again later'
                )
                error_code = 503
                continue

            if row.locked_out:
                error_msg = 'workspace deployment is locked out (unavailable), please contact us '
                error_code = 400
                continue

            if not accept_instantiate(
                request['dbsession'], data['user'], row.wtype, row.deploymentid
            ):
                error_msg = None
                error_code = 403
                continue

            if wtype is None:
                wtype = row.wtype
            elif wtype != row.wtype:
                return web.json_response(
                    {
                        'error': 'multiple request for wdeployments of different wtype not supported'
                    },
                    status=400,
                    headers=data['response_headers'],
                )
            feasible_wds.append(
                (
                    row.deploymentid,
                    compute_queuelen(
                        request['dbsession'], wdeployment_id=row.deploymentid
                    ),
                )
            )

        if len(feasible_wds) == 0:
            if error_msg is None:
                return web.Response(status=error_code, headers=data['response_headers'])
            return web.json_response(
                {'error': error_msg},
                status=error_code,
                headers=data['response_headers'],
            )

        feasible_wds.sort(key=lambda x: x[1])
        deploymentid = feasible_wds[0][0]

    else:
        # TODO: create combined search method that can be called from here and from list_deployments()

        if allow_noop:
            query = (
                request['dbsession']
                .query(rrdb.Deployment, rrdb.Instance)
                .filter(
                    rrdb.Instance.rootuser == data['user'],
                    sqlalchemy.or_(
                        rrdb.Instance.status == 'READY', rrdb.Instance.status == 'INIT'
                    ),
                    rrdb.Deployment.deploymentid == rrdb.Instance.deploymentid,
                    rrdb.Deployment.wtype == wtype,
                )
            )
            match = query.first()
            if match is not None:
                ka = (
                    request['dbsession']
                    .query(rrdb.InstanceKeepAlive)
                    .filter(rrdb.InstanceKeepAlive.instanceid == match[1].instanceid)
                    .one_or_none()
                )
                if ka is not None:
                    ka.last_ping = now()
                return web.json_response(
                    {'success': True, 'id': match[1].instanceid},
                    headers=data['response_headers'],
                )

        oldest_accepted_timestamp = datetime.fromtimestamp(
            datetime.utcnow().timestamp() - 60.0
        )
        matches = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.wtype == wtype)
            .filter(rrdb.Deployment.date_dissolved == None)
            .filter(rrdb.Deployment.locked_out == False)
            .filter(rrdb.Deployment.last_heartbeat != None)
            .filter(rrdb.Deployment.last_heartbeat >= oldest_accepted_timestamp)
        )
        if matches.count() == 0:
            return web.json_response(
                {'error': 'no matching wdeployment found'},
                status=404,
                headers=data['response_headers'],
            )
        selection = None
        # TODO: sort by icounter to use wdeployment that has fewest prior instances (load balancing)
        for match in matches.order_by(sqlalchemy.desc(rrdb.Deployment.wversion)):
            if not accept_instantiate(
                request['dbsession'], data['user'], wtype, match.deploymentid
            ):
                continue

            queuelen = compute_queuelen(
                request['dbsession'], wdeployment_id=match.deploymentid
            )
            if queuelen == 0:
                selection = match.deploymentid
                wtype = match.wtype
                break

        if selection is None:
            return web.json_response(
                {'error': 'All feasible workspace deployments are busy'},
                status=404,
                headers=data['response_headers'],
            )
        deploymentid = selection

    logger.info(
        'user {} requests to instantiate from wd {} (wtype {})'.format(
            data['user'], deploymentid, wtype
        )
    )

    if 'wt' in data['payload'] and wtype not in data['payload']['wt']:
        return web.json_response(
            {'error_message': 'token not valid for wtype {}'.format(wtype)},
            status=400,
            headers=data['response_headers'],
        )
    if (
        'opt' in data['payload']
        and 'expire_d' in data['payload']['opt']
        and data['payload']['opt']['expire_d'] != opts['expire_d']
    ):
        return web.json_response(
            {
                'error_message': 'token only valid for expire_d={}'.format(
                    data['payload']['opt']['expire_d']
                )
            },
            status=400,
            headers=data['response_headers'],
        )

    # Check for active instance or waiting users on this workspace deployment
    # and enqueue, etc., depending on options in the request.
    waiting_ub = (
        request['dbsession']
        .query(rrdb.Reservation)
        .filter(rrdb.Reservation.rfilter == 'wd:{}'.format(deploymentid))
        .count()
    )
    current_count = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            sqlalchemy.and_(
                rrdb.Instance.deploymentid == deploymentid,
                rrdb.Instance.status != 'INIT_FAIL',
                rrdb.Instance.status != 'TERMINATED',
            )
        )
        .count()
    )
    if current_count == 0 and waiting_ub == 0:
        instanceid = str(uuid.uuid4())
        if 'sshkey' in given:
            ssh_publickey = given['sshkey']
        else:
            ssh_publickey = None
        tasks.launch_instance.delay(
            wdeployment_id=deploymentid,
            instance_id=instanceid,
            user=data['user'],
            ssh_publickey=ssh_publickey,
            has_vpn=opts['vpn'],
            expire_d=opts['expire_d'],
            eurl=opts['eurl'],
            repo_args={
                'url': repo_url,
                'path': repo_path,
            },
            start_keepalive=opts['keepalive'],
        )
        return web.json_response(
            {'success': True, 'id': instanceid}, headers=data['response_headers']
        )

    elif opts['reserve']:
        if 'sshkey' in given:
            ssh_publickey = given['sshkey']
        else:
            ssh_publickey = ''
        reservation = rrdb.Reservation(
            reservationid=str(uuid.uuid4()),
            user=data['user'],
            createdtime=datetime.utcnow(),
            rfilter='wd:{}'.format(deploymentid),
            ssh_publickey=ssh_publickey,
            has_vpn=opts['vpn'],
            expire_d=opts['expire_d'],
            event_url=opts['eurl'],
        )
        request['dbsession'].add(reservation)
        res_msg = {
            'success': False,
            'id': reservation.reservationid,
        }
        return web.json_response(res_msg, headers=data['response_headers'])
    else:
        res_msg = {
            'result_message': (
                'All matching workspace deployments are busy.' ' Try again later.'
            )
        }
        return web.json_response(
            res_msg,
            status=503,  # Service Unavailable
            headers=data['response_headers'],
        )


async def terminate_instance(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    instanceid = request.match_info['inid']

    if 'in' in data['payload'] and instanceid != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )
    if 'sc' in data['payload'] and 'rw' not in data['payload']['sc']:
        return web.json_response(
            {'error_message': 'token scope does not permit termination'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instanceid)
    )
    if not data['su']:
        query.filter(rrdb.Instance.rootuser == data['user'])
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


async def get_instance_sshkey(request):
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

    if 'in' in data['payload'] and instanceid != data['payload']['in']:
        return web.json_response(
            {'error_message': 'token already constrained to another instance'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.instanceid == instanceid)
    )
    if not data['su'] and 'in' not in data['payload']:
        query = query.filter(rrdb.Instance.rootuser == data['user'])
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if row.status in ['TERMINATED', 'TERMINATING']:
        return web.json_response(
            {'error_message': 'instance already ' + row.status.lower()},
            status=400,
            headers=data['response_headers'],
        )

    if row.ssh_privatekey:
        return web.json_response(
            {'key': row.ssh_privatekey}, headers=data['response_headers']
        )
    else:
        return web.json_response(
            {'error_message': 'no secret SSH key available'},
            status=404,
            headers=data['response_headers'],
        )


async def mark_event(request):
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
    if not request.can_read_body:
        return web.json_response(
            {'error_message': 'request body required'},
            status=400,
            headers=data['response_headers'],
        )
    given = await request.json()
    if 'k' not in given or 'd' not in given or not isinstance(given['d'], dict):
        return web.json_response(
            {'error_message': 'missing event data'},
            status=400,
            headers=data['response_headers'],
        )
    if given['k'] == 'headMove':
        if (
            len(set(['ts', 'roll', 'pitch', 'yaw']).intersection(given['d'].keys()))
            != 4
        ):
            return web.json_response(
                {'error_message': 'unexpected keys in event data'},
                status=400,
                headers=data['response_headers'],
            )
    elif given['k'] == 'goalReached':
        if len(set(['ts', 'rule']).intersection(given['d'].keys())) != 2:
            return web.json_response(
                {'error_message': 'unexpected keys in event data'},
                status=400,
                headers=data['response_headers'],
            )
        if not isinstance(given['d']['ts'], int) or not isinstance(
            given['d']['rule'], str
        ):
            return web.json_response(
                {'error_message': 'unexpected event data types'},
                status=400,
                headers=data['response_headers'],
            )
    elif given['k'] == 'imageCap':
        if len(set(['ts', 'img']).intersection(given['d'].keys())) != 2:
            return web.json_response(
                {'error_message': 'unexpected keys in event data'},
                status=400,
                headers=data['response_headers'],
            )
        if not isinstance(given['d']['ts'], int) or not isinstance(
            given['d']['img'], str
        ):
            return web.json_response(
                {'error_message': 'unexpected event data types'},
                status=400,
                headers=data['response_headers'],
            )
    elif given['k'] == 'baseMove':
        if (
            len(
                set(['ts', 'linear', 'angular', 'duration']).intersection(
                    given['d'].keys()
                )
            )
            != 4
        ):
            return web.json_response(
                {'error_message': 'unexpected keys in event data'},
                status=400,
                headers=data['response_headers'],
            )
    else:
        return web.json_response(
            {'error_message': 'unknown event kind'},
            status=400,
            headers=data['response_headers'],
        )

    q = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )
    if not request['dbsession'].query(q.exists()).one()[0]:
        return web.Response(status=404, headers=data['response_headers'])

    tasks.record_instance_event.delay(
        instance_id=instance_id, event_kind=given['k'], event_data=given['d']
    )

    return web.Response(headers=data['response_headers'])


async def keep_alive(request):
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
    q = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )
    if not request['dbsession'].query(q.exists()).one()[0]:
        return web.Response(status=404, headers=data['response_headers'])
    ka = (
        request['dbsession']
        .query(rrdb.InstanceKeepAlive)
        .filter(rrdb.InstanceKeepAlive.instanceid == instance_id)
        .one_or_none()
    )
    if ka is None:
        return web.json_response(
            {'error_message': 'no keep-alive monitor for this instance'},
            status=404,
            headers=data['response_headers'],
        )
    ka.last_ping = now()
    return web.Response(headers=data['response_headers'])
