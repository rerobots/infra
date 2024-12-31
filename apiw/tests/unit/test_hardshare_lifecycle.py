"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import asyncio
import uuid

from fixtures import (
    client,
    client_no_new_hs_billingplans,
    api_token,
    api_token_su,
    hardshare_registered_wd,
)


# @patch('rerobots_apiw.tasks.register_new_user_provided.delay')
async def test_register(client, api_token, api_token_su):
    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    su_headers = {'Authorization': 'Bearer {}'.format(api_token_su)}

    resp = await client.get('/hardshare/list')
    assert resp.status == 400
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['wdeployments']) == 0

    resp = await client.post('/hardshare/register', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert 'id' in payload
    uuid.UUID(payload['id'])  # raises exception if id is not well-formed
    assert 'owner' in payload
    assert payload['owner'] == 'bilbo'

    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['wdeployments']) == 1


async def test_dissolve(hardshare_registered_wd):
    client = hardshare_registered_wd['client']
    headers = {'Authorization': 'Bearer {}'.format(hardshare_registered_wd['token'])}

    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['wdeployments']) == 1

    resp = await client.post('/hardshare/dis/{}'.format(hardshare_registered_wd['id']))
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'wrong authorization token'

    resp = await client.post(
        '/hardshare/dis/{}'.format(hardshare_registered_wd['id']), headers=headers
    )
    assert resp.status == 200
    payload = await resp.json()
    assert 'date_dissolved' in payload

    resp = await client.post(
        '/hardshare/dis/{}'.format(hardshare_registered_wd['id']), headers=headers
    )
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'already dissolved'

    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['wdeployments']) == 0

    resp = await client.get('/hardshare/list?with_dissolved', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['wdeployments']) == 1


async def test_update(client, api_token, api_token_su):
    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    su_headers = {'Authorization': 'Bearer {}'.format(api_token_su)}

    resp = await client.post('/hardshare/register', headers=headers)
    assert resp.status == 200
    wdeployment_id = (await resp.json())['id']

    # other wdeployment IDs (not owned by this user or not existing) return 404
    resp = await client.post(
        '/hardshare/wd/{}'.format('d767d9aa-0a60-4892-97bb-6db9648b498a'),
        headers=headers,
    )
    assert resp.status == 404

    # declare mistyproxy add-on
    payload = {
        'supported_addons': ['mistyproxy'],
        'addons_config': {
            'mistyproxy': {
                'ip': '192.168.1.101',
            },
        },
    }
    resp = await client.post(
        '/hardshare/wd/{}'.format(wdeployment_id), json=payload, headers=headers
    )
    assert resp.status == 200

    # try again with errors in payload
    payload['supported_addons'] = ['fake']
    resp = await client.post(
        '/hardshare/wd/{}'.format(wdeployment_id), json=payload, headers=headers
    )
    assert resp.status == 400

    payload['supported_addons'] = ['cam', 'mistyproxy']
    resp = await client.post(
        '/hardshare/wd/{}'.format(wdeployment_id), json=payload, headers=headers
    )
    assert resp.status == 200

    payload['supported_addons'] = ['mistyproxy']
    payload['addons_config'] = {'mistyproxy': 1}
    resp = await client.post(
        '/hardshare/wd/{}'.format(wdeployment_id), json=payload, headers=headers
    )
    assert resp.status == 400

    payload['supported_addons'] = ['mistyproxy']
    payload['addons_config'] = dict()
    resp = await client.post(
        '/hardshare/wd/{}'.format(wdeployment_id), json=payload, headers=headers
    )
    assert resp.status == 400
