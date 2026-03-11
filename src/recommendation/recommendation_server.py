#!/usr/bin/python
#
# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import random
import time
from concurrent import futures

import grpc

from opentelemetry import trace
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer
from opentelemetry.sdk.resources import Resource

import demo_pb2
import demo_pb2_grpc
from logger import getJSONLogger
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import (
    get_meter_provider,
    set_meter_provider,
    get_meter,
    set_meter,
    init_metrics
)


cached_ids = []
cached_ids_to_retrieve_recommendations_for = []
MAX_CACHED_IDS = 2000000

first_run = True

class RecommendationService(demo_pb2_grpc.RecommendationServiceServicer):
    def ListRecommendations(self, request, context):
        span = trace.get_current_span()
        span.set_attribute("app.products_count", len(request.product_ids))
        span.set_attribute("app.products", str(request.product_ids))

        # build and return response
        response = demo_pb2.ListRecommendationsResponse()
        product_ids = get_product_list(request.product_ids)
        if len(product_ids) <= 4:
            response.product_ids.extend(product_ids)
        else:
            # select random products to recommend
            for _ in range(4):
                random_product = random.choice(product_ids)
                response.product_ids.append(random_product)
                product_ids.remove(random_product)

        # record the number of products returned as a metric
        meter = get_meter("recommendation_service")
        recommended_products_counter = meter.create_counter(
            name="recommended_products",
            description="The number of products recommended",
            unit="1",
        )
        recommended_products_counter.add(len(response.product_ids))

        return response

    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING)

    def Watch(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.UNIMPLEMENTED)

def get_recommendations_ids(request_product_ids):
    global cached_ids
    global cached_ids_to_retrieve_recommendations_for

    with tracer.start_as_current_span("get_recommendations_ids") as span:
        can_retrieve_from_cache = all(
            p_id in cached_ids_to_retrieve_recommendations_for
            for p_id in request_product_ids
        )

        if not can_retrieve_from_cache:
            logger.info("get_recommendations_ids: cache miss")
            span.set_attribute("app.cache_hit", False)
            cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
            # Fixed: Simply collect product IDs without duplicating the cache
            cached_ids = [x.id for x in cat_response.products]
            cached_ids_to_retrieve_recommendations_for = list(request_product_ids)
            return cached_ids
        else:
            logger.info("get_recommendations_ids: cache hit")
            span.set_attribute("app.cache_hit", True)
            return cached_ids

def get_product_list(request_product_ids):
    global first_run

    with tracer.start_as_current_span("get_product_list") as span:
        # Assume that the first request is for demo mode
        if first_run:
            first_run = False
            request_product_ids_str = ''.join(request_product_ids)
            request_product_ids = request_product_ids_str.split(',')

        product_ids = get_recommendations_ids(request_product_ids)

        span.set_attribute("app.products.count", len(product_ids))

        return product_ids

if __name__ == "__main__":
    logger = getJSONLogger("recommendationservice-server")
    tracer = trace.get_tracer("recommendationservice")

    server_port = os.environ.get('PORT', "8080")
    catalog_addr = os.environ.get('PRODUCT_CATALOG_SERVICE_ADDR', '')
    if catalog_addr == "":
        raise Exception('PRODUCT_CATALOG_SERVICE_ADDR environment variable not set')

    logger.info("ProductCatalogService address: " + catalog_addr)

    # Create gRPC stub for ProductCatalogService
    channel = grpc.insecure_channel(catalog_addr)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)

    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add class to gRPC server
    service = RecommendationService()
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Start server
    logger.info("Listening on port: " + server_port)
    server.add_insecure_port('[::]:' + server_port)
    server.start()

    # Keep thread alive
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)