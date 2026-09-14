# docker build -t rerobots/infra/base -f Dockerfile .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2019 rerobots, Inc.

FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y install \
       build-essential \
       pkg-config \
       libssl-dev \
       redis \
       postgresql \
       libpq-dev \
       python3.12-dev \
       python3-pip \
       pipenv \
       curl \
       git

RUN ln -s /usr/bin/python3.12 /usr/bin/python

RUN pg_ctlcluster 16 main start \
    && su postgres sh -c 'echo "CREATE ROLE rra LOGIN;" | psql' \
    && su postgres sh -c 'createdb -O rra rrdb' \
    && su postgres sh -c 'createdb -O rra rrlogsdb' \
    && pg_ctlcluster 16 main stop

CMD ["/bin/bash", "-l"]
