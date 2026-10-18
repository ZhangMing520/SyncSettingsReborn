# -*- coding: utf-8 -*-

import contextlib
import email.message
import gzip
import io
import os
import shutil
import socket
import ssl
import tempfile
import unittest
import urllib.error
from http.client import HTTPException, IncompleteRead
from types import SimpleNamespace
from unittest import mock

from sync_settings_reborn.libs import http

BUILD_OPENER = 'sync_settings_reborn.libs.http.urllib.request.build_opener'
GET_PROXIES = 'sync_settings_reborn.libs.http.urllib.request.getproxies'
PROXY_HANDLER = 'sync_settings_reborn.libs.http.urllib.request.ProxyHandler'


def _headers(content_encoding=None):
    """Headers like the real ones: http.client uses email.message.Message,
    whose lookup is case-insensitive."""
    msg = email.message.Message()
    if content_encoding is not None:
        msg['Content-Encoding'] = content_encoding
    return msg


class _FakeResp(object):
    """Mimics an http.client response: read(n) returns b'' at EOF."""

    def __init__(self, code=200, body=b'{"ok": true}', content_encoding=None):
        self._code = code
        self._body = body
        self._pos = 0
        self.headers = _headers(content_encoding)
        self.closed = False

    def getcode(self):
        return self._code

    def read(self, n=-1):
        if n is None or n < 0:
            chunk = self._body[self._pos:]
        else:
            chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


@contextlib.contextmanager
def mock_transport(resp=None, exc=None, env_proxies=None, side_effect=None):
    """Patch the stdlib calls http.py makes, so no socket is opened.

    Yields a namespace holding the fake ``opener``, the ``build`` and
    ``proxy_handler`` mocks, the backoff ``sleep`` mock, letting a test read
    what http.py handed them.
    """
    opener = mock.MagicMock()
    if side_effect is not None:
        opener.open.side_effect = side_effect
    elif exc is not None:
        opener.open.side_effect = exc
    else:
        opener.open.return_value = resp if resp is not None else _FakeResp()
    with mock.patch(GET_PROXIES, return_value=env_proxies or {}) as build_proxies, \
            mock.patch(BUILD_OPENER, return_value=opener) as build, \
            mock.patch(PROXY_HANDLER) as proxy_handler, \
            mock.patch('sync_settings_reborn.libs.http.time.sleep') as sleep:
        yield SimpleNamespace(opener=opener, build=build,
                              proxy_handler=proxy_handler,
                              get_proxies=build_proxies, sleep=sleep)


class ResponseTest(unittest.TestCase):
    def test_text_and_json(self):
        r = http.Response(200, '{"a": 1}'.encode('utf-8'))
        self.assertEqual(r.text, '{"a": 1}')
        self.assertEqual(r.json(), {'a': 1})

    def test_non_utf8_body_is_replaced_not_raised(self):
        r = http.Response(200, b'\xff\xfe')
        self.assertEqual(r.text, '\ufffd\ufffd')


class RequestTest(unittest.TestCase):
    def test_success_returns_response_and_sends_default_headers(self):
        with mock_transport() as t:
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True})
        req = t.opener.open.call_args[0][0]
        self.assertEqual(req.get_method(), 'GET')
        sent = dict(req.header_items())
        self.assertEqual(sent['User-agent'], http.USER_AGENT)
        self.assertEqual(sent['Accept-encoding'], 'gzip')

    def test_patch_encodes_str_data(self):
        with mock_transport() as t:
            http.request('patch', 'https://example.com/g',
                         headers={'X': '1'}, data='{"a": 1}')
        req = t.opener.open.call_args[0][0]
        self.assertEqual(req.get_method(), 'PATCH')
        self.assertEqual(req.data, b'{"a": 1}')
        self.assertEqual(req.headers['X'], '1')

    def test_credentials_go_to_the_unredirected_header_set(self):
        # urllib copies req.headers into the redirected request and replays them
        # to whatever host the redirect names, so the token must not be there.
        with mock_transport() as t:
            http.request('post', 'https://example.com/g',
                         headers={'authorization': 'token t',
                                  'content-type': 'application/json'},
                         data='{}')
        req = t.opener.open.call_args[0][0]
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(req.unredirected_hdrs['Authorization'], 'token t')
        # ...while the first request still carries it.
        self.assertEqual(dict(req.header_items())['Authorization'], 'token t')

    def test_http_error_comes_back_as_response(self):
        err = urllib.error.HTTPError(
            'https://example.com', 404, 'Not Found', {},
            io.BytesIO(b'no such gist'))
        with mock_transport(exc=err):
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.text, 'no such gist')

    def test_error_status_of_a_write_comes_back_rather_than_raising(self):
        # The caller decides what a 3xx on PATCH means; http.py must not retry
        # or hide it, because the write may already have been applied.
        err = urllib.error.HTTPError(
            'https://example.com', 307, 'Redirect', {}, io.BytesIO(b''))
        with mock_transport(exc=err):
            r = http.request('patch', 'https://example.com/1')
        self.assertEqual(r.status_code, 307)

    def test_transport_failures_become_network_error(self):
        cases = [
            urllib.error.URLError(ssl.SSLError('cert verify failed')),
            urllib.error.URLError(ConnectionRefusedError('refused')),
            socket.timeout('timed out'),
            HTTPException('bad status line'),
            OSError('network is down'),
        ]
        for exc in cases:
            with self.subTest(exc=type(exc).__name__):
                with mock_transport(exc=exc):
                    with self.assertRaises(http.NetworkError):
                        http.request('GET', 'https://example.com')

    def test_gzipped_api_body_is_decoded(self):
        resp = _FakeResp(200, gzip.compress(b'{"ok": true}'),
                         content_encoding='gzip')
        with mock_transport(resp=resp):
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.json(), {'ok': True})

    def test_gzipped_error_body_is_decoded(self):
        # The error path bypasses Response construction, but the body can be
        # compressed too (we advertise gzip); callers print it in the log and
        # in UnexpectedError, so it must be readable text.
        err = urllib.error.HTTPError(
            'https://example.com', 403, 'Forbidden',
            {'Content-Encoding': 'gzip'},
            io.BytesIO(gzip.compress(b'{"message": "rate limited"}')))
        with mock_transport(exc=err):
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {'message': 'rate limited'})

    def test_unknown_content_encoding_is_refused_without_retrying(self):
        resp = _FakeResp(200, b'\x00', content_encoding='br')
        with mock_transport(resp=resp) as t:
            with self.assertRaises(http.NetworkError):
                http.request('GET', 'https://example.com')
        self.assertEqual(t.opener.open.call_count, 1)
        t.sleep.assert_not_called()

    def test_truncated_gzip_body_is_retried(self):
        compressed = gzip.compress(b'{"ok": true}')
        truncated = _FakeResp(200, compressed[:4], content_encoding='gzip')
        with mock_transport(side_effect=[truncated, _FakeResp(200, b'{"ok": true}')]) as t:
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.json(), {'ok': True})
        self.assertEqual(t.opener.open.call_count, 2)

    def test_explicit_proxies_are_merged_with_the_environment(self):
        # A settings-file http_proxy must not silence an exported HTTPS_PROXY:
        # ProxyHandler only builds a handler per scheme it is given.
        env = {'https': 'http://env:1', 'no': 'ignored'}
        with mock_transport(env_proxies=env) as t:
            http.request('GET', 'https://example.com',
                         proxies={'http': 'http://explicit:2'})
        self.assertEqual(t.proxy_handler.call_args[0][0],
                         {'https': 'http://env:1', 'http': 'http://explicit:2'})

    def test_explicit_proxy_wins_for_its_own_scheme(self):
        with mock_transport(env_proxies={'http': 'http://env:1'}) as t:
            http.request('GET', 'https://example.com',
                         proxies={'http': 'http://explicit:2'})
        self.assertEqual(t.proxy_handler.call_args[0][0],
                         {'http': 'http://explicit:2'})

    def test_without_configured_proxies_the_default_handler_is_used(self):
        # Only the environment case may fall through to build_opener's own
        # ProxyHandler, which is what honours no_proxy for both schemes.
        with mock_transport(env_proxies={'https': 'http://env:1'}) as t:
            http.request('GET', 'https://example.com')
        self.assertEqual(t.build.call_args, mock.call())
        t.proxy_handler.assert_not_called()
        t.get_proxies.assert_not_called()


class TransportRetryTest(unittest.TestCase):
    def test_get_is_retried_after_a_transport_failure(self):
        with mock_transport(side_effect=[urllib.error.URLError('reset'),
                                         _FakeResp(200, b'{"ok": true}')]) as t:
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(t.opener.open.call_count, 2)
        self.assertEqual(t.sleep.call_count, 1)

    def test_retries_stop_at_the_attempt_limit(self):
        with mock_transport(exc=urllib.error.URLError('reset')) as t:
            with self.assertRaises(http.NetworkError):
                http.request('GET', 'https://example.com')
        self.assertEqual(t.opener.open.call_count, http.RETRY_ATTEMPTS)

    def test_writes_are_never_replayed(self):
        for method in ('POST', 'PATCH', 'DELETE'):
            with self.subTest(method=method):
                with mock_transport(exc=urllib.error.URLError('reset')) as t:
                    with self.assertRaises(http.NetworkError):
                        http.request(method, 'https://example.com', data='{}')
                self.assertEqual(t.opener.open.call_count, 1)
                t.sleep.assert_not_called()

    def test_truncated_body_is_retried(self):
        class _TruncatedResp(_FakeResp):
            def read(self, n=-1):
                raise IncompleteRead(b'partial', 10)

        with mock_transport(side_effect=[_TruncatedResp(), _FakeResp(200, b'ok')]) as t:
            r = http.request('GET', 'https://example.com')
        self.assertEqual(r.content, b'ok')
        self.assertEqual(t.opener.open.call_count, 2)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'f.bin')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_streams_body_to_file(self):
        with mock_transport(resp=_FakeResp(200, b'file-bytes')):
            code = http.download('https://example.com/f', self.path)
        self.assertEqual(code, 200)
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), b'file-bytes')

    def test_download_keeps_identity_encoding(self):
        # Compressed bytes must never reach a settings file on disk.
        with mock_transport() as t:
            http.download('https://example.com/f', self.path)
        self.assertEqual(t.opener.open.call_args[0][0].get_header('Accept-encoding'), 'identity')

    def test_non_200_success_status_does_not_write_a_file(self):
        # fetch_files installs whatever is in the temp dir, so a 2xx body that
        # is not the file itself (204/206/...) must never reach disk.
        for code in (204, 206):
            with self.subTest(code=code):
                with mock_transport(resp=_FakeResp(code, b'')):
                    self.assertEqual(code, http.download('https://x/f', self.path))
                self.assertFalse(os.path.exists(self.path))

    def test_http_error_does_not_write_file(self):
        err = urllib.error.HTTPError(
            'https://example.com/f', 429, 'Rate limited', {},
            io.BytesIO(b'slow down'))
        with mock_transport(exc=err):
            self.assertEqual(429, http.download('https://example.com/f', self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_transport_error_becomes_network_error(self):
        with mock_transport(exc=urllib.error.URLError('timed out')):
            with self.assertRaises(http.NetworkError):
                http.download('https://example.com/f', self.path)

    def test_transport_failure_is_retried(self):
        with mock_transport(side_effect=[urllib.error.URLError('reset'),
                                         _FakeResp(200, b'file-bytes')]) as t:
            code = http.download('https://example.com/f', self.path)
        self.assertEqual(code, 200)
        self.assertEqual(t.opener.open.call_count, 2)
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), b'file-bytes')

    def test_gzipped_body_is_not_written(self):
        resp = _FakeResp(200, b'\x1f\x8b\x08\x00compressed',
                         content_encoding='gzip')
        with mock_transport(resp=resp):
            with self.assertRaises(http.NetworkError):
                http.download('https://example.com/f', self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_mid_stream_failure_removes_partial_file(self):
        class _BrokenResp(_FakeResp):
            def read(self, n=-1):
                raise OSError('disk exploded mid-write')

        with mock_transport(resp=_BrokenResp()):
            with self.assertRaises(http.NetworkError):
                http.download('https://example.com/f', self.path)
        self.assertFalse(os.path.exists(self.path))


if __name__ == '__main__':
    unittest.main()
