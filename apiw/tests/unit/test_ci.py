"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import time

import pytest

import jwt

from fixtures import client, api_token


async def test_create_proj(client, api_token):
    headers = {'Authorization': 'Bearer ' + api_token}
    resp = await client.post('/ci/new')
    assert resp.status == 400 or resp.status == 403

    example_repo_url = 'https://github.com/rerobots/examples.git'
    resp = await client.post(
        '/ci/new', json={'repo_url': example_repo_url}, headers=headers
    )
    assert resp.status == 200
    payload = await resp.json()
    assert 'pid' in payload
    proj_id = payload['pid']

    resp = await client.get('/ci/projects', headers=headers)
    assert resp.status == 200
    payload = await resp.json()
    assert proj_id in payload
    assert payload[proj_id]['repo_url'] == example_repo_url


async def test_start_restart_job(client, api_token):
    headers = {'Authorization': 'Bearer ' + api_token}
    example_repo_url = 'https://github.com/rerobots/examples.git'
    resp = await client.post(
        '/ci/new', json={'repo_url': example_repo_url}, headers=headers
    )
    assert resp.status == 200
    payload = await resp.json()
    proj_id = payload['pid']

    job_command = {
        'commit': '0ba76f6ec47df43e041c4b6505d03192ebd6b3b2',
        'branch': 'devel',
    }
    resp = await client.post(
        '/ci/project/{}/job'.format(proj_id), json=job_command, headers=headers
    )
    assert resp.status == 200
    payload = await resp.json()
    assert 'jid' in payload
    job_id = payload['jid']

    job_command = {'do': 'restart'}
    resp = await client.post(
        '/ci/project/{}/job/{}'.format(proj_id, job_id),
        json=job_command,
        headers=headers,
    )
    assert resp.status == 200
