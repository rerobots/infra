"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

from aiohttp import web

from . import db as rrdb
from .tasks import notify_hardshare_owners
from .requestproc import process_headers


async def get_billing_plan(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if ('username' in request.match_info) and not data['su']:
        return web.Response(status=404, headers=data['response_headers'])
    elif 'username' in request.match_info:
        username = request.match_info['username']
    else:
        username = data['user']

    ubp = None  # TODO: port billing plan
    if ubp is None:
        return web.Response(status=404, headers=data['response_headers'])

    return web.json_response(
        {
            'max_active': ubp['max_active_count'],
        },
        headers=data['response_headers'],
    )


async def send_alert(request):
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

    payload = await request.json() if request.can_read_body else None
    if payload is None or 'msg' not in payload or not isinstance(payload['msg'], str):
        return web.json_response(
            {'error_message': 'emails not correct format'},
            status=400,
            headers=data['response_headers'],
        )

    notify_hardshare_owners.delay(wdeployment_id, payload['msg'])
    return web.Response(headers=data['response_headers'])


async def manage_hook_emails(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )

    payload = await request.json() if request.can_read_body else None
    if (
        payload is None
        or 'emails' not in payload
        or not isinstance(payload['emails'], list)
    ):
        return web.json_response(
            {'error_message': 'emails not correct format'},
            status=400,
            headers=data['response_headers'],
        )
    if len(payload['emails']) == 0:
        alert_emails = None
    else:
        for addr in payload['emails']:
            if ',' in addr or '@' not in addr:
                return web.json_response(
                    {'error_message': 'email not correct format'},
                    status=400,
                    headers=data['response_headers'],
                )
        alert_emails = ','.join(payload['emails'])

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

    upattr = (
        request['dbsession']
        .query(rrdb.UserProvidedAttr)
        .filter(rrdb.UserProvidedAttr.deploymentid == wdeployment_id)
        .one_or_none()
    )
    if upattr is None:
        upattr = rrdb.UserProvidedAttr(
            deploymentid=wdeployment_id, alert_emails=alert_emails
        )
        request['dbsession'].add(upattr)
    else:
        upattr.alert_emails = alert_emails
    return web.Response(headers=data['response_headers'])
