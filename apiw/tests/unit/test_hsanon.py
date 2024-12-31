"""Unit tests involving anonymous access


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import pytest

from fixtures import cleardb, clearks, client


async def test_access_denied(client):
    resp = await client.post('/hardshare/register')
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'wrong authorization token'
    resp = await client.get('/hardshare/list')
    assert resp.status == 400
