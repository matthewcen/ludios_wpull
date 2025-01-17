# encoding=utf-8
"""Wrappers that wrap instances to Python standard library."""
import email
import urllib.request
from wpull.protocol.http.request import Request, Response


def convert_http_request(request: Request, referrer_host=None) -> urllib.request.Request:
    """Convert a HTTP request.

    Args:
        request: An instance of :class:`.http.request.Request`.
        referrer_host (str): The referrering hostname or IP address.

    Returns:
        Request: An instance of :class:`urllib.request.Request`
    """
    new_request = urllib.request.Request(
        request.url_info.url,
        origin_req_host=referrer_host,
    )

    for name, value in request.fields.get_all():
        new_request.add_header(name, value)

    return new_request


class HTTPResponseInfoWrapper(object):
    """Wraps a HTTP Response.
    """
    def __init__(self, response: Response):
        self._response: Response = response

    def info(self) -> email.message.Message:
        """Return the header fields as a Message:

        Returns:
            Message: An instance of :class:`email.message.Message`.
        """
        return email.message_from_string(str(self._response.fields))


class CookieJarWrapper(object):
    """Wraps a CookieJar.

    Args:
        cookie_jar: An instance of :class:`http.cookiejar.CookieJar`.
        save_filename (str, optional): A filename to save the cookies.
        keep_session_cookies (bool): If True, session cookies are kept when
            saving to file.
    """
    def __init__(self, cookie_jar, save_filename=None,
                 keep_session_cookies: bool = False):
        self._cookie_jar = cookie_jar
        self._save_filename = save_filename
        self._keep_session_cookies = keep_session_cookies

    def add_cookie_header(self, request: Request, referrer_host: str = None) -> None:
        """Wrapped ``add_cookie_header``.

        Args:
            referrer_host (str): An hostname or IP address of the referrer
                URL.
        """
        new_request = convert_http_request(request, referrer_host)
        self._cookie_jar.add_cookie_header(new_request)

        request.fields.clear()

        for name, value in new_request.header_items():
            request.fields.add(name, value)

    def extract_cookies(self, response:  Response, request: Request, referrer_host=None) -> None:
        """Wrapped ``extract_cookies``.

        Args:
            response: An instance of :class:`.http.request.Response`.
            request: An instance of :class:`.http.request.Request`.
            referrer_host (str): An hostname or IP address of the referrer
                URL.
        """
        new_response = HTTPResponseInfoWrapper(response)
        new_request = convert_http_request(request, referrer_host)

        self._cookie_jar.extract_cookies(new_response, new_request)

    @property
    def cookie_jar(self):
        """Return the wrapped Cookie Jar."""
        return self._cookie_jar

    def close(self) -> None:
        """Save the cookie jar if needed."""
        if self._save_filename:
            self._cookie_jar.save(
                self._save_filename,
                ignore_discard=self._keep_session_cookies
            )
