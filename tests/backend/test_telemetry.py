import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from test_main import startup  # noqa: F401 — reuse the backend's stubbed startup

from backend.app import main


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    getaddrinfo = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("an exporter test tried to reach an external host")
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


@pytest.mark.usefixtures("startup")
@pytest.mark.parametrize("receiver_status", [200, 403])
def test_request_finishes_while_otlp_receiver_is_held(monkeypatch, capsys, receiver_status):
    received = Event()
    reply = Event()
    finish_retrieval = Event()
    payloads = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            payloads.append((self.path, self.headers["Authorization"], body))
            received.set()
            reply.wait(10)
            self.send_response(receiver_status)
            self.end_headers()

    def retrieve(pipe, query, top_k):
        assert finish_retrieval.wait(10)
        return []

    monkeypatch.setattr(main.hybrid, "retrieve", retrieve)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_HEADERS", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_COMPRESSION", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_COMPRESSION", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic%20test")
    with HTTPServer(("127.0.0.1", 0), Receiver) as server:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}/otlp"
        )
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with TestClient(main.app) as client, ThreadPoolExecutor() as pool:
                future = pool.submit(
                    client.post,
                    "/query",
                    json={"question": "private test question"},
                    headers={"X-CRAG-Follow-ID": "crg-" + "a" * 32},
                )
                try:
                    assert received.wait(10)
                    assert not future.done()
                    finish_retrieval.set()
                    response = future.result(timeout=5)
                    assert response.status_code == 200
                finally:
                    finish_retrieval.set()
                    reply.set()
        finally:
            server.shutdown()
            thread.join()

    stdout = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    expected = [
        {k: row[k] for k in ("request_id", "stage", "outcome", "event_time")} for row in stdout
    ]
    actual = []
    for path, authorization, body in payloads:
        assert path == "/otlp/v1/logs" and authorization == "Basic test"
        assert b"private test question" not in body and b"crg-aaaa" not in body
        [resource] = ExportLogsServiceRequest.FromString(body).resource_logs
        assert {a.key: a.value.string_value for a in resource.resource.attributes} == {
            "service.name": "adaptive-rag-for-fastapi-docs"
        }
        for record in resource.scope_logs[0].log_records:
            attrs = {a.key: a.value.string_value for a in record.attributes}
            when = datetime.fromisoformat(attrs["event_time"])
            assert record.time_unix_nano == int(when.timestamp()) * 10**9 + when.microsecond * 1000
            assert record.body.string_value == "crag_run"
            actual.append(attrs)
    assert actual == expected
    assert len(actual) == 4 and actual[-1]["outcome"] == "succeeded"
    assert {row["request_id"] for row in actual} == {response.headers["X-Request-ID"]}
