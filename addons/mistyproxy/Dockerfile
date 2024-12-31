# docker build -t rerobots-addons/mistyproxy -f Dockerfile .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2018 rerobots, Inc.

FROM debian:stable

RUN apt-get update \
    && apt-get -y install \
       python3 \
       nginx \
       ssh

RUN ln -s /usr/bin/python3 /usr/bin/python

COPY genconfig.py /root/genconfig.py

ENTRYPOINT ["/root/genconfig.py"]
