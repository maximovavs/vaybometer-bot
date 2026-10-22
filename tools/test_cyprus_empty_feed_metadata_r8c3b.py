#!/usr/bin/env python3
"""R8-C3B: offline Golden Set for two authentic empty Cyprus Atom captures and an
authentic MeteoAlarm metadata HTTP 401 response.

Only `parse_cyprus_atom_envelope` (B0) and `inspect_lt_atom_cap_samples` (B2)
are merged code. `snapshot_usable_at()`, `combine_snapshots()`,
`metadata_mapping()`, `city_attribution()` and `national_completeness()` are
*test-only reference policies* for future acceptance, NOT production
validation; nothing in the publication path calls them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import format_datetime
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
DATA = json.loads((FIX / "cyprus_r8c3b_empty_metadata_golden_set.json").read_text(encoding="utf-8"))
SNAP = {s["id"]: s for s in DATA["snapshots"]}
RAW = {s["id"]: (FIX / s["file"]).read_bytes() for s in DATA["snapshots"]}
META = DATA["metadata_401"]
META_RAW = (FIX / META["file"]).read_bytes()
S1, S2 = "CY_ATOM_EMPTY_1005Z", "CY_ATOM_EMPTY_1705Z"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


atom = _load("cyprus_r8c3b_atom_module", "cyprus_official_atom_envelope.py")


def received(snap_id: str) -> datetime:
    return datetime.fromisoformat(SNAP[snap_id]["received_utc"])


def parse(body_id: str, headers_id: str | None = None, *, http_date: str | None = None,
          received_at: datetime | None = None):
    """Authentic bytes with authentic headers; any other pairing is a labelled synthetic envelope."""
    h = SNAP[headers_id or body_id]
    return atom.parse_cyprus_atom_envelope(
        RAW[body_id], source_url=h["url"], http_status=h["http_status"],
        content_type=h["content_type"], http_date=http_date or h["http_date"],
        received_at=received_at or received(headers_id or body_id))


def shifted(snap_id: str, delta: timedelta):
    """Synthetic: authentic body served `delta` after its own <updated> (e.g. a cached copy)."""
    when = datetime.fromisoformat(SNAP[snap_id]["feed_updated"]) + delta
    return parse(snap_id, http_date=format_datetime(when.replace(microsecond=0), usegmt=True),
                 received_at=when)


# ---- test-only reference policies (NOT production validation) -------------

def snapshot_usable_at(state: str, received_at: datetime, evaluated_at: datetime,
                       *, max_age: timedelta = atom.MAX_AGE) -> str:
    """B0 has no evaluation clock, so a stored result must age out at the consumer."""
    if state == "UNKNOWN":
        return "UNKNOWN"
    age = evaluated_at - received_at
    if age < -atom.MAX_CLOCK_SKEW or age > max_age:
        return "UNKNOWN"
    return state


def combine_snapshots(states: list[str], *, empties_confirm_clean: bool = False) -> str:
    """Several snapshots never yield more than their weakest member, and never clean."""
    if not states or "UNKNOWN" in states:
        return "UNKNOWN"
    if "ENTRIES_UNVERIFIED" in states:
        return "ENTRIES_UNVERIFIED"
    if empties_confirm_clean and len(states) >= 2:
        return "CLEAN_VERIFIED"  # faulty aggregation, mutation probes only
    return "EMPTY_UNVERIFIED"


def metadata_mapping(status: int, body: bytes, *, fallback_on_error: bool = False,
                     error_as_empty_list: bool = False) -> tuple[str, dict]:
    """No authentic HTTP 200 metadata exists, so no EMMA_ID / region / city mapping can exist."""
    if status != 200:
        if fallback_on_error:
            return "FALLBACK", {"CY_SYNTHETIC_DEFAULT": ["Limassol", "Paphos"]}  # faulty
        if error_as_empty_list:
            return "EMPTY_COVERAGE", {"__all__": []}  # faulty: error read as "no zones"
        return f"METADATA_HTTP_{status}_UNAVAILABLE", {}
    return "METADATA_UNVERIFIED_NO_AUTHENTIC_200", {}


def city_attribution(city: str, mapping: dict) -> str:
    if "__all__" in mapping and not mapping["__all__"]:
        return "CITY_CLEARED"  # only reachable through a faulty metadata reading
    for zone, cities in mapping.items():
        if city in cities:
            return "EMMA_ID_MAPPED"  # only reachable through a faulty metadata reading
    return "UNKNOWN_EMMA_CITY_MAPPING"


def national_completeness(extract: dict, b0_state: str, *, trust_cap_set_complete: bool = False) -> str:
    """`cap_set_complete` only says every discovered link was sampled; it says nothing
    about warnings that were never linked or published."""
    if (trust_cap_set_complete and extract["cap_set_complete"]
            and extract["atom_entry_count"] == 0 and b0_state == "EMPTY_UNVERIFIED"):
        return "NO_WARNINGS_CONFIRMED"  # faulty, mutation probes only
    return "UNKNOWN_NATIONAL_COMPLETENESS"


class CyprusR8C3BGoldenSet(unittest.TestCase):
    def test_fixture_bytes_and_provenance(self):
        for snap in DATA["snapshots"]:
            with self.subTest(snap=snap["id"]):
                raw = RAW[snap["id"]]
                self.assertEqual(snap["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(snap["size"], len(raw))
                self.assertEqual("actual_capture", snap["origin"])
                self.assertEqual(atom.OFFICIAL_CYPRUS_ATOM_URL, snap["url"])
                self.assertEqual(200, snap["http_status"])
                self.assertIs(False, snap["redirect_followed"])
                self.assertIs(False, snap["credentials_sent"])
                server = datetime.strptime(snap["http_date"], "%a, %d %b %Y %H:%M:%S GMT")
                self.assertLessEqual(abs(received(snap["id"]).replace(tzinfo=None) - server),
                                     timedelta(seconds=5))
        self.assertLess(datetime.fromisoformat(SNAP[S2]["requested_utc"]), received(S2))
        self.assertEqual("commonName=*.meteoalarm.org", SNAP[S2]["tls_peer_certificate"]["subject"])

    def test_first_snapshot_matches_merged_b0_provenance(self):
        b0 = json.loads((FIX / "cyprus_atom_b0_golden_set.json").read_text(encoding="utf-8"))
        self.assertEqual(SNAP[S1]["file"], b0["real_fixture"])
        self.assertEqual(SNAP[S1]["sha256"], b0["real_fixture_sha256"])
        self.assertEqual(SNAP[S1]["received_utc"], b0["real_capture_received_at"])
        self.assertEqual(SNAP[S1]["http_date"], b0["real_capture_http_date"])

    def test_snapshots_differ_only_in_updated(self):
        a, b = RAW[S1].splitlines(), RAW[S2].splitlines()
        self.assertEqual(len(a), len(b))
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        self.assertEqual(1, len(diff))
        self.assertTrue(all(line.strip().startswith(b"<updated>") for line in diff[0]))

    def test_fresh_authentic_snapshots_are_empty_unverified(self):
        for snap_id in (S1, S2):
            with self.subTest(snap=snap_id):
                result = parse(snap_id)
                self.assertEqual("EMPTY_UNVERIFIED", result.state.value)
                self.assertEqual("EMPTY_FEED_NOT_COMPLETENESS_PROOF", result.reason)
                self.assertEqual(0, result.entry_count)
                self.assertEqual(datetime.fromisoformat(SNAP[snap_id]["feed_updated"]), result.updated)
                self.assertIs(False, result.official_warnings_checked)
                self.assertIs(False, result.clean_verified)

    def test_stale_and_replayed_snapshots_are_unknown(self):
        # Synthetic envelopes over authentic bytes; boundary is B0's local MAX_AGE (30 min).
        for snap_id in (S1, S2):
            with self.subTest(snap=snap_id):
                self.assertEqual("EMPTY_UNVERIFIED", shifted(snap_id, timedelta(minutes=29)).state.value)
                stale = shifted(snap_id, timedelta(minutes=31))
                self.assertEqual(("UNKNOWN", "STALE_UPDATED"), (stale.state.value, stale.reason))
        replay_old = parse(S1, S2)  # 10:05Z body served with 17:08Z headers
        self.assertEqual(("UNKNOWN", "STALE_UPDATED"), (replay_old.state.value, replay_old.reason))
        replay_new = parse(S2, S1)  # 17:05Z body served with 10:08Z headers
        self.assertEqual(("UNKNOWN", "FUTURE_UPDATED"), (replay_new.state.value, replay_new.reason))

    def test_b0_has_no_evaluation_clock_so_consumer_must_age_out(self):
        # FACT about merged B0: a pure function of its inputs, no "now".
        source = (HERE / "cyprus_official_atom_envelope.py").read_text(encoding="utf-8")
        self.assertNotIn("now(", source)
        state = parse(S1).state.value
        self.assertEqual("EMPTY_UNVERIFIED", state)  # stays so however late it is re-evaluated
        # Test-only consumer contract:
        self.assertEqual("EMPTY_UNVERIFIED", snapshot_usable_at(state, received(S1), received(S1)))
        self.assertEqual("UNKNOWN", snapshot_usable_at(state, received(S1), received(S2)))
        self.assertEqual("UNKNOWN", snapshot_usable_at(state, received(S2), received(S2) + timedelta(minutes=31)))
        self.assertEqual("UNKNOWN", snapshot_usable_at(state, received(S2), received(S2) - timedelta(minutes=6)))

    def test_multiple_empty_snapshots_never_clean(self):
        self.assertEqual({"UNKNOWN", "EMPTY_UNVERIFIED", "ENTRIES_UNVERIFIED"},
                         {s.value for s in atom.AtomEnvelopeState})
        fresh = [parse(S1).state.value, parse(S2).state.value]
        self.assertEqual("EMPTY_UNVERIFIED", combine_snapshots(fresh))
        self.assertEqual("EMPTY_UNVERIFIED", combine_snapshots(fresh * 5))
        self.assertEqual("UNKNOWN", combine_snapshots(fresh + [parse(S1, S2).state.value]))
        self.assertEqual("UNKNOWN", combine_snapshots([]))
        for states in (fresh, fresh * 5):
            self.assertNotIn(combine_snapshots(states), DATA["prohibited_positive_states"])
        for snap_id in (S1, S2):
            result = parse(snap_id)
            self.assertIs(False, result.official_warnings_checked)
            self.assertIs(False, result.clean_verified)

    def test_metadata_401_fixture_is_authentic_and_negative(self):
        self.assertEqual(META["sha256"], hashlib.sha256(META_RAW).hexdigest())
        self.assertEqual(META["size"], len(META_RAW))
        self.assertEqual(b'{"error":"Unauthorized"}', META_RAW)
        self.assertEqual({"error": "Unauthorized"}, json.loads(META_RAW))
        self.assertIs(False, META["credentials_sent"])
        endpoints = [r["endpoint"] for r in META["responses"]]
        self.assertEqual(["https://api.meteoalarm.org/metadata/v1/geocodes",
                          "https://api.meteoalarm.org/metadata/v1/geocode-aliases",
                          "https://api.meteoalarm.org/metadata/v1/regions"], endpoints)
        self.assertTrue(all(r["http_status"] == 401 for r in META["responses"]))
        self.assertTrue(all(r["content_type"].startswith("application/json") for r in META["responses"]))
        self.assertEqual(3, len({r["x_request_id"] for r in META["responses"]}))
        for token in (b"EMMA", b"CY0", b"geocode", b"region", b"Limassol"):
            self.assertNotIn(token.lower(), META_RAW.lower())

    def test_metadata_401_never_assigns_emma_region_or_city(self):
        for response in META["responses"]:
            with self.subTest(endpoint=response["endpoint"]):
                reason, mapping = metadata_mapping(response["http_status"], META_RAW)
                self.assertEqual("METADATA_HTTP_401_UNAVAILABLE", reason)
                self.assertEqual({}, mapping)
                for city in DATA["cities"]:
                    value = city_attribution(city, mapping)
                    self.assertEqual("UNKNOWN_EMMA_CITY_MAPPING", value)
                    self.assertNotIn(value, DATA["prohibited_positive_states"])
        # C2A geocode contract cases stay unmapped as well.
        c2a = json.loads((FIX / "cyprus_r8c2a_contract_golden_set.json").read_text(encoding="utf-8"))
        geo = [c for c in c2a["contract_cases"] if c["kind"] == "geocode"]
        self.assertTrue(geo)
        self.assertTrue(all(c["expected"] == "UNKNOWN_EMMA_CITY_MAPPING" for c in geo))

    def test_cap_set_complete_with_zero_links_is_not_national_completeness(self):
        ext = DATA["c3a_capture_extract"]
        self.assertIs(True, ext["cap_set_complete"])
        self.assertEqual(0, ext["typed_cap_link_count"])
        self.assertEqual(0, ext["atom_entry_count"])
        self.assertEqual("sample_only_not_completeness_proof", ext["capture_mode"])
        self.assertIs(False, ext["clean_verified"])
        self.assertIs(False, ext["official_warnings_checked"])
        value = national_completeness(ext, parse(S2).state.value)
        self.assertEqual("UNKNOWN_NATIONAL_COMPLETENESS", value)
        self.assertNotIn(value, DATA["prohibited_positive_states"])

    def test_c2a_unproven_records_metadata_401(self):
        c2a = json.loads((FIX / "cyprus_r8c2a_contract_golden_set.json").read_text(encoding="utf-8"))
        unproven = c2a["basis"]["unproven"]
        self.assertIn("CAP.sender", unproven)
        self.assertTrue(any("HTTP 401" in item and "metadata" in item and "R8-C3A" in item
                            for item in unproven))

    def test_not_wired_into_publication(self):
        repo = HERE.parent
        names = ("cyprus_official_atom_envelope", "cyprus_official_cap_offline", "r8c3b")
        for path in sorted(repo.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in names:
                self.assertNotIn(name, text, f"{path.name} imports {name}")

    def test_b2_stays_lithuania_only_on_authentic_cy_snapshot(self):
        lt = _load("cyprus_r8c3b_lt_module", "cyprus_official_cap_offline.py")
        result = lt.inspect_lt_atom_cap_samples(
            RAW[S2], {}, source_url=atom.OFFICIAL_CYPRUS_ATOM_URL,
            received_at=received(S2), http_date_utc=received(S2))
        self.assertEqual(("UNKNOWN", "UNTRUSTED_SOURCE"), (result.state.value, result.reason))
        self.assertIs(False, result.official_warnings_checked)
        self.assertIs(False, result.clean_verified)

    def test_fault_injections_all_killed(self):
        mutations = run_mutation_probes()
        self.assertEqual(11, len(mutations))
        self.assertTrue(all(mutations.values()), mutations)


def run_mutation_probes() -> dict[str, bool]:
    """11 counterfactual faults: 6 against merged B0/B2, 5 against the test-only policies.

    A killed policy mutant is *not* proof of a production guard.
    """
    killed = {}
    with patch.object(atom, "MAX_AGE", timedelta(hours=8)):
        killed["B0_stale_guard_widened_replay_accepted"] = parse(S1, S2).state.value != "UNKNOWN"
    with patch.object(atom, "MAX_CLOCK_SKEW", timedelta(hours=8)):
        killed["B0_future_guard_widened_replay_accepted"] = parse(S2, S1).state.value != "UNKNOWN"
    with patch.object(atom.AtomEnvelopeResult, "official_warnings_checked",
                      new_callable=PropertyMock, return_value=True):
        killed["B0_illegal_checked_true"] = parse(S2).official_warnings_checked is not False
    with patch.object(atom.AtomEnvelopeResult, "clean_verified",
                      new_callable=PropertyMock, return_value=True):
        killed["B0_illegal_clean_true"] = parse(S2).clean_verified is not False
    with patch.object(atom, "EXPECTED_FEED_ID", "tag:meteoalarm.org,2021-02-19:LT"):
        killed["B0_feed_id_pinned_to_other_country"] = parse(S2).state.value != "EMPTY_UNVERIFIED"
    lt = _load("cyprus_r8c3b_lt_mutant_module", "cyprus_official_cap_offline.py")
    with patch.object(lt, "LT_FEED", atom.OFFICIAL_CYPRUS_ATOM_URL):
        killed["B2_LT_source_boundary_bypassed"] = lt.inspect_lt_atom_cap_samples(
            RAW[S2], {}, source_url=atom.OFFICIAL_CYPRUS_ATOM_URL,
            received_at=received(S2), http_date_utc=received(S2)).reason != "UNTRUSTED_SOURCE"
    state = parse(S1).state.value
    killed["SPEC_stored_snapshot_never_ages"] = snapshot_usable_at(
        state, received(S1), received(S2), max_age=timedelta.max) != "UNKNOWN"
    killed["SPEC_two_empties_become_clean"] = combine_snapshots(
        [state, parse(S2).state.value], empties_confirm_clean=True) in DATA["prohibited_positive_states"]
    _, mapping = metadata_mapping(401, META_RAW, fallback_on_error=True)
    killed["SPEC_401_falls_back_to_default_emma"] = city_attribution("Limassol", mapping) != "UNKNOWN_EMMA_CITY_MAPPING"
    _, mapping = metadata_mapping(401, META_RAW, error_as_empty_list=True)
    killed["SPEC_401_read_as_no_zones_clears_city"] = city_attribution("Paphos", mapping) != "UNKNOWN_EMMA_CITY_MAPPING"
    killed["SPEC_cap_set_complete_trusted_as_national"] = national_completeness(
        DATA["c3a_capture_extract"], parse(S2).state.value,
        trust_cap_set_complete=True) != "UNKNOWN_NATIONAL_COMPLETENESS"
    return killed


if __name__ == "__main__":
    blocked = []

    def no_network(*args, **kwargs):
        blocked.append((args, kwargs))
        raise AssertionError("NETWORK_FORBIDDEN_IN_R8C3B")
    for p in (patch.object(socket.socket, "connect", no_network),
              patch.object(socket.socket, "connect_ex", no_network),
              patch.object(socket, "create_connection", no_network),
              patch.object(urllib_request, "urlopen", no_network)):
        p.start()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CyprusR8C3BGoldenSet)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    assert blocked == [], "network attempted during tests"
    print("R8-C3B blocked network attempts: 0")
    print("R8-C3B mutation probes:")
    for k, was_killed in run_mutation_probes().items():
        print(f"{'KILLED' if was_killed else 'SURVIVED'} {k}")
