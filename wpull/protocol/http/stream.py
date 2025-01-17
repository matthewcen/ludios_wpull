# encoding=utf8
'''HTML protocol streamers.'''
import gettext
import http.client
import itertools
import logging
import re
import zlib

import asyncio

from wpull.protocol.abstract.stream import close_stream_on_error, \
    DataEventDispatcher
import wpull.decompression
from wpull.errors import NetworkError, ProtocolError
from wpull.protocol.http.chunked import ChunkedTransferReader
from wpull.protocol.http.request import Response
import wpull.protocol.http.util


_ = gettext.gettext
_logger = logging.getLogger(__name__)


DEFAULT_NO_CONTENT_CODES = frozenset(itertools.chain(
    range(100, 200),
    [http.client.NO_CONTENT, http.client.NOT_MODIFIED]
))
'''Status codes where a response body is prohibited.'''


class Stream(object):
    '''HTTP stream reader/writer.

    Args:
        connection (:class:`.connection.Connection`): An established
            connection.
        keep_alive (bool): If True, use HTTP keep-alive.
        ignore_length (bool): If True, Content-Length headers will be ignored.
            When using this option, `keep_alive` should be False.

    Attributes:
        connection: The underlying connection.
    '''
    def __init__(self, connection, keep_alive=True, ignore_length=False):
        self._connection = connection
        self._keep_alive = keep_alive
        self._ignore_length = ignore_length
        self._data_event_dispatcher = DataEventDispatcher()
        self._read_size = 4096
        self._decompressor = None

    @property
    def connection(self):
        return self._connection

    @property
    def data_event_dispatcher(self) -> DataEventDispatcher:
        return self._data_event_dispatcher

    @close_stream_on_error
    async def write_request(self, request, full_url=False):
        '''Send the request's HTTP status line and header fields.

        This class will automatically connect the connection if the
        connection is closed.

        Coroutine.
        '''
        _logger.debug('Sending headers.')

        if hasattr(request, 'prepare_for_send'):
            request.prepare_for_send(full_url=full_url)

        if self._ignore_length:
            request.fields['Connection'] = 'close'

        data = request.to_bytes()

        self._data_event_dispatcher.notify_write(data)

        # XXX: Connection lost is raised too early on Python 3.2, 3.3 so
        # don't flush but check for connection closed on reads
        await self._connection.write(data, drain=False)

    @close_stream_on_error
    async def write_body(self, file, length=None):
        '''Send the request's content body.

        Coroutine.
        '''
        _logger.debug('Sending body.')

        file_is_async = (asyncio.iscoroutine(file.read) or
                         asyncio.iscoroutinefunction(file.read))

        _logger.debug(f"Body is async: {file_is_async}")

        if length is not None:
            bytes_left = length

        while True:
            if length is not None:
                if bytes_left <= 0:
                    break
                read_size = min(bytes_left, self._read_size)
            else:
                read_size = self._read_size

            if file_is_async:
                data = await file.read(read_size)
            else:
                data = file.read(read_size)

            if not data:
                break

            self._data_event_dispatcher.notify_write(data)

            if bytes_left <= self._read_size:
                # XXX: Connection lost is raised too early on Python 3.2, 3.3
                # so don't flush on the last chunk but check for connection
                # closed on reads
                drain = False
            else:
                drain = True

            await self._connection.write(data, drain=drain)

            if length is not None:
                bytes_left -= len(data)

    @close_stream_on_error
    async def read_response(self, response=None):
        '''Read the response's HTTP status line and header fields.

        Coroutine.
        '''
        _logger.debug('Reading header.')

        if response is None:
            response = Response()

        header_lines = []
        bytes_read = 0

        while True:
            try:
                data = await self._connection.readline()
            except ValueError as error:
                raise ProtocolError(
                    f'Invalid header: {error}') from error

            self._data_event_dispatcher.notify_read(data)

            if not data.endswith(b'\n'):
                raise NetworkError('Connection closed.')
            elif data in (b'\r\n', b'\n'):
                break

            header_lines.append(data)
            assert data.endswith(b'\n')

            bytes_read += len(data)

            if bytes_read > 32768:
                raise ProtocolError('Header too big.')

        if not header_lines:
            raise ProtocolError('No header received.')

        response.parse(b''.join(header_lines))

        return response

    @close_stream_on_error
    async def read_body(self, request, response, file=None, raw=False):
        '''Read the response's content body.

        Coroutine.
        '''
        if is_no_body(request, response):
            return

        if not raw:
            self._setup_decompressor(response)

        read_strategy = self.get_read_strategy(response)

        if self._ignore_length and read_strategy == 'length':
            read_strategy = 'close'

        if read_strategy == 'chunked':
            await self._read_body_by_chunk(response, file, raw=raw)
        elif read_strategy == 'length':
            await self._read_body_by_length(response, file)
        else:
            await self._read_body_until_close(response, file)

        should_close = wpull.protocol.http.util.should_close(
            request.version, response.fields.get('Connection'))

        if not self._keep_alive or should_close:
            _logger.debug('Not keep-alive. Closing connection.')
            self.close()

    async def _read_body_until_close(self, response, file):
        '''Read the response until the connection closes.

        Coroutine.
        '''
        _logger.debug('Reading body until close.')

        file_is_async = hasattr(file, 'drain')

        while True:
            data = await self._connection.read(self._read_size)

            if not data:
                break

            self._data_event_dispatcher.notify_read(data)

            content_data = self._decompress_data(data)

            if file:
                file.write(content_data)

                if file_is_async:
                    await file.drain()

        content_data = self._flush_decompressor()

        if file:
            file.write(content_data)

            if file_is_async:
                await file.drain()

    async def _read_body_by_length(self, response, file):
        '''Read the connection specified by a length.

        Coroutine.
        '''
        _logger.debug('Reading body by length.')

        file_is_async = hasattr(file, 'drain')

        try:
            body_size = int(response.fields['Content-Length'])

            if body_size < 0:
                raise ValueError('Content length cannot be negative.')

        except ValueError as error:
            _logger.warning("Invalid content length: {error}")

            await self._read_body_until_close(response, file)
            return

        bytes_left = body_size

        while bytes_left > 0:
            data = await self._connection.read(self._read_size)

            if not data:
                break

            bytes_left -= len(data)

            if bytes_left < 0:
                data = data[:bytes_left]

                _logger.warning(_('Content overrun.'))
                self.close()

            self._data_event_dispatcher.notify_read(data)

            content_data = self._decompress_data(data)

            if file:
                file.write(content_data)

                if file_is_async:
                    await file.drain()

        if bytes_left > 0:
            raise NetworkError('Connection closed.')

        content_data = self._flush_decompressor()

        if file and content_data:
            file.write(content_data)

            if file_is_async:
                await file.drain()

    async def _read_body_by_chunk(self, response, file, raw=False):
        '''Read the connection using chunked transfer encoding.

        Coroutine.
        '''
        reader = ChunkedTransferReader(self._connection)

        file_is_async = hasattr(file, 'drain')

        while True:
            chunk_size, data = await reader.read_chunk_header()

            self._data_event_dispatcher.notify_read(data)
            if raw:
                file.write(data)

            if not chunk_size:
                break

            while True:
                content, data = await reader.read_chunk_body()

                self._data_event_dispatcher.notify_read(data)

                if not content:
                    if raw:
                        file.write(data)

                    break

                content = self._decompress_data(content)

                if file:
                    file.write(content)

                    if file_is_async:
                        await file.drain()

        content = self._flush_decompressor()

        if file:
            file.write(content)

            if file_is_async:
                await file.drain()

        trailer_data = await reader.read_trailer()

        self._data_event_dispatcher.notify_read(trailer_data)

        if file and raw:
            file.write(trailer_data)

            if file_is_async:
                await file.drain()

        response.fields.parse(trailer_data)

    @classmethod
    def get_read_strategy(cls, response):
        '''Return the appropriate algorithm of reading response.

        Returns:
            str: ``chunked``, ``length``, ``close``.
        '''
        chunked_match = re.match(
            r'chunked($|;)',
            response.fields.get('Transfer-Encoding', '')
        )

        if chunked_match:
            return 'chunked'
        elif 'Content-Length' in response.fields:
            return 'length'
        else:
            return 'close'

    def _setup_decompressor(self, response):
        '''Set up the content encoding decompressor.'''
        encoding = response.fields.get('Content-Encoding', '').lower()

        if encoding == 'gzip':
            self._decompressor = wpull.decompression.GzipDecompressor()
        elif encoding == 'deflate':
            self._decompressor = wpull.decompression.DeflateDecompressor()
        else:
            self._decompressor = None

    def _decompress_data(self, data):
        '''Decompress the given data and return the uncompressed data.'''
        if self._decompressor:
            try:
                return self._decompressor.decompress(data)
            except zlib.error as error:
                raise ProtocolError(
                    f'zlib error: {error}.'
                ) from error
        else:
            return data

    def _flush_decompressor(self):
        '''Return any data left in the decompressor.'''
        if self._decompressor:
            try:
                return self._decompressor.flush()
            except zlib.error as error:
                raise ProtocolError(
                    f'zlib flush error: {error}.'
                ) from error
        else:
            return b''

    def closed(self):
        '''Return whether the connection is closed.'''
        return self._connection.closed()

    def close(self):
        '''Close the connection.'''
        self._connection.close()

    async def reconnect(self):
        '''Connect the connection if needed.

        Coroutine.
        '''
        if self._connection.closed():
            self._connection.reset()

            await self._connection.connect()


def is_no_body(request, response, no_content_codes=DEFAULT_NO_CONTENT_CODES):
    '''Return whether a content body is not expected.'''
    if 'Content-Length' not in response.fields \
            and 'Transfer-Encoding' not in response.fields \
            and (
                response.status_code in no_content_codes
                or request.method.upper() == 'HEAD'
            ):
        return True
    else:
        return False
