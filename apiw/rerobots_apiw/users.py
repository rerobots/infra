"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import logging

from aiohttp import web
import sqlalchemy

from . import db as rrdb
from .requestproc import process_headers
from . import notify


logger = logging.getLogger(__name__)


async def join_mailinglist(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if request.can_read_body:
        given = await request.json()
    else:
        return web.Response(status=400, headers=data['response_headers'])
    if 'spamhole' in given:
        return web.Response(status=200, headers=data['response_headers'])
    if 'name' not in given or 'emailaddr' not in given:
        return web.Response(status=400, headers=data['response_headers'])
    try:
        if 'why_interested' not in given:
            request['dbsession'].add(
                rrdb.MailingListMember(
                    topic=request.match_info['topic'],
                    name=given['name'],
                    emailaddr=given['emailaddr'],
                )
            )
        else:
            request['dbsession'].add(
                rrdb.MailingListMember(
                    topic=request.match_info['topic'],
                    name=given['name'],
                    emailaddr=given['emailaddr'],
                    why_interested=given['why_interested'],
                )
            )
        request['dbsession'].commit()
    except sqlalchemy.exc.IntegrityError as err:
        request['dbsession'].rollback()
        logger.warning('caught {}: {}'.format(type(err), err))
        return web.Response(status=400, headers=data['response_headers'])

    notify.new_mailinglist_subscriber.delay(
        request.match_info['topic'],
        given['name'],
        given['emailaddr'],
        why_interested=given.get('why_interested', None),
    )
    return web.Response(status=200)


async def get_orgs(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not data['su']:
        return web.Response(status=404, headers=data['response_headers'])

    username = request.match_info['username']

    orgs = {}  # TODO: port org code
    return web.json_response(orgs, headers=data['response_headers'])
