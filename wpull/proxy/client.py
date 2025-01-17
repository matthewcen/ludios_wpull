'''Proxy support for HTTP requests.'''
import asyncio
import base64
import io
import logging

import wpull.string
from wpull.errors import NetworkError
from wpull.network.pool import ConnectionPool
from wpull.protocol.http.request import RawRequest
from wpull.protocol.http.stream import Stream

_logger = logging.getLogger(__name__)


class HTTPProxyConnectionPool(ConnectionPool):
    '''Establish pooled connections to a HTTP proxy.

    Args:
        proxy_address (tuple): Tuple containing host and port of the proxy
            server.
        connection_pool (:class:`.connection.ConnectionPool`): Connection pool
        proxy_ssl (bool): Whether to connect to the proxy using HTTPS.
        authentication (tuple): Tuple containing username and password.
        ssl_context: SSL context for SSL connections on TCP tunnels.
        host_filter (:class:`.proxy.hostfilter.HostFilter`): Host filter which
            for deciding whether a connection is routed through the proxy. A
            test result that returns True is routed through the proxy.
    '''
    def __init__(self, proxy_address, *args,
                 proxy_ssl=False, authentication=None, ssl_context=True,
                 host_filter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._proxy_address = proxy_address
        self._proxy_ssl = proxy_ssl
        self._authentication = authentication
        self._ssl_context = ssl_context
        self._host_filter = host_filter

        base64.b64encode(f'{authentication[0]}:{authentication[1]}'.encode('ascii')).decode('ascii')

        self._auth_header_value =  f"Basic {base64.b64encode(f'{authentication[0]}:{authentication[1]}'.encode('ascii')).decode('ascii')}" if authentication else None

        self._connection_map = {}

    async def acquire(self, host, port, use_ssl=False, host_key=None):
        await self.acquire_proxy(host, port, use_ssl=use_ssl,
                                      host_key=host_key)

    async def acquire_proxy(self, host, port, use_ssl=False, host_key=None,
                      tunnel=True):
        '''Check out a connection.

        This function is the same as acquire but with extra arguments
        concerning proxies.

        Coroutine.
        '''
        if self._host_filter and not self._host_filter.test(host):
            connection = await \
                super().acquire(host, port, use_ssl, host_key)

            return connection

        host_key = host_key or (host, port, use_ssl)
        proxy_host, proxy_port = self._proxy_address

        connection = await super().acquire(
            proxy_host, proxy_port, self._proxy_ssl, host_key=host_key
        )
        connection.proxied = True

        _logger.debug('Request for proxy connection.')

        if connection.closed():
            _logger.debug('Connecting to proxy.')
            await connection.connect()

            if tunnel:
                await self._establish_tunnel(connection, (host, port))

            if use_ssl:
                ssl_connection = await connection.start_tls(self._ssl_context)
                ssl_connection.proxied = True
                ssl_connection.tunneled = True

                self._connection_map[ssl_connection] = connection
                connection.wrapped_connection = ssl_connection

                return ssl_connection

        if connection.wrapped_connection:
            ssl_connection = connection.wrapped_connection
            self._connection_map[ssl_connection] = connection
            return ssl_connection
        else:
            return connection

    async def release(self, proxy_connection):
        connection = self._connection_map.pop(proxy_connection, proxy_connection)
        await super().release(connection)

    def no_wait_release(self, proxy_connection):
        connection = self._connection_map.pop(proxy_connection, proxy_connection)
        super().no_wait_release(connection)

    async def _establish_tunnel(self, connection, address):
        '''Establish a TCP tunnel.

        Coroutine.
        '''
        host = f'[{address[0]}]' if ':' in address[0] else address[0]
        port = address[1]
        request = RawRequest('CONNECT', f'{host}:{port}')

        self.add_auth_header(request)

        stream = Stream(connection, keep_alive=True)

        _logger.debug('Sending Connect.')
        await stream.write_request(request)

        _logger.debug('Read proxy response.')
        response = await stream.read_response()

        if response.status_code != 200:
            debug_file = io.BytesIO()
            _logger.debug('Read proxy response body.')
            await stream.read_body(request, response, file=debug_file)

            debug_file.seek(0)
            _logger.debug(ascii(debug_file.read()))

        if response.status_code == 200:
            connection.tunneled = True
        else:
            raise NetworkError(
                f'Proxy does not support CONNECT: {response.status_code} {wpull.string.printable_str(response.reason)}'
            )

    def add_auth_header(self, request):
        '''Add the username and password to the HTTP request.'''
        if self._authentication:
            request.fields['Proxy-Authorization'] = self._auth_header_value
