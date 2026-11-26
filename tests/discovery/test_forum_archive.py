"""Forum mining keeps native leads and proves only what its posts contain."""
import unittest

from sonos_firmware.forum_archive import lead_urls


class ForumLeadTests(unittest.TestCase):
    def test_native_manifest_and_installer_links_are_kept(self):
        roots, installers = lead_urls(
            '<a href="https://update.sonos.com/firmware/Prod/2025-Sonos-19-vNxIWcz6o6-SP-1-Prima/update.upm">'
            'x</a> <a href="https://update-software.sonos.com/software/mac/mdcr/SonosDesktopController131.dmg">'
            'y</a>')
        self.assertEqual(roots, ['https://update.sonos.com/firmware/Prod/'
                                 '2025-Sonos-19-vNxIWcz6o6-SP-1-Prima/update.upm'])
        self.assertEqual(installers, ['https://update-software.sonos.com/software/mac/mdcr/'
                                      'SonosDesktopController131.dmg'])


    def test_unrelated_hosts_and_payload_bytes_are_ignored(self):
        roots, installers = lead_urls(
            'https://example.com/firmware/Prod/1.0/update.upm '
            'https://update.sonos.com/firmware/Prod/a/thing.upd '
            'https://www.last.fm/api/show/track.updateNowPlaying')
        self.assertEqual(roots, ['https://update.sonos.com/firmware/Prod/a/update.upm'])
        self.assertEqual(installers, [])


if __name__ == '__main__':
    unittest.main()
