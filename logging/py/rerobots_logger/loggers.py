#!/usr/bin/env python
"""logger objects for the daemon


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import signal
import time

import pika
import pika.adapters.asyncio_connection
import requests

from . import db


logger = logging.getLogger(__name__)


class RerobotsLogger:
    """receives logs from remote agents in rerobots infrastructure"""

    def __init__(self, host='127.0.0.1', event_loop=None):
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.host = host
        self.counter_of_start = 0
        self.channel = None

    def inc_start_counter(self):
        logger.debug('in RerobotsLogger.inc_start_counter()')
        self.counter_of_start += 1

    def start(self):
        logger.debug('in RerobotsLogger.start()')
        self.inc_start_counter()
        param = pika.ConnectionParameters(host=self.host, heartbeat=20)
        self._conn = pika.adapters.asyncio_connection.AsyncioConnection(
            param,
            on_open_callback=self._on_connection,
            on_open_error_callback=self._on_open_error,
            on_close_callback=self._on_close,
            custom_ioloop=self.loop,
        )

    def _on_close(self, connection, err):
        logger.error(
            'connection to RabbitMQ server closed with {}: {}'.format(type(err), err)
        )
        if self.counter_of_start > 5:
            logger.error(
                'RerobotsLogger: too many restarts (attempts) of connection to RabbitMQ server'
            )
            os.kill(os.getpid(), signal.SIGABRT)
        time.sleep(2)
        logger.info('RerobotsLogger: attempting to re-connect to RabbitMQ server')
        self.start()

    def _on_open_error(self, connection, error_message=None):
        if self.counter_of_start > 5:
            logger.error(
                'RerobotsLogger: too many restarts (attempts) of connection to RabbitMQ server'
            )
            os.kill(os.getpid(), signal.SIGABRT)
        time.sleep(2)
        logger.info('RerobotsLogger: attempting to re-connect to RabbitMQ server')
        self.start()

    def _on_connection(self, connection):
        self._conn.channel(on_open_callback=self._on_open_channel)

    def _on_open_channel(self, channel):
        self.channel = channel
        self._queue_name = 'logger'
        self.channel.queue_declare(
            queue=self._queue_name, callback=self._start_consuming
        )
        logger.info('RerobotsLogger: opened new channel')

    def _bind_queue(self, okframe):
        self._queue_name = okframe.method.queue
        logger.info(
            'RerobotsLogger: queue declared with name {}'.format(self._queue_name)
        )
        self.channel.queue_bind(
            queue=self._queue_name,
            exchange='',
            routing_key='logger',
            callback=self._start_consuming,
        )

    def _start_consuming(self, okframe):
        logger.info(
            'RerobotsLogger: starting to consume on channel with queue {}'.format(
                self._queue_name
            )
        )
        self.channel.add_on_cancel_callback(self.channel.close)
        self.channel.basic_consume(
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

    def cancel(self):
        self.channel.basic_cancel()

    def handle_incoming_message(self, channel, method, prop, body):
        try:
            payload = json.loads(body.decode())
            now = datetime.fromtimestamp(time.time(), timezone.utc)
            ts_from_agent = datetime.fromtimestamp(float(payload['ts']), timezone.utc)
            args = {
                'agent_id': payload['id'],
                'agent_kind': payload['kind'],
                'message_level': payload['lvl'],
                'message': payload['msg'],
                'timestamp_from_agent': ts_from_agent,
                'timestamp': now,
            }
            with db.create_session_context() as session:
                session.add(db.Log(**args))
        except:
            logger.error('failure to save remote log message: {}'.format(body))
            return
        try:
            args['timestamp_from_agent'] = ts_from_agent.isoformat()
            args['timestamp'] = now.isoformat()
            uri = 'http://127.0.0.1:9200/rerobots-logs/rra'
            res = requests.post(uri, json=args)
            if not res.ok:
                logger.error(
                    'response to uri: {}: {}'.format(res.status_code, res.text)
                )
        except:
            logger.error(
                'exception caught while trying to post new rra log entry to Elasticsearch cluster'
            )
