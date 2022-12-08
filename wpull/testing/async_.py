import functools
import unittest

from tornado.platform.asyncio import BaseAsyncIOLoop
from tornado.testing import gen_test
import asyncio


class AsyncTestCase(unittest.IsolatedAsyncioTestCase()):


def async_test(func=None, timeout=30):
    # gen_test uses environment variable ASYNC_TEST_TIMEOUT as default if set, otherwise 5 seconds
    return gen_test(func, timeout)

class TornadoAsyncIOLoop(BaseAsyncIOLoop):
    def initialize(self, event_loop):
        super().initialize(event_loop)
