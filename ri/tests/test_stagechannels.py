"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import asyncio
import functools
import json

from rerobots_infra import RerobotsQChannel


async def test_start_cancel():
    rqc = RerobotsQChannel(basename='test_channel', agenttype='aw')
    assert not rqc.is_open()
    rqc.start()
    assert rqc.is_open()
    rqc.stop()
    assert not rqc.is_open()


async def test_send():
    rqc = RerobotsQChannel(basename='test_channel', agenttype='aw')
    assert not rqc.is_open()
    rqc.start()
    rqc.send_message('ea', {'command': 'fake'})
    # TODO: add timeout
    captured_message = await rqc.outq.get()
    assert len(captured_message) == 1
    assert 'command' in captured_message
    assert captured_message['command'] == 'fake'
    rqc.stop()


async def test_receive():
    resultq = asyncio.Queue()

    def handle_incoming_message(q, channel, method, prop, body):
        q.put_nowait(body)

    cb = functools.partial(handle_incoming_message, resultq)

    rqc = RerobotsQChannel(
        basename='test_channel', agenttype='aw', callback_handle_incoming=cb
    )
    rqc.start()
    await rqc.inject_recv({'command': 'fake'})
    # TODO: add timeout
    while not rqc.inq.empty():
        await asyncio.sleep(0.1)
    rqc.stop()

    assert not resultq.empty()
    received_message = json.loads(await resultq.get())
    assert len(received_message) == 1
    assert 'command' in received_message
    assert received_message['command'] == 'fake'
