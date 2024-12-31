"""APIW WSGI module

gunicorn loads the app from here.


SCL <scott@rerobots>
Copyright (C) 2017 rerobots, Inc.
"""

from .factory import create_application


application = create_application()
