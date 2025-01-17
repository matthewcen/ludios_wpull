import abc
import asyncio
import enum
import gettext
import logging

import time
from typing import Optional, Sequence, TypeVar, Generic, Iterator, Tuple, Set

_logger = logging.getLogger(__name__)

POISON_PILL = object()
ITEM_PRIORITY = 1
POISON_PRIORITY = 0


WorkItemT = TypeVar('WorkItemT')


class ItemTask(Generic[WorkItemT], metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def process(self, work_item: WorkItemT):
        pass


class ItemSource(Generic[WorkItemT], metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def get_item(self) -> Optional[WorkItemT]:
        pass


class ItemQueue(Generic[WorkItemT]):
    def __init__(self):
        self._queue = asyncio.PriorityQueue()
        self._unfinished_items = 0
        self._worker_ready_condition = asyncio.Condition()
        self._entry_count = 0

    async def put_item(self, item: WorkItemT):
        while self._queue.qsize() > 0:
            await self._worker_ready_condition.acquire()
            await self._worker_ready_condition.wait()
            self._worker_ready_condition.release()

        self._unfinished_items += 1
        self._queue.put_nowait((ITEM_PRIORITY, self._entry_count, item))
        self._entry_count += 1

    def put_poison_nowait(self):
        self._queue.put_nowait((POISON_PRIORITY, self._entry_count, POISON_PILL))
        self._entry_count += 1

    async def get(self) -> WorkItemT:
        priority, entry_count, item = await self._queue.get()

        await self._worker_ready_condition.acquire()
        self._worker_ready_condition.notify_all()
        self._worker_ready_condition.release()

        return item

    async def item_done(self):
        self._unfinished_items -= 1
        assert self._unfinished_items >= 0

        await self._worker_ready_condition.acquire()
        self._worker_ready_condition.notify_all()
        self._worker_ready_condition.release()

    @property
    def unfinished_items(self) -> int:
        return self._unfinished_items

    async def wait_for_worker(self):
        await self._worker_ready_condition.acquire()
        await self._worker_ready_condition.wait()
        self._worker_ready_condition.release()


class Worker(object):
    def __init__(self, item_queue: ItemQueue, tasks: Sequence[ItemTask]):
        self._item_queue = item_queue
        self._tasks = tasks
        self._worker_id_counter = 0

    async def process_one(self, _worker_id=None):
        item = await self._item_queue.get()

        if item == POISON_PILL:
            return item

        _logger.debug(f"Worker id {_worker_id} Processing item {item}")

        for task in self._tasks:
            await task.process(item)

        _logger.debug(f"Worker id {_worker_id} Processed item {item}")

        await self._item_queue.item_done()

        return item

    async def process(self):
        worker_id = self._worker_id_counter
        self._worker_id_counter += 1

        _logger.debug('Worker process id=%s', worker_id)

        while True:
            item = await self.process_one(_worker_id=worker_id)

            if item == POISON_PILL:
                _logger.debug('Worker quitting.')
                break


class Producer(object):
    def __init__(self, item_source: ItemSource, item_queue: ItemQueue):
        self._item_source = item_source
        self._item_queue = item_queue
        self._running = False

    async def process_one(self):
        _logger.debug('Get item from source')
        item = await self._item_source.get_item()

        if item:
            await self._item_queue.put_item(item)
            return item

    async def process(self):
        self._running = True

        while self._running:
            item = await self.process_one()

            if not item and self._item_queue.unfinished_items == 0:
                self.stop()
                break
            elif not item:
                await self._item_queue.wait_for_worker()

    def stop(self):
        if self._running:
            _logger.debug('Producer stopping.')
            self._running = False


class PipelineState(enum.Enum):
    stopped = 'stopped'
    running = 'running'
    stopping = 'stopping'


class Pipeline(object):
    def __init__(self, item_source: ItemSource, tasks: Sequence[ItemTask],
                 item_queue: Optional[ItemQueue]=None):
        self._item_queue = item_queue or ItemQueue()
        self._tasks = tasks
        self._producer = Producer(item_source, self._item_queue)
        self._worker = Worker(self._item_queue, tasks)

        self._state = PipelineState.stopped
        self._concurrency = 1
        self._producer_task = None
        self._worker_tasks = set()
        self._unpaused_event = asyncio.Event()

        self.skippable = False

    @property
    def tasks(self):
        return self._tasks

    async def process(self):
        if self._state == PipelineState.stopped:
            self._state = PipelineState.running
            self._producer_task = asyncio.get_event_loop().create_task(self._run_producer_wrapper())
            self._unpaused_event.set()

        while self._state == PipelineState.running:
            await self._process_one_worker()

        await self._shutdown_processing()

    async def _process_one_worker(self):
        assert self._state == PipelineState.running, self._state

        while len(self._worker_tasks) < self._concurrency:
            _logger.debug('Creating worker')
            worker_task = asyncio.get_event_loop().create_task(self._worker.process())
            self._worker_tasks.add(worker_task)

        if self._worker_tasks:
            wait_coroutine = asyncio.wait(
                self._worker_tasks, return_when=asyncio.FIRST_COMPLETED)
            done_tasks = (await wait_coroutine)[0]

            _logger.debug('%d worker tasks completed', len(done_tasks))

            for task in done_tasks:
                task.result()
                self._worker_tasks.remove(task)
        else:
            await self._unpaused_event.wait()

    async def _shutdown_processing(self):
        assert self._state == PipelineState.stopping

        _logger.debug('Exited workers loop.')

        if self._worker_tasks:
            _logger.debug('Waiting for workers to stop.')
            await asyncio.wait(self._worker_tasks)

        _logger.debug('Waiting for producer to stop.')

        self._worker_tasks.clear()

        await self._producer_task

        self._state = PipelineState.stopped

    def stop(self):
        if self._state == PipelineState.running:
            self._state = PipelineState.stopping
            self._producer.stop()
            self._kill_workers()

    async def _run_producer_wrapper(self):
        '''Run the producer, if exception, stop engine.'''
        try:
            await self._producer.process()
        except Exception as error:
            if not isinstance(error, StopIteration):
                # Stop the workers so the producer exception will be handled
                # when we finally await this coroutine
                _logger.debug('Producer died.', exc_info=True)
                self.stop()
            raise
        else:
            self.stop()

    def _kill_workers(self):
        for dummy in range(len(self._worker_tasks)):
            _logger.debug('Put poison pill.')
            self._item_queue.put_poison_nowait()

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @concurrency.setter
    def concurrency(self, new_concurrency: int):
        if new_concurrency < 0:
            raise ValueError('Concurrency cannot be negative')

        change = new_concurrency - self._concurrency
        self._concurrency = new_concurrency

        if self._state != PipelineState.running:
            return

        if change < 0:
            for dummy in range(abs(change)):
                _logger.debug('Put poison pill for less workers.')
                self._item_queue.put_poison_nowait()
        elif change > 0:
            _logger.debug('Put 1 poison pill to trigger more workers.')
            self._item_queue.put_poison_nowait()

        if self._concurrency:
            self._unpaused_event.set()
        else:
            self._unpaused_event.clear()

    def _warn_discarded_items(self):
        plural = self._item_queue.unfinished_items
        _logger.warning(f"Discarding {self._item_queue.unfinished_items} unprocessed item{'s' if plural else ''}.")


class PipelineSeries(object):
    def __init__(self, pipelines: Iterator[Pipeline]):
        self._pipelines = tuple(pipelines)
        self._concurrency = 1
        self._concurrency_pipelines = set()

    @property
    def pipelines(self) -> Tuple[Pipeline]:
        return self._pipelines

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @concurrency.setter
    def concurrency(self, new_concurrency: int):
        self._concurrency = new_concurrency

        for pipeline in self._pipelines:
            if pipeline in self._concurrency_pipelines:
                pipeline.concurrency = new_concurrency

    @property
    def concurrency_pipelines(self) -> Set[Pipeline]:
        return self._concurrency_pipelines
