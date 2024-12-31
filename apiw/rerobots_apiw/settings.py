"""API worker settings

SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

import json
import os
import os.path

import aiocache


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


# SECURITY: In production, use DEBUG=False.
DEBUG = True

# Runtime environment: test, development, mock, staging-mock, staging, production
if DEBUG:
    RUNTIME_ENVIRON = os.environ.get('RRENVIRON', 'development')
else:
    RUNTIME_ENVIRON = os.environ.get('RRENVIRON', 'production')

if RUNTIME_ENVIRON == 'production':
    ORIGIN = 'https://api.rerobots.net'
elif RUNTIME_ENVIRON in ['staging', 'staging-mock']:
    ORIGIN = 'https://api.staging.rerobots.net'
else:
    ORIGIN = 'http://127.0.0.1:8080'

# For WebSocket
if ORIGIN.startswith('http://'):
    WS_ORIGIN = 'ws://' + ORIGIN[len('http://') :]
else:
    WS_ORIGIN = 'wss://' + ORIGIN[len('https://') :]

DB_URL = 'postgresql://rra:@127.0.0.1/rrdb'
if os.environ.get('REROBOTS_ENVIRONMENT') == 'CI':
    DB_URL = 'postgresql:///rrdb'
elif RUNTIME_ENVIRON.startswith('staging'):
    DB_URL = 'postgresql://rra:@postgres:5432/rrdb'

AMQP_HOST = '127.0.0.1'
AMQP_PORT = 5672
if RUNTIME_ENVIRON.startswith('staging'):
    AMQP_HOST = 'rabbitmq'
    AMQP_PORT = 5672

REDIS_HOST = '127.0.0.1'
REDIS_PORT = 6379
if RUNTIME_ENVIRON.startswith('staging'):
    REDIS_HOST = 'redis'
    REDIS_PORT = 6379

WEBUI_ORIGIN = 'https://rerobots.net'
if RUNTIME_ENVIRON.startswith('staging'):
    WEBUI_ORIGIN = 'https://staging.rerobots.net'
elif DEBUG:
    WEBUI_ORIGIN = 'http://localhost:3000'

TH_ADDR = os.environ.get('REROBOTS_TH_ADDR', None)
if TH_ADDR is None and RUNTIME_ENVIRON in ['development', 'mock']:
    TH_ADDR = '127.0.0.1'

TH_HOSTKEY = os.environ.get('REROBOTS_TH_HOSTKEY', None)
if TH_HOSTKEY is None and RUNTIME_ENVIRON in ['development', 'mock']:
    TH_HOSTKEY = 'fake'

TH_CONTAINER_PROVIDER = os.environ.get('REROBOTS_TH_RUNTIME', None)
if TH_CONTAINER_PROVIDER is None:
    if RUNTIME_ENVIRON in ['development', 'mock']:
        TH_CONTAINER_PROVIDER = 'podman'
    else:
        TH_CONTAINER_PROVIDER = 'docker'

# SECURITY WARNING: keep this private key secret in production!
# pair with PUBLIC_KEY
if 'REROBOTS_API_SECRET_KEY' in os.environ:
    PRIVATE_KEY = os.environ['REROBOTS_API_SECRET_KEY']
else:
    with open(os.path.join(BASE_DIR, 'etc', 'apiw-secret.key')) as fp:
        PRIVATE_KEY = fp.read()
if 'REROBOTS_API_PUBLIC_KEY' in os.environ:
    PUBLIC_KEY = os.environ['REROBOTS_API_PUBLIC_KEY']
else:
    with open(os.path.join(BASE_DIR, 'etc', 'apiw-public.key')) as fp:
        PUBLIC_KEY = fp.read()


# SECURITY WARNING: keep this MailGun API key secret in production!
# cf. https://app.mailgun.com/app/domains/mg.rerobots.net
with open(os.path.join(BASE_DIR, 'etc', 'mailgun-secret.key')) as fp:
    MAILGUN_API_KEY = fp.read()

if 'REROBOTS_WEBUI_PUBLIC_KEY' in os.environ:
    WEBUI_PUBLIC_KEY = os.environ['REROBOTS_WEBUI_PUBLIC_KEY']
else:
    with open(os.path.join(BASE_DIR, 'etc', 'webui-public.key')) as fp:
        WEBUI_PUBLIC_KEY = fp.read()

# SECURITY WARNING: keep this private key secret in production!
if 'REROBOTS_WEBUI_SECRET_KEY' in os.environ:
    WEBUI_SECRET_KEY = os.environ['REROBOTS_WEBUI_SECRET_KEY']
else:
    try:
        with open(os.path.join(BASE_DIR, 'etc', 'webui-secret.key')) as fp:
            WEBUI_SECRET_KEY = fp.read()
    except:
        WEBUI_SECRET_KEY = None

if DEBUG or RUNTIME_ENVIRON.startswith('staging'):
    SENTRY_DSN = None
else:
    with open(os.path.join(BASE_DIR, 'etc', 'apiw-sentry.dsn')) as fp:
        SENTRY_DSN = fp.read()

# SECURITY WARNING: keep this Slack webhook URL secret in production!
with open(os.path.join(BASE_DIR, 'etc', 'slack-webhooks')) as fp:
    SLACK_WEBHOOKS = json.loads(fp.read())

# Emails for administrative notifications
ADMINS = []


if DEBUG or RUNTIME_ENVIRON.startswith('staging'):
    CACHE_TTL = 0
else:
    CACHE_TTL = 300

aiocache.caches.set_config(
    {
        'default': {
            'cache': 'aiocache.RedisCache',
            'serializer': {
                'class': 'aiocache.serializers.StringSerializer',
            },
        },
    }
)
