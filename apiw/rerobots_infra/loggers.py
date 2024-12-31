#!/usr/bin/env python
"""logging tools, log handlers


SCL <scott@rerobots>
Copyright (C) 2019 rerobots, Inc.
"""

import logging
import json
import socket
import time


class RLogSenderHandler(logging.Handler):
    def __init__(self, agent_type, agent_id, port=6666, host='127.0.0.1'):
        assert agent_type in ['aw', 'th', 'ea', 'proxy']
        super().__init__()
        self._to = (host, port)
        self._conn = None
        self.agent_type = agent_type
        self.agent_id = agent_id
        self.__reconnect()

    def __reconnect(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except:
                pass
        self._conn = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)

    def emit(self, record):
        rf = self.format(record)
        body = json.dumps(
            {
                'id': self.agent_id,
                'kind': self.agent_type,
                'lvl': record.levelname,
                'msg': rf,
                'ts': str(time.time()),
            }
        )
        rawbody = (body + '\n').encode()
        try:
            self._conn.sendto(rawbody, self._to)
        except:
            self.__reconnect()
            try:
                self._conn.sendto(rawbody, self._to)
            except:
                time.sleep(3)
                self.__reconnect()
                self._conn.sendto(rawbody, self._to)

    def close(self):
        self._conn.close()
        super().close()
