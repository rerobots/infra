"""hardshare client proxy to AMQP

The basic idea is to bridge communications to the user_provided
workspace, which does not have direct access to the infra AMQP.


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import asyncio
import json
import logging
import time
import uuid

import pika
import pika.adapters.asyncio_connection
import sqlalchemy

from . import db as rrdb
from . import settings


logger = logging.getLogger(__name__)


class CommandChannel:
    def __init__(
        self,
        wdeployment_id,
        ws_send=None,
        pid=None,
        rkey=None,
        red=None,
        event_loop=None,
    ):
        self.wdeployment_id = wdeployment_id
        self.pid = pid
        self.rkey = rkey
        self.current_instance_id = None
        self.prior_instance = None
        self.ws_send = ws_send
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.red = red
        self.expected_resp = dict()
        self.host = settings.AMQP_HOST
        self.port = settings.AMQP_PORT
        self._exchange_name = 'eacommand.{}'.format(self.wdeployment_id)
        self.received_acks = dict()
        self.channel = None
        self._closing = False
        self._consumer = None

    def start(self):
        param = pika.ConnectionParameters(host=self.host, port=self.port, heartbeat=20)
        self._conn = pika.adapters.asyncio_connection.AsyncioConnection(
            param,
            on_open_callback=self._on_connection,
            on_open_error_callback=self._on_open_error,
            on_close_callback=self._on_close,
            custom_ioloop=self.loop,
        )

    def _on_close(self, connection, err):
        if self._closing:
            return
        logger.warning(
            'connection to RabbitMQ server closed with {}: {}; restarting'.format(
                type(err), err
            )
        )
        self.start()

    def _on_open_error(self, connection, error_message=None):
        logger.warning('open error; attempting to re-connect to RabbitMQ server')
        time.sleep(1)
        self.start()

    def _on_connection(self, connection):
        self._conn.channel(on_open_callback=self._on_open_channel)

    def _on_open_channel(self, channel):
        self.channel = channel
        self.channel.exchange_declare(
            exchange=self._exchange_name,
            exchange_type=pika.exchange_type.ExchangeType.fanout,
            callback=self._on_exchange_ready,
        )

    def _on_exchange_ready(self, okframe):
        self.channel.queue_declare(
            '', exclusive=True, auto_delete=True, callback=self._on_queue_declared
        )

    def _on_queue_declared(self, okframe):
        self._queue_name = okframe.method.queue
        self.channel.queue_bind(
            exchange=self._exchange_name,
            queue=self._queue_name,
            callback=self._start_consuming,
        )

    def _start_consuming(self, okframe):
        logger.info('starting to consume on queue {}'.format(self._queue_name))
        self.channel.add_on_cancel_callback(self.close_via_cancel)
        self._consumer = self.channel.basic_consume(
            queue=self._queue_name,
            on_message_callback=self.handle_incoming_message,
            auto_ack=True,
        )

    def is_open(self):
        try:
            if not self._conn.is_open:
                return False
        except:
            return False
        if self.channel is None:
            return False
        if not self.channel.is_open:
            return False
        return True

    def _close_channel(self, _unused_frame):
        self._consumer = None
        self.channel.close()
        if self._closing and not self._conn.is_closing and not self._conn.is_closed:
            self._conn.close()

    def _stop_consuming(self):
        if self._consumer is not None:
            self.channel.basic_cancel(self._consumer, self._close_channel)

    def close_via_cancel(self):
        if self.is_open():
            self._stop_consuming()

    def stop(self):
        self._closing = True
        if not self.is_open():
            return
        self._stop_consuming()

    def send_to_apiw(self, payload):
        logger.debug('eacommand_rx: {}'.format(json.dumps(payload)))
        self.channel.basic_publish(
            exchange='', routing_key='eacommand_rx', body=json.dumps(payload)
        )

    async def handle_response(self, msg):
        logger.debug('handle_response(msg={})'.format(msg))
        if 'mi' not in msg or not isinstance(msg['mi'], str):
            logger.error(
                'CommandChannel.handle_response: '
                'given message does not have required ID'
            )
            return
        if 'cmd' not in msg or msg['cmd'] not in ['ACK', 'NACK']:
            logger.error(
                'CommandChannel.handle_response: '
                'given message does not have required `cmd`'
            )
            return
        try:
            v = self.expected_resp.pop(msg['mi'])
        except:
            for k in ['mi', 'cmd']:
                if len(msg[k]) > 64:
                    msg[k] = msg[k][:64]
            logger.warning(
                'unexpected response message {} (id: {})'.format(msg['cmd'], msg['mi'])
            )
            return
        if v['cmd'] == 'INSTANCE_LAUNCH':
            logger.debug(
                'handling response to INSTANCE_LAUNCH, sending {}'.format(msg['cmd'])
            )
            self.send_to_apiw(
                {
                    'command': msg['cmd'],
                    'message_id': msg['mi'],
                }
            )
            self.current_instance_id = v['instance_id']

        elif v['cmd'] == 'INSTANCE_DESTROY':
            with rrdb.create_session_context() as session:
                inst = (
                    session.query(rrdb.Instance)
                    .filter(rrdb.Instance.instanceid == v['instance_id'])
                    .one_or_none()
                )
                if inst is None:
                    logger.error(
                        'client-side success of destroy command for unknown instance: {}'.format(
                            v['instance_id']
                        )
                    )
                elif msg['cmd'] != v['awsent']:
                    logger.error(
                        (
                            'received NACK from client in response to INSTANCE_DESTROY for '
                            'instance {}, '
                            'but ACK was already sent to APIW'.format(v['instance_id'])
                        )
                    )
                else:
                    self.prior_instance = (
                        self.current_instance_id,
                        time.monotonic(),
                        msg['mi'],
                    )
                    self.current_instance_id = None

        else:
            logger.warning(
                'CommandChannel.handle_response: received response for unknown command {}'.format(
                    v['cmd']
                )
            )

    def handle_incoming_message(self, channel, method, prop, body):
        logger.debug('received: {}'.format(body))
        msg = json.loads(str(body, encoding='utf-8'))
        self.process_command(msg)

    def process_command(self, msg):
        if 'command' not in msg:
            logger.warning('received message that lacks `command`')
            return
        if 'did' not in msg and 'iid' not in msg:
            logger.warning(
                'received message that lacks `did` and `iid`: {}'.format(msg)
            )
            return

        if msg['command'] not in ['STATUS', 'ACK']:
            pid_ad = self.red.hget(self.rkey, 'ad')
            if pid_ad is None or pid_ad != self.pid:
                return

        if msg['command'] == 'STATUS':
            with rrdb.create_session_context() as session:
                try:
                    if 'did' in msg:
                        wd = (
                            session.query(rrdb.Deployment)
                            .filter(rrdb.Deployment.deploymentid == msg['did'])
                            .one_or_none()
                        )
                        if wd is not None:
                            wdeployment_id = wd.deploymentid
                    else:
                        wd = None
                    if 'iid' in msg:
                        inst = (
                            session.query(rrdb.Instance)
                            .filter(rrdb.Instance.instanceid == msg['iid'])
                            .one_or_none()
                        )
                        if inst is not None:
                            wdeployment_id = inst.deploymentid
                            instance_status = inst.status
                            instance_id = inst.instanceid
                            if instance_status == 'READY':
                                instance_hostkey = inst.hostkey
                            else:
                                instance_hostkey = None
                            if len(inst.associated_th) > 0:
                                current_th = {
                                    'id': inst.associated_th,
                                    'ipv4': inst.listening_ipaddr,
                                    'port': inst.listening_port,
                                }
                            else:
                                current_th = None
                        else:
                            instance_status = 'NONE'
                            current_th = None
                    else:
                        inst = None
                except Exception as err:
                    logger.error('caught {}: {}'.format(type(err), err))
                    return

            if inst is None and wd is None:
                logger.warning(
                    'no matching instance ({}) nor wdeployment ({})'.format(
                        msg.get('did', None), msg.get('iid', None)
                    )
                )
                return

            if not self.red.hexists(self.rkey, 'ad'):
                status = 'NONE'
            elif inst is None:
                status = 'NONE'
            else:
                status = instance_status
            payload = {
                'command': 'STATUS',
                'id': wdeployment_id,
                'status': status,
            }
            if inst is not None:
                payload['iid'] = instance_id
                if current_th is not None:
                    payload['thid'] = current_th['id']
                    payload['ipv4'] = current_th['ipv4']
                    payload['port'] = current_th['port']
                if instance_hostkey is not None:
                    payload['hostkey'] = instance_hostkey
            self.send_to_apiw(payload)

        elif msg['command'] == 'INSTANCE LAUNCH':
            if 'vpn' in msg:
                conntype = 'vpn' if msg['vpn'] else 'sshtun'
            else:
                conntype = 'sshtun'
            payload = {
                'v': 0,
                'cmd': 'INSTANCE_LAUNCH',
                'id': msg['iid'],
                'mi': msg['message_id'],
                'ct': conntype,
                'pr': msg['publickey'],
            }
            if 'repo_url' in msg:
                payload['repo'] = msg['repo_url']
            if 'repo_path' in msg:
                payload['repo_path'] = msg['repo_path']
            assert msg['message_id'] not in self.expected_resp
            self.expected_resp[msg['message_id']] = {
                'cmd': payload['cmd'],
                'instance_id': msg['iid'],
                'deployment_id': msg['did'],
                'conntype': conntype,
            }
            self.loop.create_task(self.ws_send(json.dumps(payload)))

        elif msg['command'] == 'INSTANCE DESTROY':
            payload = {
                'v': 0,
                'cmd': 'INSTANCE_DESTROY',
                'id': msg['iid'],
                'mi': msg['message_id'],
            }
            assert msg['message_id'] not in self.expected_resp
            self.expected_resp[msg['message_id']] = {
                'cmd': payload['cmd'],
                'instance_id': msg['iid'],
                'awsent': 'ACK',
            }
            self.loop.create_task(self.ws_send(json.dumps(payload)))

        elif msg['command'] == 'VPN NEWCLIENT':
            if 'message_id' not in msg:
                logger.warning(
                    'received `VPN NEWCLIENT` message that lacks `message_id`'
                )
                return
            # TODO
            res_msg = {
                'command': 'NACK',
                'req': 'VPN NEWCLIENT',
                'message_id': msg['message_id'],
                'desc': 'not implemented yet (hardshare server)',
            }
            self.send_to_apiw(res_msg)

        elif msg['command'] == 'ACK':
            if msg['did'] not in self.received_acks:
                logger.warning('received unexpected ACK to `NEW` status message')
            else:
                self.received_acks[self.wdeployment_id] = True

        else:
            raise ValueError('received unrecognized command: ' + str(msg['command']))

    async def heartbeat(self):
        while True:
            await asyncio.sleep(10)
            self.send_to_apiw(
                {
                    'command': 'HEARTBEAT',
                    'id': self.wdeployment_id,
                }
            )

    def send_status(self, instance_id):
        with rrdb.create_session_context() as session:
            inst = (
                session.query(rrdb.Instance)
                .filter(rrdb.Instance.instanceid == instance_id)
                .one_or_none()
            )
            if inst is None:
                logger.error(
                    'request to send status for unknown instance {}'.format(instance_id)
                )
                return
            wdeployment_id = inst.deploymentid
            instance_status = inst.status
            instance_id = inst.instanceid
            if instance_status == 'READY':
                instance_hostkey = inst.hostkey
            else:
                instance_hostkey = None
            if len(inst.associated_th) > 0:
                current_th = {
                    'id': inst.associated_th,
                    'ipv4': inst.listening_ipaddr,
                    'port': inst.listening_port,
                }
            else:
                current_th = None
        status = instance_status
        payload = {
            'command': 'STATUS',
            'id': wdeployment_id,
            'status': status,
        }
        payload['iid'] = instance_id
        if current_th is not None:
            payload['thid'] = current_th['id']
            payload['ipv4'] = current_th['ipv4']
            payload['port'] = current_th['port']
        if instance_hostkey is not None:
            payload['hostkey'] = instance_hostkey
        self.send_to_apiw(payload)


class ConnectionChannel:
    def __init__(
        self, deployment_id, ws_send, pid=None, rkey=None, red=None, event_loop=None
    ):
        self.wdeployment_id = deployment_id
        self.pid = pid
        self.rkey = rkey
        self.ws_send = ws_send
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.red = red
        self.host = settings.AMQP_HOST
        self.port = settings.AMQP_PORT
        self._queue_name = 'eatunnel.{}'.format(self.wdeployment_id)
        self.thportalq = None
        self.current_th = None
        self.thvpnq = asyncio.Queue()
        self.expected_resp = dict()
        self.channel = None
        self.associate_tasks = dict()  # instance_id => task
        self._closing = False
        self._consumer = None

    def start(self):
        param = pika.ConnectionParameters(host=self.host, port=self.port, heartbeat=20)
        self._conn = pika.adapters.asyncio_connection.AsyncioConnection(
            param,
            on_open_callback=self._on_connection,
            on_open_error_callback=self._on_open_error,
            on_close_callback=self._on_close,
            custom_ioloop=self.loop,
        )

    def _on_close(self, connection, err):
        if self._closing:
            return
        logger.warning(
            'connection to RabbitMQ server closed with {}: {}; restarting'.format(
                type(err), err
            )
        )
        self.start()

    def _on_open_error(self, connection, error_message=None):
        logger.warning('open error; attempting to re-connect to RabbitMQ server')
        time.sleep(1)
        self.start()

    def _on_connection(self, connection):
        self._conn.channel(on_open_callback=self._on_open_channel)

    def _on_open_channel(self, channel):
        self.channel = channel
        self.channel.queue_declare(
            queue=self._queue_name,
            callback=self._start_consuming,
            exclusive=False,
            auto_delete=True,
        )

    def _start_consuming(self, okframe):
        logger.info('starting to consume on queue {}'.format(self._queue_name))
        self.channel.add_on_cancel_callback(self.close_via_cancel)
        self._consumer = self.channel.basic_consume(
            queue=self._queue_name,
            on_message_callback=self.handle_incoming_message,
            auto_ack=True,
        )

    def is_open(self):
        try:
            if not self._conn.is_open:
                return False
        except:
            return False
        if self.channel is None:
            return False
        if not self.channel.is_open:
            return False
        return True

    def _close_channel(self, _unused_frame):
        self._consumer = None
        self.channel.close()
        if self._closing and not self._conn.is_closing and not self._conn.is_closed:
            self._conn.close()

    def _stop_consuming(self):
        if self._consumer is not None:
            self.channel.basic_cancel(self._consumer, self._close_channel)

    def close_via_cancel(self):
        if self.is_open():
            self._stop_consuming()

    def stop(self):
        self._closing = True
        if not self.is_open():
            return
        self._stop_consuming()

    def send_to_thportal(self, payload):
        self.channel.basic_publish(
            exchange='thportal', routing_key='', body=json.dumps(payload)
        )

    async def handle_response(self, msg):
        logger.debug('handle_response(msg={})'.format(msg))
        if 'mi' not in msg or not isinstance(msg['mi'], str):
            logger.error(
                'ConnectionChannel.handle_response: '
                'given message does not have required ID'
            )
            return
        if 'cmd' not in msg or msg['cmd'] not in ['ACK', 'NACK']:
            logger.error(
                'ConnectionChannel.handle_response: '
                'given message does not have required `cmd`'
            )
            return
        try:
            v = self.expected_resp.pop(msg['mi'])
        except:
            for k in ['mi', 'cmd']:
                if len(msg[k]) > 64:
                    msg[k] = msg[k][:64]
            logger.warning(
                'unexpected response message {} (id: {})'.format(msg['cmd'], msg['mi'])
            )
            return

        if v['cmd'] == 'TH_PING':
            logger.debug('handling response to TH_PING, sending {}'.format(msg['cmd']))
            self.send_to_thportal(
                {
                    'command': msg['cmd'],
                    'id': msg[
                        'id'
                    ],  # TODO: SECURITY: can client forge wdeployment here?
                    'thid': msg['thid'],
                    'inid': v['inid'],
                }
            )

        else:
            logger.warning('received response for unknown command {}'.format(v['cmd']))

    def handle_incoming_message(self, channel, method, prop, body):
        logger.debug('received on channel thportal: {}'.format(body))
        msg = json.loads(str(body, encoding='utf-8'))
        if 'did' not in msg or msg['did'] != self.wdeployment_id:
            return

        pid_ad = self.red.hget(self.rkey, 'ad')
        if pid_ad is None or pid_ad != self.pid:
            return

        if 'command' not in msg:
            logger.warning('received message that lacks `command`')
            return

        if msg['command'] == 'BID':
            if self.thportalq is None:
                logger.warning(
                    'received unexpected BID message from {} on thportal channel'.format(
                        msg['id']
                    )
                )
                if msg['inid'] in self.associate_tasks:
                    self.associate_tasks[msg['inid']].cancel()
                    del self.associate_tasks[msg['inid']]
            else:
                logger.debug(
                    'received BID message from {} on thportal channel'.format(msg['id'])
                )
                self.thportalq.put_nowait(msg)

        elif msg['command'] == 'ACCEPT':
            if self.thportalq is None:
                logger.warning(
                    'received unexpected ACCEPT message from {} on thportal channel'.format(
                        msg['id']
                    )
                )
                if msg['inid'] in self.associate_tasks:
                    self.associate_tasks[msg['inid']].cancel()
                    del self.associate_tasks[msg['inid']]
            else:
                logger.debug(
                    'received ACCEPT message from {} on thportal channel'.format(
                        msg['id']
                    )
                )
                self.thportalq.put_nowait(msg)

        elif (msg['command'] == 'ACK' or msg['command'] == 'NACK') and (
            'req' in msg and msg['req'][:3] == 'VPN'
        ):
            logger.debug('received VPN-related message: {}'.format(msg))
            self.thvpnq.put_nowait(msg)

        elif msg['command'] == 'CHECK ASSOCIATED':
            with rrdb.create_session_context() as session:
                try:
                    inst = (
                        session.query(rrdb.Instance)
                        .filter(
                            rrdb.Instance.deploymentid == self.wdeployment_id,
                            rrdb.Instance.status != 'TERMINATING',
                            rrdb.Instance.status != 'TERMINATED',
                        )
                        .order_by(sqlalchemy.desc(rrdb.Instance.starttime))
                        .first()
                    )
                    instance_id = inst.instanceid if inst is not None else None
                except Exception as err:
                    logger.warning('caught {}: {}'.format(type(err), err))
                    instance_id = None
            if instance_id is None or ('inid' in msg and instance_id != msg['inid']):
                payload = {
                    'command': 'NACK',
                    'id': self.wdeployment_id,
                    'thid': msg['id'],
                }
                if 'inid' in msg:
                    payload['inid'] = msg['inid']
                self.send_to_thportal(payload)
                return

            # TODO: also send expected instance to client
            message_id = str(uuid.uuid4())
            assert message_id not in self.expected_resp
            self.expected_resp[message_id] = {
                'cmd': 'TH_PING',
                'thid': msg['id'],
                'inid': instance_id,
            }
            self.loop.create_task(
                self.ws_send(
                    json.dumps(
                        {
                            'v': 0,
                            'cmd': 'TH_PING',
                            'thid': msg['id'],
                            'id': self.wdeployment_id,
                            'inid': instance_id,
                            'mi': message_id,
                        }
                    )
                )
            )

        else:
            logger.warning('received unrecognized command: ' + str(msg['command']))
            return

    async def associate_tunnelhub(
        self, ws_send, instance_id, message_id, conntype, publickey
    ):
        assert self.thportalq is None
        self.thportalq = asyncio.Queue()
        TIMEOUT = 60  # s
        ACCEPT_TIMEOUT = 10  # seconds, waiting for ACCEPT after REQUEST
        outstanding_request = None
        request_sent_time = None
        start_t = time.monotonic()
        while time.monotonic() - start_t < TIMEOUT:
            if outstanding_request is not None:
                while self.thportalq.empty() and (
                    time.time() - request_sent_time <= ACCEPT_TIMEOUT
                ):
                    await asyncio.sleep(1)
                if self.thportalq.empty():
                    logger.info(
                        'timed out waiting for ACCEPT from {ESENDER} to my REQUEST; queue size is {QSIZE}'.format(
                            ESENDER=outstanding_request,
                            QSIZE=self.thportalq.qsize(),
                        )
                    )
                    outstanding_request = None
                    continue
                else:
                    msg = await self.thportalq.get()

            elif self.thportalq.empty():
                logger.info('searching for tunnel hub to use...')
                self.send_to_thportal(
                    {
                        'command': 'SEARCH',
                        'id': self.wdeployment_id,
                        'inid': instance_id,
                        'region': '',  # TODO
                    }
                )
                await asyncio.sleep(2)
                continue

            else:
                msg = await self.thportalq.get()

            if msg['command'] == 'BID':
                if outstanding_request is not None:
                    logger.warning('received superfluous BID message')
                    continue
                self.send_to_thportal(
                    {
                        'command': 'REQUEST',
                        'id': self.wdeployment_id,
                        'inid': instance_id,
                        'thid': msg['id'],
                        'mode': conntype,
                        'pubkey': publickey,
                    }
                )
                outstanding_request = msg['id']
                request_sent_time = time.time()

            elif msg['command'] == 'ACCEPT':
                if outstanding_request is not None and msg['id'] != outstanding_request:
                    logger.warning(
                        'received unsolicited ACCEPT message from {RECEIVED}, instead of {EXPECTED}'.format(
                            RECEIVED=msg['id'], EXPECTED=outstanding_request
                        )
                    )
                    continue
                if 'thport' not in msg:
                    thport = 22
                    thuser = 'rrea'
                else:
                    thport = msg['thport']
                    thuser = 'root'
                self.current_th = {
                    'thid': msg['id'],
                    'ipv4': msg['ipv4'],
                    'hostkey': msg['hostkey'],
                    'port': msg['port'],
                    'th_infra_port': thport,
                    'thuser': thuser,
                }
                outstanding_request = None
                logger.info(
                    'established new assocation with ' 'tunnel hub {}'.format(
                        self.current_th['thid']
                    )
                )
                self.thportalq = None
                with rrdb.create_session_context() as session:
                    inst = (
                        session.query(rrdb.Instance)
                        .filter(rrdb.Instance.instanceid == instance_id)
                        .one()
                    )
                    inst.associated_th = self.current_th['thid']
                    inst.listening_ipaddr = self.current_th['ipv4']
                    inst.listening_port = self.current_th['port']
                await ws_send(
                    json.dumps(
                        {
                            'v': 0,
                            'cmd': 'TH_ACCEPT',
                            'mi': message_id,
                            'id': instance_id,
                            'thid': self.current_th['thid'],
                            'ipv4': self.current_th['ipv4'],
                            'hostkey': self.current_th['hostkey'],
                            'port': self.current_th['port'],
                            'thport': self.current_th['th_infra_port'],
                            'thuser': self.current_th['thuser'],
                        }
                    )
                )
                return

        logger.warning('timed out')

    async def create_vpn(self):
        logger.info('requesting VPN for this instance...')
        TIMEOUT = 300
        message_ids = []
        for attempt in range(5):
            message_ids.append(str(uuid.uuid4()))
            self.send_to_thportal(
                {
                    'command': 'VPN CREATE',
                    'id': self.wdeployment_id,
                    'thid': self.current_th['thid'],
                    'message_id': message_ids[-1],
                }
            )
            sent_time = time.time()
            confirmed = False
            while time.time() - sent_time < TIMEOUT:
                await asyncio.sleep(5)
                if not self.thvpnq.empty():
                    msg = await self.thvpnq.get()
                    if 'message_id' in msg and msg['message_id'] in message_ids:
                        message_ids.remove(msg['message_id'])
                        confirmed = msg['command'] == 'ACK'
                        if (
                            (not confirmed)
                            and ('desc' in msg)
                            and msg['desc'].startswith('there is already VPN')
                        ):
                            pass
                        else:
                            break
                    else:
                        await self.thvpnq.put(msg)
            if confirmed:
                logger.info('VPN created!')
                break
            else:
                logger.warning('failed to create VPN. trying again...')
        if not confirmed:
            self.status = 'INIT_FAIL'
            logger.info('marked instance as {}'.format(self.status))
            return

    async def get_vpn_newclient(self):
        logger.info(
            'requesting new client credentials for one of the hosts in this instance...'
        )
        TIMEOUT = 20
        for attempt in range(5):
            msg_id = str(uuid.uuid4())
            self.send_to_thportal(
                {
                    'command': 'VPN NEWCLIENT',
                    'id': self.wdeployment_id,
                    'thid': self.current_th['thid'],
                    'message_id': msg_id,
                }
            )
            sent_time = time.time()
            confirmed = False
            while time.time() - sent_time < TIMEOUT:
                await asyncio.sleep(5)
                if not self.thvpnq.empty():
                    msg = await self.thvpnq.get()
                    if 'message_id' in msg and msg['message_id'] == msg_id:
                        confirmed = msg['command'] == 'ACK'
                        break
                    else:
                        await self.thvpnq.put(msg)
            if confirmed:
                logger.info('new VPN client credentials received!')
                break
            else:
                logger.warning('failed to make new VPN client. trying again...')
        if not confirmed:
            self.status = 'INIT_FAIL'
            logger.info('marked instance as {}'.format(self.status))
            return
