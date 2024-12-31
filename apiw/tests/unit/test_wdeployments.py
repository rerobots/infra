"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import copy
from datetime import datetime
import json

import pytest

from fixtures import client, wdconfig

import rerobots_apiw.db as rrdb
from rerobots_apiw import tasks


pytestmark = pytest.mark.filterwarnings(
    "ignore: Using or importing the ABCs from 'collections'"
)


async def test_anonymous_wd_list(client):
    resp = await client.get('/deployments')
    assert resp.status == 200


async def test_register_wd(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1
    wdconfig2 = {
        'id': 'd71c2e10-6627-407b-917b-a9caef4df274',
        'type': 'null',
        'wversion': 0,
        'supported_addons': [],
        'addons_config': dict(),
        'region': 'us:cali',
        'desc_yaml': '',
    }
    tasks.create_new_wdeployment(wdconfig2['id'], wdconfig2)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 2


async def test_duplicate_new_wd(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1


async def test_emptyqueue(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/queuelen/07ae6006-36a2-4399-8ad3-bf9e58633a05')
    assert resp.status == 200
    body = await resp.json()
    assert body['id'] == '07ae6006-36a2-4399-8ad3-bf9e58633a05'
    assert body['len'] == 0


async def test_wd_info(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployment/07ae6006-36a2-4399-8ad3-bf9e58633a05')
    assert resp.status == 200
    body = await resp.json()
    assert 'dissolved' not in body
    assert body['online']
    for compare_key in [
        'id',
        'type',
        ('wversion', 'type_version'),
        'supported_addons',
        'region',
    ]:
        if isinstance(compare_key, tuple):
            assert wdconfig[compare_key[0]] == body[compare_key[1]]
        else:
            assert wdconfig[compare_key] == body[compare_key]

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
        date_dissolved_expected = str(wd.date_dissolved)

    resp = await client.get('/deployment/07ae6006-36a2-4399-8ad3-bf9e58633a05')
    assert resp.status == 200
    body = await resp.json()
    assert body['dissolved'] == date_dissolved_expected
    assert not body['online']


async def test_wd_dissolved_search(client, wdconfig):
    # declare new wdeployment
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1

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

    # verify not returned in simple list of workspace deployments
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 0

    # but OK if with_dissolved (in several different forms)
    resp = await client.get('/deployments?with_dissolved')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1
    resp = await client.get('/deployments', params={'with_dissolved': 1})
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1
    resp = await client.get('/deployments', params={'with_dissolved': 'False'})
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 0


async def test_wd_update_addons(client, wdconfig):
    # declare new wdeployment
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1

    # check no supported_addons yet
    resp = await client.get('/deployment/07ae6006-36a2-4399-8ad3-bf9e58633a05')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['supported_addons']) == 0

    # add mistyproxy
    update_info = {
        'id': '07ae6006-36a2-4399-8ad3-bf9e58633a05',
        'supported_addons': ['mistyproxy'],
        'addons_config': json.dumps(
            {
                'mistyproxy': {
                    'ip': '10.174.50.1',
                },
            }
        ),
    }
    tasks.update_wdeployment(update_info['id'], update_info)
    resp = await client.get('/deployment/07ae6006-36a2-4399-8ad3-bf9e58633a05')
    assert resp.status == 200
    body = await resp.json()
    assert body['supported_addons'] == ['mistyproxy']

    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == update_info['id'])
            .one()
        )
        addons_config = json.loads(wd.addons_config)
    assert 'mistyproxy' in addons_config
    assert 'ip' in addons_config['mistyproxy']


async def test_wd_list(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload['workspace_deployments']) == 1
    wdid = payload['workspace_deployments'][0]
    assert 'info' not in payload

    resp = await client.get('/deployments?info')
    assert resp.status == 200
    payload = await resp.json()
    assert wdid in payload['info']

    assert 'queuelen' in payload['info'][wdid]

    for compare_key in [
        'type',
        ('wversion', 'type_version'),
        'supported_addons',
        'region',
    ]:
        if isinstance(compare_key, tuple):
            assert wdconfig[compare_key[0]] == payload['info'][wdid][compare_key[1]]
        else:
            assert wdconfig[compare_key] == payload['info'][wdid][compare_key]
