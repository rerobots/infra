"""logger daemon settings


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import os


# SECURITY: In production, use DEBUG=False. Otherwise, internal
# details may be leaked. The leak may not allow, e.g., unauthorized
# remote code execution, but it may forsake some user's privacy.
DEBUG = True

if DEBUG:
    RUNTIME_ENVIRON = os.environ.get('RRENVIRON', 'development')
else:
    RUNTIME_ENVIRON = os.environ.get('RRENVIRON', 'production')

DB_URL = 'postgresql://rra:@127.0.0.1/rrlogsdb'
if RUNTIME_ENVIRON == 'staging':
    DB_URL = 'postgresql://rra:@postgres:5432/rrdb'
elif DEBUG:
    DB_URL = 'sqlite:///db.sqlite3'
