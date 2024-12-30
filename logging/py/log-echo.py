#!/usr/bin/env python
"""
SCL <scott@rerobots>
Copyright (C) 2022 rerobots, Inc.
"""

import socketserver
import sys

import pika


class UEcho(socketserver.BaseRequestHandler):
    def __init__(self, *args, **kwargs):
        self.__amqp_conn = pika.BlockingConnection()
        self.__amqp_chan = self.__amqp_conn.channel()
        self.__pika_properties = pika.BasicProperties(
            content_type='text/plain', delivery_mode=1
        )
        super().__init__(*args, **kwargs)

    def amqp_reconnect(self):
        self.__amqp_conn = pika.BlockingConnection()
        self.__amqp_chan = self.__amqp_conn.channel()

    def amqp_send(self, body):
        self.__amqp_chan.basic_publish(
            exchange='',
            routing_key='logger',
            body=body,
            properties=self.__pika_properties,
        )

    def handle(self):
        body = self.request[0].decode('utf-8')
        try:
            self.amqp_send(body)
        except:
            self.amqp_reconnect()
            self.amqp_send(body)


def main_cli(argv=None):
    if argv is None:
        argv = sys.argv

    if len(argv) != 1:
        sys.exit(1)

    listen_port = int(argv[0])

    server = socketserver.UDPServer(('127.0.0.1', listen_port), UEcho)
    server.serve_forever()


if __name__ == '__main__':
    sys.exit(main_cli(sys.argv[1:]))
