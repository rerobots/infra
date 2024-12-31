"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import asyncio
from datetime import datetime

import sqlalchemy

from fixtures import api_token, client, wdconfig, wd_null, wd_null_dissolved, wd_null2

import rerobots_apiw.db as rrdb
from rerobots_apiw import tasks


async def test_wd_no_launch_permission(wd_null, api_token):
    client, wdeployment_id = wd_null['client'], wd_null['id']

    resp = await client.post('/new/{}'.format(wdeployment_id))
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'wrong authorization token'

    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await client.post('/new/{}'.format(wdeployment_id[:-1]), headers=headers)
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'unrecognized command'

    resp = await client.post('/new/{}'.format(wdeployment_id), headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert 'id' in payload
    assert payload['success']
    assert 'sshkey' not in payload


async def test_wtype_no_launch_permission(wd_null, api_token):
    client, wdeployment_id = wd_null['client'], wd_null['id']

    resp = await client.post('/new/null')
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'wrong authorization token'

    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await client.post('/new/nul', headers=headers)
    assert resp.status == 404

    resp = await client.post('/new/null', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert 'id' in payload
    assert payload['success']
    assert 'sshkey' not in payload


async def test_wd_no_launch_dissolved(wd_null_dissolved, api_token):
    client, wdeployment_id = wd_null_dissolved['client'], wd_null_dissolved['id']

    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await asyncio.wait_for(
        client.post('/new/{}'.format(wdeployment_id), headers=headers), timeout=5
    )
    assert resp.status == 400
    resp = await asyncio.wait_for(client.post('/new/null', headers=headers), timeout=5)
    assert resp.status == 404


async def test_wd_no_launch_old_heartbeat(wd_null, api_token):
    client, wdeployment_id = wd_null['client'], wd_null['id']

    headers = {'Authorization': 'Bearer {}'.format(api_token)}

    # NULL last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = None

    resp = await asyncio.wait_for(
        client.post('/new/{}'.format(wdeployment_id), headers=headers), timeout=5
    )
    assert resp.status == 503

    # old last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = datetime.fromtimestamp(1)

    resp = await asyncio.wait_for(
        client.post('/new/{}'.format(wdeployment_id), headers=headers), timeout=5
    )
    assert resp.status == 503

    # recent last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = datetime.utcnow()

    resp = await asyncio.wait_for(
        client.post('/new/{}'.format(wdeployment_id), headers=headers), timeout=5
    )
    assert resp.status == 200


async def test_wtype_no_launch_old_heartbeat(wd_null, api_token):
    client, wdeployment_id = wd_null['client'], wd_null['id']

    headers = {'Authorization': 'Bearer {}'.format(api_token)}

    # NULL last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = None

    resp = await asyncio.wait_for(client.post('/new/null', headers=headers), timeout=5)
    assert resp.status == 404

    # old last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = datetime.fromtimestamp(1)

    resp = await asyncio.wait_for(client.post('/new/null', headers=headers), timeout=5)
    assert resp.status == 404

    # recent last_heartbeat
    with rrdb.create_session_context() as session:
        wd = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        wd.last_heartbeat = datetime.utcnow()

    resp = await asyncio.wait_for(client.post('/new/null', headers=headers), timeout=5)
    assert resp.status == 200


async def test_wdmulti_no_launch_old_heartbeat(wd_null2, api_token):
    client, wdeployment_ids = wd_null2['client'], wd_null2['ids']

    headers = {'Authorization': 'Bearer {}'.format(api_token)}

    def with_recent_heartbeat(count):
        it = 0
        with rrdb.create_session_context() as session:
            for wd in session.query(rrdb.Deployment).filter(
                sqlalchemy.or_(
                    *[rrdb.Deployment.deploymentid == wd for wd in wdeployment_ids]
                )
            ):
                if it < count:
                    wd.last_heartbeat = datetime.utcnow()
                else:
                    wd.last_heartbeat = datetime.fromtimestamp(1)
                it += 1

    # none recent last_heartbeat
    with_recent_heartbeat(0)
    resp = await asyncio.wait_for(
        client.post('/new', json={'wds': wdeployment_ids}, headers=headers), timeout=5
    )
    assert resp.status == 503

    # one with recent last_heartbeat
    with_recent_heartbeat(1)
    resp = await asyncio.wait_for(
        client.post('/new', json={'wds': wdeployment_ids}, headers=headers), timeout=5
    )
    assert resp.status == 200

    # both recent last_heartbeat
    with_recent_heartbeat(2)
    resp = await asyncio.wait_for(
        client.post('/new', json={'wds': wdeployment_ids}, headers=headers), timeout=5
    )
    assert resp.status == 200
