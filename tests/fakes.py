"""A tiny fake Home Assistant for tests (httpx MockTransport)."""
import json

import httpx


class FakeHA:
    def __init__(self, states=None, token="tok"):
        self.states = {s["entity_id"]: s for s in (states or [])}
        self.token = token
        self.calls: list[tuple[str, dict]] = []   # (service, payload)
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "unauthorized"})
        path = request.url.path
        if request.method == "GET" and path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        if request.method == "GET" and path == "/api/states":
            return httpx.Response(200, json=list(self.states.values()))
        if request.method == "GET" and path.startswith("/api/states/"):
            s = self.states.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=s) if s else httpx.Response(404)
        if request.method == "POST" and path.startswith("/api/services/light/"):
            self.calls.append((path.rsplit("/", 1)[1], json.loads(request.content)))
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)


def light(entity, state="on", **attrs):
    return {"entity_id": entity, "state": state, "attributes": attrs}
