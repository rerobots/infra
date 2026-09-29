#!/usr/bin/env python
"""database-specific structures for logger daemon


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

from contextlib import contextmanager
import logging

import sqlalchemy
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Text, DateTime

from .settings import DB_URL


logger = logging.getLogger(__name__)


Base = declarative_base()


class Log(Base):
    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True)
    agent_id = Column(String)
    agent_kind = Column(String)
    message_level = Column(String)
    message = Column(Text)
    timestamp_from_agent = Column(DateTime)
    timestamp = Column(DateTime)


class SessionSingleton:
    def __init__(self):
        self.Session = None

    def __call__(self):
        if self.Session is None:
            engine = sqlalchemy.create_engine(DB_URL)
            Base.metadata.create_all(engine)
            self.Session = sessionmaker(bind=engine)
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
