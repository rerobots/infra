"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

from datetime import datetime

import pytest

from fixtures import client, wdconfig, hs_wdconfig, api_token
from fixtures import api_token_su

import rerobots_apiw.db as rrdb
from rerobots_apiw import tasks


pytestmark = pytest.mark.filterwarnings(
    "ignore: Using or importing the ABCs from 'collections'"
)


async def test_admin_list_wd(client, hs_wdconfig, api_token_su):
    headers = {'Authorization': 'Bearer ' + api_token_su}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)

    resp = await client.get('/hardshare/list/bilbo', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert body['wdeployments'] == [hs_wdconfig['id']], body

    resp = await client.get('/hardshare/list/noone', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 0


async def test_admin_deny(client, api_token, api_token_su):
    resp = await client.get(
        '/hardshare/owners', headers={'Authorization': 'Bearer ' + api_token}
    )
    assert resp.status == 404
    resp = await client.get(
        '/hardshare/owners', headers={'Authorization': 'Bearer ' + api_token_su}
    )
    assert resp.status == 200
    assert len((await resp.json())['owners']) == 0

    resp = await client.get(
        '/hardshare/list/bilbo', headers={'Authorization': 'Bearer ' + api_token}
    )
    assert resp.status == 404
    resp = await client.get(
        '/hardshare/list/bilbo', headers={'Authorization': 'Bearer ' + api_token_su}
    )
    assert resp.status == 200


async def test_owners_list(client, hs_wdconfig, api_token_su):
    headers = {'Authorization': 'Bearer ' + api_token_su}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)

    resp = await client.get('/hardshare/owners', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert 'owners' in body
    assert body['owners'] == ['bilbo']

    hs_wdconfig['id'] = '3b62e84d-ea10-4f70-880c-d90f090a0a90'
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 2
    resp = await client.get('/hardshare/owners', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['owners']) == 1, body

    hs_wdconfig['id'] = '26ce5a3d-fcad-44ee-8efa-bfec39efc041'
    hs_wdconfig['hs'] = {'owner': 'frodo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 3
    resp = await client.get('/hardshare/owners', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert set(body['owners']) == set(['bilbo', 'frodo'])


async def test_register_wd(client, hs_wdconfig, api_token):
    headers = {'Authorization': 'Bearer ' + api_token}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1

    resp = await client.get('/deployment/' + hs_wdconfig['id'])
    assert resp.status == 200
    body = await resp.json()
    assert set(body['supported_addons']) == set(['cmd', 'cmdsh'])
    assert 'addons_config' not in body

    resp = await client.get('/deployment/' + hs_wdconfig['id'], headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert set(body['supported_addons']) == set(['cmd', 'cmdsh'])
    assert len(body['addons_config']) == 0

    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1
    wd = body['wdeployments'][0]
    assert wd['id'] == '07ae6006-36a2-4399-8ad3-bf9e58633a05'
    assert wd['desc'] is None


async def test_no_instances(client, hs_wdconfig, api_token):
    headers = {'Authorization': 'Bearer ' + api_token}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/hardshare/instances', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body) == 0


async def test_add_description(client, hs_wdconfig, api_token):
    headers = {'Authorization': 'Bearer ' + api_token}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.post('/hardshare/{}'.format(hs_wdconfig['id']), headers=headers)
    assert resp.status == 400
    resp = await client.post(
        '/hardshare/{}'.format(hs_wdconfig['id']),
        json={'desc': 'my device'},
        headers=headers,
    )
    assert resp.status == 200
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1
    wd = body['wdeployments'][0]
    assert wd['id'] == hs_wdconfig['id']
    assert wd['desc'] == 'my device'


async def test_wd_dissolved_list(client, hs_wdconfig, api_token):
    # declare new wdeployment
    headers = {'Authorization': 'Bearer ' + api_token}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1

    # mark as dissolved
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(
                rrdb.Deployment.deploymentid == '07ae6006-36a2-4399-8ad3-bf9e58633a05'
            )
            .one_or_none()
        )
        assert wd is not None
        wd.date_dissolved = datetime.utcnow()

    # verify not returned in simple list of owned workspace deployments
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 0

    # but OK if with_dissolved (in several different forms)
    resp = await client.get('/hardshare/list?with_dissolved', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1
    resp = await client.get(
        '/hardshare/list', params={'with_dissolved': 1}, headers=headers
    )
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1
    resp = await client.get(
        '/hardshare/list', params={'with_dissolved': 'False'}, headers=headers
    )
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 0


async def test_wd_dissolve(client, hs_wdconfig, api_token):
    # declare new wdeployment
    headers = {'Authorization': 'Bearer ' + api_token}
    hs_wdconfig['hs'] = {'owner': 'bilbo'}
    tasks.create_new_wdeployment(hs_wdconfig['id'], hs_wdconfig)
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 1

    # receive request to dissolve from hardshare server (mock)
    dissolved = datetime.utcnow()
    tasks.dissolve_user_provided(
        wdeployment_id='07ae6006-36a2-4399-8ad3-bf9e58633a05',
        owner='bilbo',
        date_dissolved=dissolved,
    )

    # verify not appearing in GET /hardshare/list
    resp = await client.get('/hardshare/list', headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert len(body['wdeployments']) == 0
