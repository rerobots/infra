# docker build -t rerobots-infra/http-proxy -f Dockerfile .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2023 rerobots, Inc.

FROM debian:stable

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install \
       nginx

COPY empty-nginx.conf /etc/nginx/nginx.conf

ENTRYPOINT ["/usr/sbin/nginx", "-g", "daemon off;"]
