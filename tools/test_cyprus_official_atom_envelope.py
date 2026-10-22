#!/usr/bin/env python3
"""R8-B0 offline-only Golden Set; all mutants are explicitly synthetic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
SOURCE_FILE = ROOT / "cyprus_official_atom_envelope.py"
spec = importlib.util.spec_from_file_location("cyprus_official_atom_envelope", SOURCE_FILE)
assert spec is not None and spec.loader is not None
import sys
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

ATOM_URL = module.OFFICIAL_CYPRUS_ATOM_URL
RAW = (FIXTURES / "cyprus_atom_empty_2026-10-22.xml").read_bytes()
GOLDEN = json.loads((FIXTURES / "cyprus_atom_b0_golden_set.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(GOLDEN["real_capture_received_at"])
HTTP_DATE = GOLDEN["real_capture_http_date"]


def _replace_once(text: bytes, before: bytes, after: bytes) -> bytes:
    if text.count(before) != 1:
        raise AssertionError(f"fixture anchor count != 1 for {before!r}")
    return text.replace(before, after, 1)


def make_case(kind: str) -> dict:
    inp = dict(xml_bytes=RAW, source_url=ATOM_URL, http_status=200,
               content_type="application/atom+xml; charset=utf-8",
               http_date=HTTP_DATE, received_at=NOW)
    if kind == "none":
        pass
    elif kind == "wrong_url": inp["source_url"] = "https://example.test/atom"
    elif kind == "http_error": inp["http_status"] = 503
    elif kind == "wrong_content_type": inp["content_type"] = "text/html"
    elif kind == "missing_http_date": inp["http_date"] = None
    elif kind == "wrong_http_date": inp["http_date"] = "not a date"
    elif kind == "date_clock_skew": inp["received_at"] = NOW + timedelta(hours=2)
    elif kind == "naive_receive": inp["received_at"] = NOW.replace(tzinfo=None)
    elif kind == "wrong_payload_type": inp["xml_bytes"] = RAW.decode("utf-8")
    elif kind == "zero_bytes": inp["xml_bytes"] = b""
    elif kind == "huge_xml": inp["xml_bytes"] = RAW + b" " * (module.MAX_XML_BYTES + 1)
    elif kind == "doctype": inp["xml_bytes"] = _replace_once(RAW, b"<feed ", b"<!DOCTYPE feed><feed ")
    elif kind == "entity": inp["xml_bytes"] = _replace_once(RAW, b"<feed ", b"<!ENTITY x 'a'><feed ")
    elif kind == "nul_byte": inp["xml_bytes"] = RAW + b"\x00"
    elif kind == "malformed": inp["xml_bytes"] = RAW[:-7]
    elif kind == "wrong_xml_root": inp["xml_bytes"] = _replace_once(_replace_once(RAW, b"<feed ", b"<other "), b"</feed>", b"</other>")
    elif kind == "wrong_country": inp["xml_bytes"] = _replace_once(RAW, b":CY</id>", b":DE</id>")
    elif kind == "duplicate_id": inp["xml_bytes"] = _replace_once(RAW, b"</id>", b"</id><id>duplicate</id>")
    elif kind == "wrong_self_href": inp["xml_bytes"] = _replace_once(RAW, ATOM_URL.encode(), b"https://example.test/atom")
    elif kind == "duplicate_self_link":
        line = next(line for line in RAW.splitlines(keepends=True) if b'rel="self"' in line)
        inp["xml_bytes"] = _replace_once(RAW, line, line + line)
    elif kind == "bad_self_type": inp["xml_bytes"] = _replace_once(RAW, b'type="application/atom+xml"', b'type="text/html"')
    elif kind == "missing_generator": inp["xml_bytes"] = _replace_once(RAW, b"MeteoAlarm Producer Server", b"")
    elif kind == "missing_title": inp["xml_bytes"] = _replace_once(RAW, b"MeteoAlarm - Alerting Europe for Extreme Weather", b"")
    elif kind == "wrong_author": inp["xml_bytes"] = _replace_once(RAW, b"<name>meteoalarm.org</name>", b"<name>example.test</name>")
    elif kind == "missing_updated": inp["xml_bytes"] = _replace_once(RAW, b"2026-10-22T10:05:03.855448Z", b"")
    elif kind == "naive_updated": inp["xml_bytes"] = _replace_once(RAW, b"2026-10-22T10:05:03.855448Z", b"2026-10-22T10:05:03")
    elif kind == "stale_updated": inp["xml_bytes"] = _replace_once(RAW, b"2026-10-22T10:05:03.855448Z", b"2026-10-21T10:05:03Z")
    elif kind == "future_updated": inp["xml_bytes"] = _replace_once(RAW, b"2026-10-22T10:05:03.855448Z", b"2026-10-23T10:05:03Z")
    elif kind == "one_entry_without_cap": inp["xml_bytes"] = _replace_once(RAW, b"</feed>", b"<entry><id>synthetic</id></entry></feed>")
    else: raise AssertionError(f"unknown Golden Set mutation: {kind}")
    return inp


class CyprusAtomEnvelopeGoldenSet(unittest.TestCase):
    def test_fixture_integrity(self):
        self.assertEqual(985, len(RAW))
        self.assertEqual(GOLDEN["real_fixture_sha256"], hashlib.sha256(RAW).hexdigest())
        self.assertEqual("real_capture_2026-10-22_empty_atom_plus_synthetic_envelope_mutations", GOLDEN["source"])

    def test_golden_set(self):
        cases = GOLDEN["cases"]
        self.assertGreaterEqual(len(cases), 25)
        self.assertEqual(len(cases), len({c["id"] for c in cases}))
        for case in cases:
            with self.subTest(case=case["id"]):
                result = module.parse_cyprus_atom_envelope(**make_case(case["mutate"]))
                self.assertEqual(case["state"], result.state.value)
                self.assertEqual(case["reason"], result.reason)
                self.assertIs(result.official_warnings_checked, False)
                self.assertIs(result.clean_verified, False)

    def test_actual_empty_feed_details(self):
        result = module.parse_cyprus_atom_envelope(**make_case("none"))
        self.assertEqual(0, result.entry_count)
        self.assertEqual(module.EXPECTED_FEED_ID, result.feed_id)
        self.assertEqual(datetime(2026, 10, 22, 10, 5, 3, 855448, tzinfo=timezone.utc), result.updated)
        self.assertIs(result.clean_verified, False)

    def test_no_network_or_publication_imports(self):
        text = SOURCE_FILE.read_text(encoding="utf-8")
        for forbidden in ("import requests", "import urllib", "import socket", "import post_common", "import safe_test_post", "def get_weather", "urlopen("):
            self.assertNotIn(forbidden, text)

    def test_result_cannot_upgrade_to_verified(self):
        from dataclasses import FrozenInstanceError
        result = module.parse_cyprus_atom_envelope(**make_case("none"))
        with self.assertRaises((AttributeError, FrozenInstanceError)):
            result.clean_verified = True
        with self.assertRaises((AttributeError, FrozenInstanceError)):
            result.official_warnings_checked = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
