"""CI/CD


SCL <scott@rerobots>
Copyright (C) 2020 rerobots, Inc.
"""

import base64
import logging
import os
import time

import aiohttp
from aiohttp import web
import jwt

from . import db as rrdb
from .requestproc import process_headers
from .settings import PRIVATE_KEY
from .settings import DEBUG


logger = logging.getLogger(__name__)


async def post_ci_job(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()

    if 'job_id' in request.match_info:
        job_id = request.match_info['job_id']
        if 'do' not in given:
            return web.json_response(
                {'error_message': 'request is missing required parameters'},
                status=400,
                headers=data['response_headers'],
            )
        if given['do'] != 'restart':
            return web.json_response(
                {'error_message': '`do` value must be \'restart\''},
                status=400,
                headers=data['response_headers'],
            )
    else:
        job_id = None
        if 'branch' not in given or 'commit' not in given:
            return web.json_response(
                {'error_message': 'request is missing required parameters'},
                status=400,
                headers=data['response_headers'],
            )

    cip = (
        request['dbsession']
        .query(rrdb.CIProject)
        .filter(
            rrdb.CIProject.user == data['user'],
            rrdb.CIProject.name == request.match_info['project_name'],
        )
        .one_or_none()
    )
    if cip is None:
        return web.Response(status=404, headers=data['response_headers'])

    if job_id is None:
        cij = rrdb.CIJob(project=cip, ref=given['commit'], branch=given['branch'])
        request['dbsession'].add(cij)
    else:
        cij = (
            request['dbsession']
            .query(rrdb.CIJob)
            .filter(rrdb.CIJob.project == cip, rrdb.CIJob.id == job_id)
            .one_or_none()
        )
        if cij is None:
            return web.Response(status=404, headers=data['response_headers'])

    build = rrdb.CIBuild(job=cij)
    request['dbsession'].add(build)
    request['dbsession'].commit()

    user_jwt = request.headers['AUTHORIZATION'].split()[1]

    now = time.time()
    tok = jwt.encode(
        {'exp': int(now) + 10, 'nbf': int(now) - 1}, key=PRIVATE_KEY, algorithm='RS256'
    )
    headers = {
        'Authorization': 'Bearer {}'.format(str(tok, encoding='utf-8')),
    }

    payload = {
        'build_pk': build.id,
        'job': build.job_id,
        'proj': cip.name,  # build.job.project_id,
        'commit': cij.ref,
        'branch': cij.branch,
        'repo_url': cip.repo_url,
        'user': data['user'],
        'user_jwt': user_jwt,
    }
    if DEBUG:
        newbuild_url = 'http://127.0.0.1:9000/api/new/build'
    else:
        newbuild_url = 'https://ci.rerobots.net/api/new/build'
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            res = await session.post(newbuild_url, json=payload)
            if res.status != 200:
                return web.Response(status=500, headers=data['response_headers'])
    except aiohttp.client_exceptions.ClientConnectorError:
        if DEBUG:
            logger.warning(
                'connect refused at {}; ignoring because DEBUG'.format(newbuild_url)
            )
        else:
            raise

    return web.json_response({'jid': build.job_id}, headers=data['response_headers'])


async def create_project(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    if data['user'].startswith('sandbox_anon'):
        return web.Response(status=403, headers=data['response_headers'])

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()
    if 'repo_url' not in given:
        return web.json_response(
            {'error_message': 'request is missing required parameter: repo_url'},
            status=400,
            headers=data['response_headers'],
        )

    pname = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b'=').decode('ascii')
    # TODO: check that pname is unique in the ci_projects table
    cip = rrdb.CIProject(name=pname, user=data['user'], repo_url=given['repo_url'])
    request['dbsession'].add(cip)
    request['dbsession'].commit()

    now = time.time()
    tok = jwt.encode(
        {'exp': int(now) + 10, 'nbf': int(now) - 1}, key=PRIVATE_KEY, algorithm='RS256'
    )
    headers = {
        'Authorization': 'Bearer {}'.format(str(tok, encoding='utf-8')),
    }
    payload = {
        'name': pname,
        'id': cip.id,
    }
    if DEBUG:
        registration_url = 'http://127.0.0.1:9000/api/new'
    else:
        registration_url = 'https://ci.rerobots.net/api/new'
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            res = await session.post(registration_url, json=payload)
            if res.status != 200:
                return web.Response(status=500, headers=data['response_headers'])
    except aiohttp.client_exceptions.ClientConnectorError:
        if DEBUG:
            logger.warning(
                'connect refused at {}; ignoring because DEBUG'.format(registration_url)
            )
        else:
            raise

    return web.json_response({'pid': pname}, headers=data['response_headers'])


async def get_projects_list(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.json_response(
            {'error_message': 'wrong authorization token'},
            status=400,
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(status=403, headers=data['response_headers'])
    projs = dict()
    for row in (
        request['dbsession']
        .query(rrdb.CIProject)
        .filter(rrdb.CIProject.user == data['user'])
    ):
        projs[row.name] = {
            'created': str(row.created),
            'repo_url': row.repo_url,
        }
    return web.json_response(projs, headers=data['response_headers'])
