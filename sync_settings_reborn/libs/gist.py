# -*- coding: utf-8 -*-

import json
import re
from functools import wraps

from . import http as http_lib
from .http import NetworkError
from .logger import logger
from . import settings

# Compiled once: proxies is consulted on every HTTP request.
_PROXY_URL_RE = re.compile(
    r'^(?:http)s?://'  # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
    r'localhost|'  # localhost...
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
    r'(?::\d+)?'  # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)


class NotFoundError(RuntimeError):
    pass


class UnexpectedError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


class UnprocessableDataError(RuntimeError):
    pass


def auth(func):
    @wraps(func)
    def auth_wrapper(self, *args, **kwargs):
        if self.token is None:
            raise AuthenticationError('GitHub`s credentials are required')
        return func(self, *args, **kwargs)

    return auth_wrapper


def with_gid(func):
    @wraps(func)
    def with_gid_wrapper(self, *args, **kwargs):
        if not args[0]:
            raise ValueError('The given id `{}` is not valid'.format(args[0]))
        return func(self, *args, **kwargs)

    return with_gid_wrapper


class Gist:
    def __init__(self, token=None, http_proxy='', https_proxy=''):
        self.token = token
        self.http_proxy = http_proxy
        self.https_proxy = https_proxy

    @classmethod
    def from_settings(cls):
        """Build a client from the plugin's stored token/proxy settings."""
        return cls(
            token=settings.get('access_token'),
            http_proxy=settings.get('http_proxy'),
            https_proxy=settings.get('https_proxy'),
        )

    @staticmethod
    def make_uri(endpoint=''):
        e = '' if not endpoint else '/{}'.format(endpoint)
        return 'https://api.github.com/gists{}'.format(e)

    @auth
    def create(self, data):
        if not isinstance(data, dict) or not len(data):
            raise ValueError('Gist can`t be created without data')
        return self.__do_request('post', self.make_uri(), json.dumps(data)).json()

    @auth
    @with_gid
    def update(self, gid, data):
        if not isinstance(data, dict) or not len(data):
            raise ValueError('Gist can`t be updated without data')
        return self.__do_request('patch', self.make_uri(gid), json.dumps(data)).json()

    @auth
    @with_gid
    def delete(self, gid):
        response = self.__do_request('delete', self.make_uri(gid))
        return response.status_code == 204

    @with_gid
    def get(self, gid):
        return self.__do_request('get', self.make_uri(gid)).json()

    @with_gid
    def commits(self, gid):
        return self.__do_request('get', self.make_uri('{}/commits'.format(gid))).json()

    def __do_request(self, verb, url, data=None):
        try:
            response = http_lib.request(verb, url, headers=self.headers,
                                        data=data, proxies=self.proxies)
        except NetworkError as e:
            raise NetworkError('Can`t perform this action due to network errors. reason: {}'.format(str(e)))
        if response.status_code >= 300:
            logger.warning(response.text)
        if response.status_code == 404:
            raise NotFoundError('The requested gist do not exists, or the token has not enough permissions')
        if response.status_code in [401, 403]:
            raise AuthenticationError('The credentials are invalid, or the token does not have permissions')
        if response.status_code == 422:
            raise UnprocessableDataError('The provided data has errors')
        if response.status_code >= 300:
            raise UnexpectedError('Unexpected Error, Reason: {}'.format(response.text))

        return response

    @property
    def headers(self):
        return {} if not self.token else {
            'accept': 'application/vnd.github.v3+json',
            'content-type': 'application/json',
            'authorization': 'token {}'.format(self.token)
        }

    @property
    def proxies(self):
        def check_proxy_url(url):
            return url and isinstance(url, str) and _PROXY_URL_RE.match(url) is not None
        proxies = dict()
        if check_proxy_url(self.http_proxy):
            proxies['http'] = self.http_proxy
        if check_proxy_url(self.https_proxy):
            proxies['https'] = self.https_proxy
        return proxies
