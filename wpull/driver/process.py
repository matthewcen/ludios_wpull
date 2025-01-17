'''RPC processes.'''

import abc
import gettext
import json
import logging
import subprocess
import atexit
import errno
import time

import asyncio



_ = gettext.gettext
_logger = logging.getLogger(__name__)


class Process(object):
    '''Subprocess wrapper.'''
    def __init__(self, proc_args, stdout_callback=None, stderr_callback=None):
        self._proc_args = proc_args
        self._stdout_callback = stdout_callback
        self._stderr_callback = stderr_callback
        self._process = None
        self._stderr_reader = None
        self._stdout_reader = None

    @property
    def process(self):
        '''Return the underlying process.'''
        return self._process

    async def start(self, use_atexit=True):
        '''Start the executable.

        Args:
            use_atexit (bool): If True, the process will automatically be
                terminated at exit.
        '''
        assert not self._process

        _logger.debug('Starting process %s', self._proc_args)

        process_future = asyncio.create_subprocess_exec(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            *self._proc_args
        )
        self._process = await process_future

        self._stderr_reader = asyncio.ensure_future(self._read_stderr())
        self._stdout_reader = asyncio.ensure_future(self._read_stdout())

        if use_atexit:
            atexit.register(self.close)

    def close(self):
        '''Terminate or kill the subprocess.

        This function is blocking.
        '''
        if not self._process:
            return

        if self._process.returncode is not None:
            return

        _logger.debug('Terminate process.')

        try:
            self._process.terminate()
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise

        for dummy in range(10):
            if self._process.returncode is not None:
                return

            time.sleep(0.05)

        _logger.debug('Failed to terminate. Killing.')

        try:
            self._process.kill()
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise

    async def _read_stdout(self):
        '''Continuously read the stdout for messages.'''
        try:
            while self._process.returncode is None:
                line = await self._process.stdout.readline()

                _logger.debug('Read stdout line %s', repr(line))

                if not line:
                    break

                if self._stdout_callback:
                    await self._stdout_callback(line)

        except Exception:
            _logger.exception('Unhandled read stdout exception.')
            raise

    async def _read_stderr(self):
        '''Continuously read stderr for error messages.'''
        try:
            while self._process.returncode is None:
                line = await self._process.stderr.readline()

                if not line:
                    break

                if self._stderr_callback:
                    await self._stderr_callback(line)

        except Exception:
            _logger.exception('Unhandled read stderr exception.')
            raise
