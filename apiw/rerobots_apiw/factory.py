"""
SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import asyncio
import logging

try:
    from asyncio import get_running_loop
except ImportError:
    from asyncio import get_event_loop as get_running_loop

from aiohttp import web
import redis

from . import addons
from . import anon, commands, cap, ci, instances, hardshare, users
from . import hsadmin
from . import mgmt
from . import mock
from .channels import PortAccessManager, EACommandChannel
from . import db as rrdb
from .init import init_logging, init_sentry
from . import settings


logger = logging.getLogger(__name__)


@web.middleware
async def dbmiddleware_handler(request, handler):
    with rrdb.create_session_context() as session:
        request['dbsession'] = session
        response = await handler(request)
    return response


@web.middleware
async def cors_handler(request, handler):
    """Middleware to add CORS response headers"""
    response = await handler(request)
    if response is None:
        return None
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,DELETE'
    if (
        'Origin' in request.headers
        and request.headers['Origin'] == 'https://robotpuzzles.com'
    ):
        response.headers['Access-Control-Allow-Origin'] = 'https://robotpuzzles.com'
    elif (
        'Origin' in request.headers
        and request.headers['Origin'] == 'https://hardshare.dev'
    ):
        response.headers['Access-Control-Allow-Origin'] = 'https://hardshare.dev'
    elif (
        'Origin' in request.headers
        and request.headers['Origin'] == 'https://caminobot.com'
    ):
        response.headers['Access-Control-Allow-Origin'] = 'https://caminobot.com'
    elif (
        'Origin' in request.headers
        and request.headers['Origin'] == 'https://learnrobot.dev'
    ):
        response.headers['Access-Control-Allow-Origin'] = 'https://learnrobot.dev'
    elif (
        'Origin' in request.headers
        and request.headers['Origin'] == settings.WEBUI_ORIGIN
    ):
        response.headers['Access-Control-Allow-Origin'] = settings.WEBUI_ORIGIN
    elif not settings.DEBUG:
        response.headers['Access-Control-Allow-Origin'] = '*'
    else:  # DEBUG
        if request.headers.get('Origin') in [
            'http://localhost:8000',
            'http://127.0.0.1:8000',
            'http://localhost:3000',
        ]:
            response.headers['Access-Control-Allow-Origin'] = request.headers.get(
                'Origin'
            )
        else:
            logger.warning(
                'DEBUG: unknown origin: {}'.format(request.headers.get('Origin'))
            )
            response.headers['Access-Control-Allow-Origin'] = '*'
    return response


async def show_options(request):
    """OPTIONS to support CORS"""
    return web.Response(
        headers={'Access-Control-Allow-Headers': 'Authorization,Content-Type'}
    )


async def start_bg(main):
    """start background workers"""
    loop = get_running_loop()
    main['red'] = redis.StrictRedis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
    )
    main['pam'] = PortAccessManager(basename='portaccess', event_loop=loop)
    main['pam'].start()
    main['eacommand'] = EACommandChannel(event_loop=loop)
    main['eacommand'].start()
    while not main['eacommand'].is_open() or not main['pam'].is_open():
        logger.debug('waiting for AMQP channels to open...')
        await asyncio.sleep(1)
    logger.debug('all AMQP channels opened')


async def cleanup_bg(main):
    main['pam'].stop()
    main['eacommand'].stop()
    logging.shutdown()


def create_application():
    uuid_re = r'[a-fA-F0-9]{8}\-[a-fA-F0-9]{4}\-[a-fA-F0-9]{4}\-[a-fA-F0-9]{4}\-[a-fA-F0-9]{12}'

    init_logging()
    init_sentry()

    application = web.Application(middlewares=[dbmiddleware_handler, cors_handler])
    application.on_startup.append(start_bg)
    application.on_cleanup.append(cleanup_bg)
    application.router.add_get('/', commands.index, allow_head=False)
    application.router.add_get(
        '/workspaces', commands.list_workspace_types, allow_head=False
    )
    application.router.add_get(
        r'/deployments', commands.list_deployments, allow_head=False
    )
    application.router.add_get(
        r'/queuelen/{did:[a-zA-Z\d_\-0-9\=]+}', commands.get_queuelen, allow_head=False
    )
    application.router.add_get(
        r'/deployments/{wt:[a-zA-Z\d_\-]+}', commands.list_deployments, allow_head=False
    )
    application.router.add_get(
        r'/deployment/{did:' + uuid_re + '}',
        commands.get_deployment_info,
        allow_head=False,
    )
    application.router.add_get(
        r'/instances', instances.get_instances_list, allow_head=False
    )
    application.router.add_get(
        r'/instances/{wt:[a-zA-Z\d_\-]+}',
        instances.get_instances_list,
        allow_head=False,
    )
    application.router.add_get(
        r'/instance/{inid:' + uuid_re + '}',
        instances.get_instance_info,
        allow_head=False,
    )
    application.router.add_get(
        r'/instance/{inid:' + uuid_re + '}/sshkey',
        instances.get_instance_sshkey,
        allow_head=False,
    )
    application.router.add_get(
        r'/firewall/{inid:' + uuid_re + '}',
        commands.get_firewall_rules,
        allow_head=False,
    )
    application.router.add_post(
        r'/firewall/{inid:' + uuid_re + '}', commands.change_firewall_rules
    )
    application.router.add_delete(
        r'/firewall/{inid:' + uuid_re + '}', commands.clear_firewall_rules
    )
    application.router.add_get(
        r'/vpn/{inid:' + uuid_re + '}', commands.get_vpn_info, allow_head=False
    )
    application.router.add_post(
        r'/vpn/{inid:' + uuid_re + '}', commands.make_new_vpnclient
    )

    application.router.add_post(r'/new', instances.request_instance)
    application.router.add_post(
        r'/new/{did:' + uuid_re + '}', instances.request_instance
    )
    application.router.add_post(r'/new/{wt:[a-zA-Z_0-9]+}', instances.request_instance)

    application.router.add_get(r'/qlen', commands.get_queue_lengths)
    application.router.add_get(r'/qlen/{wt:[a-zA-Z_0-9]+}', commands.get_queue_lengths)

    application.router.add_post(
        r'/terminate/{inid:' + uuid_re + '}', instances.terminate_instance
    )
    application.router.add_get(
        r'/reservations', commands.get_reservations_list, allow_head=False
    )
    application.router.add_delete(
        r'/reservation/{inid:' + uuid_re + '}', commands.delete_reservation
    )
    # application.router.add_post(r'/signin', commands.signin)
    application.router.add_post(
        r'/revoke/{sha256:[a-fA-F0-9]{64}}', commands.revoke_token
    )
    application.router.add_post(r'/purge', commands.purge_tokens)

    application.router.add_post(r'/ci/new', ci.create_project)
    application.router.add_get(r'/ci/projects', ci.get_projects_list, allow_head=False)
    application.router.add_post(
        r'/ci/project/{project_name:[a-zA-Z0-9\_\-]+}/job', ci.post_ci_job
    )
    application.router.add_post(
        r'/ci/project/{project_name:[a-zA-Z0-9\_\-]+}/job/{job_id:[0-9]+}',
        ci.post_ci_job,
    )

    # TODO: UPDATE (?) (or POST?)  https://api.rerobots.net/ci/project/<project_id> to adjust parameters like the repository URL
    # TODO: delete a CI project, DELETE https://api.rerobots.net/ci/project/<project_id> this marks the project as deleted, after which no new job requests will be accepted.

    application.router.add_get(r'/rules', cap.get_access_rules, allow_head=False)
    application.router.add_get(
        r'/deployment/{wdid:' + uuid_re + r'}/rules',
        cap.get_access_rules,
        allow_head=False,
    )
    application.router.add_post(
        r'/deployment/{wdid:' + uuid_re + r'}/rule', cap.create_access_rule
    )
    application.router.add_delete(
        r'/deployment/{wdid:' + uuid_re + r'}/rule/{ruleid:[0-9]+}',
        cap.delete_access_rule,
    )

    application.router.add_post(
        r'/deployment/{wdid:' + uuid_re + r'}/lockout', mgmt.lockout_wd
    )
    application.router.add_delete(
        r'/deployment/{wdid:' + uuid_re + r'}/lockout', mgmt.lockout_wd
    )

    application.router.add_post(
        r'/addon/cam/{inid:' + uuid_re + '}', addons.apply_addon_cam
    )
    application.router.add_get(
        r'/addon/cam/{inid:' + uuid_re + '}', addons.status_addon_cam, allow_head=False
    )
    application.router.add_get(
        r'/addon/cam/{inid:'
        + uuid_re
        + r'}/{cameraid:[0-9]+}/feed/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.addon_cam_stream,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cam/{inid:'
        + uuid_re
        + r'}/{cameraid:[0-9]+}/upload/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.addon_cam_upload,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cam/{inid:' + uuid_re + r'}/{cameraid:[0-9]+}/img',
        addons.addon_cam_snapshot,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/cam/{inid:' + uuid_re + '}', addons.remove_addon_cam
    )

    # apply the add-on to the named instance, which must exist
    application.router.add_post(
        r'/addon/vnc/{inid:' + uuid_re + '}', addons.apply_addon_vnc
    )

    # get status of the add-on
    application.router.add_get(
        r'/addon/vnc/{inid:' + uuid_re + '}', addons.status_addon_vnc, allow_head=False
    )

    # start the VNC tunnel
    application.router.add_post(
        r'/addon/vnc/{inid:' + uuid_re + '}/start', addons.addon_start_vnc
    )

    # stop (delete) the VNC tunnel; a new one can be opened via /start
    application.router.add_post(
        r'/addon/vnc/{inid:' + uuid_re + '}/stop', addons.addon_stop_vnc
    )

    # remove the add-on from the name instance.
    application.router.add_delete(
        r'/addon/vnc/{inid:' + uuid_re + '}', addons.remove_addon_vnc
    )

    application.router.add_post(
        r'/addon/wstcp/{inid:' + uuid_re + '}', addons.wstcp.apply_addon
    )
    application.router.add_get(
        r'/addon/wstcp/{inid:' + uuid_re + '}',
        addons.wstcp.status_addon,
        allow_head=False,
    )
    application.router.add_post(
        r'/addon/wstcp/{inid:' + uuid_re + '}/start', addons.wstcp.addon_start
    )
    application.router.add_post(
        r'/addon/wstcp/{inid:' + uuid_re + '}/stop', addons.wstcp.addon_stop
    )
    application.router.add_delete(
        r'/addon/wstcp/{inid:' + uuid_re + '}', addons.wstcp.remove_addon
    )

    application.router.add_post(
        r'/addon/mistyproxy/{inid:' + uuid_re + '}', addons.apply_addon_mistyproxy
    )
    application.router.add_get(
        r'/addon/mistyproxy/{inid:' + uuid_re + '}',
        addons.status_addon_mistyproxy,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/mistyproxy/{inid:' + uuid_re + '}', addons.remove_addon_mistyproxy
    )

    application.router.add_post(
        r'/addon/vscode/{inid:' + uuid_re + '}', addons.fulldevel.apply_addon_vscode
    )
    application.router.add_get(
        r'/addon/vscode/{inid:' + uuid_re + '}',
        addons.fulldevel.status_addon_vscode,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/vscode/{inid:' + uuid_re + '}', addons.fulldevel.remove_addon_vscode
    )

    application.router.add_post(
        r'/addon/py/{inid:' + uuid_re + '}', addons.minidevel.apply_addon_py
    )
    application.router.add_get(
        r'/addon/py/{inid:' + uuid_re + '}',
        addons.minidevel.status_addon_py,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/py/{inid:' + uuid_re + '}', addons.minidevel.remove_addon_py
    )

    application.router.add_post(
        r'/addon/java/{inid:' + uuid_re + '}', addons.minidevel.apply_addon_java
    )
    application.router.add_get(
        r'/addon/java/{inid:' + uuid_re + '}',
        addons.minidevel.status_addon_java,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/java/{inid:' + uuid_re + '}', addons.minidevel.remove_addon_java
    )

    application.router.add_post(
        r'/addon/drive/{inid:' + uuid_re + '}', addons.apply_addon_drive
    )
    application.router.add_get(
        r'/addon/drive/{inid:' + uuid_re + '}',
        addons.status_addon_drive,
        allow_head=False,
    )
    application.router.add_post(
        r'/addon/drive/{inid:' + uuid_re + '}/tx', addons.drive_send_command
    )
    application.router.add_get(
        r'/addon/drive/{inid:' + uuid_re + r'}/rx/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.drive_rx_commands,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/drive/{inid:' + uuid_re + '}', addons.remove_addon_drive
    )

    application.router.add_post(
        r'/addon/cmd/{inid:' + uuid_re + '}', addons.apply_addon_cmd
    )
    application.router.add_get(
        r'/addon/cmd/{inid:' + uuid_re + '}', addons.status_addon_cmd, allow_head=False
    )
    application.router.add_post(
        r'/addon/cmd/{inid:' + uuid_re + '}/tx', addons.cmd_send_command
    )
    application.router.add_post(
        r'/addon/cmd/{inid:' + uuid_re + '}/file', addons.cmd_send_file
    )
    application.router.add_get(
        r'/addon/cmd/{inid:' + uuid_re + r'}/rx/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.cmd_rx_commands,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/cmd/{inid:' + uuid_re + '}', addons.remove_addon_cmd
    )
    application.router.add_get(
        r'/addon/cmd/{inid:' + uuid_re + '}/stdout',
        addons.cmd_readline_stdout,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmd/{inid:' + uuid_re + r'}/stdout/{cmdid:[a-zA-Z_\-0-9\=]+}',
        addons.cmd_readline_stdout,
        allow_head=False,
    )
    application.router.add_post(
        r'/addon/cmd/{inid:' + uuid_re + r'}/cancel/{cmdid:[a-zA-Z_\-0-9\=]+}',
        addons.cmd_cancel_job,
    )

    application.router.add_post(
        r'/addon/cmdsh/{inid:' + uuid_re + '}', addons.cmdsh.apply_addon
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + '}',
        addons.cmdsh.get_status,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/cmdsh/{inid:' + uuid_re + '}', addons.cmdsh.remove
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/rx',
        addons.cmdsh.attach_main,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/rx/{shid:' + uuid_re + '}',
        addons.cmdsh.attach_sh,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/new',
        addons.cmdsh.newshell,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/new/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.cmdsh.newshell,
        allow_head=False,
    )
    application.router.add_post(
        r'/addon/cmdsh/{inid:' + uuid_re + '}/file', addons.cmdsh.send_file
    )
    application.router.add_post(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/file/{hid:[0-9]}', addons.cmdsh.send_file
    )

    application.router.add_post(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}', addons.cmdsh.apply_addon
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}',
        addons.cmdsh.get_status,
        allow_head=False,
    )
    application.router.add_delete(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}', addons.cmdsh.remove
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}/rx',
        addons.cmdsh.attach_main,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}/rx/{shid:' + uuid_re + '}',
        addons.cmdsh.attach_sh,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}/new',
        addons.cmdsh.newshell,
        allow_head=False,
    )
    application.router.add_get(
        r'/addon/cmdsh/{inid:' + uuid_re + r'}/{hid:[0-9]}/new/{tok:[a-zA-Z_\-0-9\=]+}',
        addons.cmdsh.newshell,
        allow_head=False,
    )

    application.router.add_post(r'/hardshare/cam', hardshare.apply_cam)
    application.router.add_get(
        r'/hardshare/cam', hardshare.status_cam, allow_head=False
    )
    application.router.add_get(
        r'/hardshare/cam/{hscamid:' + uuid_re + r'}/upload',
        hardshare.cam_upload,
        allow_head=False,
    )
    application.router.add_delete(
        r'/hardshare/cam/{hscamid:' + uuid_re + r'}', hardshare.remove_cam
    )

    application.router.add_get(
        r'/hardshare/list', hardshare.list_my_wdeployments, allow_head=False
    )
    application.router.add_post(
        r'/hardshare/{wdid:' + uuid_re + r'}', hardshare.edit_attr_wd
    )
    application.router.add_get(
        r'/hardshare/owners', hardshare.list_owners, allow_head=False
    )
    application.router.add_get(
        r'/hardshare/list/{owner:[a-zA-Z_0-9]+}',
        hardshare.admin_list_wdeployments,
        allow_head=False,
    )

    application.router.add_get(
        r'/hardshare/instances', hardshare.instances_on_mine, allow_head=False
    )
    application.router.add_get(
        r'/hardshare/instance/{inid:' + uuid_re + r'}',
        hardshare.instance_on_mine,
        allow_head=False,
    )
    application.router.add_post(
        r'/hardshare/terminate/{inid:' + uuid_re + r'}',
        hardshare.terminate_instance_on_mine,
    )

    application.router.add_post(
        r'/hardshare/register', hardshare.register_new_wdeployment
    )
    application.router.add_post(
        r'/hardshare/wd/{did:' + uuid_re + '}', hardshare.update_wdeployment
    )
    application.router.add_get(
        r'/hardshare/check/{did:' + uuid_re + '}',
        hardshare.check_wdeployment_data,
        allow_head=False,
    )
    application.router.add_post(
        r'/hardshare/dis/{did:' + uuid_re + '}', hardshare.dissolve_wdeployment
    )
    application.router.add_route(
        'GET', r'/hardshare/ad/{did:' + uuid_re + '}', hardshare.advertise_wdeployment
    )

    application.router.add_post(
        r'/hardshare/hook/email/{did:' + uuid_re + '}', hsadmin.manage_hook_emails
    )
    application.router.add_post(
        r'/hardshare/alert/{did:' + uuid_re + '}', hsadmin.send_alert
    )

    application.router.add_get(
        r'/hardshare/billing', hsadmin.get_billing_plan, allow_head=False
    )
    application.router.add_get(
        r'/hardshare/billing/{username:[a-zA-Z_0-9]+}',
        hsadmin.get_billing_plan,
        allow_head=False,
    )

    application.router.add_post(
        r'/instance/{inid:' + uuid_re + '}/event', instances.mark_event
    )
    application.router.add_post(
        r'/instance/{inid:' + uuid_re + '}/ka', instances.keep_alive
    )

    application.router.add_post(
        r'/mailinglist/{topic:(puzzles|hardshare|learnrobot|camino)}/XPJTs6Fy_e21zjuvmujwNfzmgFbIn6-JpXlC13sQbZU',
        users.join_mailinglist,
    )
    application.router.add_get(
        r'/user/{username:[a-zA-Z_0-9]+}/org', users.get_orgs, allow_head=False
    )

    application.router.add_post(
        r'/token/anon/{wtype:(fixed_misty2|multi_kobuki)}/qHtlNyVxgYrneQxXIdaasL8K5d0Zhj5UMk4StYrohLM',
        anon.get_api_token,
    )

    if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
        application.router.add_get(
            r'/proxy/{ihash:[a-f0-9]{64}}/{kind:(py|java)}/{ptoken:[a-f0-9]{64}}',
            mock.minidevel_websocket,
        )

    application.router.add_options(r'/{route:.+}', show_options)

    application.router.add_route('*', r'/{route:.+}', commands.unrecognized)

    return application
