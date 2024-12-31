"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import asyncio
import copy
from datetime import datetime
import unittest.mock
import os
import os.path
import time
import uuid

import pytest

import jwt
import redis
import sqlalchemy

import rerobots_apiw.db as rrdb
from rerobots_apiw.factory import create_application
from rerobots_apiw.settings import DB_URL
from rerobots_apiw import tasks


def cleardb():
    engine = sqlalchemy.create_engine(DB_URL, echo=True)
    conn = engine.connect()
    tablenames = []
    for attr in dir(rrdb):
        if attr[0] == '_':
            continue
        if hasattr(getattr(rrdb, attr), '__tablename__'):
            tablenames.append(getattr(getattr(rrdb, attr), '__tablename__'))
    trans = conn.begin()
    for tablename in tablenames:
        try:
            conn.execute(sqlalchemy.text(f'DELETE FROM {tablename}'))
        except sqlalchemy.exc.OperationalError:
            pass
    trans.commit()
    conn.close()


def clearks():
    red = redis.StrictRedis(host='127.0.0.1', port=6379, db=0)
    red.flushall()


if 'REROBOTS_WEBUI_SECRET_KEY' in os.environ:
    WEBUI_SECRET_KEY = os.environ['REROBOTS_WEBUI_SECRET_KEY']
else:
    import rerobots_apiw

    with open(
        os.path.join(
            os.path.dirname(rerobots_apiw.__file__), '..', 'etc', 'webui-secret.key'
        )
    ) as fp:
        WEBUI_SECRET_KEY = fp.read()


def mock_channels(monkeypatch):
    def start(self):
        class Channel:
            def __init__(self):
                pass

            def basic_cancel(self):
                pass

            def close(self):
                pass

        self.channel = Channel()

    def is_open(self):
        return True

    def stop(self):
        pass

    def send_to_wd(self, wdeployment_id, payload):
        pass

    def send_to_apiw(self, payload):
        if payload['command'] == 'NEW':
            self.process_command(
                {
                    'command': 'ACK',
                    'did': payload['id'],
                }
            )

        elif payload['command'] == 'DISSOLVE HS':
            self.process_command(
                {
                    'command': 'ACK',
                    'did': payload['id'],
                }
            )

        elif payload['command'] == 'UPDATE':
            self.process_command(
                {
                    'command': 'ACK',
                    'did': payload['id'],
                }
            )

        else:
            raise Exception('Unexpected eacommand_rx: {}'.format(payload))

    def send_to_thportal(self, payload):
        pass

    monkeypatch.setattr(rerobots_apiw.channels.EACommandChannel, 'start', start)
    monkeypatch.setattr(rerobots_apiw.channels.EACommandChannel, 'is_open', is_open)
    monkeypatch.setattr(rerobots_apiw.channels.EACommandChannel, 'stop', stop)
    monkeypatch.setattr(
        rerobots_apiw.channels.EACommandChannel, 'send_to_wd', send_to_wd
    )

    monkeypatch.setattr(rerobots_apiw.hschannels.CommandChannel, 'start', start)
    monkeypatch.setattr(rerobots_apiw.hschannels.CommandChannel, 'is_open', is_open)
    monkeypatch.setattr(rerobots_apiw.hschannels.CommandChannel, 'stop', stop)
    monkeypatch.setattr(
        rerobots_apiw.hschannels.CommandChannel, 'send_to_apiw', send_to_apiw
    )

    monkeypatch.setattr(rerobots_apiw.hschannels.ConnectionChannel, 'start', start)
    monkeypatch.setattr(rerobots_apiw.hschannels.ConnectionChannel, 'is_open', is_open)
    monkeypatch.setattr(rerobots_apiw.hschannels.ConnectionChannel, 'stop', stop)
    monkeypatch.setattr(
        rerobots_apiw.hschannels.ConnectionChannel, 'send_to_thportal', send_to_thportal
    )


def mock_tasks(monkeypatch):
    def register_new_user_provided(*args, **kwargs):
        rerobots_apiw.tasks.register_new_user_provided(*args, **kwargs)

    def update_user_provided(*args, **kwargs):
        rerobots_apiw.tasks.update_user_provided(*args, **kwargs)

    def dissolve_user_provided(*args, **kwargs):
        rerobots_apiw.tasks.dissolve_user_provided(*args, **kwargs)

    def launch_instance(*args, **kwargs):
        pass

    monkeypatch.setattr(
        rerobots_apiw.tasks.register_new_user_provided,
        'delay',
        register_new_user_provided,
    )
    monkeypatch.setattr(
        rerobots_apiw.tasks.update_user_provided, 'delay', update_user_provided
    )
    monkeypatch.setattr(
        rerobots_apiw.tasks.dissolve_user_provided, 'delay', dissolve_user_provided
    )
    monkeypatch.setattr(rerobots_apiw.tasks.launch_instance, 'delay', launch_instance)


@pytest.fixture
def client(event_loop, aiohttp_client, monkeypatch):
    mock_channels(monkeypatch)
    mock_tasks(monkeypatch)
    yield event_loop.run_until_complete(aiohttp_client(create_application()))
    cleardb()
    clearks()


@pytest.fixture
def client_no_new_hs_billingplans(event_loop, aiohttp_client, monkeypatch):
    mock_channels(monkeypatch)
    mock_tasks(monkeypatch)
    yield event_loop.run_until_complete(aiohttp_client(create_application()))
    cleardb()
    clearks()


@pytest.fixture
def wdconfig():
    return {
        'id': '07ae6006-36a2-4399-8ad3-bf9e58633a05',
        'type': 'null',
        'wversion': 0,
        'supported_addons': [],
        'addons_config': dict(),
        'region': 'us:cali',
        'desc_yaml': '',
    }


@pytest.fixture
def hs_wdconfig(wdconfig):
    wdconfig['type'] = 'user_provided'
    return wdconfig


@pytest.fixture
def api_token():
    creationtime = int(time.time())
    payload = {
        'sub': 'bilbo',
        'iss': 'rerobots.net',
        'aud': 'rerobots.net',
        'exp': creationtime + 60,
        'nbf': creationtime - 1,
    }
    return str(
        jwt.encode(payload, key=WEBUI_SECRET_KEY, algorithm='RS256'), encoding='utf-8'
    )


@pytest.fixture
def api_token_su():
    creationtime = int(time.time())
    payload = {
        'sub': 'bilbo',
        'su': True,
        'iss': 'rerobots.net',
        'aud': 'rerobots.net',
        'exp': creationtime + 60,
        'nbf': creationtime - 1,
    }
    return str(
        jwt.encode(payload, key=WEBUI_SECRET_KEY, algorithm='RS256'), encoding='utf-8'
    )


@pytest.fixture
async def wd_null(client, wdconfig):
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 1
    return {'client': client, 'id': wdconfig['id']}


@pytest.fixture
async def wd_null2(client, wd_null, wdconfig):
    wdconfig2 = copy.deepcopy(wdconfig)
    wdconfig2['id'] = '26331d32-3de3-4150-814b-e31db0f1b273'
    tasks.create_new_wdeployment(wdconfig['id'], wdconfig)
    tasks.create_new_wdeployment(wdconfig2['id'], wdconfig2)
    resp = await client.get('/deployments')
    assert resp.status == 200
    body = await resp.json()
    assert len(body['workspace_deployments']) == 2
    return {'client': client, 'ids': [wd_null['id'], wdconfig2['id']]}


@pytest.fixture
def wd_null_dissolved(wd_null):
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
    return {'client': wd_null['client'], 'id': wd_null['id']}


@pytest.fixture
async def hardshare_registered_wd(client, api_token, api_token_su):
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

    return {'client': client, 'id': payload['id'], 'token': api_token, 'owner': 'bilbo'}
