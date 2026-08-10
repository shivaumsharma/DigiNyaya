"""Integration tests for app.core.versioning.ApiVersionRewriteMiddleware,
added during the production-readiness review (2026-08-08) so any future
external consumer (mobile app, partner integration) has a stable /api/v1
path to pin to, without duplicating every route declaration.
"""
from __future__ import annotations


class TestApiVersionRewrite:
    def test_v1_prefixed_path_reaches_the_same_route_as_unversioned(self, client):
        unversioned = client.get("/api/dispute-types")
        versioned = client.get("/api/v1/dispute-types")
        assert unversioned.status_code == 200
        assert versioned.status_code == 200
        assert versioned.json() == unversioned.json()

    def test_v1_prefix_alone_maps_to_bare_api_path(self, client):
        # /api/v1 (no trailing path) must rewrite to exactly /api, not /api/.
        r = client.get("/api/v1")
        r_unversioned = client.get("/api")
        assert r.status_code == r_unversioned.status_code

    def test_query_string_survives_the_rewrite(self, client):
        r = client.get("/api/v1/sample-claim", params={"dispute_type": "consumer_dispute"})
        assert r.status_code == 200
        assert r.json()["claim"]["dispute_type"] == "consumer_dispute"

    def test_unversioned_path_is_untouched(self, client):
        r = client.get("/api/dispute-types")
        assert r.status_code == 200

    def test_non_api_paths_are_not_rewritten(self, client):
        # /auth/... must never be touched by the /api/v1 rewrite.
        r = client.get("/api/v1auth/me")  # not a real path -- must 404, not silently match /auth
        assert r.status_code == 404

    def test_post_requests_are_rewritten_too(self, client):
        r = client.post(
            "/api/v1/classify-dispute-type",
            json={"description": "My landlord kept my deposit for no reason at all.", "selected_type": "consumer_dispute"},
        )
        r_unversioned = client.post(
            "/api/classify-dispute-type",
            json={"description": "My landlord kept my deposit for no reason at all.", "selected_type": "consumer_dispute"},
        )
        assert r.status_code == r_unversioned.status_code
