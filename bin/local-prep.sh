#!/bin/sh -e


if [[ -z "$RUNC" ]]; then
    RUNC=podman
fi


$RUNC cp img/rabbitmq/local-prep.sh infra_rabbitmq_1:/root/local-prep.sh
$RUNC exec infra_rabbitmq_1 /root/local-prep.sh

$RUNC cp img/schema/init.sh infra_postgres_1:/root/init.sh

# If this fails, add `-f` to drop existing databases.
$RUNC exec infra_postgres_1 /root/init.sh

cd apiw
pipenv run ./tests/seed.py
cd ..

cd web
pipenv run ./manage.py migrate
./tools/seed-devel-db.sh
cd ..
