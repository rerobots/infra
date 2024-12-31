#!/bin/sh -e
#
# SCL <scott@rerobots>
# Copyright (C) 2017 rerobots, Inc.
set -e

TEMPFILE=tempfile
echo 'GatewayPorts clientspecified' > $TEMPFILE
echo 'StrictModes no' >> $TEMPFILE
cat /etc/ssh/sshd_config >> $TEMPFILE
sh -c "cat $TEMPFILE > /etc/ssh/sshd_config"
rm $TEMPFILE
/etc/init.d/ssh reload
