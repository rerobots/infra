"""
SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

from celery.utils.log import get_task_logger
import requests

from .celery import app as capp
from . import db as rrdb
from .settings import MAILGUN_API_KEY, SLACK_WEBHOOKS
from .settings import ADMINS


logger = get_task_logger(__name__)


def send_raw_email(to, subject, body):
    logger.debug(f'sending email to {to}')
    if not MAILGUN_API_KEY:
        # empty key string => no-op
        # This codepath is motivated to support testing, staging without contacting Mailgun.
        logger.info('skipping send because no Mailgun key')
        return
    base_uri = 'api.mailgun.net/v3/{}/messages'.format('mg.rerobots.net')
    payload = {
        'from': 'rerobots API <api@mg.rerobots.net>',
        'to': to,
        'subject': subject,
        'text': body,
    }
    try:
        res = requests.post(
            'https://api:{}@{}'.format(MAILGUN_API_KEY, base_uri), data=payload
        )
        if not res.ok:
            logger.warning('POST request failed to Mailgun')
        else:
            logger.info('sent successfully')
    except Exception as err:
        logger.error(
            'caught {EXCEPTMSG} ({EXCEPTTYPE}): caught exception when trying to send raw email: '
            '(to: "{TO}", subject: "{SUBJ}")'.format(
                EXCEPTMSG=err, EXCEPTTYPE=type(err), TO=to, SUBJ=subject
            )
        )
        raise


@capp.task
def to_admins(message, title=None):
    """Send email to administrators (always emergency?)

    Adds prefix 'ALERT: ' to title if given, otherwise email subject is simply ALERT.
    """
    if not MAILGUN_API_KEY:
        logger.error(f'unable to send without key; title: {title}; message: {message}')
        return
    if title is None:
        title = 'ALERT'
    else:
        title = 'ALERT: ' + title
    send_raw_email(to=ADMINS, subject=title, body=message)


@capp.task
def new_instance(user, instance_id, wdeployment_id):
    if user.startswith('sandbox_anon_') or user.startswith('anon_'):
        is_anon = True
        if user.startswith('sandbox_anon_'):
            uid = int(user[13:])
            anon_url = f'https://rerobots.net/admin/anonymous/{uid}'
        else:
            anon_url = None
    else:
        is_anon = False
    with rrdb.create_session_context() as session:
        wdeployment = (
            session.query(rrdb.Deployment)
            .filter(rrdb.Deployment.deploymentid == wdeployment_id)
            .one()
        )
        if is_anon and anon_url:
            message = f'anonymous {anon_url} started instance {instance_id} on {wdeployment_id} ({wdeployment.wtype})'
        else:
            message = f'{user} started instance {instance_id} on {wdeployment_id} ({wdeployment.wtype})'
    try:
        res = requests.post(SLACK_WEBHOOKS['security'], json={'text': message})
        if not res.ok:
            logger.error('failed to post to Slack webhook')
    except Exception as err:
        logger.error(f'caught {type(err)}: {err}')


@capp.task
def new_mailinglist_subscriber(topic, name, emailaddr, why_interested=None):
    if why_interested:
        message = f'new subscriber for `{topic}`: {name} <{emailaddr}>, who wrote: "{why_interested}"'
    else:
        message = f'new subscriber for `{topic}`: {name} <{emailaddr}>'
    try:
        res = requests.post(SLACK_WEBHOOKS['security'], json={'text': message})
        if not res.ok:
            logger.error('failed to post to Slack webhook')
    except Exception as err:
        logger.error('caught {}: {}'.format(type(err), err))
