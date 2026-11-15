"""Background worker orchestration script for Forge.

Runs Outbox Relay Worker and SQS Consumer services (Webhook Dispatcher, CSV Exporter)
with graceful shutdown and health reporting.
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Ensure apps/api and apps are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "apps" / "api"))
sys.path.insert(0, str(BASE_DIR / "apps"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

import pymongo
from django.conf import settings

from workers.consumers.csv_exporter import CSVStreamingExporter
from workers.consumers.webhook_dispatcher import WebhookDispatcherConsumer
from workers.relay.publisher import OutboxRelayWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("forge.workers")


def run_workers(service: str = "all", poll_interval: float = 1.0, once: bool = False):
    mongo_client = pymongo.MongoClient(
        getattr(settings, "MONGO_URI", "mongodb://localhost:27017/?replicaSet=rs0")
    )

    relay = OutboxRelayWorker(mongo_client=mongo_client)
    webhook_consumer = WebhookDispatcherConsumer(
        mongo_client=mongo_client,
        allow_insecure_http=settings.DEBUG,
    )
    csv_consumer = CSVStreamingExporter(mongo_client=mongo_client)

    running = True

    def handle_exit(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received. Finishing in-flight work and stopping...")
        running = False

    try:
        signal.signal(signal.SIGINT, handle_exit)
        signal.signal(signal.SIGTERM, handle_exit)
    except Exception:
        # Some environments (e.g. non-main thread in Windows) don't support custom signal hooks
        pass

    logger.info(
        "Forge Worker runner started (service=%s, poll_interval=%ss, once=%s)",
        service,
        poll_interval,
        once,
    )

    while running:
        published_count = 0
        webhook_count = 0
        csv_count = 0

        if service in ("all", "relay"):
            try:
                published_count = relay.publish_pending_batch()
                if published_count > 0:
                    logger.info("Relay published %d event(s) to SQS", published_count)
            except Exception as e:
                logger.debug("Relay publish note: %s", e)

        if service in ("all", "webhook"):
            try:
                webhook_count = webhook_consumer.process_messages(
                    max_messages=10, wait_time_seconds=1
                )
                if webhook_count > 0:
                    logger.info("Webhook consumer processed %d message(s)", webhook_count)
            except Exception as e:
                logger.debug("Webhook consumer poll note: %s", e)

        if service in ("all", "csv"):
            try:
                csv_count = csv_consumer.process_messages(max_messages=10, wait_time_seconds=1)
                if csv_count > 0:
                    logger.info("CSV exporter processed %d message(s)", csv_count)
            except Exception as e:
                logger.debug("CSV consumer poll note: %s", e)

        if once:
            logger.info("Single batch iteration completed (--once active). Exiting.")
            break

        if published_count == 0 and webhook_count == 0 and csv_count == 0:
            time.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forge Distributed Worker Runner")
    parser.add_argument(
        "--service",
        choices=["all", "relay", "webhook", "csv"],
        default="all",
        help="Worker service to execute",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling sleep interval in seconds",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single polling iteration and exit",
    )
    args = parser.parse_args()
    run_workers(service=args.service, poll_interval=args.interval, once=args.once)
