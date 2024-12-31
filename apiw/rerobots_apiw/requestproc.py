"""common routines for request processing


SCL <scott@rerobots>
Copyright (C) 2018 rerobots, Inc.
"""

import hashlib
import logging
import time

import geoip2.database
import jwt

from .settings import WEBUI_PUBLIC_KEY
from . import db as rrdb


logger = logging.getLogger(__name__)


def get_peername(request):
    if 'X-Forwarded-For' in request.headers:
        return request.headers['X-Forwarded-For']
    else:
        return str(request.transport.get_extra_info('peername')[0])


def geo_lookup(addr):
    # TODO: change calling code so this function can assume `addr` is single IP address?
    if ',' in addr:
        addr = addr.split(',')[0].strip()
    try:
        reader = geoip2.database.Reader('/usr/share/GeoIP/GeoLite2-City.mmdb')
        c = reader.city(addr)
        georegion = '{}, {}'.format(c.city.names['en'], c.country.names['en'])
    except:
        georegion = None
    return georegion


def checktoken(dbsession, authheader, ephkey=None, origin=None):
    authheader_parts = authheader.split()
    if len(authheader_parts) != 2:
        logger.info('received malformed token')
        return None
    token = authheader_parts[1]
    try:
        payload = jwt.decode(
            token,
            issuer='rerobots.net',
            audience='rerobots.net',
            key=WEBUI_PUBLIC_KEY,
            algorithms=['RS256'],
        )
    except jwt.exceptions.DecodeError:
        logger.info('received token that results in jwt.exceptions.DecodeError')
        return None
    except jwt.exceptions.ImmatureSignatureError:
        logger.info(
            'received token that results in jwt.exceptions.ImmatureSignatureError'
        )
        return None
    except jwt.exceptions.InvalidIssuerError:
        logger.info(
            'received token with wrong issuer (so, jwt.exceptions.InvalidIssuerError)'
        )
        return None
    except jwt.exceptions.InvalidAudienceError:
        logger.info(
            'received token with wrong audience (so, jwt.exceptions.InvalidAudienceError)'
        )
        return None
    except jwt.exceptions.ExpiredSignatureError:
        logger.info(
            'received token that results in jwt.exceptions.ExpiredSignatureError'
        )
        return None
    except Exception as err:
        logger.warning(f'caught {type(err)}: {err}')
        return None
    token_hash = hashlib.sha256(bytes(token, encoding='utf-8')).hexdigest()
    row = (
        dbsession.query(rrdb.APIToken)
        .filter(rrdb.APIToken.sha256 == token_hash)
        .first()
    )
    if row is None:
        if origin:
            dbsession.add(
                rrdb.APIToken(
                    user=payload['sub'], token=token, sha256=token_hash, origin=origin
                )
            )
        else:
            dbsession.add(
                rrdb.APIToken(user=payload['sub'], token=token, sha256=token_hash)
            )
    elif row.revoked:
        return None
    sig_prefix = token.split('.')[2][:10]
    # TODO: refactor code that uses checktoken(), to use `sub` key
    # instead of `user`? however, `user` is more consistent and clear,
    # whereas `sub` ("subject") is as used in the JWT standard (RFC 7519).
    payload['user'] = payload['sub']
    payload['org'] = payload.get('org', None)
    payload['sig_prefix'] = sig_prefix
    return payload


def process_headers(request, token=None):
    """Process request headers, handle auth, prepare response headers, etc.

    return (sufficient_requests, data), where `sufficient_requests` is
    as in rate_limit(), and `data` is a dict object that contains
    various data obtained from the request headers, including:

    * `data['user']`: if authentication succeeded, the username; else, None.
    * `data['su']`: whether the user is a superuser, which has
          different implications for different commands.
    * `data['payload']`: if the request included a valid JWT, the JWT payload;
          else, None.
    * `data['version']`: requested API version, or if not specified,
          the current stable API version
    """
    data = {'version': 1}
    origin = get_peername(request)
    if (
        ('AUTHORIZATION' in request.headers)
        and request.headers['AUTHORIZATION'].startswith('Bearer ')
    ) or token is not None:
        if token is None:
            authheader = request.headers['AUTHORIZATION']
        else:
            authheader = 'Bearer {}'.format(token)
        data['payload'] = checktoken(
            dbsession=request['dbsession'], authheader=authheader, origin=origin
        )
        if data['payload'] is not None:
            data['user'] = data['payload']['user']
            data['org'] = data['payload']['org']
            if 'su' in data['payload'] and data['payload']['su'] == True:
                data['su'] = True
            else:
                data['su'] = False

        else:
            data['user'] = None  # Silently fallback to unauthorized request
            data['su'] = False

    else:
        data['payload'] = None
        data['user'] = None
        data['su'] = False

    should_handle, data['response_headers'] = rate_limit(
        request, jwt_payload=data['payload'], origin=origin
    )
    data['response_headers'].update({'Server': 'rerobots.net'})
    return (should_handle, data)


def rate_limit(request, jwt_payload=None, origin=None):
    """Handle request rate limiting.

    return (sufficient_requests, headers),
    where `headers` is `dict` of HTTP headers to include in the response,
    and `sufficient_requests` is True if and only if the request
    should be handled, i.e., there was at least 1 allowed request
    remaining before this call of rate_limit().
    """
    if jwt_payload is None or ('sig_prefix' not in jwt_payload):
        peername = origin if origin else get_peername(request)
        ttl_origin = 'peername://' + peername
        credit = 60
    else:
        ttl_origin = 'tok://' + jwt_payload['sig_prefix']
        credit = 3600
    logger.debug('rate-limiting update for origin {}'.format(ttl_origin))
    if not request.app['red'].exists(ttl_origin):
        request.app['red'].set(ttl_origin, int(time.time()) + 3600)
        request.app['red'].set(ttl_origin + '-ratelimit-remaining', credit)
        request.app['red'].expire(ttl_origin, 3600)
        request.app['red'].expire(ttl_origin + '-ratelimit-remaining', 3600)
        sufficient = True
    elif int(request.app['red'].get(ttl_origin + '-ratelimit-remaining')) < 0:
        request.app['red'].set(ttl_origin + '-ratelimit-remaining', 0)
        sufficient = False
    else:
        sufficient = True
    request.app['red'].decr(ttl_origin + '-ratelimit-remaining')
    return (
        sufficient,
        {
            'X-RateLimit-Limit': str(credit),
            'X-RateLimit-Remaining': str(
                request.app['red'].get(ttl_origin + '-ratelimit-remaining'),
                encoding='utf-8',
            ),
            'X-RateLimit-Reset': str(
                request.app['red'].get(ttl_origin), encoding='utf-8'
            ),
        },
    )
