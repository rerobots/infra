#!/bin/sh -e

rabbitmqctl add_vhost core
rabbitmqctl set_permissions --vhost core guest '.*' '.*' '.*'
rabbitmqctl add_vhost webui
rabbitmqctl set_permissions --vhost webui guest '.*' '.*' '.*'
