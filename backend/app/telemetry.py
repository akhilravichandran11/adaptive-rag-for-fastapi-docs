"""OTLP export for the app's CRAG run events."""

import os
from contextlib import contextmanager
from datetime import datetime

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


@contextmanager
def crag_telemetry():
    if not (
        os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    ):
        yield None
        return

    provider = LoggerProvider(
        resource=Resource({"service.name": "adaptive-rag-for-fastapi-docs"}), shutdown_on_exit=False
    )
    try:
        processor = BatchLogRecordProcessor(OTLPLogExporter(), schedule_delay_millis=250)
        provider.add_log_record_processor(processor)
        logger = provider.get_logger("crag.run")

        def emit(event):
            when = datetime.fromisoformat(event["event_time"])
            logger.emit(
                timestamp=int(when.timestamp()) * 1_000_000_000 + when.microsecond * 1000,
                body="crag_run",
                attributes=event,
                severity_text="INFO",
                severity_number=SeverityNumber.INFO,
            )

        yield emit
    finally:
        provider.shutdown()
