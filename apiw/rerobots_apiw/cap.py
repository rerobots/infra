"""Capabilities, access rules


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

from datetime import datetime
import logging

from aiohttp import web
import sqlalchemy

from . import db as rrdb
from .requestproc import process_headers


logger = logging.getLogger(__name__)


async def get_access_rules(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    # Parse query parameters
    if 'to_user' in request.query:
        if not data['su'] and data['user'] != request.query['to_user']:
            return web.json_response(status=403, headers=data['response_headers'])
        else:
            to_user = request.query['to_user']
    else:
        if data['su']:
            to_user = None
        else:
            to_user = data['user']

    if 'wdid' in request.match_info:
        wd = (
            request['dbsession']
            .query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == request.match_info['wdid'])
            .one_or_none()
        )
        if wd is None:
            res_msg = {'error_message': 'no workspace deployment found with given ID'}
            return web.json_response(
                res_msg, status=404, headers=data['response_headers']
            )
        wdeployment_id = wd.deploymentid

    else:
        wdeployment_id = None

    query = request['dbsession'].query(rrdb.DeploymentACL)
    if wdeployment_id is not None:
        query = query.filter(rrdb.DeploymentACL.wdeployment_id == wdeployment_id)
    if to_user is not None:
        query = query.filter(
            sqlalchemy.or_(
                rrdb.DeploymentACL.user == '*',
                rrdb.DeploymentACL.user == 'type:anon',
                rrdb.DeploymentACL.user == to_user,
            )
        )

    matches = [
        {
            'id': row.id,
            'date_created': str(row.date_created),
            'user': row.user,
            'wdeployment_id': row.wdeployment_id,
            'capability': row.capability,
            'param': row.param,
        }
        for row in query
    ]

    return web.json_response({'rules': matches}, headers=data['response_headers'])


async def create_access_rule(request):
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
        given = None
    if given is None or not ('cap' in given):
        return web.json_response(
            {'error_message': 'post body required, but not given'},
            status=400,
            headers=data['response_headers'],
        )

    wd = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == request.match_info['wdid'])
        .one_or_none()
    )
    if wd is None:
        res_msg = {'error_message': 'no workspace deployment found with given ID'}
        return web.json_response(res_msg, status=404, headers=data['response_headers'])

    if not data['su'] and wd.wtype != 'user_provided':
        return web.Response(status=403, headers=data['response_headers'])

    if wd.wtype == 'user_provided':
        upsupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(rrdb.UserProvidedSupp.deploymentid == wd.deploymentid)
            .one_or_none()
        )
        if upsupp is None:
            # TODO: log error
            return web.Response(status=500, headers=data['response_headers'])
        if (
            upsupp.owner != data['user']
            and (data['org'] is None or upsupp.owner != data['org'])
            and not data['su']
        ):
            return web.Response(status=403, headers=data['response_headers'])

    if 'user' not in given:
        to_user = '*'
    else:
        to_user = given['user']

    capability = given['cap']

    if capability not in ['CAP_NO_INSTANTIATE', 'CAP_INSTANTIATE']:
        return web.json_response(
            {'error_message': 'unrecognized capability (`cap` field)'},
            status=400,
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.DeploymentACL)
        .filter(
            rrdb.DeploymentACL.wdeployment_id == wd.deploymentid,
            rrdb.DeploymentACL.capability == capability,
            rrdb.DeploymentACL.user == to_user,
        )
    )

    if query.count() == 0:
        dacl = rrdb.DeploymentACL(
            date_created=datetime.utcnow(),
            user=to_user,
            capability=capability,
            wdeployment_id=wd.deploymentid,
        )
        request['dbsession'].add(dacl)
        request['dbsession'].commit()
        rule_id = dacl.id
    else:
        # No-op if already exists
        rule_id = query.first().id

    return web.json_response({'rule_id': rule_id}, headers=data['response_headers'])


async def delete_access_rule(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wd = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == request.match_info['wdid'])
        .one_or_none()
    )
    if wd is None:
        res_msg = {'error_message': 'no workspace deployment found with given ID'}
        return web.json_response(res_msg, status=404, headers=data['response_headers'])

    if not data['su'] and wd.wtype != 'user_provided':
        return web.Response(status=403, headers=data['response_headers'])

    if wd.wtype == 'user_provided':
        upsupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(rrdb.UserProvidedSupp.deploymentid == wd.deploymentid)
            .one_or_none()
        )
        if upsupp is None:
            # TODO: log error
            return web.Response(status=500, headers=data['response_headers'])
        if (
            upsupp.owner != data['user']
            and (data['org'] is None or upsupp.owner != data['org'])
            and not data['su']
        ):
            return web.Response(status=403, headers=data['response_headers'])

    dacl = (
        request['dbsession']
        .query(rrdb.DeploymentACL)
        .filter(
            sqlalchemy.and_(
                rrdb.DeploymentACL.wdeployment_id == wd.deploymentid,
                rrdb.DeploymentACL.id == request.match_info['ruleid'],
            )
        )
        .one_or_none()
    )
    if dacl is None:
        return web.Response(status=404, headers=data['response_headers'])
    request['dbsession'].delete(dacl)

    return web.Response(headers=data['response_headers'])


def accept_instantiate(session, username, wtype, wdeployment_id):
    dacl_q = session.query(rrdb.DeploymentACL).filter(
        rrdb.DeploymentACL.wdeployment_id == wdeployment_id
    )
    if (
        dacl_q.filter(rrdb.DeploymentACL.user == username)
        .filter(rrdb.DeploymentACL.capability == 'CAP_INSTANTIATE')
        .count()
        == 0
    ):
        if (
            dacl_q.filter(rrdb.DeploymentACL.user == username)
            .filter(rrdb.DeploymentACL.capability == 'CAP_NO_INSTANTIATE')
            .count()
            > 0
        ):
            return False
        if (
            not username.startswith('sandbox_anon')
            or dacl_q.filter(rrdb.DeploymentACL.user == 'type:anon')
            .filter(rrdb.DeploymentACL.capability == 'CAP_INSTANTIATE')
            .count()
            == 0
        ):
            if (
                dacl_q.filter(rrdb.DeploymentACL.user == 'type:anon')
                .filter(rrdb.DeploymentACL.capability == 'CAP_NO_INSTANTIATE')
                .count()
                > 0
            ):
                return False
            if (
                dacl_q.filter(rrdb.DeploymentACL.user == '*')
                .filter(rrdb.DeploymentACL.capability == 'CAP_INSTANTIATE')
                .count()
                == 0
            ):
                if (
                    dacl_q.filter(rrdb.DeploymentACL.user == '*')
                    .filter(rrdb.DeploymentACL.capability == 'CAP_NO_INSTANTIATE')
                    .count()
                    > 0
                ):
                    return False
                if wtype == 'user_provided':
                    return False
    return True
