#!/bin/sh -e


if [[ -z "$RUNC" ]]; then
    RUNC=podman
fi


cd img

cd redis
$RUNC build -t rerobots/ext/redis .
cd ..

cd rabbitmq
$RUNC build -t rerobots/ext/rabbitmq .
cd ..

cd postgres
$RUNC build -t rerobots/ext/postgres .
cd ..
