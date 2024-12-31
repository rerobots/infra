"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import jwt

from fixtures import client, api_token


async def test_anonymous_get_token(client, api_token):
    # Cannot request if authorized already as user
    headers = {'Authorization': 'Bearer ' + api_token}
    resp = await client.post(
        '/token/anon/multi_kobuki/qHtlNyVxgYrneQxXIdaasL8K5d0Zhj5UMk4StYrohLM',
        headers=headers,
    )
    assert resp.status == 400

    # Anonymous request: OK
    resp = await client.post(
        '/token/anon/multi_kobuki/qHtlNyVxgYrneQxXIdaasL8K5d0Zhj5UMk4StYrohLM'
    )
    assert resp.status == 200
    payload = await resp.json()
    assert 'name' in payload
    assert payload['name'].startswith('anonymous_')
    a_id = int(payload['name'][len('anonymous_') :])
    assert 'tok' in payload
