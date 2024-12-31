"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

from copy import deepcopy
from datetime import datetime

import pytest

from fixtures import client, wdconfig

import rerobots_apiw.db as rrdb
from rerobots_apiw import tasks


pytestmark = pytest.mark.filterwarnings(
    "ignore: Using or importing the ABCs from 'collections'"
)


async def test_anonymous_wtypes_list_empty(client):
    resp = await client.get('/workspaces')
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload) == 1 and 'workspace_types' in payload
    assert len(payload['workspace_types']) == 0


async def test_anonymous_wtypes_list(client):
    wdconfig = {
        'id': '07ae6006-36a2-4399-8ad3-bf9e58633a05',
        'type': 'null',
        'wversion': 0,
        'supported_addons': [],
        'addons_config': dict(),
        'region': 'us:cali',
        'desc_yaml': '',
    }
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/workspaces')
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload) == 1 and 'workspace_types' in payload
    assert payload['workspace_types'] == ['null']


async def test_anonymous_wtypes_nonduplicate(client):
    base_config = {
        'id': None,
        'type': 'null',
        'wversion': 0,
        'supported_addons': [],
        'addons_config': dict(),
        'region': 'us:cali',
        'desc_yaml': '',
    }
    for wdid in [
        '07ae6006-36a2-4399-8ad3-bf9e58633a05',
        'c1f03409-cc3e-4984-b0c8-01b7da0901ba',
    ]:
        config = deepcopy(base_config)
        config['id'] = wdid
        tasks.create_new_wdeployment(config['id'], config)

    resp = await client.get('/workspaces')
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload) == 1 and 'workspace_types' in payload
    assert payload['workspace_types'] == ['null']


async def test_wd_dissolved_wtypes(client, wdconfig):
    # declare new wdeployment of wtype null
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/workspaces')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_types']) == 1

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

    # verify no wtypes listed after dissolving the only workspace deployment
    resp = await client.get('/workspaces')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_types']) == 0

    # but OK if with_dissolved (in several different forms)
    resp = await client.get('/workspaces?with_dissolved')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_types']) == 1
    resp = await client.get('/workspaces', params={'with_dissolved': 1})
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_types']) == 1
    resp = await client.get('/workspaces', params={'with_dissolved': 'False'})
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_types']) == 0
