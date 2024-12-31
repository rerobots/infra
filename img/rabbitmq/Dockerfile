# docker build -t rerobots/ext/rabbitmq .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2022 rerobots, Inc.

FROM ubuntu:22.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install \
       rabbitmq-server

COPY rabbitmq-env.conf /etc/rabbitmq/rabbitmq-env.conf
COPY rabbitmq.conf /etc/rabbitmq/rabbitmq.conf
COPY rabbitmq-definitions.json /etc/rabbitmq/rabbitmq-definitions.json


CMD ["/usr/sbin/rabbitmq-server"]
