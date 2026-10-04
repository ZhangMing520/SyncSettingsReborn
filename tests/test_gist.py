# -*- coding: utf-8 -*-

import unittest
from unittest import mock

from sync_settings_reborn.libs import gist, http


class TestRequestErrors(unittest.TestCase):
    def setUp(self):
        self.api = gist.Gist(token='token', http_proxy='', https_proxy='')

    def _mock_http(self):
        patcher = mock.patch('sync_settings_reborn.libs.gist.http_lib.request')
        m = patcher.start()
        self.addCleanup(patcher.stop)
        return m

    def _response(self, status_code):
        return http.Response(status_code, b'{"message": "boom"}')

    def test_transport_error_becomes_network_error(self):
        # The http layer maps every transport failure (SSL, refused connection,
        # timeout) to http.NetworkError; Gist keeps its own error message.
        m = self._mock_http()
        m.side_effect = http.NetworkError('cert verify failed')
        with self.assertRaises(gist.NetworkError):
            self.api.get('gid')

    def test_404_becomes_not_found_error(self):
        m = self._mock_http()
        m.return_value = self._response(404)
        with self.assertRaises(gist.NotFoundError):
            self.api.get('gid')

    def test_422_becomes_unprocessable_error(self):
        m = self._mock_http()
        m.return_value = self._response(422)
        with self.assertRaises(gist.UnprocessableDataError):
            self.api.get('gid')

    def test_server_error_becomes_unexpected_error(self):
        m = self._mock_http()
        m.return_value = self._response(500)
        with self.assertRaises(gist.UnexpectedError):
            self.api.get('gid')

    def test_redirect_of_a_write_becomes_unexpected_error(self):
        # urllib will not redirect PATCH, so a 3xx has to surface as an error:
        # silently retrying could apply the write twice.
        m = self._mock_http()
        m.return_value = self._response(307)
        with self.assertRaises(gist.UnexpectedError):
            self.api.update('gid', {'files': {}})

    def test_ok_returns_parsed_json(self):
        m = self._mock_http()
        m.return_value = self._response(200)
        self.assertEqual(self.api.get('gid'), {'message': 'boom'})

    def test_request_uses_post_for_create(self):
        m = self._mock_http()
        m.return_value = self._response(201)
        self.api.create({'files': {}})
        method, url = m.call_args[0]
        self.assertEqual(method.upper(), 'POST')
        self.assertEqual(url, gist.Gist.make_uri())
