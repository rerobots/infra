# docker build -t rerobots/ext/postgres .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2022 rerobots, Inc.

FROM ubuntu:22.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install \
       postgresql

COPY pg_hba.conf /etc/postgresql/14/main/pg_hba.conf


CMD ["/usr/bin/pg_ctlcluster", "14", "main", "start", "-o", "-h 0.0.0.0", "--foreground"]
