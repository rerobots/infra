# docker build -t rerobots/ext/redis .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2022 rerobots, Inc.

FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install \
       redis


CMD ["redis-server", "--protected-mode", "no", "--bind", "0.0.0.0"]
