# SPDX-License-Identifier: Apache-2.0

# Copyright 2020 Contributors to OpenLEADR

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# flake8: noqa

import logging

from openleadr.service.decorators import handler, service
from openleadr.service.vtn_service import VTNService
from openleadr.service.event_service import EventService
from openleadr.service.poll_service import PollService
from openleadr.service.registration_service import RegistrationService
from openleadr.service.report_service import ReportService
from openleadr.objects import (
    Event, EventDescriptor, EventSignal, Interval, Target, ActivePeriod,
    Report, ReportDescription, ReportPayload, ReportInterval, Opt, Vavailability
)
from openleadr.enums import (
    EVENT_STATUS, SIGNAL_TYPE, SIGNAL_NAME, OPT, OPT_REASON,
    REPORT_NAME, READING_TYPE, REPORT_TYPE, STATUS_CODES
)
from openleadr import utils, errors, messaging

# IMPORTANT: Remove this line to break the circular import
# from .client import OpenADRClient  # DELETE THIS LINE!

__all__ = [
    'handler',
    'service',
    'VTNService',
    'EventService',
    'PollService',
    'RegistrationService',
    'ReportService',
    'Event',
    'EventDescriptor',
    'EventSignal',
    'Interval',
    'Target',
    'ActivePeriod',
    'Report',
    'ReportDescription',
    'ReportPayload',
    'ReportInterval',
    'Opt',
    'Vavailability',
    'EVENT_STATUS',
    'SIGNAL_TYPE',
    'SIGNAL_NAME',
    'OPT',
    'OPT_REASON',
    'REPORT_NAME',
    'READING_TYPE',
    'REPORT_TYPE',
    'STATUS_CODES',
    'utils',
    'errors',
    'messaging',
]


def enable_default_logging(level=logging.INFO):
    """
    Turn on logging to stdout.
    :param level integer: The logging level you wish to use.
                          Defaults to logging.INFO.
    """
    import sys
    import logging
    logger = logging.getLogger('openleadr')
    handler_names = [handler.name for handler in logger.handlers]
    if 'openleadr_default_handler' not in handler_names:
        logger.setLevel(level)
        logging_handler = logging.StreamHandler(stream=sys.stdout)
        logging_handler.set_name('openleadr_default_handler')
        logging_handler.setLevel(logging.DEBUG)
        logger.addHandler(logging_handler)