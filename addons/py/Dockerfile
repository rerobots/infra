# docker build -t rerobots-addons/py -f Dockerfile .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2020 rerobots, Inc.

FROM debian:stable

RUN apt-get update \
    && apt-get -y install \
       python3 \
       python3-pip

RUN ln -s /usr/bin/python3 /usr/bin/python \
    && rm /usr/bin/pip && ln -s /usr/bin/pip3 /usr/bin/pip

RUN pip install --break-system-packages aiohttp

# Packages that a user might want
RUN pip install --break-system-packages requests
RUN pip install --break-system-packages websocket-client

COPY cmdp /root/cmdp

ENTRYPOINT ["/usr/bin/python3", "/root/cmdp"]
