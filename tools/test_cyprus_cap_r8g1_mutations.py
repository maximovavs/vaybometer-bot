"""R8-G1 offline negative code mutation gate: every listed weakened guard must FAIL tests.

Runs in isolated tempfile copies only. No production imports, no network.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE / 'cyprus_cap_r8g1_contract.py'
TEST = HERE / 'test_cyprus_cap_r8g1_contract.py'

# One deliberate mutation at a time. A passing test suite is a dangerous survivor.
MUTATIONS = {
    'M01_state_clean': ("state: str = 'UNKNOWN'", "state: str = 'CLEAN_VERIFIED'"),
    'M02_allow_post': ("publication_action: str = 'NO_POST'", "publication_action: str = 'POST'"),
    'M03_claim_checked': ("official_warnings_checked: bool = False", "official_warnings_checked: bool = True"),
    'M04_claim_clean': ("clean_verified: bool = False", "clean_verified: bool = True"),
    'M05_claim_city_clear': ("city_cleared: bool = False", "city_cleared: bool = True"),
    'M06_allow_water': ("positive_water_guidance_allowed: bool = False", "positive_water_guidance_allowed: bool = True"),
    'M07_claim_inventory': ("national_inventory_complete: bool = False", "national_inventory_complete: bool = True"),
    'M08_wrong_authority': ("if oid != AUTHORITY_OID:", "if oid == AUTHORITY_OID:"),
    'M09_trust_sender': ("if cap_sender is not None:", "if cap_sender is None:"),
    'M10_city_mapping': ("return 'UNKNOWN_EMMA_CITY_MAPPING'", "return 'EMMA_ID_MAPPED'"),
    'M11_ignore_geography': ("if not match:", "if match:"),
    'M12_trust_cancel': ("return 'UNKNOWN_CAP_AUTHENTICITY'", "return 'CLEAN_VERIFIED'"),
    'M13_empty_feed_clean': ("return unknown('EMPTY_UNVERIFIED')", "return unknown('CLEAN_VERIFIED')"),
    'M14_ignore_sha256': ("hashlib.sha256(atom).hexdigest() != digest:", "hashlib.sha256(atom).hexdigest() == digest:"),
    'M15_time_boundary': ("if t < c:\n", "if t <= c:\n"),
    'M16_cap_url_wrong_country': ("/api/v1/warnings/feeds-cyprus/", "/api/v1/warnings/feeds-lithuania/"),
    'M17_bypass_http_status': ("if (source.get('http_status') != 200", "if (source.get('http_status') == 200"),
    'M18_accept_cap_wrong_status': ("atom_status != status", "atom_status == status"),
}


class NegativeMutations(unittest.TestCase):
    def test_all_safety_mutations_killed(self):
        original = CORE.read_text('utf-8')
        with tempfile.TemporaryDirectory(prefix='cy_r8g1_mutations_') as root:
            base = pathlib.Path(root)
            for label,(needle,replacement) in MUTATIONS.items():
                with self.subTest(mutation=label):
                    self.assertEqual(1, original.count(needle),
                                     f'mutation anchor drift: {label}')
                    dest = base / label
                    dest.mkdir()
                    (dest/CORE.name).write_text(original.replace(needle,replacement), 'utf-8')
                    shutil.copy2(TEST, dest / TEST.name)
                    p = subprocess.run([sys.executable, '-m', 'unittest',
                                        'test_cyprus_cap_r8g1_contract'], cwd=dest,
                                       capture_output=True, text=True, timeout=25)
                    self.assertIn('Ran 11 tests', p.stdout + p.stderr,
                                  f'{label}: test collection/import failure, not a valid mutation kill')
                    self.assertNotEqual(0, p.returncode,
                                        f'UNCAUGHT {label}: test suite passed after weakening guard')


if __name__ == '__main__':
    unittest.main(verbosity=2)
