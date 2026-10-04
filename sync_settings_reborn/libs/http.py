# -*- coding: utf-8 -*-

"""Tiny HTTP layer built only on the Python standard library.

Why not ``requests``: the dependency Package Control distributes as
``requests`` is pinned at upstream requests 2.15.1 (2017), which vendors a
urllib3 that does ``from collections import Mapping`` (removed in Python 3.10)
and a requests whose ``utils`` imports ``cgi`` (removed in Python 3.13).
Sublime Text build 4205+ runs plugins under Python 3.14, so that dependency
cannot import at all — the failure surfaces (via requests' own fallback import)
as a misleading ``ModuleNotFoundError: No module named 'urllib3'``.

urllib.request covers everything this plugin needs (JSON API calls, a streamed
raw-file download, explicit proxies and a hard timeout) with zero third-party
packages, on every supported plugin host (3.8 / 3.14). Certificate verification
uses the host trust store, which is what requests fell back to here too:
``certifi`` is not a Package Control channel dependency, so it was never
installed.
"""

import http.client
import json
import os
import shutil
import socket
import urllib.error
import urllib.request


# Bound every request (including the background auto-sync loop) so a hanging
# connection can never pin a worker thread forever.
REQUEST_TIMEOUT = 30

# GitHub rejects API calls without a User-Agent; python-requests used to send
# its own, so send an explicit one in its place.
USER_AGENT = 'SyncSettingsReborn'

# urllib's transport failures. ``urllib.error.HTTPError`` is a subclass of
# URLError but carries a status the caller must classify, so every handler
# chain that uses this tuple re-raises or converts HTTPError first.
_TRANSPORT_ERRORS = (urllib.error.URLError, http.client.HTTPException,
                     socket.timeout, OSError)


class NetworkError(RuntimeError):
    """A transport-level failure (DNS, refused connection, timeout, TLS)."""


class Response(object):
    """Minimal stand-in for the slice of the requests API the plugin uses."""

    def __init__(self, status_code, content=b''):
        self.status_code = status_code
        self.content = content

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def json(self):
        return json.loads(self.text)


def _build_opener(proxies):
    # ProxyHandler dispatches on scheme and, given an explicit mapping, ignores
    # the environment entirely — so a settings-file ``http_proxy`` alone would
    # send api.github.com direct even when HTTPS_PROXY is exported. Merge the
    # two, with the explicitly configured proxy winning per scheme.
    explicit = dict((k, v) for k, v in (proxies or {}).items() if v)
    if not explicit:
        # Nothing configured: the default ProxyHandler already honours
        # HTTP_PROXY/HTTPS_PROXY and no_proxy, as requests did.
        return urllib.request.build_opener()
    merged = dict((k, v) for k, v in urllib.request.getproxies().items()
                  if v and k != 'no')  # no_proxy is applied by proxy_bypass()
    merged.update(explicit)
    return urllib.request.build_opener(urllib.request.ProxyHandler(merged))


def _new_request(url, headers=None, data=None, method='GET'):
    # Without an explicit Accept-Encoding the client accepts "anything", so a
    # transparent proxy could return gzipped bytes that we would then write
    # into a settings file.
    all_headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}
    if headers:
        all_headers.update(headers)

    body = data.encode('utf-8') if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=all_headers,
                                 method=method.upper())
    # urllib replays req.headers to whatever host a redirect names, which would
    # leak the token; requests strips credentials on such a redirect. Proxy
    # credentials are not handled here because ProxyHandler adds its own only
    # once the request is already open.
    authorization = req.headers.pop('Authorization', None)
    if authorization is not None:
        req.add_unredirected_header('Authorization', authorization)
    return req


def _close(response):
    try:
        response.close()
    except Exception:
        pass


def _read_error_body(error):
    try:
        return error.read() or b''
    except Exception:
        return b''
    finally:
        _close(error)


def _reject_transformed_body(response):
    """Raise unless the body arrives exactly as it is stored on the server."""
    encoding = (response.headers.get('Content-Encoding') or '').strip().lower()
    if encoding not in ('', 'identity'):
        raise NetworkError('cannot decode Content-Encoding: {}'.format(encoding))


def _open(what, method, url, headers=None, data=None, proxies=None,
          timeout=REQUEST_TIMEOUT):
    """Open ``url`` and return the live response with its body still unread.

    A final HTTP error status propagates as :class:`urllib.error.HTTPError` for
    the caller to classify; only transport failures become
    :class:`NetworkError`. ``what`` names the operation in that message.
    """
    req = _new_request(url, headers=headers, data=data, method=method)
    try:
        return _build_opener(proxies).open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except _TRANSPORT_ERRORS as e:
        raise NetworkError('{} failed: {}'.format(what, e))


def request(method, url, headers=None, data=None, proxies=None,
            timeout=REQUEST_TIMEOUT):
    """Perform an HTTP request and return a :class:`Response`.

    ``data`` may be a ``str`` (encoded as UTF-8) or ``bytes``. Final HTTP
    statuses (4xx/5xx) come back on the Response — callers map them to their
    domain errors. Transport failures raise :class:`NetworkError`.

    Redirects follow urllib's own rules: GET/HEAD for any 3xx, POST for
    301/302/303 (downgraded to a bodyless GET). urllib refuses to redirect
    PATCH/DELETE, so those come back as a 3xx Response. GitHub's gist API does
    not redirect writes.
    """
    try:
        resp = _open('request', method, url, headers=headers, data=data,
                     proxies=proxies, timeout=timeout)
    except urllib.error.HTTPError as e:
        # A final HTTP status (with a body) is a normal response for callers to
        # classify (404 / 401 / 422 / 5xx ...).
        return Response(e.code, _read_error_body(e))

    try:
        _reject_transformed_body(resp)
        return Response(resp.getcode(), resp.read())
    except _TRANSPORT_ERRORS as e:
        raise NetworkError('response read failed: {}'.format(e))
    finally:
        _close(resp)


def download(url, file_path, proxies=None, timeout=REQUEST_TIMEOUT):
    """Stream ``url`` into ``file_path``; return the HTTP status code.

    Only a 200 body is written: any other status — including another 2xx, whose
    body is not the file it stands for — leaves ``file_path`` untouched instead
    of creating an empty or partial file. Transport failures raise
    :class:`NetworkError`.
    """
    try:
        resp = _open('download', 'GET', url, proxies=proxies, timeout=timeout)
    except urllib.error.HTTPError as e:
        _read_error_body(e)
        return e.code

    try:
        code = resp.getcode()
        if code != 200:
            return code
        _reject_transformed_body(resp)
        with open(file_path, 'wb') as f:
            shutil.copyfileobj(resp, f)
        return code
    except _TRANSPORT_ERRORS as e:
        # Never leave a truncated file behind for the restore step to pick up.
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise NetworkError('download failed: {}'.format(e))
    finally:
        _close(resp)
