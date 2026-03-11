# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import os
import random
from concurrent import futures

import grpc
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from logger import getJSONLogger
logger = getJSONLogger('recommendation-server')

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider

from metrics import (
    init_metrics
)


cached_ids = []
cached_ids_to_retrieve_recommendations_for = []
MAX_CACHED_IDS = 2000000

first_run = True

class RecommendationService(demo_pb2_grpc.RecommendationServiceServicer):
    def ListRecommendations(self, request, context):
        prod_list = get_product_list(request.product_ids)
        span = trace.get_current_span()
        span.set_attribute("app.products_recommended.count", len(prod_list))
        logger.info(f"[Recv ListRecommendations] product_ids={request.product_ids}")

        # build and return response
        response = demo_pb2.ListRecommendationsResponse()
        response.product_ids.extend(prod_list)

        return response

    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING)

    def Watch(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.UNIMPLEMENTED)

def get_recommendations_ids(request_product_ids):
    global cached_ids

    with tracer.start_as_current_span("get_recommendations_ids") as span:
        can_retrieve_from_cache = True
        for p_id in request_product_ids:
            if p_id not in cached_ids_to_retrieve_recommendations_for:
                can_retrieve_from_cache = False

        if not can_retrieve_from_cache:
            logger.info("get_recommendations_ids: cache miss")
            span.set_attribute("app.cache_hit", False)
            cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
            ids_to_add = []
            for x in cat_response.products:
                ids_to_add.append(x.id)
            if len(ids_to_add) + len(cached_ids) < MAX_CACHED_IDS:
                cached_ids = cached_ids + ids_to_add
            return cached_ids
        else:
            logger.info("get_recommendations_ids: cache hit")
            span.set_attribute("app.cache_hit", True)
            return cached_ids

def get_product_list(request_product_ids):
    global first_run

    with tracer.start_as_current_span("get_product_list") as span:
        max_responses = 5
        request_product_ids_str = ''.join(request_product_ids)
        request_product_ids = request_product_ids_str.split(',')

        product_ids = get_recommendations_ids(request_product_ids)

        span.set_attribute("app.products.count", len(product_ids))

        # Create a filtered list of products excluding the products received as input
        filtered_products = list(set(product_ids) - set(request_product_ids))
        num_products = len(filtered_products)
        span.set_attribute("app.filtered_products.count", num_products)
        num_return = min(max_responses, num_products)

        # Sample list of indicies to return
        indices = random.sample(range(num_products), num_return)
        # Fetch product ids from indices
        prod_list = [filtered_products[i] for i in indices]

        return prod_list


if __name__ == "__main__":
    # Initialize tracer
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    tracer = trace.get_tracer("recommendation")

    # Initialize meter
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Initialize gRPC instrumentation
    grpc_server_instrumentor = GrpcInstrumentorServer()
    grpc_server_instrumentor.instrument()

    # Initialize feature flags
    api.set_provider(FlagdProvider())

    # Initialize metrics
    init_metrics(meter_provider.get_meter("recommendation"))

    # Create gRPC channel to product catalog
    catalog_addr = os.environ.get('PRODUCT_CATALOG_SERVICE_ADDR', '')
    channel = grpc.insecure_channel(catalog_addr)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)

    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add services
    service = RecommendationService()
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Start server
    port = os.environ.get('RECOMMENDATION_SERVICE_PORT', 8080)
    server.add_insecure_port(f'[::]:{port}')
    logger.info(f"Recommendation service started, listening on port {port}")
    server.start()
    server.wait_for_termination()
