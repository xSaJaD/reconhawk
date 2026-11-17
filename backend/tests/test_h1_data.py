"""Offline H1 schema regressions: no API keys, account calls, or target traffic."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.integrations.hackerone import (
    HackerOneClient,
    HackerOneError,
    classify_program,
    normalize_scope_details,
    normalize_scopes,
)


def asset(identifier, asset_type="URL", submission=True, bounty=True, **extra):
    return {"id": "scope-1", "attributes": {
        "asset_identifier": identifier,
        "asset_type": asset_type,
        "eligible_for_submission": submission,
        "eligible_for_bounty": bounty,
        **extra,
    }}


class HackerOneDataTests(unittest.TestCase):
    def test_paid_program_uses_bounty_category(self):
        self.assertEqual(classify_program({"offers_bounties": True}), "bounty")

    def test_vdp_requires_explicit_negative_flag(self):
        self.assertEqual(classify_program({"offers_bounties": False}), "vdp")

    def test_missing_null_or_non_boolean_is_unknown(self):
        for attrs in (None, {}, {"offers_bounties": None}, {"offers_bounties": "false"},
                      {"offers_bounties": "true"}, {"offers_bounties": 0},
                      {"offers_bounties": 1}, {"eligible_for_bounty": True}):
            with self.subTest(attrs=attrs):
                self.assertEqual(classify_program(attrs), "unknown")

    def test_submission_eligibility_is_independent_from_payment(self):
        details = normalize_scope_details([
            asset("free.example.test", bounty=False),
            asset("paid.example.test", submission=False, bounty=True),
        ])
        self.assertEqual(details["scope_in"], ["free.example.test"])
        self.assertEqual(details["scope_out"], ["paid.example.test"])
        self.assertFalse(details["assets"][0]["eligible_for_bounty"])

    def test_paths_ports_and_case_are_preserved_without_host_broadening(self):
        details = normalize_scope_details([
            asset("https://API.example.test:8443/V1/Accounts/*?mode=Test"),
            asset("api.example.test/V2/Users"),
            asset("*.example.test", "WILDCARD"),
        ])
        self.assertEqual(details["scope_in"], [
            "https://api.example.test:8443/V1/Accounts/*?mode=Test",
            "https://api.example.test/V2/Users",
            "*.example.test",
        ])

    def test_http_api_domain_and_ip_assets_are_supported(self):
        details = normalize_scope_details([
            asset("https://api.example.test/v1", "API"),
            asset("example.test", "DOMAIN"),
            asset("8.8.8.8", "IP_ADDRESS"),
            asset("2001:4860:4860::8888", "IP_ADDRESS"),
        ])
        self.assertEqual(details["scope_in"], [
            "https://api.example.test/v1", "example.test", "8.8.8.8",
            "https://[2001:4860:4860::8888]",
        ])

    def test_non_http_assets_are_retained_without_authorizing_hosting_sites(self):
        original = [
            asset("https://github.com/example/project", "SOURCE_CODE"),
            asset("com.example.app", "GOOGLE_PLAY_APP_ID"),
            asset("8.8.8.0/24", "CIDR"),
        ]
        details = normalize_scope_details(original)
        self.assertEqual(details["scope_in"], [])
        self.assertEqual(details["unsupported_count"], 3)
        self.assertEqual(details["asset_count"], 3)
        self.assertEqual([row["asset_identifier"] for row in details["assets"]],
                         [row["attributes"]["asset_identifier"] for row in original])
        self.assertTrue(all(row["normalization_status"] == "unsupported"
                            for row in details["assets"]))

    def test_unknown_submission_flags_are_denied_and_explained(self):
        rows = [asset("null.example.test", submission=None),
                asset("string.example.test", submission="true")]
        missing = asset("missing.example.test")
        del missing["attributes"]["eligible_for_submission"]
        details = normalize_scope_details(rows + [missing])
        self.assertEqual(details["scope_in"], [])
        self.assertEqual(details["ambiguous_count"], 3)
        self.assertEqual(len(details["scope_out"]), 3)
        self.assertEqual(len(details["warnings"]), 3)
        self.assertTrue(all(row["normalization_status"] == "review_required"
                            for row in details["assets"]))

    def test_exact_deny_wins_and_entries_are_deduplicated(self):
        rows = [asset("example.test"), asset("example.test"),
                asset("example.test", submission=False)]
        self.assertEqual(normalize_scopes(rows), ([], ["example.test"]))

    def test_asset_instructions_and_severity_are_retained(self):
        details = normalize_scope_details([
            asset("example.test", instruction="Only test /sandbox", max_severity="high")])
        self.assertEqual(details["assets"][0]["instruction"], "Only test /sandbox")
        self.assertEqual(details["assets"][0]["max_severity"], "high")

    def test_invalid_rows_fail_the_snapshot_instead_of_dropping_exclusions(self):
        for bad in (None, {}, {"attributes": {}}, {"attributes": "broken"},
                    {"attributes": {"asset_identifier": "", "asset_type": "URL"}}):
            with self.subTest(bad=bad), self.assertRaises(HackerOneError):
                normalize_scope_details([asset("*.example.test"), bad])

    def test_unrepresentable_web_targets_fail_closed(self):
        for target in ("*", "*.*", "*.com", "https://user:secret@example.test",
                       "file:///etc/passwd", "api.example.test:0", "api.example.test:99999",
                       "https://[broken", "api.example.test\n.other.test"):
            with self.subTest(target=target), self.assertRaises(HackerOneError):
                normalize_scope_details([asset(target)])

    def test_compatibility_tuple_and_empty_snapshot(self):
        self.assertEqual(normalize_scopes([]), ([], []))
        self.assertEqual(normalize_scope_details([])["asset_count"], 0)

    def test_idna_is_canonicalized_for_the_http_guard(self):
        self.assertEqual(normalize_scopes([asset("bücher.example")]),
                         (["xn--bcher-kva.example"], []))

    def test_api_error_exposes_status_and_retry_after(self):
        response = httpx.Response(429, json={"errors": [{"detail": "Slow down"}]},
                                  headers={"Retry-After": "12.5"})
        with self.assertRaises(HackerOneError) as error:
            HackerOneClient._handle(response)
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.retry_after, 12.5)
        self.assertIn("Slow down", str(error.exception))

    def test_auth_error_keeps_diagnostic_status(self):
        with self.assertRaises(HackerOneError) as error:
            HackerOneClient._handle(httpx.Response(401))
        self.assertEqual(error.exception.status_code, 401)

    def test_invalid_retry_after_is_not_a_sleep_instruction(self):
        for delay in ("NaN", "Infinity", "-5", "nonsense"):
            with self.subTest(delay=delay), self.assertRaises(HackerOneError) as error:
                HackerOneClient._handle(httpx.Response(503, json={}, headers={"Retry-After": delay}))
            self.assertIsNone(error.exception.retry_after)

    def test_non_json_and_malformed_json_are_diagnostic_failures(self):
        for response in (httpx.Response(503, text="Unavailable"),
                         httpx.Response(200, json=[])):
            with self.subTest(status=response.status_code), self.assertRaises(HackerOneError):
                HackerOneClient._handle(response)


class HackerOnePaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_programs_continue_beyond_the_old_twenty_page_cutoff(self):
        client = HackerOneClient("fixture", "fixture")
        async def page(path, params):
            number = params["page[number]"]
            return {"data": [{"id": str(number)}], "links": {
                "next": f"https://api.hackerone.com/page/{number + 1}" if number < 23 else None}}
        client._get = AsyncMock(side_effect=page)
        rows = await client.list_programs()
        self.assertEqual(len(rows), 23)
        self.assertEqual(rows[-1]["id"], "23")

    async def test_scope_pages_are_complete_and_handle_is_path_encoded(self):
        client = HackerOneClient("fixture", "fixture")
        client._get = AsyncMock(side_effect=[
            {"data": [asset("first.example.test")], "links": {"next": "/next"}},
            {"data": [asset("last.example.test", submission=False)], "links": {}},
        ])
        rows = await client.get_scopes("test/handle")
        self.assertEqual(len(rows), 2)
        self.assertEqual(client._get.call_args_list[0].args[0],
                         "/programs/test%2Fhandle/structured_scopes")
        self.assertEqual(client._get.call_args_list[1].args[1]["page[number]"], 2)

    async def test_empty_intermediate_page_cannot_look_like_success(self):
        client = HackerOneClient("fixture", "fixture")
        client._get = AsyncMock(return_value={"data": [], "links": {"next": "/page/2"}})
        with self.assertRaises(HackerOneError):
            await client.get_scopes("test")

    async def test_repeated_next_link_is_a_failed_snapshot(self):
        client = HackerOneClient("fixture", "fixture")
        client._get = AsyncMock(return_value={"data": [asset("example.test")],
                                              "links": {"next": "/same-page"}})
        with self.assertRaises(HackerOneError):
            await client.list_programs()
        self.assertEqual(client._get.await_count, 2)

    async def test_missing_or_wrong_collection_shape_is_a_failure(self):
        for result in ({}, {"data": None}, {"data": {}},
                       {"data": ["broken"]}, {"data": [], "links": []}):
            with self.subTest(result=result):
                client = HackerOneClient("fixture", "fixture")
                client._get = AsyncMock(return_value=result)
                with self.assertRaises(HackerOneError):
                    await client.get_scopes("test")

    async def test_page_limit_does_not_return_partial_data(self):
        client = HackerOneClient("fixture", "fixture")
        async def page(path, params):
            return {"data": [asset("example.test")],
                    "links": {"next": f"/page/{params['page[number]'] + 1}"}}
        client._get = AsyncMock(side_effect=page)
        with self.assertRaises(HackerOneError):
            await client.get_scopes("test")
        self.assertEqual(client._get.await_count, 100)

    async def test_empty_terminal_collection_is_valid(self):
        client = HackerOneClient("fixture", "fixture")
        client._get = AsyncMock(return_value={"data": [], "links": {}})
        self.assertEqual(await client.get_scopes("test"), [])

    async def test_pacing_applies_to_each_http_get(self):
        original_client = httpx.AsyncClient
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [], "links": {}})
        with patch("app.integrations.hackerone.httpx.AsyncClient", side_effect=lambda **kw:
                   original_client(transport=httpx.MockTransport(handle), **kw)), \
             patch("app.integrations.hackerone.asyncio.sleep", new_callable=AsyncMock) as delay:
            client = HackerOneClient("fixture", "fixture", min_request_interval=100)
            await client._get("/programs")
            await client._get("/programs")
        self.assertEqual(len(requests), 2)
        delay.assert_awaited_once()
        self.assertGreater(delay.call_args.args[0], 90)
        self.assertLessEqual(delay.call_args.args[0], 100)


if __name__ == "__main__":
    unittest.main()
