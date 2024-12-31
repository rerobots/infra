"""Upkeep and management of hardware


SCL <scott@rerobots>
Copyright (C) 2022 rerobots, Inc.
"""

import logging

from aiohttp import web

from . import db as rrdb
from .requestproc import process_headers


logger = logging.getLogger(__name__)


async def lockout_wd(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    wdeployment_id = request.match_info['wdid']

    owner = data['user'] if data['org'] is None else data['org']

    if not data['su']:
        usupp = (
            request['dbsession']
            .query(rrdb.UserProvidedSupp)
            .filter(
                rrdb.UserProvidedSupp.owner == owner,
                rrdb.UserProvidedSupp.deploymentid == wdeployment_id,
            )
            .one_or_none()
        )
        if usupp is None:
            return web.Response(status=404, headers=data['response_headers'])

    wd = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == wdeployment_id)
        .one_or_none()
    )
    if wd is None:
        return web.Response(status=404, headers=data['response_headers'])

    if request.method == 'POST':
        if not wd.locked_out:
            wd.locked_out = True
    elif request.method == 'DELETE':
        if wd.locked_out:
            wd.locked_out = False

    return web.Response(status=200, headers=data['response_headers'])
