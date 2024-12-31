#!/bin/sh

if [ "$1" = "-f" ]; then
    echo "Delete existing databases, if any? (y/N)"
    read resp
    if [ "$resp" != "y" ]; then
        echo "No action taken"
        exit 1
    else
        su postgres sh -c 'echo "DROP DATABASE IF EXISTS rrdb; DROP DATABASE IF EXISTS  rrlogsdb; DROP ROLE rra;" | psql'
    fi
fi

su postgres sh -c 'echo "CREATE ROLE rra LOGIN; CREATE DATABASE rrdb OWNER rra; CREATE DATABASE rrlogsdb OWNER rra;" | psql'
