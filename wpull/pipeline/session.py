import asyncio
import gettext
import logging

from typing import Optional

from wpull.database.base import AddURLInfo, NotFound
from wpull.pipeline.app import AppSession
from wpull.pipeline.item import URLRecord, Status, URLResult, URLProperties, \
    URLData, LinkType
from wpull.pipeline.pipeline import ItemSource
from wpull.protocol.abstract.request import URLPropertyMixin, \
    ProtocolResponseMixin, BaseResponse, BaseRequest
from wpull.url import parse_url_or_log

_logger = logging.getLogger(__name__)
_ = gettext.gettext


class ItemSession(object):
    """Item for a URL that needs to processed."""
    def __init__(self, app_session: AppSession, url_record: URLRecord):
        self.app_session = app_session
        self.url_record = url_record
        self._processed: bool = False
        self._try_count_incremented: bool = False
        self._add_url_batch: list = []

        self._request = None
        self._response = None

    @property
    def is_virtual(self) -> bool:
        return False

    @property
    def is_processed(self):
        """Return whether the item has been processed."""
        return self._processed

    @property
    def request(self) -> BaseRequest:
        return self._request

    @request.setter
    def request(self, request: BaseRequest):
        self._request = request

    @property
    def response(self) -> BaseResponse:
        return self._response

    @response.setter
    def response(self, response: BaseResponse):
        self._response = response

    def skip(self):
        """Mark the item as processed without download."""
        _logger.debug(f"Skipping ‘{self.url_record.url}’.")
        self.app_session.factory['URLTable'].check_in(self.url_record.url, Status.skipped)

        self._processed = True

    def set_status(self, status: Status, increment_try_count: bool = True,
                   filename: str = None):
        """Mark the item with the given status.

        Args:
            status: a value from :class:`Status`.
            increment_try_count: if True, increment the ``try_count``
                value
        """
        url = self.url_record.url
        assert not self._try_count_incremented, (url, status)

        if increment_try_count:
            self._try_count_incremented = True

        _logger.debug(f"Marking URL {url} status {status}.")

        url_result = URLResult()
        url_result.filename = filename

        self.app_session.factory['URLTable'].check_in(
            url,
            status,
            increment_try_count=increment_try_count,
            url_result=url_result,
        )

        self._processed = True

    def add_url(self, url: str, url_properites: Optional[URLProperties]=None,
                url_data: Optional[URLData]=None):
        url_info = parse_url_or_log(url)
        if not url_info:
            return

        url_properties = url_properites or URLProperties()
        url_data = url_data or URLData()
        add_url_info = AddURLInfo(url, url_properties, url_data)

        self._add_url_batch.append(add_url_info)

        if len(self._add_url_batch) >= 1000:
            self.app_session.factory['URLTable'].add_many(self._add_url_batch)
            self._add_url_batch.clear()

    def add_child_url(self, url: str, inline: bool=False,
                      link_type: Optional[LinkType]=None,
                      post_data: Optional[str]=None,
                      level: Optional[int]=None,
                      replace: bool=False):
        """Add links scraped from the document with automatic values.

        Args:
            url: A full URL. (It can't be a relative path.)
            inline: Whether the URL is an embedded object.
            link_type: Expected link type.
            post_data: URL encoded form data. The request will be made using
                POST. (Don't use this to upload files.)
            level: The child depth of this URL.
            replace: Whether to replace the existing entry in the database
                table so it will be redownloaded again.

        This function provides values automatically for:

        * ``inline``
        * ``level``
        * ``parent``: The referrering page.
        * ``root``

        See also :meth:`add_url`.
        """
        url_properties = URLProperties()
        url_properties.level = self.url_record.level + 1 if level is None else level
        url_properties.inline_level = (self.url_record.inline_level or 0) + 1 if inline else None
        url_properties.parent_url = self.url_record.url
        url_properties.root_url = self.url_record.root_url or self.url_record.url
        url_properties.link_type = link_type
        url_data = URLData()
        url_data.post_data = post_data

        if replace:
            self.app_session.factory['URLTable'].remove_many([url])

        self.add_url(url, url_properties, url_data)

    def child_url_record(self, url: str, inline: bool=False,
                         link_type: Optional[LinkType]=None,
                         post_data: Optional[str]=None,
                         level: Optional[int]=None):
        """Return a child URLRecord.

        This function is useful for testing filters before adding to table.
        """
        url_record = URLRecord()
        url_record.url = url
        url_record.status = Status.todo
        url_record.try_count = 0
        url_record.level = self.url_record.level + 1 if level is None else level
        url_record.root_url = self.url_record.root_url or self.url_record.url
        url_record.parent_url = self.url_record.url
        url_record.inline_level = (self.url_record.inline_level or 0) + 1 if inline else 0
        url_record.link_type = link_type
        url_record.post_data = post_data

        return url_record

    def finish(self):
        self.app_session.factory['URLTable'].add_many(self._add_url_batch)
        self._add_url_batch.clear()

    def update_record_value(self, **kwargs):
        self.app_session.factory['URLTable'].update_one(self.url_record.url, **kwargs)
        for key, value in kwargs.items():
            setattr(self.url_record, key, value)


class URLItemSource(ItemSource[ItemSession]):
    def __init__(self, app_session: AppSession):
        self._app_session = app_session

    async def get_item(self) -> Optional[ItemSession]:
        try:
            url_record = self._app_session.factory['URLTable'].check_out(Status.todo)
        except NotFound:
            try:
                url_record = self._app_session.factory['URLTable'].check_out(Status.error)
            except NotFound:
                return None

        item_session = ItemSession(self._app_session, url_record)
        return item_session
