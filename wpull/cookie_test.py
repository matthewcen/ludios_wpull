# encoding=utf-8
import email
from http.cookiejar import CookieJar
import http.cookiejar
import os
import sys
import tempfile
import unittest
import urllib.request

from wpull.cookie import DeFactoCookiePolicy, BetterMozillaCookieJar


# from Lib/test/test_http_cookiejar.py
class FakeResponse(object):
    def __init__(self, headers=None, url=None):
        """
        headers: list of RFC822-style 'Key: value' strings
        """
        self._headers = email.message_from_string("\n".join(headers))

        self._url = url or []

    def info(self):
        return self._headers


class TestCookie(unittest.TestCase):
    def setUp(self):
        http.cookiejar.debug = True

    def test_length(self):
        cookie_jar = CookieJar()
        policy = DeFactoCookiePolicy(cookie_jar=cookie_jar)
        cookie_jar.set_policy(policy)

        request = urllib.request.Request('http://example.com/')
        response = FakeResponse(
            [
                f"Set-Cookie: k={'a' * 400}"
            ],
            'http://example.com/'
        )

        cookie_jar.extract_cookies(response, request)

        print(cookie_jar._cookies)

        self.assertTrue(cookie_jar._cookies['example.com']['/'].get('k'))

        request = urllib.request.Request('http://example.com/')
        response = FakeResponse(
            [
                f"Set-Cookie: k={'a' * 5000}"
            ],
            'http://example.com/'
        )

        cookie_jar.extract_cookies(response, request)

        self.assertFalse(cookie_jar._cookies['example.com']['/'].get('k2'))

    def test_domain_limit(self):
        cookie_jar = CookieJar()
        policy = DeFactoCookiePolicy(cookie_jar=cookie_jar)
        cookie_jar.set_policy(policy)

        request = urllib.request.Request('http://example.com/')

        for key in range(55):
            response = FakeResponse(
                [
                    f'Set-Cookie: k{key}=a'
                ],
                'http://example.com/'
            )

            cookie_jar.extract_cookies(response, request)

            if key < 50:
                self.assertTrue(
                    cookie_jar._cookies['example.com']['/']
                    .get(f'k{key}')
                )
            else:
                self.assertFalse(
                    cookie_jar._cookies['example.com']['/']
                    .get(f'k{key}')
                )

        response = FakeResponse(
            [
                'Set-Cookie: k3=b'
            ],
            'http://example.com/'
        )

        cookie_jar.extract_cookies(response, request)
        self.assertEqual(
            'b',
            cookie_jar._cookies['example.com']['/']['k3'].value
        )

    def test_ascii(self):
        cookie_jar = CookieJar()
        policy = DeFactoCookiePolicy(cookie_jar=cookie_jar)
        cookie_jar.set_policy(policy)

        request = urllib.request.Request('http://example.com/')
        response = FakeResponse(
            [
                'Set-Cookie: k=🐭'
            ],
            'http://example.com/'
        )

        cookie_jar.extract_cookies(response, request)

        print(cookie_jar._cookies)

        self.assertFalse(cookie_jar._cookies.get('example.com'))

    def test_empty_value(self):
        cookie_jar = CookieJar()
        policy = DeFactoCookiePolicy(cookie_jar=cookie_jar)
        cookie_jar.set_policy(policy)

        request = urllib.request.Request('http://example.com/')
        response = FakeResponse(
            [
                'Set-Cookie: k'
            ],
            'http://example.com/'
        )

        cookie_jar.extract_cookies(response, request)

        print(cookie_jar._cookies)

        self.assertTrue(cookie_jar._cookies.get('example.com'))

    def test_load_bad_cookie(self):
        cookie_jar = BetterMozillaCookieJar()

        with self.assertRaises(http.cookiejar.LoadError):
            with tempfile.TemporaryDirectory() as temp_dir:
                filename = os.path.join(temp_dir, 'cookies.txt')
                with open(filename, 'w') as file:
                    file.write('You know what they say:\n')
                    file.write('All toasters toast toast!')

                cookie_jar.load(filename)
