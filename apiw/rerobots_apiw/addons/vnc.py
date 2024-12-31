import asyncio
import json
import logging
import os
import signal
import tempfile

from aiohttp import web

from .. import db as rrdb
from ..requestproc import process_headers
from ..util import create_subprocess_exec


logger = logging.getLogger(__name__)


async def addon_vnc_stop_job(user, instance_id):
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        instance = (
            session.query(rrdb.Instance)
            .filter(rrdb.Instance.instanceid == instance_id)
            .one()
        )
        instance_status = instance.status
        ssh_privatekey = str(instance.ssh_privatekey)
        ipv4 = instance.listening_ipaddr
        port = instance.listening_port

    if instance_status != 'READY':
        logger.warning(
            'instance not READY, so cannot kill remote VNC server if it exists'
        )
    else:
        # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
        # provide key text via stdin of child process.
        tmp_fd, privatekey_path = tempfile.mkstemp()
        privatekey_file = os.fdopen(tmp_fd, 'w')
        privatekey_file.write(ssh_privatekey)
        privatekey_file.close()

        # TODO: run this in a Docker container? mainly intended as security
        vncserver_cmd = [
            'ssh',
            '-o',
            'UserKnownHostsFile=/dev/null',
            '-o',
            'StrictHostKeyChecking=no',
            '-i',
            privatekey_path,
            '-p',
            str(port),
            '{}@{}'.format(addon_config['user'], ipv4),
            'vncserver',
            '-kill',
            ':1',
        ]
        logger.info('run: {}'.format(vncserver_cmd))

        vncserver_p = await create_subprocess_exec(*vncserver_cmd)
        rc = await vncserver_p.wait()
        if rc != 0:
            logger.warning(
                f'on instance {instance_id}, command exitcode {rc}: {" ".join(vncserver_cmd)}'
            )

    for pid in addon_config['pid']:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except Exception as err:
            logger.warning(
                'failed to kill {}. does that process exist? ({}: {})'.format(
                    pid, type(err), err
                )
            )
    addon_config['pid'] = []
    addon_config['status'] = 'ready'
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)
        activeaddon.port = 0


async def addon_vnc_waitdelete_job(user, instance_id):
    while True:
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == '{}:vnc'.format(instance_id),
                )
                .one_or_none()
            )
            if activeaddon is None:
                return
            addon_config = json.loads(activeaddon.config)
            if addon_config['status'] == 'ready':
                session.delete(activeaddon)
                return
        await asyncio.sleep(1)


async def addon_vnc_start_job(user, instance_id):
    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to /stop while status=`starting`
    while True:
        logger.debug('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == '{}:vnc'.format(instance_id),
                )
                .one()
            )
            addon_config = json.loads(activeaddon.config)
            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            ssh_privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port

        if instance_status == 'READY' and len(ipv4) > 0:
            logger.info(
                'instance READY with IPv4 addr {} and port {}'.format(ipv4, port)
            )
            break
        await asyncio.sleep(1)

    # Find available port number for listening
    with rrdb.create_session_context() as session:
        aport_numbers = set(
            [p for p in session.query(rrdb.ActiveAddon.port).all() if p != 0]
        )

    tmp_fd, unixsocket = tempfile.mkstemp()
    logger.info('for instance {}, unix socket path: {}'.format(instance_id, unixsocket))
    os.close(tmp_fd)
    os.unlink(unixsocket)

    # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
    # provide key text via stdin of child process.
    tmp_fd, privatekey_path = tempfile.mkstemp()
    privatekey_file = os.fdopen(tmp_fd, 'w')
    privatekey_file.write(ssh_privatekey)
    privatekey_file.close()

    # TODO: run these processes in a Docker container? mainly intended as security
    vncserver_cmd = [
        'ssh',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-i',
        privatekey_path,
        '-p',
        str(port),
        '{}@{}'.format(addon_config['user'], ipv4),
        'vncserver',
    ]
    logger.info('run: {}'.format(vncserver_cmd))
    vncserver_p = await create_subprocess_exec(*vncserver_cmd)
    rc = await vncserver_p.wait()
    if rc != 0:
        logger.warning(
            f'on instance {instance_id}, command exitcode {rc}: {" ".join(vncserver_cmd)}'
        )

    sshtunnel_cmd = [
        'ssh',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-T',
        '-N',
        '-L',
        '{}:127.0.0.1:5901'.format(unixsocket),
        '-i',
        privatekey_path,
        '-p',
        str(port),
        '{}@{}'.format(addon_config['user'], ipv4),
    ]
    logger.info('run: {}'.format(sshtunnel_cmd))
    sshtunnel_p = await create_subprocess_exec(*sshtunnel_cmd)

    vnc_listenport = None
    for candidate in range(6801, 6900):
        if candidate not in aport_numbers:
            # SECURITY TODO: assembling the websockify command has the risk of shell
            # injection. However, all of the arguments are trusted, i.e., created from
            # routines of rerobots code or configuration files maintained by rerobots,
            # and not by user input.  The pre-assembly is motivated by the py virtualenv.
            websockify_cmd = ' '.join(
                [
                    'websockify',
                    '--cert=fullchain.pem',
                    '--key=privkey.pem',
                    '--ssl-only',
                    '--unix-target={}'.format(unixsocket),
                    '--auth-plugin=rerobots_websockify.RerobotsAuthPlugin',
                    '--auth-source=\'{"user": "' + user + '"}\'',
                    str(candidate),
                ]
            )
            logger.info('run: {}'.format(websockify_cmd))
            websockify_p = await create_subprocess_exec(
                *[
                    'bash',
                    '-c',
                    'source PY3/bin/activate && exec {}'.format(websockify_cmd),
                ]
            )
            try:
                await asyncio.wait_for(websockify_p.wait(), 2)
            except asyncio.TimeoutError:
                vnc_listenport = candidate
                break

    if vnc_listenport is None:
        logger.error(
            'error: no available add-on port numbers for instance {}'.format(
                instance_id
            )
        )
        return
    else:
        logger.info(
            'add-on port number for instance {} will be {}'.format(
                instance_id, vnc_listenport
            )
        )

    addon_config['pid'] = [sshtunnel_p.pid, websockify_p.pid]
    addon_config['status'] = 'active'
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
            )
            .one()
        )
        activeaddon.config = json.dumps(addon_config)
        activeaddon.port = vnc_listenport


async def apply_addon_vnc(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    if request.can_read_body:
        given = await request.json()
    else:
        given = dict()

    # TODO:
    # if 'privkey' not in given:
    #    return web.Response(body=json.dumps({'error_message': '`privkey` parameter is required'}),
    #                        status=403,
    #                        content_type='application/json',
    #                        headers=data['response_headers'])
    # tunneling_private_key = given['privkey']
    if 'user' in given:
        tunneling_user = given['user']
    else:
        tunneling_user = 'root'

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    wdeployment = (
        request['dbsession']
        .query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == row.deploymentid)
        .one()
    )
    if 'vnc' not in wdeployment.supported_addons:
        # TODO: after creating more add-ons, this quick substring
        # should be changed to the general case of casting to a list
        # from comma-separated values.
        return web.Response(
            body=json.dumps(
                {'error_message': 'this instance does not support the `vnc` add-on'}
            ),
            status=503,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
        )
    )
    if query.count() > 0:
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'add-on `vnc` already applied to instance {}'.format(
                        instance_id
                    )
                }
            ),
            status=503,  # Service Unavailable
            content_type='application/json',
            headers=data['response_headers'],
        )

    config = {
        'user': tunneling_user,
        #'key': tunneling_private_key,  # TODO
        'status': 'ready',  # status \in {ready, active, starting, stopping}
        'pid': [],
    }
    active_addon = rrdb.ActiveAddon(
        instanceid_with_addon='{}:vnc'.format(instance_id),
        user=data['user'],
        config=json.dumps(config),
    )
    request['dbsession'].add(active_addon)
    return web.Response(status=200, headers=data['response_headers'])


async def status_addon_vnc(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `vnc` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    payload = {
        'status': addon_config['status'],
        'remote_user': addon_config['user'],
    }
    if addon_config['status'] == 'active' and row.port != 0:
        payload['port'] = row.port

    return web.json_response(payload, headers=data['response_headers'])


async def addon_start_vnc(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `vnc` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'ready':
        payload = {'error_message': ''}
        if addon_config['status'] == 'active':
            payload['error_message'] = (
                'add-on is already active. perhaps you should /stop it first?'
            )
        else:
            payload['error_message'] = 'add-on is not ready; try again later'
        return web.Response(
            body=json.dumps(payload),
            status=503,  # Service Unavailable
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config['status'] = 'starting'
    row.config = json.dumps(addon_config)

    request.app.loop.create_task(
        addon_vnc_start_job(user=data['user'], instance_id=instance_id)
    )

    return web.Response(status=200, headers=data['response_headers'])


async def addon_stop_vnc(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
        )
    )
    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `vnc` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] not in ['active', 'starting']:
        payload = {'error_message': ''}
        if addon_config['status'] == 'ready' or addon_config['status'] == 'stopping':
            payload['error_message'] = 'add-on is not active.'
        else:
            payload['error_message'] = (
                'add-on not active but cannot be stopped now; try again later'
            )
        return web.Response(
            body=json.dumps(payload),
            status=503,  # Service Unavailable
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config['status'] = 'stopping'
    row.config = json.dumps(addon_config)

    request.app.loop.create_task(
        addon_vnc_stop_job(user=data['user'], instance_id=instance_id)
    )

    return web.Response(status=200, headers=data['response_headers'])


async def remove_addon_vnc(request):
    should_handle, data = process_headers(request)
    if data['user'] is None:
        return web.Response(
            body=json.dumps({'error_message': 'wrong authorization token'}),
            status=400,
            content_type='application/json',
            headers=data['response_headers'],
        )
    if not should_handle:
        return web.Response(
            status=403,
            content_type='application/json',
            headers=data['response_headers'],
        )

    instance_id = request.match_info['inid']
    query = (
        request['dbsession']
        .query(rrdb.Instance)
        .filter(
            rrdb.Instance.instanceid == instance_id,
            rrdb.Instance.rootuser == data['user'],
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps({'error_message': 'instance not found'}),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    query = (
        request['dbsession']
        .query(rrdb.ActiveAddon)
        .filter(
            rrdb.ActiveAddon.user == data['user'],
            rrdb.ActiveAddon.instanceid_with_addon == '{}:vnc'.format(instance_id),
        )
    )

    row = query.one_or_none()
    if row is None:
        return web.Response(
            body=json.dumps(
                {'error_message': 'add-on `vnc` not active on this instance'}
            ),
            status=404,
            content_type='application/json',
            headers=data['response_headers'],
        )

    addon_config = json.loads(row.config)
    if addon_config['status'] != 'ready' and addon_config['status'] != 'stopping':
        return web.Response(
            body=json.dumps(
                {
                    'error_message': 'add-on is not ready to be deleted; perhaps you should /stop it first?'
                }
            ),
            status=503,  # Service Unavailable
            content_type='application/json',
            headers=data['response_headers'],
        )
    elif addon_config['status'] == 'stopping':
        request.app.loop.create_task(
            addon_vnc_waitdelete_job(user=data['user'], instance_id=instance_id)
        )
    else:  # addon_config['status'] == 'ready':
        request['dbsession'].delete(row)

    return web.Response(status=200, headers=data['response_headers'])
