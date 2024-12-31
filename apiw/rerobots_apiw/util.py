"""
SCL <scott@rerobots>
Copyright (C) 2023 rerobots, Inc.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import subprocess
import time

from . import settings


logger = logging.getLogger(__name__)


async def create_subprocess_exec(
    program,
    *args,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
):
    return await asyncio.create_subprocess_exec(
        program, *args, stdin=stdin, stdout=stdout, stderr=stderr
    )


def now():
    return datetime.now(timezone.utc)


def get_container_addr(pid, timeout=5):
    logger.info('attempting to get IPv4 address of container')
    sleep_duration = 1
    start_t = time.monotonic()
    addr = None
    while time.monotonic() - start_t < timeout:
        command = [
            settings.TH_CONTAINER_PROVIDER,
            'inspect',
            pid,
        ]
        try:
            res = subprocess.check_output(command, universal_newlines=True)
        except subprocess.CalledProcessError:
            logger.warning(f'caught CalledProcessError on `{command}`; sleeping...')
            time.sleep(sleep_duration)
            continue
        cdata = json.loads(res)
        if (
            len(cdata) < 1
            or 'NetworkSettings' not in cdata[0]
            or 'IPAddress' not in cdata[0]['NetworkSettings']
        ):
            logger.info('did not find IPv4 address; sleeping...')
            time.sleep(sleep_duration)
            continue
        else:
            # NetworkSettings.Networks.*.IPAddress
            addr_candidates = [cdata[0]['NetworkSettings']['IPAddress']]
            if 'Networks' in cdata[0]['NetworkSettings']:
                for net in cdata[0]['NetworkSettings']['Networks'].values():
                    addr_candidates.append(net['IPAddress'])
            for candidate in addr_candidates:
                if addr is None:
                    addr = candidate
                elif addr == '127.0.0.1' or addr == '':
                    addr = candidate
            if addr == '' or addr is None:
                addr = '127.0.0.1'
                logger.info(f'address field is empty; using default: {addr}')
            else:
                logger.info(f'found address: {addr}')
            break
    return addr


def is_public_tunnelhub(hostname):
    # TODO: support other hosts
    return hostname == 'api.rerobots.net'
