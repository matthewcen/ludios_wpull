'''Resource monitor.'''
import collections
import logging
from typing import NamedTuple


_logger = logging.getLogger(__name__)


try:
    import psutil
except ImportError as error:
    _logger.warning(f"psutil: {error}. Resource monitoring will be unavailable.")
    psutil = None


class ResourceInfo(NamedTuple):
    # Resource level information
    path: str  # File path of the resource. ``None`` is provided for memory usage.
    free: int  # Number of bytes available.
    limit: int  # Minimum bytes of the resource.
    name: str = "ResourceInfoType"


class ResourceMonitor(object):
    '''Monitor available resources such as disk space and memory.

    Args:
        resource_paths (list): List of paths to monitor. Recommended paths
            include temporary directories and the current working directory.
        min_disk (int, optional): Minimum disk space in bytes.
        min_memory (int, optional): Minimum memory in bytes.
    '''
    def __init__(self, resource_paths: list[str] = ('/',), min_disk: int = 10000,
                 min_memory: int = 10000):
        assert not isinstance(resource_paths, str), type(resource_paths)

        self._resource_paths: list[str] = resource_paths
        self._min_disk: int = min_disk
        self._min_memory: int = min_memory

        if not psutil:
            raise OSError("module 'psutil' is not available")

    def get_info(self) -> ResourceInfo:
        '''Return ResourceInfo instances.'''
        if self._min_disk:
            for path in self._resource_paths:
                usage = psutil.disk_usage(path)

                yield ResourceInfo(path, usage.free, self._min_disk)

        if self._min_memory:
            usage = psutil.virtual_memory()

            yield ResourceInfo(None, usage.available, self._min_memory)

    def check(self) -> ResourceInfo:
        '''Check resource levels.

         Returns:
            None, ResourceInfo: If None is provided, no levels are exceeded.
                Otherwise, the first ResourceInfo exceeding limits is returned.
        '''
        info: ResourceInfo
        for info in self.get_info():
            if info.free < info.limit:
                return info
