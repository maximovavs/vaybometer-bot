"""R8-E4A offline TEST-ONLY derived warning regression. Never a production validator.

Only metadata, independently authored synthetic XML and acceptance invariants.
No raw copyrighted source documents, network, or production publisher imports.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
FX = HERE / "fixtures"
CASE = json.loads((FX / "cyprus_r8e4a_emma68_derived.json").read_text("utf-8"))
ATOM_PATH = FX / "cyprus_r8e4a_atom_SYNTHETIC.xml"
CAP_PATH = FX / "cyprus_r8e4a_cap_SYNTHETIC.xml"
ATOM = "{http://www.w3.org/2005/Atom}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"


def utc(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("NAIVE_TIME")
    return dt.astimezone(timezone.utc)


def phase_at(when: str, warning: dict) -> str:
    now, issued = utc(when), utc(warning["issued_local"])
    start, end = utc(warning["starts_local"]), utc(warning["dom_valid_until_exclusive_local"])
    if not issued < start < end:
        return "UNKNOWN"
    if now < issued:
        return "NOT_YET_ISSUED"
    if now < start:
        return "UPCOMING"
    if now < end:
        return "ACTIVE"
    return "EXPIRED_HISTORY"


def reference_gate(case: dict, when: str, *, draft_omits_warning: bool = False,
                   draft_claims_clean: bool = False, positive_water_guidance: bool = False,
                   unresolved_update_cancel: bool = False, empty_feed: bool = False,
                   city_claimed_cleared: bool = False) -> dict:
    """Test-only safety policy, NOT production behavior. Emulates fail-closed requirements."""
    w, a = case["warning"], case["acceptance"]
    phase = phase_at(when, w)
    known_warning = phase in ("UPCOMING", "ACTIVE")
    # Neither one positive alert nor an empty feed proves a complete national inventory.
    complete = False
    clean = False
    checked = False
    city_mapped = False
    unsafe = (phase == "UNKNOWN" or draft_claims_clean or city_claimed_cleared or unresolved_update_cancel
              or (known_warning and (draft_omits_warning or positive_water_guidance)))
    # Fail closed on unsupported positive water guidance even if no active alert.
    unsafe = unsafe or positive_water_guidance
    return {
        "phase": phase, "show_warning": known_warning,
        "decision": "NO_POST" if unsafe else "TEST_ONLY_REVIEW",
        "clean_verified": clean, "official_warnings_checked": checked,
        "complete_national_inventory": complete, "city_mapping": w["city_mapping_state"],
        "city_cleared": city_mapped, "empty_feed_clears": False,
        "source_crosscheck": a["stored_triple_result"],
        "later_lifecycle_state": "UNKNOWN" if unresolved_update_cancel else "NOT_OBSERVED_IN_CAPTURE",
    }


def validate_fixture(xml_bytes: bytes, *, expected_root: str, expected_id: str) -> ET.Element:
    if b"SYNTHETIC" not in xml_bytes or b"DOCTYPE" in xml_bytes or b"ENTITY" in xml_bytes:
        raise ValueError("NOT_A_SAFE_SYNTHETIC_FIXTURE")
    root = ET.fromstring(xml_bytes)
    if root.tag != expected_root:
        raise ValueError("WRONG_ROOT")
    field = (root.findtext((ATOM if expected_root == ATOM + "feed" else CAP) + "identifier") or "")
    if expected_root == ATOM + "feed":
        field = root.findtext(ATOM + "entry/" + CAP + "identifier") or ""
    if field != expected_id:
        raise ValueError("IDENTIFIER_MISMATCH")
    if expected_root == CAP + "alert" and root.findtext(CAP + "sender") != "synthetic-test@example.invalid":
        raise ValueError("UNSAFE_SYNTHETIC_SENDER")
    return root


class DerivedOnlyEm68(unittest.TestCase):
    def test_provenance_is_metadata_only(self):
        p = CASE["provenance"]
        self.assertFalse(p["raw_included"])
        self.assertEqual(1, p["atom"]["entries_observed"])
        self.assertEqual(68, CASE["warning"]["dom_warning_number"])
        self.assertEqual("CY001", CASE["warning"]["cap_emma_id"])
        self.assertEqual("UNKNOWN_EMMA_CITY_MAPPING", CASE["warning"]["city_mapping_state"])
        for name in ("atom", "cap", "national_html"):
            s = p[name]["sha256"]
            self.assertEqual(64, len(s))
            bytes.fromhex(s)
            self.assertTrue(p[name]["url"].startswith("https://"))
        self.assertFalse(p["cap"]["sender_independently_authenticated"])
        self.assertFalse(p["cap"]["cap_signature_observed"])

    def test_precipitation_units_and_bounds(self):
        w = CASE["warning"]
        self.assertEqual([35, 55], w["rate_mm_per_hour"])
        self.assertTrue(w["hail_possible"])
        self.assertEqual([7, 8], w["gusts_beaufort"])
        self.assertEqual("yellow", w["color"])
        self.assertEqual("isolated_heavy_thunderstorms", w["hazard"])
        self.assertEqual("Actual", w["status_observed"])
        self.assertEqual("Alert", w["message_type_observed"])
        self.assertEqual(timedelta(seconds=1), utc(w["dom_valid_until_exclusive_local"]) - utc(w["cap_expires_local"]))

    def test_times_and_timezone_boundaries(self):
        w = CASE["warning"]
        for t, expected in (
            ("2026-10-27T15:45:59+02:00", "NOT_YET_ISSUED"),
            ("2026-10-27T15:46:00+02:00", "UPCOMING"),
            ("2026-10-28T01:59:59+02:00", "UPCOMING"),
            ("2026-10-28T02:00:00+02:00", "ACTIVE"),
            ("2026-10-28T17:59:59+02:00", "ACTIVE"),
            ("2026-10-28T18:00:00+02:00", "EXPIRED_HISTORY"),
            ("2026-10-28T00:00:00Z", "ACTIVE"),
        ):
            with self.subTest(time=t):
                self.assertEqual(expected, phase_at(t, w))
        with self.assertRaises(ValueError):
            phase_at("2026-10-28T12:00:00", w)

    def test_fixture_synthetic_atom_and_cap_are_not_raw_originals(self):
        a, c = ATOM_PATH.read_bytes(), CAP_PATH.read_bytes()
        id_ = "SYNTHETIC-CY-68-DO-NOT-PUBLISH"
        atom = validate_fixture(a, expected_root=ATOM + "feed", expected_id=id_)
        cap = validate_fixture(c, expected_root=CAP + "alert", expected_id=id_)
        self.assertEqual("https://example.invalid/test-only-cap-68",
                         atom.find(ATOM + "entry/" + ATOM + "link").attrib["href"])
        self.assertEqual("synthetic-test@example.invalid", cap.findtext(CAP + "sender"))
        self.assertEqual("CY001", cap.findtext(CAP + "info/" + CAP + "area/" + CAP + "geocode/" + CAP + "value"))
        original_digests = {CASE["provenance"][k]["sha256"] for k in ("atom", "cap", "national_html")}
        self.assertTrue(original_digests.isdisjoint({hashlib.sha256(a).hexdigest(), hashlib.sha256(c).hexdigest()}))
        self.assertIn(b"SYNTHETIC", a + c)

    def test_invalid_synthetic_cap_fields_rejected(self):
        raw = CAP_PATH.read_bytes()
        for old, new in (
            (b"SYNTHETIC-CY-68-DO-NOT-PUBLISH", b"UNKNOWN-TEST-IDENTIFIER"),
            (b"<sender>synthetic-test@example.invalid</sender>", b"<sender>realperson@example.com</sender>"),
            (b"<alert", b"<notice"),
        ):
            with self.subTest(old=old):
                with self.assertRaises((ValueError, ET.ParseError)):
                    validate_fixture(raw.replace(old, new, 1), expected_root=CAP + "alert", expected_id="SYNTHETIC-CY-68-DO-NOT-PUBLISH")

    def test_upcoming_and_active_warning_cannot_be_suppressed(self):
        for at in ("2026-10-27T20:00:00+02:00", "2026-10-28T14:00:00+02:00"):
            g = reference_gate(CASE, at)
            self.assertTrue(g["show_warning"])
            self.assertFalse(g["clean_verified"])
            self.assertFalse(g["official_warnings_checked"])
            for flag in ("draft_omits_warning", "draft_claims_clean", "positive_water_guidance",
                         "unresolved_update_cancel", "city_claimed_cleared"):
                self.assertEqual("NO_POST", reference_gate(CASE, at, **{flag: True})["decision"])

    def test_empty_feed_never_means_no_official_warnings(self):
        g = reference_gate(CASE, "2026-10-28T08:00:00+02:00", empty_feed=True)
        self.assertTrue(g["show_warning"])
        self.assertFalse(g["empty_feed_clears"])
        self.assertFalse(g["complete_national_inventory"])
        self.assertEqual("UNKNOWN_EMMA_CITY_MAPPING", g["city_mapping"])
        self.assertFalse(g["city_cleared"])

    def test_unresolved_lifecycle_is_unknown_not_clear(self):
        for at in ("2026-10-27T20:00:00+02:00", "2026-10-28T18:01:00+02:00"):
            g = reference_gate(CASE, at, unresolved_update_cancel=True)
            self.assertEqual("NO_POST", g["decision"])
            self.assertEqual("UNKNOWN", g["later_lifecycle_state"])
            self.assertFalse(g["clean_verified"])

    def test_derived_case_does_not_promote_verified_source(self):
        g = reference_gate(CASE, "2026-10-28T08:00:00+02:00")
        self.assertEqual("CAP_MATCH", g["source_crosscheck"])
        # The *stored acceptance contract* must fail mutation probes too;
        # reference_gate currently hardcodes these values to False.
        for field in ("official_warnings_checked", "clean_verified", "city_cleared"):
            with self.subTest(acceptance_field=field):
                self.assertIs(CASE["acceptance"][field], False)
        self.assertFalse(g["official_warnings_checked"])
        self.assertFalse(g["clean_verified"])
        self.assertFalse(g["city_cleared"])
        self.assertFalse(g["complete_national_inventory"])

    def test_fail_closed_on_tampered_or_unknown_warning_times(self):
        bad = copy.deepcopy(CASE)
        bad["warning"]["starts_local"] = "2026-10-29T02:00:00+02:00"
        bad["warning"]["dom_valid_until_exclusive_local"] = "2026-10-28T18:00:00+02:00"
        self.assertEqual("UNKNOWN", phase_at("2026-10-28T08:00:00+02:00", bad["warning"]))
        self.assertEqual("NO_POST", reference_gate(bad, "2026-10-28T08:00:00+02:00")["decision"])

    def test_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("NO_NETWORK")):
            self.assertEqual("UPCOMING", phase_at("2026-10-27T20:00:00+02:00", CASE["warning"]))
            self.assertEqual("TEST_ONLY_REVIEW", reference_gate(CASE, "2026-10-28T08:00:00+02:00")["decision"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
