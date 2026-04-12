#!/bin/sh -e


podman cp img/rabbitmq/local-prep.sh infra_rabbitmq_1:/root/local-prep.sh
podman exec infra_rabbitmq_1 /root/local-prep.sh

podman cp img/schema/init.sh infra_postgres_1:/root/init.sh

# If this fails, add `-f` to drop existing databases.
podman exec infra_postgres_1 /root/init.sh

cd apiw
pipenv run ./tests/seed.py
cd ..

cd web
pipenv run ./manage.py migrate
./tools/seed-devel-db.sh
cd ..
