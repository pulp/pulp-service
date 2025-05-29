#!/usr/bin/env python3

import sys
import time
import logging
from django.db import connection, utils

logger = logging.getLogger(__name__)

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)
    print("Waiting on postgresql to start...")
    logger.info("Waiting on postgresql to start...")
    for dummy in range(100):
        try:
            connection.ensure_connection()
            break
        except utils.OperationalError as exp:
            logger.warning("Connection failed. Trying again...", exc_info=True)
            time.sleep(3)

    else:
        logger.error("Unable to reach postgres.")
        print("Unable to reach postgres.")
        sys.exit(1)

    logger.info("Postgres started.")
    print("Postgres started.")
    sys.exit(0)
