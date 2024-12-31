"""
SCL <scott@rerobots>
Copyright (C) 2023 rerobots, Inc.
"""

import hashlib
import json
import os
import os.path
import subprocess
import tempfile
import time
import uuid

from celery.utils.log import get_task_logger
import redis

from .celery import app as capp
from . import db as rrdb
from .util import get_container_addr, is_public_tunnelhub
from . import settings


logger = get_task_logger(__name__)


MAIN_INGRESS = 'proxyingress'


@capp.task
def start_mistyproxy(user, instance_id):
    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to DELETE while status=`starting`
    while True:
        logger.debug('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == f'{instance_id}:mistyproxy',
                )
                .one_or_none()
            )
            if activeaddon is None:
                addon_config = None
            else:
                addon_config = json.loads(activeaddon.config)
                ptoken = addon_config['ptoken']

            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port
            wdeployment_id = instance.deploymentid
            th_hostname = instance.th_hostname

        if (
            (addon_config is not None)
            and instance_status == 'READY'
            and (
                (len(ipv4) > 0 and port > 0)
                or settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']
            )
        ):
            logger.info(f'instance READY with IPv4 addr {ipv4} and port {port}')
            break
        time.sleep(1)

    pid = f'{instance_id}__mistyproxy'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()

    if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon
                    == f'{instance_id}:mistyproxy',
                )
                .one()
            )
            addon_config = json.loads(activeaddon.config)
            addon_config['status'] = 'active'
            addon_config['url'] = [
                'api.staging.rerobots.net:666/faketoken',
                f'{settings.ORIGIN}/proxy/{ihash}/mistyproxy/{ptoken}',
            ]
            addon_config['fwdport'] = 666
            activeaddon.config = json.dumps(addon_config)
        return

    targetipaddr = None
    with rrdb.create_session_context() as session:
        wdeployment = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        try:
            addons_config = json.loads(wdeployment.addons_config)
            if 'mistyproxy' in addons_config:
                targetipaddr = addons_config['mistyproxy']['ip']
        except Exception as err:
            logger.error(f'error parsing addons_config.mistyproxy: {type(err)}: {err}')
            return

    # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
    # provide key text via stdin of child process.
    # TODO: unlink privatekey_path eventually
    tmp_fd, privatekey_path = tempfile.mkstemp()
    privatekey_file = os.fdopen(tmp_fd, 'w')
    privatekey_file.write(privatekey)
    privatekey_file.close()

    if targetipaddr is None:
        if wdeployment_id == 'f06c8740-02a0-48ec-bdde-69ff88b71afd':
            targetipaddr = '10.174.50.1'
        else:
            targetipaddr = '10.34.54.1'

    start_container = [
        settings.TH_CONTAINER_PROVIDER,
        'run',
        '-d',
        '-p',
        '80',
        '--name',
        pid,
        'rerobots-addons/mistyproxy',
        '-h',
        ptoken,
    ]
    if not is_public_tunnelhub(th_hostname):
        start_container.extend(['-t', f'{th_hostname}:2210'])
    cp_private_key = [
        settings.TH_CONTAINER_PROVIDER,
        'cp',
        privatekey_path,
        f'{pid}:/root/id_rsa',
    ]
    get_port = [settings.TH_CONTAINER_PROVIDER, 'port', pid, '80']
    cmds = [start_container, cp_private_key]
    if is_public_tunnelhub(th_hostname):
        cmds.append(
            [
                settings.TH_CONTAINER_PROVIDER,
                'exec',
                '-d',
                pid,
                'ssh',
                '-T',
                '-N',
                '-i',
                '/root/id_rsa',
                '-o',
                'UserKnownHostsFile=/dev/null',
                '-o',
                'StrictHostKeyChecking=no',
                '-L',
                f'127.0.0.1:8000:{targetipaddr}:80',
                '-p',
                str(port),
                f'root@{ipv4}',
            ]
        )
    for cmd in cmds:
        logger.info(f'run: {cmd}')
        subprocess.check_call(cmd)

    cmd_p = subprocess.run(
        get_port, stdout=subprocess.PIPE
    )  # Wait to complete because this is prerequisite
    if cmd_p.returncode != 0:
        raise Exception('addon mistyproxy: failed to get port number')
    fwdaddrport = cmd_p.stdout.decode('utf-8').split('\n')[0].strip()
    fwdaddr, fwdport = fwdaddrport.split(':')

    addr = get_container_addr(pid)
    if addr is None:
        addr = '127.0.0.1'
        logger.warning(f'get_container_addr returned None; default to {addr}')

    urls = [
        f'api.rerobots.net:{fwdport}/{ptoken}',
        f'https://api.rerobots.net/proxy/{ihash}/mistyproxy/{ptoken}',
    ]

    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:mistyproxy',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)

        addon_config['status'] = 'active'
        addon_config['url'] = urls
        addon_config['fwdport'] = fwdport

        activeaddon.config = json.dumps(addon_config)

        session.add(
            rrdb.ActiveProxy(
                instance_hash=ihash,
                addon='mistyproxy',
                token=ptoken,
                address=addr,
                port=fwdport,
            )
        )

    reload_nginx()

    iptables_tcp_new_port = (
        f'INPUT -p tcp -m tcp --dport {fwdport} -m conntrack --ctstate NEW -j ACCEPT'
    )
    logger.warning(f'skipping iptables call: {iptables_tcp_new_port}')
    # subprocess.check_call(['sudo', 'iptables', '-A'] + iptables_tcp_new_port.split(), stdout=subprocess.PIPE)


@capp.task
def stop_mistyproxy(user, instance_id):
    pid = f'{instance_id}__mistyproxy'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:mistyproxy',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        ptoken = addon_config['ptoken']

        ap = (
            session.query(rrdb.ActiveProxy)
            .filter(
                rrdb.ActiveProxy.instance_hash == ihash,
                rrdb.ActiveProxy.addon == 'mistyproxy',
                rrdb.ActiveProxy.token == ptoken,
            )
            .one_or_none()
        )
        session.delete(activeaddon)
        if ap is not None:
            fwdport = ap.port
            session.delete(ap)
        elif settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
            # ActiveProxy row not created when mocking
            return
        else:
            logger.warning(
                f'vacuous call, no active proxy found for {ihash}, mistyproxy, {ptoken}'
            )
            return

    reload_nginx()

    iptables_tcp_new_port = (
        f'INPUT -p tcp -m tcp --dport {fwdport} -m conntrack --ctstate NEW -j ACCEPT'
    )
    logger.warning(f'skipping iptables call: {iptables_tcp_new_port}')
    # subprocess.check_call(['sudo', 'iptables', '-D'] + iptables_tcp_new_port.split(), stdout=subprocess.PIPE)
    subprocess.check_call([settings.TH_CONTAINER_PROVIDER, 'rm', '-f', pid])


def start_minidevel(user, instance_id, kind):
    assert kind in ['py', 'java']

    # TODO:
    # The main challenge is to be aware if another request (via this
    # APIW or another) arrives to DELETE while status=`starting`
    while True:
        logger.debug('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}',
                )
                .one_or_none()
            )
            if activeaddon is None:
                addon_config = None
            else:
                addon_config = json.loads(activeaddon.config)
                ptoken = addon_config['ptoken']

            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port

        if (
            (addon_config is not None)
            and instance_status == 'READY'
            and (
                (len(ipv4) > 0 and port > 0)
                or settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']
            )
        ):
            logger.info(f'instance READY with IPv4 addr {ipv4} and port {port}')
            break
        time.sleep(1)

    pid = f'{instance_id}__{kind}'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()

    if settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}',
                )
                .one()
            )
            addon_config = json.loads(activeaddon.config)
            addon_config['status'] = 'active'
            addon_config['url'] = f'{settings.WS_ORIGIN}/proxy/{ihash}/{kind}/{ptoken}'
            activeaddon.config = json.dumps(addon_config)
        return

    # TODO: another idea: use /dev/stdin as identity file (`-i` arg) and, then,
    # provide key text via stdin of child process.
    # TODO: unlink privatekey_path eventually
    tmp_fd, privatekey_path = tempfile.mkstemp()
    privatekey_file = os.fdopen(tmp_fd, 'w')
    privatekey_file.write(privatekey)
    privatekey_file.close()

    start_container = [
        settings.TH_CONTAINER_PROVIDER,
        'run',
        '--read-only',
        '--mount=type=tmpfs,destination=/tmp,tmpfs-size=10485760',
        '-m',
        '104857600',
        '--pids-limit',
        '1000',
        '-d',
        '-p',
        '8888',
        '--name',
        pid,
        f'rerobots-addons/{kind}',
        ptoken,
    ]
    get_port = [settings.TH_CONTAINER_PROVIDER, 'port', pid, '8888']
    cmds = [start_container]
    for cmd in cmds:
        logger.info('run: {}'.format(cmd))
        cmd_p = subprocess.run(cmd)  # Wait to complete because this is prerequisite

    cmd_p = subprocess.run(
        get_port, stdout=subprocess.PIPE
    )  # Wait to complete because this is prerequisite
    if cmd_p.returncode != 0:
        raise Exception('failed to get port number')
    fwdaddrport = cmd_p.stdout.decode('utf-8').split('\n')[0].strip()
    fwdaddr, fwdport = fwdaddrport.split(':')

    addr = get_container_addr(pid)
    if addr is None:
        addr = '127.0.0.1'
        logger.warning(f'get_container_addr returned None; default to {addr}')

    url = f'wss://api.rerobots.net/proxy/{ihash}/{kind}/{ptoken}'

    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        addon_config['status'] = 'active'
        addon_config['url'] = url

        activeaddon.config = json.dumps(addon_config)

        session.add(
            rrdb.ActiveProxy(
                instance_hash=ihash,
                addon=kind,
                token=ptoken,
                address=addr,
                port=fwdport,
            )
        )

    reload_nginx()


def stop_minidevel(user, instance_id, kind):
    assert kind in ['py', 'java']

    pid = f'{instance_id}__{kind}'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()

    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:{kind}',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        ptoken = addon_config['ptoken']

        ap = (
            session.query(rrdb.ActiveProxy)
            .filter(
                rrdb.ActiveProxy.instance_hash == ihash,
                rrdb.ActiveProxy.addon == kind,
                rrdb.ActiveProxy.token == ptoken,
            )
            .one_or_none()
        )
        session.delete(activeaddon)
        if ap is not None:
            fwdport = ap.port
            session.delete(ap)
        elif settings.RUNTIME_ENVIRON in ['mock', 'staging-mock']:
            # ActiveProxy row not created when mocking
            return
        else:
            logger.warning(
                f'vacuous call, no active proxy found for {ihash}, {kind}, {ptoken}'
            )
            return

    reload_nginx()

    iptables_tcp_new_port = (
        f'INPUT -p tcp -m tcp --dport {fwdport} -m conntrack --ctstate NEW -j ACCEPT'
    )
    logger.warning(f'skipping iptables call: {iptables_tcp_new_port}')
    # subprocess.check_call(['sudo', 'iptables', '-D'] + iptables_tcp_new_port.split(), stdout=subprocess.PIPE)
    subprocess.check_call([settings.TH_CONTAINER_PROVIDER, 'rm', '-f', pid])


@capp.task
def start_minidevel_py(user, instance_id):
    start_minidevel(user=user, instance_id=instance_id, kind='py')


@capp.task
def stop_minidevel_py(user, instance_id):
    stop_minidevel(user=user, instance_id=instance_id, kind='py')


@capp.task
def start_minidevel_java(user, instance_id):
    start_minidevel(user=user, instance_id=instance_id, kind='java')


@capp.task
def stop_minidevel_java(user, instance_id):
    stop_minidevel(user=user, instance_id=instance_id, kind='java')


@capp.task
def start_vscode(user, instance_id):
    while True:
        logger.debug('checking instance status...')
        with rrdb.create_session_context() as session:
            activeaddon = (
                session.query(rrdb.ActiveAddon)
                .filter(
                    rrdb.ActiveAddon.user == user,
                    rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:vscode',
                )
                .one_or_none()
            )
            if activeaddon is None:
                addon_config = None
            else:
                addon_config = json.loads(activeaddon.config)
                ptoken = addon_config['ptoken']

            instance = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one()
            )
            instance_status = instance.status
            privatekey = str(instance.ssh_privatekey)
            ipv4 = instance.listening_ipaddr
            port = instance.listening_port

        if (
            (addon_config is not None)
            and instance_status == 'READY'
            and len(ipv4) > 0
            and port > 0
        ):
            logger.info(f'instance READY with IPv4 addr {ipv4} and port {port}')
            break
        time.sleep(1)

    pid = f'{instance_id}__vscode'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()

    tmp_fd, privatekey_path = tempfile.mkstemp()
    privatekey_file = os.fdopen(tmp_fd, 'w')
    privatekey_file.write(privatekey)
    privatekey_file.close()

    start_container = [
        settings.TH_CONTAINER_PROVIDER,
        'run',
        '-d',
        '-p',
        '80',
        '--name',
        pid,
        'rerobots-addons/mistyproxy',
        '-h',
        ptoken,
    ]
    cp_private_key = [
        settings.TH_CONTAINER_PROVIDER,
        'cp',
        privatekey_path,
        '{}:/root/id_rsa'.format(pid),
    ]
    get_port = [settings.TH_CONTAINER_PROVIDER, 'port', pid, '80']
    start_vscode = [
        settings.TH_CONTAINER_PROVIDER,
        'exec',
        '-d',
        pid,
        'ssh',
        '-i',
        '/root/id_rsa',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-p',
        str(port),
        '{}@{}'.format('root', ipv4),
        'code-server',
        '--auth=none',
    ]
    start_ssh_fwd = [
        settings.TH_CONTAINER_PROVIDER,
        'exec',
        '-d',
        pid,
        'ssh',
        '-T',
        '-N',
        '-i',
        '/root/id_rsa',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-o',
        'StrictHostKeyChecking=no',
        '-L',
        '127.0.0.1:8000:127.0.0.1:8080',
        '-p',
        str(port),
        '{}@{}'.format('root', ipv4),
    ]
    cmds = [start_container, cp_private_key, start_vscode, start_ssh_fwd]
    for cmd in cmds:
        logger.info('run: {}'.format(cmd))
        subprocess.check_call(cmd)

    cmd_p = subprocess.run(
        get_port, stdout=subprocess.PIPE
    )  # Wait to complete because this is prerequisite
    if cmd_p.returncode != 0:
        raise Exception('addon vscode: failed to get port number')
    fwdaddrport = cmd_p.stdout.decode('utf-8').split('\n')[0].strip()
    fwdaddr, fwdport = fwdaddrport.split(':')

    addr = get_container_addr(pid)
    if addr is None:
        addr = '127.0.0.1'
        logger.warning(f'get_container_addr returned None; default to {addr}')

    url = f'https://api.rerobots.net/proxy/{ihash}/vscode/{ptoken}/'

    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:vscode',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)

        addon_config['status'] = 'active'
        addon_config['url'] = url

        activeaddon.config = json.dumps(addon_config)

        session.add(
            rrdb.ActiveProxy(
                instance_hash=ihash,
                addon='vscode',
                token=ptoken,
                address=addr,
                port=fwdport,
            )
        )

    reload_nginx()

    iptables_tcp_new_port = (
        f'INPUT -p tcp -m tcp --dport {fwdport} -m conntrack --ctstate NEW -j ACCEPT'
    )
    logger.warning(f'skipping iptables call: {iptables_tcp_new_port}')
    # subprocess.check_call(['sudo', 'iptables', '-A'] + iptables_tcp_new_port.split(), stdout=subprocess.PIPE)


@capp.task
def stop_vscode(user, instance_id):
    pid = f'{instance_id}__vscode'
    ihash = hashlib.sha256(bytes(instance_id, encoding='utf-8')).hexdigest()
    with rrdb.create_session_context() as session:
        activeaddon = (
            session.query(rrdb.ActiveAddon)
            .filter(
                rrdb.ActiveAddon.user == user,
                rrdb.ActiveAddon.instanceid_with_addon == f'{instance_id}:vscode',
            )
            .one()
        )
        addon_config = json.loads(activeaddon.config)
        ptoken = addon_config['ptoken']

        ap = (
            session.query(rrdb.ActiveProxy)
            .filter(
                rrdb.ActiveProxy.instance_hash == ihash,
                rrdb.ActiveProxy.addon == 'vscode',
                rrdb.ActiveProxy.token == ptoken,
            )
            .one_or_none()
        )
        session.delete(activeaddon)
        if ap is not None:
            fwdport = ap.port
            session.delete(ap)
        else:
            logger.warning(
                f'vacuous call, no active proxy found for {ihash}, vscode, {ptoken}'
            )
            return

    reload_nginx()

    iptables_tcp_new_port = (
        f'INPUT -p tcp -m tcp --dport {fwdport} -m conntrack --ctstate NEW -j ACCEPT'
    )
    logger.warning(f'skipping iptables call: {iptables_tcp_new_port}')
    # subprocess.check_call(['sudo', 'iptables', '-D'] + iptables_tcp_new_port.split(), stdout=subprocess.PIPE)
    subprocess.check_call([settings.TH_CONTAINER_PROVIDER, 'rm', '-f', pid])


def gen_nginx():
    PREFIX0 = """# Automatically generated by rerobots_apiw
# by rerobots, Inc.


worker_processes 4;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;

    map $http_upgrade $connection_upgrade {
        default upgrade;
        '' close;
    }
"""
    UPSTREAM_PART = """
    upstream {APPSERVERNAME} {{
        server {ADDR}:{PORT} fail_timeout=0;
    }}
"""
    PREFIX1 = """
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
"""
    PATHSTRIP = """
        location /proxy/{INSTANCEHASH}/{ADDON}/{TOKEN} {{
            rewrite /proxy/{INSTANCEHASH}/{ADDON}(.*)$ $1 break;

            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Host $http_host;
            proxy_redirect off;
            proxy_buffering off;
            proxy_pass http://{APPSERVERNAME};

            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
        }}
"""
    SUFFIX = """
        location / {
            return 404;
        }

    }
}
"""
    with rrdb.create_session_context() as session:
        out = PREFIX0
        for ap in session.query(rrdb.ActiveProxy):
            if ap.addon in ['py', 'java']:
                port = 8888
            else:
                port = 80
            out += UPSTREAM_PART.format(
                ADDR=ap.address,
                PORT=port,
                APPSERVERNAME='app_server_{}_{}'.format(ap.instance_hash, ap.addon),
            )

        out += PREFIX1
        for ap in session.query(rrdb.ActiveProxy):
            out += PATHSTRIP.format(
                INSTANCEHASH=ap.instance_hash,
                ADDON=ap.addon,
                TOKEN=ap.token,
                APPSERVERNAME='app_server_{}_{}'.format(ap.instance_hash, ap.addon),
            )
        out += SUFFIX
    return out


def reload_nginx():
    config = gen_nginx()
    tmp_fd, config_path = tempfile.mkstemp()
    config_file = os.fdopen(tmp_fd, 'wt')
    config_file.write(config)
    config_file.close()
    copy_command = [
        settings.TH_CONTAINER_PROVIDER,
        'cp',
        config_path,
        f'{MAIN_INGRESS}:/etc/nginx/nginx.conf',
    ]
    reload_command = [
        settings.TH_CONTAINER_PROVIDER,
        'exec',
        MAIN_INGRESS,
        'nginx',
        '-s',
        'reload',
    ]

    mkey = 'proxynginx'
    try:
        red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )
        k = str(uuid.uuid4())
        start_t = time.monotonic()
        while time.monotonic() - start_t < 30:
            red.setnx(mkey, k)
            res = str(red.get(mkey), encoding='utf-8')
            if res == k:
                red.expire(mkey, 10)
                break
            time.sleep(2)

        subprocess.check_call(copy_command)
        cp = subprocess.run(reload_command)
        if cp.returncode != 0:
            logger.error(f'failed to {reload_command}; trying again...')
            subprocess.check_call(reload_command)

        red.delete(mkey)

    except Exception as err:
        logger.error(f'while trying to get mutex, caught {type(err)}: {err}')
