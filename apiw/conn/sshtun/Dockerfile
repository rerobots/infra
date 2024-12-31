# docker build -t rerobots-infra/th-sshtunnel -f Dockerfile .
#
#
# SCL <scott@rerobots>
# Copyright (C) 2018 rerobots, Inc.

FROM debian:stable

RUN apt-get update \
    && apt-get -y install \
       openssh-server \
    && mkdir -p /var/run/sshd \
    && mkdir -p /root/.ssh

COPY sshtunnel-container-bootstrap.sh /tmp/sshtunnel-container-bootstrap.sh
RUN /tmp/sshtunnel-container-bootstrap.sh

CMD ["/usr/sbin/sshd", "-D", "-e"]
