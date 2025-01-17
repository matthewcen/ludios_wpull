import gzip
import os

from wpull.application.builder import Builder
from wpull.application.options import AppArgumentParser
from wpull.testing.integration.base import HTTPGoodAppTestCase
import wpull.testing.async_


class TestWARCHTTPGoodApp(HTTPGoodAppTestCase):
    def test_app_args_warc_size(self):
        arg_parser = AppArgumentParser()
        args = arg_parser.parse_args([
            self.get_url('/'),
            '--warc-file', 'test',
            '-4',
            '--no-robots',
            '--warc-max-size', '1k',
            '--warc-cdx'
        ])
        builder = Builder(args, unit_test=True)

        app = builder.build()
        exit_code = yield from app.run()

        self.assertTrue(os.path.exists('test-00000.warc.gz'))
        self.assertTrue(os.path.exists('test-meta.warc.gz'))
        self.assertTrue(os.path.exists('test.cdx'))

        self.assertEqual(0, exit_code)
        self.assertGreaterEqual(builder.factory['Statistics'].files, 1)

    def test_app_args_warc(self):
        arg_parser = AppArgumentParser()
        args = arg_parser.parse_args([
            self.get_url('/'),
            '--no-parent',
            '--recursive',
            '--page-requisites',
            '--warc-file', 'test',
            '-4',
            '--no-robots',
            '--no-warc-digests',
        ])
        builder = Builder(args, unit_test=True)

        app = builder.build()
        exit_code = yield from app.run()

        self.assertTrue(os.path.exists('test.warc.gz'))

        with gzip.GzipFile('test.warc.gz') as in_file:
            data = in_file.read()
            self.assertIn(b'FINISHED', data)

        self.assertEqual(0, exit_code)
        self.assertGreaterEqual(builder.factory['Statistics'].files, 1)

    def test_app_args_warc_with_cdx(self):
        arg_parser = AppArgumentParser()
        args = arg_parser.parse_args([
            self.get_url('/'),
            '--no-parent',
            '--warc-file', 'test',
            '-4',
            '--no-robots',
            '--warc-cdx',
        ])
        builder = Builder(args, unit_test=True)

        app = builder.build()
        exit_code = yield from app.run()
        self.assertEqual(0, exit_code)
        self.assertGreaterEqual(builder.factory['Statistics'].files, 1)

    def test_app_args_warc_dedup(self):
        arg_parser = AppArgumentParser()

        with open('dedup.cdx', 'wb') as out_file:
            out_file.write(b' CDX a k u\n')
            out_file.write(
                self.get_url('/static/my_file.txt').encode('ascii')
            )
            out_file.write(b' KQ4IUKATKL63FT5GMAE2YDRV3WERNL34')
            out_file.write(b' <under-the-deer>\n')

        args = arg_parser.parse_args([
            self.get_url('/static/my_file.txt'),
            '--no-parent',
            '--warc-file', 'test',
            '--no-warc-compression',
            '-4',
            '--no-robots',
            '--warc-dedup', 'dedup.cdx',
        ])

        builder = Builder(args, unit_test=True)
        app = builder.build()
        exit_code = yield from app.run()

        with open('test.warc', 'rb') as in_file:
            data = in_file.read()

            self.assertIn(b'KQ4IUKATKL63FT5GMAE2YDRV3WERNL34', data)
            self.assertIn(b'Type: revisit', data)
            self.assertIn(b'<under-the-deer>', data)

        self.assertEqual(0, exit_code)
        self.assertGreaterEqual(builder.factory['Statistics'].files, 1)
