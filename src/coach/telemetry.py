"""OpenTelemetry : traces et logs envoyés en OTLP/HTTP au Collector du projet « monitoring » du NAS.

Désactivé tant que OTEL_EXPORTER_OTLP_ENDPOINT n'est pas défini (dev local, tests, CI) : `tracer` reste alors le
traceur sans effet de l'API, et les spans posés dans le code ne coûtent rien. Les autres réglages (nom du
service, attributs de ressource, délais) passent par les variables OTEL_* standard, lues par le SDK.
"""

import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:  # importé aussi par la CLI et l'ingestion, sans FastAPI
    from fastapi import FastAPI

tracer = trace.get_tracer("coach")

_configured = False


def enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure() -> None:
    """Branche l'export OTLP (une fois par process). Seul appel utile hors API : la CLI (synchro Garmin nocturne).
    Les spans et logs en attente partent à la sortie du process (shutdown des fournisseurs par atexit)."""
    if enabled():
        _configure_providers()


def setup(app: "FastAPI") -> None:
    """`configure`, puis instrumente l'application FastAPI."""
    if not enabled():
        return
    _configure_providers()
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # /api/health : appelé en boucle par les sondes, sans intérêt dans Tempo.
    # send/receive : un span par message ASGI, du bruit pour une API JSON.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="api/health", exclude_spans=["send", "receive"])


def _configure_providers() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    # Imports locaux : le SDK et les exporteurs ne sont chargés que si la télémétrie est active.
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # OTEL_SERVICE_NAME et OTEL_RESOURCE_ATTRIBUTES (project=savapav…) sont lus par Resource.create ; un attribut
    # passé ici l'emporterait sur eux, d'où le nom par défaut posé seulement s'ils n'en donnent pas.
    resource = Resource.create()
    if resource.attributes.get(SERVICE_NAME, "").startswith("unknown_service"):
        resource = resource.merge(Resource({SERVICE_NAME: "savapav"}))

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    # En plus de la sortie console : chaque log porte le trace_id du span en cours (lien Loki → Tempo).
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

    # Appels sortants : Ollama (httpx, llm.py) et Garmin Connect (requests, bibliothèque garminconnect).
    HTTPXClientInstrumentor().instrument()
    RequestsInstrumentor().instrument()
