import codecs
import gettext
import itertools
import asyncio
import logging
import sys

from wpull.database.base import AddURLInfo
from wpull.database.sqltable import GenericSQLURLTable
from wpull.pipeline.app import AppSession
from wpull.pipeline.pipeline import ItemTask
import wpull.util
import wpull.url

_ = gettext.gettext
_logger = logging.getLogger(__name__)


class DatabaseSetupTask(ItemTask[AppSession]):
    async def process(self, session: AppSession):
        if session.args.database_uri:
            session.factory.class_map[
                'URLTableImplementation'] = GenericSQLURLTable
            url_table_impl = session.factory.new(
                'URLTableImplementation', session.args.database_uri)
        else:
            url_table_impl = session.factory.new(
                'URLTableImplementation', path=session.args.database)

        url_table = session.factory.new('URLTable', url_table_impl)

        # TODO: add a test for this
        _logger.debug(_('Releasing any in-progress items in database.'))
        url_table.release()


class InputURLTask(ItemTask[AppSession]):
    async def process(self, session: AppSession):
        url_table = session.factory['URLTable']
        url_count = 0

        for batch in wpull.util.grouper(self._read_input_urls(session), 1000):
            urls = url_table.add_many(AddURLInfo(url_info.url, None, None) for url_info in batch if url_info)
            # TODO: attach hook for notifying progress
            url_count += len(urls)

        # TODO: check if database is empty
        # TODO: add a test for this
        # if not url_count:
        #     raise ValueError(_('No URLs found in input file.'))

    @classmethod
    def _read_input_urls(cls, session: AppSession, default_scheme='http'):
        '''Read the URLs provided by the user.'''

        url_string_iter = session.args.urls or ()
        # FIXME: url rewriter isn't created yet
        url_rewriter = session.factory.get('URLRewriter')

        if session.args.input_file:
            if session.args.force_html:
                lines = cls._input_file_as_html_links(session)
            else:
                lines = cls._input_file_as_lines(session)

            url_string_iter = itertools.chain(url_string_iter, lines)

        base_url = session.args.base

        for url_string in url_string_iter:
            _logger.debug(f"Parsing URL {url_string}")

            if base_url:
                url_string = wpull.url.urljoin(base_url, url_string)

            try:
                url_info = wpull.url.URLInfo.parse(
                    url_string, default_scheme=default_scheme)

                _logger.debug(f"Parsed URL {url_info}'")

                if url_rewriter:
                    # TODO: this logic should be a hook
                    url_info = url_rewriter.rewrite(url_info)
                    _logger.debug(f"Rewritten URL {url_info}")

                yield url_info

            except ValueError as e:
                _logger.info(f"Invalid URL {url_string}: {e}")

    @classmethod
    def _input_file_as_lines(cls, session: AppSession):
        '''Read lines from input file and return them.'''
        if session.args.input_file == sys.stdin:
            input_file = session.args.input_file
        else:
            reader = codecs.getreader(session.args.local_encoding or 'utf-8')
            input_file = reader(session.args.input_file)

        return input_file

    @classmethod
    def _input_file_as_html_links(cls, session: AppSession):
        '''Read input file as HTML and return the links.'''
        scrape_result = session.factory['HTMLScraper'].scrape_file(
            session.args.input_file,
            encoding=session.args.local_encoding or 'utf-8'
        )

        for context in scrape_result.link_contexts:
            yield context.link
