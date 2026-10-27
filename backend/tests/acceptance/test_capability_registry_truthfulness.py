"""Acceptance gate: capability registry truthfulness."""

TOKEN = "acceptance-control-token"


def test_unimplemented_and_disconnected_capabilities_are_unavailable(client):
    r = client.get(
        "/api/v1/capabilities", headers={"X-Control-Token": TOKEN}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["consistency_issues"] == []

    by_id = {c["id"]: c for c in body["capabilities"]}
    assert by_id["browser_control"]["status"] == "UNAVAILABLE"
    assert by_id["device_integration"]["status"] == "UNAVAILABLE"


def test_unknown_capability_is_not_fabricated(client):
    r = client.get(
        "/api/v1/capabilities/no_such_capability",
        headers={"X-Control-Token": TOKEN},
    )
    assert r.status_code == 404
