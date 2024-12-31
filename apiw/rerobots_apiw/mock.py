import logging

import aiohttp
from aiohttp import web

from . import db as rrdb
from .requestproc import process_headers
from . import settings


logger = logging.getLogger(__name__)


async def minidevel_websocket(request):
    should_handle, data = process_headers(request)
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if settings.RUNTIME_ENVIRON not in ['mock', 'staging-mock']:
        logger.error('called mock handler when not in mock mode!')
        return web.Response(status=404, headers=data['response_headers'])

    ihash = request.match_info['ihash']
    kind = request.match_info['kind']
    ptoken = request.match_info['ptoken']
    logger.info(f'called with ihash {ihash}, kind {kind}, ptoken {ptoken}')

    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(rrdb.ActiveAddon.config.ilike(f'%{ptoken}%'))
            .one_or_none()
        )
        if activeaddon is None:
            return web.Response(status=404, headers=data['response_headers'])

    ws = web.WebSocketResponse(autoping=True, heartbeat=5.0)
    await ws.prepare(request)
    logger.info('opened WebSocket connection')

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            logger.info(f'received: {msg.data}')
        elif msg.type == aiohttp.WSMsgType.CLOSED:
            break
        elif msg.type == aiohttp.WSMsgType.ERROR:
            logger.error(f'error: {msg}')
            break
        else:
            logger.warning(f'unrecognized message type: {msg}')

    return ws
