"""
SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import logging

from aiohttp import web

from . import db as rrdb
from .requestproc import process_headers


logger = logging.getLogger(__name__)


async def get_billing(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    acc = dict()
    wdcache = dict()
    for instance in (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(rrdb.Instance.rootuser == data['user'])
    ):
        if instance.deploymentid not in wdcache:
            wd = (
                request['dbsession']
                .query(rrdb.Deployment)
                .filter(rrdb.Deployment.deploymentid == instance.deploymentid)
                .one()
            )
            wdcache[instance.deploymentid] = (wd.wtype, str(wd.wversion))
        if wdcache[instance.deploymentid][0] not in acc:
            acc[wdcache[instance.deploymentid][0]] = dict(
                [(wdcache[instance.deploymentid][1], 0.0)]
            )
        elif (
            wdcache[instance.deploymentid][1]
            not in acc[wdcache[instance.deploymentid][0]]
        ):
            acc[wdcache[instance.deploymentid][0]][
                wdcache[instance.deploymentid][1]
            ] = 0.0
        acc[wdcache[instance.deploymentid][0]][wdcache[instance.deploymentid][1]] += (
            instance.endtime - instance.starttime
        ).total_seconds()

    # e.g., {"acc": {"null": {"1": 75.059662}}}
    # TODO
    return web.json_response(dict(), headers=data['response_headers'])


async def get_history(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
