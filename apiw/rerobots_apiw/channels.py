"""
SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

import asyncio
from datetime import datetime
import json
import logging
import time

import pika
import pika.adapters.asyncio_connection
import redis

from . import db as rrdb
from . import tasks, tunnel_hub_tasks
from . import settings

if settings.DEBUG:
    from rerobots_infra import RerobotsQChannel as RerobotsChannel
else:
    from rerobots_infra import RerobotsChannel


logger = logging.getLogger(__name__)


class PortAccessManager(RerobotsChannel):
    def __init__(self, basename='portaccess', event_loop=None):
        self.red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )
        super().__init__(
            basename=basename,
            agenttype='aw',
            host=settings.AMQP_HOST,
            port=settings.AMQP_PORT,
            event_loop=event_loop,
        )

    def send_command(self, msg):
        self.send_message('th', msg)

    def handle_incoming_message(self, channel, method, prop, body):
        logger.debug('received on channel portaccess: {}'.format(body))
        msg = json.loads(str(body, encoding='utf-8'))
        assert 'message_id' in msg
        if msg['command'] == 'NACK':
            logging.error('received NACK for message {}'.format(msg['message_id']))
        else:
            self.red.set(msg['message_id'], str(msg['rules']))
            self.red.expire(msg['message_id'], 60)


class EACommandChannel:
    def __init__(self, event_loop=None):
        self.red = redis.StrictRedis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0
        )
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.host = settings.AMQP_HOST
        self.port = settings.AMQP_PORT
        self._queue_name = 'eacommand_rx'
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
        channel.add_on_close_callback(self._on_channel_close)
        channel.add_on_cancel_callback(self.close_via_cancel)
        self.channel.queue_declare(
            queue=self._queue_name, callback=self._start_consuming
        )

    def _start_consuming(self, okframe):
        logger.info('starting to consume on queue {}'.format(self._queue_name))
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

    def _on_channel_close(self, chan, exc):
        if self._closing and not self._conn.is_closing and not self._conn.is_closed:
            logger.info(f'channel closed with {type(exc)}: {exc}')
            self._conn.close()
        else:
            logger.error(f'channel closed with {type(exc)}: {exc}')
            self.restart_channel()

    def _on_cancel_consume_ok(self, _unused_frame):
        self._consumer = None
        self.channel.close()

    def _stop_consuming(self):
        if self._consumer is not None:
            self.channel.basic_cancel(self._consumer, self._on_cancel_consume_ok)

    def close_via_cancel(self):
        if self.is_open():
            self._stop_consuming()

    def restart_channel(self):
        if self.is_open():
            self._stop_consuming()
        self.start()

    def stop(self):
        self._closing = True
        if not self.is_open():
            return
        self._stop_consuming()

    def send_to_wd(self, wdeployment_id, payload):
        self.channel.basic_publish(
            exchange=f'eacommand.{wdeployment_id}',
            routing_key='',
            body=json.dumps(payload),
        )

    def handle_incoming_message(self, channel, method, prop, body):
        logger.debug('received: {}'.format(body))
        msg = json.loads(str(body, encoding='utf-8'))
        assert 'command' in msg
        self.process_rx(msg)

    def process_rx(self, msg):
        if 'req' in msg:
            if msg['req'] == 'VPN NEWCLIENT':
                self.red.hset(msg['message_id'], 'result', msg['command'])
                if msg['command'] == 'ACK':
                    self.red.hset(msg['message_id'], 'client_id', msg['client_id'])
                    self.red.hset(msg['message_id'], 'ovpn_config', msg['ovpn'])
            elif msg['req'] == 'INSTANCE LAUNCH':
                if (
                    self.red.exists(msg['message_id'])
                    and self.red.type(msg['message_id']) != b'hash'
                ):
                    self.red.delete(msg['message_id'])
                self.red.hset(msg['message_id'], 'result', msg['command'])
                if 'st' in msg:
                    self.red.hset(msg['message_id'], 'st', msg['st'])
            elif msg['req'] == 'INSTANCE DESTROY':
                if (
                    self.red.exists(msg['message_id'])
                    and self.red.type(msg['message_id']) != b'hash'
                ):
                    self.red.delete(msg['message_id'])
                self.red.hset(msg['message_id'], 'result', msg['command'])
                if 'st' in msg:
                    self.red.hset(msg['message_id'], 'st', msg['st'])
            else:
                logger.warning('unknown req "{}"'.format(msg['req']))
                self.red.set(msg['message_id'], msg['command'])
            self.red.expire(msg['message_id'], 30)

        elif msg['command'] == 'NEW':
            tasks.create_new_wdeployment.delay(wdeployment_id=msg['id'], config=msg)
            payload = {
                'command': 'ACK',
                'did': msg['id'],
            }
            if 'message_id' in msg:
                payload['message_id'] = msg['message_id']
            self.send_to_wd(msg['id'], payload)

        elif msg['command'] == 'UPDATE':
            tasks.update_wdeployment.delay(wdeployment_id=msg['id'], config=msg)
            payload = {
                'command': 'ACK',
                'did': msg['id'],
            }
            self.send_to_wd(msg['id'], payload)

        elif msg['command'] == 'STATUS':
            with rrdb.create_session_context() as session:
                instance = (
                    session.query(rrdb.Instance)
                    .filter(rrdb.Instance.instanceid == msg['iid'])
                    .one_or_none()
                )
                if instance is not None:
                    if (
                        instance.status == 'INIT_FAIL'
                        or (
                            instance.status == 'TERMINATED'
                            and msg['status'] != 'TERMINATED'
                        )
                        or (
                            instance.status == 'TERMINATING'
                            and msg['status'] not in ['TERMINATING', 'TERMINATED']
                        )
                    ):
                        logger.warning(
                            'for instance {} received status update {} when db shows status as {}'.format(
                                msg['iid'], msg['status'], instance.status
                            )
                        )
                        return

                    instance.status = msg['status']
                    if instance.status == 'READY' and instance.ready_at is None:
                        instance.ready_at = datetime.utcnow()
                    if 'ipv4' in msg:
                        instance.listening_ipaddr = msg['ipv4']
                        if 'port' in msg:
                            instance.listening_port = int(msg['port'])

            self.red.hset('instance:' + msg['iid'], 'updated', int(time.time()))
            self.red.hset('instance:' + msg['iid'], 'status', msg['status'])
            if 'nbhd' in msg:
                self.red.hset('instance:' + msg['iid'], 'nbhd', json.dumps(msg['nbhd']))
            for entry in ['thid', 'port', 'ipv4', 'hostkey']:
                if entry in msg:
                    self.red.hset('instance:' + msg['iid'], entry, msg[entry])
                elif self.red.hexists('instance:' + msg['iid'], entry):
                    self.red.hdel('instance:' + msg['iid'], entry)

        elif msg['command'] == 'HEARTBEAT':
            logger.debug('received heartbeat from wdeployment {}'.format(msg['id']))
            with rrdb.create_session_context() as session:
                wd = (
                    session.query(rrdb.Deployment)
                    .filter(rrdb.Deployment.deploymentid == msg['id'])
                    .one_or_none()
                )
                if wd is None:
                    logger.warning('unknown wdeployment {}'.format(msg['id']))
                else:
                    wd.last_heartbeat = datetime.utcnow()

        elif msg['command'] == 'LOCKOUT':
            logger.debug('received lockout from wdeployment {}'.format(msg['id']))
            with rrdb.create_session_context() as session:
                wd = (
                    session.query(rrdb.Deployment)
                    .filter(rrdb.Deployment.deploymentid == msg['id'])
                    .one_or_none()
                )
                if wd is None:
                    logger.warning('unknown wdeployment {}'.format(msg['id']))
                else:
                    wd.locked_out = True

        elif msg['command'] == 'CREATE SSHTUN':
            logger.debug('received create-sshtun for instance {}'.format(msg['id']))
            tunnel_hub_tasks.create_sshtun.delay(
                instance_id=msg['id'], pubkey=msg['pubkey'], request_id=msg['rid']
            )

        else:
            self.red.set(msg['message_id'], msg['command'])
            self.red.expire(msg['message_id'], 30)
