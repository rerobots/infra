#!/usr/bin/env python
"""templates for channels


SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import asyncio
import functools
import json
import logging
import time

import pika
import pika.adapters.asyncio_connection


logger = logging.getLogger(__name__)


class RerobotsQChannel:
    """base class for defining entirely local, Queue-based messaging channels

    The motivating use-case is testing without a RabbitMQ server, and
    indeed, there are extra methods here to support tests.

    The API is sufficiently similar to RerobotsChannel such that
    drop-in replacement is feasible. Development as a separate class,
    instead of a switch when constructing RerobotsChannel, is to
    cleanly separate code.
    """

    def __init__(
        self,
        basename,
        agenttype,
        host='127.0.0.1',
        port=5672,
        event_loop=None,
        allow_agenttypes=None,
        callback_handle_incoming=None,
    ):
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.basename = basename
        self.agenttype = agenttype
        self.allow_agenttypes = allow_agenttypes
        self.host = host
        self.port = port
        self.inq = asyncio.Queue()
        self.outq = asyncio.Queue()
        self.__active = False
        self.__sender = None
        assert callback_handle_incoming is None or callable(callback_handle_incoming)
        self.callback_handle_incoming = callback_handle_incoming

    def start(self):
        self.__active = True
        if self.__sender is None:
            self.__sender = self.loop.create_task(self.__pass_incoming())

    def stop(self):
        self.__active = False
        if self.__sender is not None:
            self.__sender.cancel()
            self.__sender = None

    def is_open(self):
        return self.__active

    def get_exchange_name(self, agenttype=None):
        """Generate exchange name, depending on agent type

        This method is a copy of get_exchange_name() of RerobotsChannel.
        """
        if agenttype is None:
            return self.basename + '.' + self.agenttype
        else:
            return self.basename + '.' + agenttype

    def handle_incoming_message(self, channel, method, prop, body):
        raise NotImplementedError('Derived classes should define this method.')

    async def send_message_task(self, for_agenttype, msg):
        await self.outq.put(msg)

    def send_message(self, for_agenttype, msg):
        self.loop.create_task(self.send_message_task(for_agenttype, msg))

    async def __pass_incoming(self):
        try:
            exname = self.get_exchange_name()
            if self.callback_handle_incoming:
                cb = self.callback_handle_incoming
            else:
                cb = self.handle_incoming_message
            while True:
                body = await self.inq.get()
                cb(exname, None, None, body)
        except asyncio.CancelledError:
            return

    def inject_recv_sync(self, body):
        self.loop.create_task(self.inject_recv(body))

    async def inject_recv(self, body):
        body = json.dumps(body)
        body = bytes(body, encoding='utf-8')
        await self.inq.put(body)

    async def message_pusher(self):
        while True:
            body = await self.inq.get()
            self.handle_incoming_message(
                channel=None, method=None, prop=None, body=body
            )


class RerobotsChannel:
    """base class for defining rerobots messaging channels

    The channel name is formed by appending `agenttype` to `basename`
    with dot-separation. Possible values of `agenttype` are 'aw',
    'ea', and 'th', which abbreviates 'APIW', 'endpoint arbiter', and
    'tunnel hub', as defined in the protocol specifications. Agent
    types can be modified by the parameter `allow_agenttypes`,
    including the addition of new types.

    When instantiating a channel, the `agenttype` should correspond to
    agent to which the instance is attached. E.g., the tunnel hub
    daemon on the thportal channel would use `agenttype='th'`,
    resulting in the channel name 'thportal.th'.
    """

    def __init__(
        self,
        basename,
        agenttype,
        host='127.0.0.1',
        port=5672,
        event_loop=None,
        allow_agenttypes=None,
    ):
        if allow_agenttypes is None:
            self.allow_agenttypes = ['aw', 'th', 'ea']
        else:
            self.allow_agenttypes = allow_agenttypes
        assert agenttype in self.allow_agenttypes
        if event_loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = event_loop
        self.basename = basename
        self.agenttype = agenttype
        self.host = host
        self.port = port
        self._conn = None
        self.channels = dict()
        self._closing = False
        self._consumers = dict()

    def start(self):
        """Start AMQP connection"""
        logger.debug('in RerobotsChannel.start()')
        param = pika.ConnectionParameters(host=self.host, port=self.port, heartbeat=20)
        self._conn = pika.adapters.asyncio_connection.AsyncioConnection(
            param,
            on_open_callback=self._on_connection,
            on_open_error_callback=self._on_open_error,
            on_close_callback=self._on_conn_close,
            custom_ioloop=self.loop,
        )

    def get_exchange_name(self, agenttype=None):
        """Generate exchange name, depending on agent type

        if agenttype is None (default), use self.agenttype.
        """
        if agenttype is None:
            return self.basename + '.' + self.agenttype
        return self.basename + '.' + agenttype

    def _on_conn_close(self, connection, err):
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
        for agenttype in self.allow_agenttypes:
            on_open = functools.partial(self._on_open_channel, agenttype)
            self._conn.channel(on_open_callback=on_open)

    def _on_open_channel(self, for_agenttype, channel):
        self.channels[for_agenttype] = channel
        channel.add_on_close_callback(self._on_channel_close)
        channel.add_on_cancel_callback(self.close_via_cancel)
        if for_agenttype == self.agenttype:
            exchange_name = self.get_exchange_name()
            self.channels[self.agenttype].exchange_declare(
                exchange=exchange_name,
                exchange_type=pika.exchange_type.ExchangeType.fanout,
                callback=self._declare_queue,
            )
        else:
            exchange_name = self.get_exchange_name(for_agenttype)
            self.channels[for_agenttype].exchange_declare(
                exchange=exchange_name,
                exchange_type=pika.exchange_type.ExchangeType.fanout,
            )
        logger.info('opened new channel, declaring exchange: {}'.format(exchange_name))

    def _declare_queue(self, okframe):
        self.channels[self.agenttype].queue_declare(
            '', exclusive=True, callback=self._bind_queue
        )

    def _bind_queue(self, okframe):
        self._queue_name = okframe.method.queue
        logger.info('queue declared with name {}'.format(self._queue_name))
        self.channels[self.agenttype].queue_bind(
            exchange=self.get_exchange_name(),
            queue=self._queue_name,
            callback=self._start_consuming,
        )

    def _start_consuming(self, okframe):
        logger.info(
            'starting to consume on channel with queue {}'.format(self._queue_name)
        )
        time.sleep(10)
        self._consumers[self.agenttype] = self.channels[self.agenttype].basic_consume(
            queue=self._queue_name,
            on_message_callback=self.handle_incoming_message,
            auto_ack=True,
        )

    def send_message(self, for_agenttype, msg):
        """send message

        msg should be a `dict` object. it will be sent in JSON.

        for_agenttype is one of 'th', 'ea', 'aw', etc., as defined in the
        protocol specifications.
        """
        logger.debug(
            'on channel {CHANNEL}, '
            'sending to agent type {AGENTTYPE} '
            'message: {MESSAGE}'.format(
                CHANNEL=self.basename, AGENTTYPE=for_agenttype, MESSAGE=msg
            )
        )
        assert for_agenttype != self.agenttype
        self.channels[for_agenttype].basic_publish(
            exchange=self.get_exchange_name(for_agenttype),
            routing_key='',
            body=json.dumps(msg),
        )

    def is_open(self):
        try:
            if not self._conn.is_open:
                return False
        except:
            return False
        if len(self.channels) == 0:
            return False
        for agenttype, channel in self.channels.items():
            if not channel.is_open:
                return False
        return True

    def is_closed(self):
        try:
            if not self._conn.is_closed:
                return False
        except:
            pass
        return True

    def handle_incoming_message(self, channel, method, prop, body):
        raise NotImplementedError('Derived classes should define this method.')

    def _any_channels_open(self):
        for agenttype, channel in self.channels.items():
            if channel.is_open:
                return True
        return False

    def _any_consumers(self):
        for agenttype, consumer_tag in self._consumers.items():
            if consumer_tag is not None:
                return True
        return False

    def _on_channel_close(self, chan, exc):
        logger.info('channel closed with {}: {}'.format(type(exc), exc))
        if not self._any_channels_open():
            if self._closing and not self._conn.is_closing and not self._conn.is_closed:
                self._conn.close()

    def _on_cancel_consume_ok(self, _unused_frame, agenttype):
        self._consumers[agenttype] = None
        if self.channels[agenttype].is_open:
            self.channels[agenttype].close()

    def _stop_consuming(self):
        for agenttype, consumer_tag in self._consumers.items():
            if consumer_tag is None:
                continue
            if self.channels[agenttype].is_open:
                on_cancel_consume_ok = functools.partial(
                    self._on_cancel_consume_ok, agenttype=agenttype
                )
                self.channels[agenttype].basic_cancel(
                    consumer_tag, on_cancel_consume_ok
                )

    def _close_channels(self):
        for agenttype, channel in self.channels.items():
            if agenttype not in self._consumers and channel.is_open:
                channel.close()
        self._stop_consuming()

    def close_via_cancel(self, _unused_frame):
        if self._conn is None or not self._conn.is_open:
            return
        self._close_channels()

    def stop(self):
        self._closing = True
        if not self.is_open():
            return
        self._close_channels()
