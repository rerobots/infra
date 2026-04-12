Infrastructure and API
======================

Organization
------------

* apiw - API workers
* ri - code shared among several components (the directory name abbreviates "rerobots infra")
* web - dashboard

Orchestration of staging and production is provided by scripts and Ansible plays
in the `release-tools` repository.


Prepare for running locally
---------------------------

* PostgreSQL (https://www.postgresql.org/) development files, to build Psycopg (https://www.psycopg.org/)
* Install pipenv (https://pipenv.pypa.io/en/stable/installation.html)
* `pipenv install -d` in each component directory, including web/
* Install pnpm (https://pnpm.io/installation)
* `pnpm install` in the directory web/client/


Running locally
---------------

    podman-compose up
    bin/local-prep.sh

Given the default DEBUG settings of the dashboard,

    export REROBOTS_ORIGIN=http://192.168.1.16:8080
    pipenv run python -m rerobots_apiw --host 0.0.0.0

    export REROBOTS_TH_RUNTIME=podman
    export REROBOTS_TH_ADDR=127.0.0.1
    export REROBOTS_TH_HOSTKEY=fake
    pipenv run celery -A rerobots_apiw worker -E -l INFO -Q celery,tunnel,proxy
    pipenv run celery -A rerobots_apiw beat -l INFO

If DEBUG=True,
then these agents start a queue-based mock of message channels, so to
actually have the agents communicate temporarily skip the imports of
RerobotsQChannel (e.g., change `if DEBUG` to `if False`).


Testing
-------

    podman-compose up

as described in the section above. Prepare keys,

    cd web
    cat /dev/random | base64 | head -c 50 > etc/django-secret.key

and create RSA key pairs using generate-keys.sh from the `release-tools` repo, or e.g.,

    ssh-keygen -b 4096 -m PEM -t rsa -N '' -f testingkey
    cp testingkey etc/webui-secret.key
    cp testingkey.pub etc/webui-public.key


License
-------

This is free software, released under the Apache License, Version 2.0.
You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
