"""
SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import pytest

from fixtures import client, client_no_new_hs_billingplans, api_token, api_token_su


async def test_billingplans_get_no_plan(
    client_no_new_hs_billingplans, api_token, api_token_su
):
    # Concise alias
    client = client_no_new_hs_billingplans

    # anonymous not permitted
    resp = await client.get('/hardshare/billing/bilbo')
    assert resp.status == 400
    payload = await resp.json()
    assert payload['error_message'] == 'wrong authorization token'

    # non-superuser not permitted (presents as 404 Not Found)
    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await client.get('/hardshare/billing/bilbo', headers=headers)
    assert resp.status == 404

    # OK superuser, but no row for Bilbo yet
    headers = {'Authorization': 'Bearer {}'.format(api_token_su)}
    resp = await client.get('/hardshare/billing/bilbo', headers=headers)
    assert resp.status == 404


@pytest.mark.skip('need to move billing table')
async def test_max_active_no_plan(client_no_new_hs_billingplans, api_token):
    # fails to register if no billing plan
    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await client_no_new_hs_billingplans.post(
        '/hardshare/register', headers=headers
    )
    assert resp.status == 400


@pytest.mark.skip('need to move billing table')
async def test_max_active(client, api_token):
    # first register succeeds
    headers = {'Authorization': 'Bearer {}'.format(api_token)}
    resp = await client.post('/hardshare/register', headers=headers)
    assert resp.status == 200
    wdid = (await resp.json())['id']

    # second register fails because max. active count is 1
    resp = await client.post('/hardshare/register', headers=headers)
    assert resp.status == 400

    # dissolve first, then try register again
    resp = await client.post('/hardshare/dis/{}'.format(wdid), headers=headers)
    assert resp.status == 200
    resp = await client.post('/hardshare/register', headers=headers)
    assert resp.status == 200
