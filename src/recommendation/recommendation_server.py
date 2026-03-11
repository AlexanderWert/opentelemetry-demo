# Copyright 2020 Google LLC
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
import grpc
from concurrent import futures

import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from opentelemetry import context, baggage, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from logger import getJSONLogger
logger = getJSONLogger('recommendationservice-server')

from opentelemetry_setup import (
    init_tracer,
    init_metrics
)


first_run = True

class RecommendationService(demo_pb2_grpc.RecommendationServiceServicer):

    def ListRecommendations(self, request, context):
        prod_list = get_product_list(request.product_ids)
        max_responses = 5

        # fetch list of products from product catalog stub
        num_products = len(prod_list)
        num_return = min(max_responses, num_products)
        # sample list of indicies to return
        indices = random.sample(range(num_products), num_return)
        # fetch product ids from indices
        prod_ids = [prod_list[i] for i in indices]
        logger.info("[Recv ListRecommendations] product_ids={}".format(prod_ids))

        # build and return response
        response = demo_pb2.ListRecommendationsResponse()
        response.product_ids.extend(prod_ids)
        return response

    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING)

    def Watch(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.UNIMPLEMENTED)


def get_product_list(request_product_ids):
    global first_run

    with tracer.start_as_current_span("get_product_list") as span:
        if first_run or not request_product_ids:
            first_run = False
        else:
            request_product_ids_str = ''.join(request_product_ids)
            request_product_ids = request_product_ids_str.split(',')

        cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
        product_ids = [x.id for x in cat_response.products]

        span.set_attribute("app.products.count", len(product_ids))

        filtered_products = list(set(product_ids) - set(request_product_ids))
        return filtered_products


if __name__ == "__main__":
    logger.info("initializing recommendation service")

    tracer = init_tracer('recommendation')
    meter = init_metrics('recommendation')

    catalog_addr = os.environ.get('PRODUCT_CATALOG_SERVICE_ADDR', '')
    if catalog_addr == '':
        raise Exception('PRODUCT_CATALOG_SERVICE_ADDR environment variable not set')
    logger.info("product catalog address: " + catalog_addr)
    channel = grpc.insecure_channel(catalog_addr)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)

    # create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # add class to gRPC server
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(RecommendationService(), server)

    # add health checking service to gRPC server
    health_pb2_grpc.add_HealthServicer_to_server(health_pb2.HealthServicer(), server)

    port = os.environ.get('RECOMMENDATION_SERVICE_PORT', 8080)
    logger.info("listening on port: " + str(port))
    server.add_insecure_port('[::]:' + str(port))
    server.start()
    server.wait_for_termination()
