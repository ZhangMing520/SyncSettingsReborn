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

import gzip
import http.client
import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
import zlib


# Bound every request (including the background auto-sync loop) so a hanging
# connection can never pin a worker thread forever.
REQUEST_TIMEOUT = 30

# Retry policy for transport failures. Only idempotent requests get more than
# one attempt: a write may have reached the server before the connection broke,
# and replaying it would apply it twice. Backoff doubles per attempt.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 0.5
_IDEMPOTENT_METHODS = frozenset(('GET', 'HEAD'))

# GitHub rejects API calls without a User-Agent; python-requests used to send
# its own, so send an explicit one in its place.
USER_AGENT = 'SyncSettingsReborn'

# API responses are parsed in memory, so they may be compressed on the wire.
# Files streamed to disk must stay identity-encoded: writing gzip bytes into a
# settings file would corrupt it.
API_ACCEPT_ENCODING = 'gzip'
FILE_ACCEPT_ENCODING = 'identity'

# urllib's transport failures. ``urllib.error.HTTPError`` is a subclass of
# URLError but carries a status the caller must classify, so every handler
# chain that uses this tuple re-raises or converts HTTPError first.
_TRANSPORT_ERRORS = (urllib.error.URLError, http.client.HTTPException,
                     socket.timeout, OSError)


class NetworkError(RuntimeError):
    """A transport-level failure (DNS, refused connection, timeout, TLS)."""


class _NonRetryableNetworkError(NetworkError):
    """A transport outcome retrying cannot fix, such as a transformed body."""


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


def _new_request(url, headers=None, data=None, method='GET',
                 accept_encoding=FILE_ACCEPT_ENCODING):
    all_headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': accept_encoding}
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
        raw = error.read() or b''
    except Exception:
        return b''
    finally:
        _close(error)
    try:
        return _decode_body(error, raw)
    except NetworkError:
        # A malformed error body must not mask the status the caller classifies.
        return raw


def _content_encoding(response):
    return (response.headers.get('Content-Encoding') or '').strip().lower()


def _reject_transformed_body(response):
    """Refuse anything but the exact bytes stored on the server (write path)."""
    encoding = _content_encoding(response)
    if encoding not in ('', 'identity'):
        raise _NonRetryableNetworkError('cannot decode Content-Encoding: {}'.format(encoding))


def _decode_body(response, raw):
    """Undo a compression the API request asked for; the result stays in memory."""
    encoding = _content_encoding(response)
    if encoding in ('', 'identity'):
        return raw
    if encoding == 'gzip':
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as e:
            # A truncated stream is a transport failure, so let it be retried.
            raise NetworkError('cannot decode gzip body: {}'.format(e))
    raise _NonRetryableNetworkError('cannot decode Content-Encoding: {}'.format(encoding))


def _attempts_for(method):
    return RETRY_ATTEMPTS if method.upper() in _IDEMPOTENT_METHODS else 1


def _run_with_retries(operation, attempts):
    for attempt in range(attempts):
        try:
            return operation()
        except _NonRetryableNetworkError:
            raise
        except NetworkError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(RETRY_BACKOFF * (2 ** attempt))


def _open(what, method, url, headers=None, data=None, proxies=None,
          timeout=REQUEST_TIMEOUT, accept_encoding=FILE_ACCEPT_ENCODING):
    """Open ``url`` and return the live response with its body still unread.

    A final HTTP error status propagates as :class:`urllib.error.HTTPError` for
    the caller to classify; only transport failures become
    :class:`NetworkError`. ``what`` names the operation in that message.
    """
    req = _new_request(url, headers=headers, data=data, method=method,
                       accept_encoding=accept_encoding)
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
    domain errors. Transport failures raise :class:`NetworkError`; idempotent
    methods are retried first, writes never are.

    Redirects follow urllib's own rules: GET/HEAD for any 3xx, POST for
    301/302/303 (downgraded to a bodyless GET). urllib refuses to redirect
    PATCH/DELETE, so those come back as a 3xx Response. GitHub's gist API does
    not redirect writes.
    """
    def once():
        try:
            resp = _open('request', method, url, headers=headers, data=data,
                         proxies=proxies, timeout=timeout,
                         accept_encoding=API_ACCEPT_ENCODING)
        except urllib.error.HTTPError as e:
            # A final HTTP status (with a body) is a normal response for callers
            # to classify (404 / 401 / 422 / 5xx ...).
            return Response(e.code, _read_error_body(e))

        try:
            return Response(resp.getcode(), _decode_body(resp, resp.read()))
        except _TRANSPORT_ERRORS as e:
            raise NetworkError('response read failed: {}'.format(e))
        finally:
            _close(resp)

    return _run_with_retries(once, _attempts_for(method))


def download(url, file_path, proxies=None, timeout=REQUEST_TIMEOUT):
    """Stream ``url`` into ``file_path``; return the HTTP status code.

    Only a 200 body is written: any other status — including another 2xx, whose
    body is not the file it stands for — leaves ``file_path`` untouched instead
    of creating an empty or partial file. Transport failures raise
    :class:`NetworkError`; the GET is retried first.
    """
    def once():
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

    return _run_with_retries(once, RETRY_ATTEMPTS)
