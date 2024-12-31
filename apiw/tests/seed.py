#!/usr/bin/env python
"""
SCL <scott@rerobots>
Copyright (C) 2022 rerobots, Inc.
"""

from datetime import datetime, timedelta
import json
import os
import uuid

import rerobots_apiw.db as rrdb
from rerobots_apiw.tasks import _create_new_wdeployment_main


username = 'staging_user'

hardshare_wdeployment_id = '0646a578-363e-49dc-ba42-270738e90fcc'
null_wdeployment_id = '851f38c8-40ed-4852-a44c-2303c98706a7'
basic_misty2_wdeployment_id = '57e16158-2cc9-4c47-964c-c3b4cb31a355'


with rrdb.create_session_context() as session:
    session.query(rrdb.DeploymentACL).delete()
    session.query(rrdb.UserProvidedSupp).delete()
    session.query(rrdb.UserProvidedAttr).delete()
    session.query(rrdb.Deployment).delete()
    session.query(rrdb.Instance).delete()
    session.commit()

    if (
        session.query(rrdb.Deployment.deploymentid)
        .filter(rrdb.Deployment.deploymentid == hardshare_wdeployment_id)
        .count()
        == 0
    ):
        _create_new_wdeployment_main(
            hardshare_wdeployment_id,
            {
                'type': 'user_provided',
                'wversion': 1,
                'region': '',
                'supported_addons': ['cam'],
                'addons_config': json.dumps(
                    {
                        'cam': {
                            '0': {
                                'path': '/dev/video0',
                            },
                        },
                    }
                ),
                'hs': {
                    'owner': username,
                },
            },
        )

    if (
        session.query(rrdb.Deployment.deploymentid)
        .filter(rrdb.Deployment.deploymentid == null_wdeployment_id)
        .count()
        == 0
    ):
        _create_new_wdeployment_main(
            null_wdeployment_id,
            {
                'type': 'null',
                'wversion': 1,
                'region': 'us:cali',
                'supported_addons': ['cam', 'cmdsh'],
                'addons_config': json.dumps(
                    {
                        'cam': {
                            '0': {
                                'path': '/dev/video0',
                            },
                        },
                    }
                ),
            },
        )

    if (
        session.query(rrdb.Deployment.deploymentid)
        .filter(rrdb.Deployment.deploymentid == basic_misty2_wdeployment_id)
        .count()
        == 0
    ):
        _create_new_wdeployment_main(
            basic_misty2_wdeployment_id,
            {
                'type': 'basic_misty2',
                'wversion': 1,
                'region': 'us:cali',
                'supported_addons': ['cam', 'mistyproxy', 'py'],
                'addons_config': json.dumps(
                    {
                        'mistyproxy': {
                            'ip': '192.168.1.1',
                        },
                        'cam': {
                            '0': {
                                'path': '/dev/video0',
                                'rotate': '270',
                            },
                        },
                    }
                ),
            },
        )

    hardshare_wd = (
        session.query(rrdb.Deployment)
        .filter(rrdb.Deployment.deploymentid == hardshare_wdeployment_id)
        .one()
    )

    t = datetime.utcnow()
    hardshare_instance = rrdb.Instance(
        deploymentid=hardshare_wdeployment_id,
        instanceid=str(uuid.uuid4()),
        rootuser=username,
        starttime=t - timedelta(minutes=11),
        terminating_started_at=t - timedelta(minutes=1),
        endtime=t,
        status='TERMINATED',
        has_vpn=False,
        ssh_privatekey='fake',
        event_url='',
    )
    session.add(hardshare_instance)
    hardshare_wd.instance_counter = rrdb.Deployment.instance_counter + 1
    session.commit()
