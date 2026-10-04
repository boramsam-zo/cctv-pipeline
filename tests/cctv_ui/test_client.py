"""Server-side stored-media route selection; no HTTP or analysis calls."""
import unittest
from unittest.mock import patch

from apps.cctv_ui import client


class MediaClientTests(unittest.TestCase):
    def test_source_uses_whole_upload_route_not_event_clip(self):
        with patch.object(client, 'request', return_value=b'full-original') as request:
            for kind in ('source', 'original'):
                self.assertEqual(client.fetch_media('a' * 32, None, kind), b'full-original')
                request.assert_called_with('/jobs/' + 'a' * 32 + '/source', binary=True)

    def test_event_media_and_preview_keep_existing_read_routes(self):
        for kind in ('clip', 'roi_clip', 'frame', 'clip_preview', 'roi_clip_preview'):
            with self.subTest(kind=kind), patch.object(client, 'request', return_value=b'asset') as request:
                self.assertEqual(client.fetch_media('a' * 32, 'event_000', kind), b'asset')
                request.assert_called_once_with('/jobs/' + 'a' * 32 + '/media/event_000/' + kind,
                                                binary=True)

    def test_invalid_identifier_kind_or_event_never_requests(self):
        with patch.object(client, 'request') as request:
            for identifier, event, kind in (
                    ('../secret', None, 'source'), ('a' * 32, '../secret', 'frame'),
                    ('a' * 32, 'event_000', 'source.json'), ('a' * 32, None, 'clip')):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    client.fetch_media(identifier, event, kind)
            request.assert_not_called()

    def test_missing_source_api_capability_preserves_404(self):
        with patch.object(client, 'request', side_effect=client.APIError('not_found', 404)):
            with self.assertRaises(client.APIError) as caught:
                client.fetch_media('a' * 32, None, 'source')
        self.assertEqual(caught.exception.status, 404)


if __name__ == '__main__':
    unittest.main()
