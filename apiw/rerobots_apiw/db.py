"""Object relational maps for rerobots database


SCL <scott@rerobots>
Copyright (C) 2017, 2018 rerobots, Inc.
"""

from contextlib import contextmanager
from datetime import datetime

import sqlalchemy
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey

from .util import now
from .settings import DB_URL


Base = declarative_base()


class Deployment(Base):
    __tablename__ = 'deployments'
    id = Column(Integer, primary_key=True)
    deploymentid = Column(String, unique=True)
    wtype = Column(String)
    wversion = Column(Integer)
    supported_addons = Column(String, default='')  # comma-separated list; no spaces
    addons_config = Column(Text, default='')  # in JSON
    description = Column(Text, default='')
    region = Column(String)
    date_created = Column(DateTime, default=datetime.utcnow)
    instance_counter = Column(Integer, default=0)
    last_heartbeat = Column(
        DateTime, default=datetime.utcnow
    )  # only defined when date_dissolved is NULL
    date_dissolved = Column(DateTime)
    locked_out = Column(
        Boolean, default=False
    )  # no new instances; OK if current instance


class UserProvidedSupp(Base):
    """Supplemental data for user_provided workspace deployments

    Everything in this table is managed and created by rerobots code.
    This is in contrast to the table user_provided_attr (class UserProvidedAttr).
    """

    __tablename__ = 'user_provided_supp'
    id = Column(Integer, primary_key=True)
    deploymentid = Column(String, unique=True)
    registration_origin = Column(String)
    owner = Column(String)


class UserProvidedAttr(Base):
    """User-provided attributes for user_provided workspace deployments

    Data in this table comes from users and so, in general, must be
    treated carefully and sanitized.
    """

    __tablename__ = 'user_provided_attr'
    id = Column(Integer, primary_key=True)
    deploymentid = Column(String, unique=True)
    description = Column(Text, default='')
    alert_emails = Column(String, default=None)


class WDMonitorHeartbeat(Base):
    __tablename__ = 'wdmonitor_heartbeat'
    id = Column(Integer, primary_key=True)
    deploymentid = Column(String, nullable=False)
    user = Column(String, nullable=False)
    constraint_uniq = UniqueConstraint(deploymentid, user)
    alert_sent = Column(DateTime(timezone=True))


class CommandFileTrace(Base):
    __tablename__ = 'command_file_traces'
    id = Column(Integer, primary_key=True)
    instance_id = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    path = Column(Text, default='')
    data = Column(Text, default='')


class DeploymentACL(Base):
    __tablename__ = 'wdeployment_access_control'
    id = Column(Integer, primary_key=True)
    date_created = Column(DateTime)  # TODO: default=datetime.utcnow
    user = Column(String)
    wdeployment_id = Column(String)
    capability = Column(String)
    param = Column(Integer)


# TODO: associated org of rootuser, if defined
class Instance(Base):
    __tablename__ = 'instances'
    id = Column(Integer, primary_key=True)
    instanceid = Column(String, unique=True)
    deploymentid = Column(String)
    rootuser = Column(String)
    starttime = Column(DateTime)
    ready_at = Column(DateTime)  # For billing, instance must have non-NULL ready_at
    terminating_started_at = Column(DateTime)
    endtime = Column(DateTime)
    status = Column(String)
    has_vpn = Column(Boolean)
    ssh_privatekey = Column(Text, default='')
    listening_ipaddr = Column(String, default='')
    listening_port = Column(Integer, default=0)
    associated_th = Column(String, default='')  # Docker container ID
    th_infra_port = Column(Integer, default=0)
    th_hostname = Column(
        String
    )  # hostname on which the associated_th container resides
    hostkey = Column(String, default='')
    event_url = Column(Text, default='')


class InstanceEvent(Base):
    __tablename__ = 'instance_events'
    id = Column(Integer, primary_key=True)
    instanceid = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=now)
    kind = Column(String, nullable=False)
    data = Column(Text)


class InstanceKeepAlive(Base):
    __tablename__ = 'instance_keepalive'
    id = Column(Integer, primary_key=True)
    instanceid = Column(String, nullable=False, unique=True)
    last_ping = Column(DateTime(timezone=True), default=now)


class InstanceExpiration(Base):
    __tablename__ = 'instance_expirations'
    id = Column(Integer, primary_key=True)
    instance_id = Column(String, unique=True)
    target_duration = Column(Integer)
    event_url = Column(Text, default='')


class Reservation(Base):
    __tablename__ = 'reservations'
    id = Column(Integer, primary_key=True)
    reservationid = Column(String, unique=True)
    user = Column(String)
    createdtime = Column(DateTime)
    expire_d = Column(Integer, default=0)
    rfilter = Column(String)
    has_vpn = Column(Boolean)
    ssh_publickey = Column(Text, default='')
    event_url = Column(Text, default='')
    handler = Column(Integer, default=0)


class VPNClient(Base):
    __tablename__ = 'vpnclients'
    id = Column(Integer, primary_key=True)
    instanceid = Column(String)
    user = Column(String)
    creationtime = Column(DateTime)
    client_id = Column(String)
    ovpn = Column(Text)


# Placeholder until implementation of linking with accounts from Web UI
User = None


class APIToken(Base):
    __tablename__ = 'apitokens'
    id = Column(Integer, primary_key=True)
    user = Column(String)
    token = Column(Text)
    sha256 = Column(String)
    creationtime = Column(DateTime, default=datetime.utcnow)
    origin = Column(String)
    revoked = Column(Boolean, default=False)


class SSHPrivateKey(Base):
    __tablename__ = 'sshprivatekeys'
    id = Column(Integer, primary_key=True)
    user = Column(String)
    reservationid = Column(String)
    key = Column(Text)
    visit_count = Column(Integer, default=0)


class ActiveAddon(Base):
    __tablename__ = 'active_addons'
    id = Column(Integer, primary_key=True)
    user = Column(String)
    instanceid_with_addon = Column(
        String, unique=True
    )  # of the form ID:addon; e.g., 'b3646b37-3f97-48cf-af30-c52cc9727de5:vnc'
    config = Column(Text)  # configuration data, always in JSON
    port = Column(
        Integer, default=0
    )  # port on which active add-on is listening, or 0 if undefined


class ActiveProxy(Base):
    __tablename__ = 'active_proxies'
    id = Column(Integer, primary_key=True)
    instance_hash = Column(String)
    addon = Column(String)
    token = Column(String)
    address = Column(String)
    port = Column(Integer)


class CIBuild(Base):
    __tablename__ = 'ci_builds'
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('ci_jobs.id'))
    job = relationship('CIJob', back_populates='ci_builds')
    request_time = Column(DateTime, default=datetime.utcnow)


class CIJob(Base):
    __tablename__ = 'ci_jobs'
    id = Column(Integer, primary_key=True)
    ci_builds = relationship('CIBuild', back_populates='job')
    project_id = Column(Integer, ForeignKey('ci_projects.id'))
    project = relationship('CIProject', back_populates='ci_jobs')
    branch = Column(String, default='')  # branch name; not required
    ref = Column(String)  # e.g, commit hash
    start_time = Column(DateTime, default=datetime.utcnow)


class CIProject(Base):
    __tablename__ = 'ci_projects'
    id = Column(Integer, primary_key=True)
    ci_jobs = relationship('CIJob', order_by=CIJob.id, back_populates='project')
    name = Column(String)
    user = Column(String)
    repo_url = Column(String)
    created = Column(DateTime, default=datetime.utcnow)
    is_dissolved = Column(Boolean, default=False)


class MailingListMember(Base):
    __tablename__ = 'mailinglist_members'
    id = Column(Integer, primary_key=True)
    joined = Column(DateTime, default=datetime.utcnow)
    name = Column(String)
    emailaddr = Column(String, unique=True)
    topic = Column(String, default='')
    why_interested = Column(String, default='')


class AnonUser(Base):
    """Anonymous users, who have highly constrained access

    username is anon_{id} where `id` is the primary key column.
    """

    __tablename__ = 'anonymous_users'
    id = Column(Integer, primary_key=True)
    creationtime = Column(DateTime, default=datetime.utcnow)
    origin = Column(String)
    wtype = Column(String, default='')


class SessionSingleton:
    def __init__(self):
        self.Session = None

    def __call__(self):
        if self.Session is None:
            engine = sqlalchemy.create_engine(DB_URL)
            Base.metadata.create_all(engine)
            self.Session = sessionmaker(bind=engine, future=True)
        return self.Session()


create_session = SessionSingleton()


@contextmanager
def create_session_context():
    session = create_session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()
