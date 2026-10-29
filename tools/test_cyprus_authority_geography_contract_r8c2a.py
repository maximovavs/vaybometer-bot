#!/usr/bin/env python3
"""R8-C2A: offline test-only Cyprus source/geo/lifecycle contract Golden Set.

NO production parser implementation, live feed, positive CY CAP, or trusted
city polygon is added. `evaluate_contract()` is a *test-only reference policy*
for future acceptance, not a verification of currently wired publication logic.
Only `parse_cyprus_atom_envelope` exercises existing merged R8-B0 code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
from urllib import request as urllib_request
import sys
import unittest
from unittest.mock import PropertyMock, patch

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
DATA = json.loads((FIX / "cyprus_r8c2a_contract_golden_set.json").read_text(encoding="utf-8"))

spec = importlib.util.spec_from_file_location("cyprus_r8c2a_atom_module", HERE / "cyprus_official_atom_envelope.py")
assert spec and spec.loader
atom = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atom
spec.loader.exec_module(atom)
RAW = (FIX / "cyprus_atom_empty_2026-10-22.xml").read_bytes()
RECEIVED = datetime(2026, 10, 22, 10, 8, 20, 95715, tzinfo=timezone.utc)
HTTP_DATE = "Thu, 22 Oct 2026 10:08:19 GMT"
OLD_UPDATED = b"2026-10-22T10:05:03.855448Z"


def atom_input(mutation: str) -> dict:
    """Mutate one *already captured* zero-entry Atom document, never live GET."""
    request = dict(xml_bytes=RAW, source_url=atom.OFFICIAL_CYPRUS_ATOM_URL,
                   http_status=200, content_type="application/atom+xml",
                   http_date=HTTP_DATE, received_at=RECEIVED)
    substitutions = {
        "wrong_country": (b":CY</id>", b":LT</id>"),
        "wrong_self": (b'rel="self" href="https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-cyprus"',
                       b'rel="self" href="https://example.invalid/cyprus"'),
        "synthetic_entry": (b"</feed>", b'<entry><id>SYNTHETIC-NOT-A-CAP</id></entry></feed>'),
        "stale_31m": (OLD_UPDATED, b"2026-10-22T09:37:18Z"),
        "future_6m": (OLD_UPDATED, b"2026-10-22T10:14:19Z"),
        "plus02_equivalent": (OLD_UPDATED, b"2026-10-22T12:05:03.855448+02:00"),
        "plus03_equivalent": (OLD_UPDATED, b"2026-10-22T13:05:03.855448+03:00"),
    }
    if mutation in substitutions:
        old, new = substitutions[mutation]
        assert request["xml_bytes"].count(old) == 1, mutation
        request["xml_bytes"] = request["xml_bytes"].replace(old, new, 1)
    elif mutation == "wrong_source":
        request["source_url"] = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-lithuania"
    elif mutation == "lookalike_source":
        request["source_url"] = "https://feeds.meteoalarm.org.evil.invalid/feeds/meteoalarm-legacy-atom-cyprus"
    elif mutation == "date_skew":
        request["received_at"] = RECEIVED + timedelta(minutes=6)
    elif mutation != "none":
        raise AssertionError(f"undefined Atom mutation: {mutation}")
    return request


def evaluate_contract(case: dict, *, allow_oid_as_sender: bool = False,
                      allow_unmapped_city: bool = False,
                      allow_cross_product_geography: bool = False,
                      allow_unresolved_cancel: bool = False) -> str:
    """Test-only acceptance contract, NOT production CAP validation.

    Optional switches represent unsafe hypothetical implementations and exist
    exclusively for mutation probes. No case can establish a clean CY feed.
    """
    b = DATA["basis"]
    kind = case["kind"]
    if kind == "authority":
        if case["oid"] != b["wmo_authority_oid"]:
            return "UNKNOWN_AUTHORITY"
        claimed = case.get("claimed_cap_sender")
        if claimed is not None:
            if allow_oid_as_sender and claimed == b["wmo_authority_oid"]:
                return "AUTHORITY_REGISTERED_SENDER_UNPROVEN"  # faulty sender acceptance
            return "CAP_SENDER_UNVERIFIED"
        return "AUTHORITY_REGISTERED_SENDER_UNPROVEN"
    if kind == "endpoint":
        return ("REGISTERED_ENDPOINT_ONLY" if case["url"] == b["registered_feed"]
                else "UNKNOWN_ENDPOINT")
    if kind == "geocode":
        if allow_unmapped_city and case.get("city") is not None:
            return "CITY_CLEARED"  # faulty city inference
        return "UNKNOWN_EMMA_CITY_MAPPING"
    if kind == "geography":
        official = (b["national_warning_geography"], b["marine_geography"])
        match = any(case["product"] == g["product"] and
                    case["distance_km"] == g["band_km"] and
                    case["direction"] == g["direction"] for g in official)
        if not match:
            if allow_cross_product_geography:
                return "GEOGRAPHY_TAXONOMY_ONLY"  # faulty mixing of inland and marine
            return "UNKNOWN_PRODUCT_CONFLATION"
        if case.get("city"):
            if allow_unmapped_city:
                return "CITY_CLEARED"
            return "UNKNOWN_LOCAL_ATTRIBUTION"
        return "GEOGRAPHY_TAXONOMY_ONLY"
    if kind == "national_html":
        # The actual #67 -> #66 HTML relation is *not* a CAP Update/references tuple.
        return "CAP_LIFECYCLE_UNVERIFIED"
    if kind == "cap_metadata":
        if case.get("duplicate_identifier"):
            return "UNKNOWN_DUPLICATE_IDENTIFIER"
        if not case["document_complete"]:
            return "UNKNOWN_CAP_DOCUMENT"
        if case["message_type"] in ("Update", "Cancel") and not (
                case["references"] and case["history_resolved"]):
            if allow_unresolved_cancel and case["message_type"] == "Cancel":
                return "NO_WARNINGS_CONFIRMED"  # faulty cancel/all-clear
            return "UNKNOWN_UNRESOLVED_REFERENCES"
        return "UNKNOWN_CAP_AUTHENTICITY"
    if kind == "coverage":
        return "UNKNOWN_NATIONAL_COMPLETENESS"
    raise AssertionError(f"unsupported contract kind {kind!r}")


class CyprusR8C2AGoldenSet(unittest.TestCase):
    def test_basis_registry_and_product_differences(self):
        b = DATA["basis"]
        self.assertEqual("2.49.0.0.196.0", b["wmo_authority_oid"])
        self.assertEqual("CY", b["country_iso_alpha2"])
        self.assertEqual("CYP", b["country_iso_alpha3"])
        self.assertEqual(atom.OFFICIAL_CYPRUS_ATOM_URL, b["registered_feed"])
        self.assertEqual(atom.EXPECTED_FEED_ID, b["feed_id_from_captured_atom"])
        land, sea = b["national_warning_geography"], b["marine_geography"]
        self.assertEqual(("land_weather_warnings", 10, "inland"),
                         (land["product"], land["band_km"], land["direction"]))
        self.assertEqual(("coastal_waters_forecast", 8, "seaward"),
                         (sea["product"], sea["band_km"], sea["direction"]))
        self.assertEqual(4, len(set(land["coastal_sectors"])))
        self.assertEqual(4, len(set(sea["sectors"])))
        self.assertIn("CAP.sender", b["unproven"])

    def test_real_empty_fixture_integrity_and_is_unverified(self):
        self.assertEqual("f7caef8f16f6ec4ef09478344bc8efc9c3dd0e5b770c251f1ff8d37596fa56cc",
                         hashlib.sha256(RAW).hexdigest())
        result = atom.parse_cyprus_atom_envelope(**atom_input("none"))
        self.assertEqual("EMPTY_UNVERIFIED", result.state.value)
        self.assertEqual(0, result.entry_count)
        self.assertIs(result.official_warnings_checked, False)
        self.assertIs(result.clean_verified, False)

    def test_atom_golden_cases_directly_against_merged_b0(self):
        cases = DATA["atom_cases"]
        self.assertEqual(12, len(cases))
        self.assertEqual(len(cases), len({c["id"] for c in cases}))
        self.assertEqual(1, sum(c["origin"] == "actual_capture" for c in cases))
        for case in cases:
            with self.subTest(case=case["id"]):
                result = atom.parse_cyprus_atom_envelope(**atom_input(case["mutation"]))
                self.assertEqual(case["expected"], result.state.value)
                self.assertIs(result.official_warnings_checked, False)
                self.assertIs(result.clean_verified, False)
                self.assertNotEqual("CLEAN_VERIFIED", result.state.value)

    def test_test_only_contract_golden_and_absent_cyprus_cap(self):
        cases = DATA["contract_cases"]
        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len(cases), len({c["id"] for c in cases}))
        for case in cases:
            with self.subTest(case=case["id"]):
                value = evaluate_contract(case)
                self.assertEqual(case["expected"], value)
                self.assertNotIn(value, DATA["prohibited_positive_states"])
                self.assertFalse(case.get("authentic_cyprus_cap", False))

    def test_merged_b2_is_lt_only_and_rejects_cy_source(self):
        # Existing B2 parser is explicitly Lithuanian structure-only.
        # Supplying CY feed/Atom must *not* upgrade it into a Cyprus CAP parser.
        spec = importlib.util.spec_from_file_location(
            "cyprus_r8c2a_lt_only_module", HERE / "cyprus_official_cap_offline.py")
        assert spec and spec.loader
        lt = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = lt
        spec.loader.exec_module(lt)
        now = RECEIVED
        result = lt.inspect_lt_atom_cap_samples(
            RAW, {}, source_url=atom.OFFICIAL_CYPRUS_ATOM_URL,
            received_at=now, http_date_utc=now)
        self.assertEqual("UNKNOWN", result.state.value)
        self.assertEqual("UNTRUSTED_SOURCE", result.reason)
        self.assertIs(result.official_warnings_checked, False)
        self.assertIs(result.clean_verified, False)

    def test_no_cap_or_spatial_positive_proofs_in_fixture(self):
        text = (FIX / "cyprus_r8c2a_contract_golden_set.json").read_text(encoding="utf-8")
        self.assertNotIn("<alert", text.casefold())
        self.assertNotIn('"city_map_verified": true', text.casefold())
        self.assertNotIn('"sender_allowlisted": true', text.casefold())
        self.assertTrue(all(not c.get("authentic_cyprus_cap") for c in DATA["contract_cases"]))
        self.assertTrue(all(c["origin"] != "real_cap" for c in DATA["atom_cases"]))

    def test_no_network_imports_in_test_only_contract(self):
        # This test file intentionally imports socket only to enforce network block.
        source = (HERE / "cyprus_official_cap_offline.py").read_text(encoding="utf-8")
        for forbidden in ("import requests", "import urllib.request", "import socket", "urlopen(", "import post_common"):
            self.assertNotIn(forbidden, source)
        self.assertFalse(hasattr(atom, "fetch_live_warning"))

    def test_fault_injections_all_killed(self):
        mutations = run_mutation_probes()
        self.assertEqual(13, len(mutations))
        self.assertTrue(all(mutations.values()), mutations)


def run_mutation_probes() -> dict[str, bool]:
    """13 counterfactual faulty behaviours detected by executable safety assertions.

    7 probes exercise real merged B0/B2 and 6 test-only contract evaluation.
    A killed contract mutant is *not* proof of a production guard in C2A.
    """
    by_id = {c["id"]: c for c in DATA["contract_cases"]}
    by_atom = {c["id"]: c for c in DATA["atom_cases"]}
    killed = {}
    with patch.object(atom, "MAX_AGE", timedelta(hours=2)):
        c = by_atom["CY_ATOM_31_MIN_OLD"]
        killed["B0_stale_31min_guard_disabled"] = (
            atom.parse_cyprus_atom_envelope(**atom_input(c["mutation"])).state.value != c["expected"])
    with patch.object(atom, "MAX_CLOCK_SKEW", timedelta(hours=1)):
        c = by_atom["CY_ATOM_FUTURE_6M"]
        killed["B0_future_guard_disabled"] = (
            atom.parse_cyprus_atom_envelope(**atom_input(c["mutation"])).state.value != c["expected"])
    with patch.object(atom.AtomEnvelopeResult, "official_warnings_checked", new_callable=PropertyMock, return_value=True):
        killed["B0_illegal_checked_true"] = (
            atom.parse_cyprus_atom_envelope(**atom_input("none")).official_warnings_checked is not False)
    with patch.object(atom.AtomEnvelopeResult, "clean_verified", new_callable=PropertyMock, return_value=True):
        killed["B0_illegal_clean_true"] = (
            atom.parse_cyprus_atom_envelope(**atom_input("none")).clean_verified is not False)
    spec = importlib.util.spec_from_file_location(
        "r8c2a_lt_mutant_module", HERE / "cyprus_official_cap_offline.py")
    assert spec and spec.loader
    lt = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lt
    spec.loader.exec_module(lt)
    args = dict(atom_xml=RAW, cap_by_url={},
                source_url=atom.OFFICIAL_CYPRUS_ATOM_URL,
                received_at=RECEIVED, http_date_utc=RECEIVED)
    with patch.object(lt, "LT_FEED", atom.OFFICIAL_CYPRUS_ATOM_URL):
        killed["B2_LT_source_boundary_bypassed"] = (
            lt.inspect_lt_atom_cap_samples(**args).reason != "UNTRUSTED_SOURCE")
    with patch.object(lt.CAPSampleResult, "official_warnings_checked", new_callable=PropertyMock,
                      return_value=True):
        killed["B2_illegal_checked_true"] = (
            lt.inspect_lt_atom_cap_samples(**args).official_warnings_checked is not False)
    with patch.object(lt.CAPSampleResult, "clean_verified", new_callable=PropertyMock,
                      return_value=True):
        killed["B2_illegal_clean_true"] = (
            lt.inspect_lt_atom_cap_samples(**args).clean_verified is not False)
    c = by_id["A01_OID_NOT_CAP_SENDER"]
    killed["SPEC_oid_as_sender"] = evaluate_contract(c, allow_oid_as_sender=True) != c["expected"]
    c = by_id["A05_UNKNOWN_CY_EMMA"]
    killed["SPEC_unknown_emma_auto_clears_city"] = evaluate_contract(c, allow_unmapped_city=True) != c["expected"]
    c = by_id["A06_CITY_OUTSIDE_INFERRED"]
    killed["SPEC_city_map_fabricated"] = evaluate_contract(c, allow_unmapped_city=True) != c["expected"]
    c = by_id["A07_MARINE_AS_LAND"]
    killed["SPEC_marine_land_mix"] = evaluate_contract(c, allow_cross_product_geography=True) != c["expected"]
    c = by_id["A09_CANCEL_WITHOUT_REFERENCES"]
    killed["SPEC_cancel_without_refs_all_clear"] = evaluate_contract(c, allow_unresolved_cancel=True) != c["expected"]
    c = by_id["A09_CANCEL_SYNTHETIC_UNRESOLVED_REF"]
    killed["SPEC_cancel_missing_history_all_clear"] = evaluate_contract(c, allow_unresolved_cancel=True) != c["expected"]
    return killed


if __name__ == "__main__":
    # Enforce zero network during the entire executable suite. A network
    # attempt fails immediately, rather than relying on network isolation.
    blocked = []
    def no_network(*args, **kwargs):
        blocked.append((args, kwargs))
        raise AssertionError("NETWORK_FORBIDDEN_IN_R8C2A")
    network_patches = [patch.object(socket.socket, "connect", no_network),
                       patch.object(socket.socket, "connect_ex", no_network),
                       patch.object(socket, "create_connection", no_network),
                       patch.object(urllib_request, "urlopen", no_network)]
    for p in network_patches:
        p.start()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CyprusR8C2AGoldenSet)
    # R8-E4A: include derived-only Golden Case in the already scheduled offline
    # PR checks without changing workflows or any production modules.
    from test_cyprus_r8e4a_emma68_derived import DerivedOnlyEm68
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DerivedOnlyEm68))
    # R8-G4: run the fail-closed CAP contract and its negative code mutations
    # through the existing R8-C2A CI entrypoint (workflow remains unchanged).
    from test_cyprus_cap_r8g1_contract import ContractTests
    from test_cyprus_cap_r8g1_mutations import NegativeMutations
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(NegativeMutations))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    assert blocked == [], "network attempted during tests"
    print("R8-C2A blocked network attempts: 0")
    print("R8-C2A mutation probes:")
    for k, killed in run_mutation_probes().items():
        print(f"{'KILLED' if killed else 'SURVIVED'} {k}")
