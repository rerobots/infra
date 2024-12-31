"""Anonymous related requests


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import logging
import time

from aiohttp import web
import jwt

from . import db as rrdb
from .requestproc import get_peername, geo_lookup, process_headers
from .settings import WEBUI_SECRET_KEY


logger = logging.getLogger(__name__)


async def get_api_token(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is not None:
        return web.json_response(
            {
                'error_message': 'cannot request anonymous token on requests made with valid API token'
            },
            status=400,
            headers=data['response_headers'],
        )
    peername = get_peername(request)
    georegion = geo_lookup(peername)
    if georegion:
        origin = '{} ({})'.format(peername, georegion)
    else:
        origin = peername
    try:
        au = rrdb.AnonUser(origin=origin, wtype=request.match_info['wtype'])
        request['dbsession'].add(au)
        request['dbsession'].commit()
        name = 'anonymous_{}'.format(au.id)
        creationtime = int(time.time())
        payload = {
            'sub': name,
            'iss': 'rerobots.net',
            'aud': 'rerobots.net',
            'wt': [request.match_info['wtype']],
            'opt': {
                'expire_d': 420,  # 7 minutes
                'icount': 1,  # maximum instance count
            },
            'exp': creationtime + 1800,
            'nbf': creationtime - 1,
        }
        tok = str(
            jwt.encode(payload, key=WEBUI_SECRET_KEY, algorithm='RS256'),
            encoding='utf-8',
        )
    except Exception as err:
        request['dbsession'].rollback()
        logger.warning('caught {}: {}'.format(type(err), err))
        return web.Response(status=400, headers=data['response_headers'])
    return web.json_response(
        {'name': name, 'tok': tok}, headers=data['response_headers']
    )
