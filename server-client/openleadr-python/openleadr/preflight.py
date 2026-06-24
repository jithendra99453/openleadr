
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, is_dataclass
from openleadr import enums, utils
import logging

logger = logging.getLogger('openleadr')


def preflight_message(message_type, message_payload):
    """
    Test message contents before sending them. Corrects benign errors (with warnings)
    and raises an Exception for uncorrectable errors. Modifies the message_payload
    dict in‑place and returns it (also for convenience).

    :param message_type str: The type of message you are sending
    :param message_payload dict: The contents of the message (will be modified)
    :return: The modified message_payload (same dict, for chaining)
    """
    # Convert any dataclass instances to dicts (recursively)
    for key, value in message_payload.items():
        if isinstance(value, list):
            message_payload[key] = [
                asdict(item) if is_dataclass(item) else item
                for item in value
            ]
        else:
            message_payload[key] = asdict(value) if is_dataclass(value) else value

    # Call the specific pre‑flight handler if it exists
    handler_name = f'_preflight_{message_type}'
    if handler_name in globals():
        globals()[handler_name](message_payload)

    return message_payload


def _preflight_oadrRegisterReport(message_payload):
    """Validate and correct an oadrRegisterReport message."""
    for report in message_payload.get('reports', []):
        # Ensure report name has METADATA_ prefix when appropriate
        if report.get('report_name') in enums.REPORT_NAME.values \
                and not report['report_name'].startswith("METADATA_"):
            report['report_name'] = 'METADATA_' + report['report_name']
            logger.warning(f"Added 'METADATA_' prefix to report name '{report['report_name']}'")

        # Validate each report description's measurement dict
        for report_desc in report.get('report_descriptions', []):
            if report_desc.get('measurement'):
                utils.validate_report_measurement_dict(report_desc['measurement'])

        # Add the correct XML namespace to each measurement
        for report_desc in report.get('report_descriptions', []):
            measurement = report_desc.get('measurement')
            if measurement:
                name = measurement.get('name')
                if name in enums._MEASUREMENT_NAMESPACES:
                    measurement['ns'] = enums._MEASUREMENT_NAMESPACES[name]
                else:
                    raise ValueError(f"Unknown measurement name: '{name}'")


def _preflight_oadrDistributeEvent(message_payload):
    """Validate and correct an oadrDistributeEvent message."""
    # Helper to safely get a timedelta from a duration (string or timedelta)
    def _to_timedelta(duration):
        if isinstance(duration, timedelta):
            return duration
        return utils.parse_duration(duration)

    for event in message_payload.get('events', []):
        # Ensure active_period exists and its duration is a timedelta
        if 'active_period' not in event:
            raise ValueError("Each event must have an 'active_period'.")
        active_duration = event['active_period'].get('duration')
        if active_duration is None:
            raise ValueError("Each active_period must have a 'duration'.")
        active_duration_td = _to_timedelta(active_duration)

        # Collect total durations of all signals
        signal_durations = []
        for signal in event.get('event_signals', []):
            intervals = signal.get('intervals', [])
            total = timedelta(seconds=0)
            for interval in intervals:
                interval_duration = interval.get('duration')
                if interval_duration is None:
                    raise ValueError("Each interval must have a 'duration'.")
                total += _to_timedelta(interval_duration)
            signal_durations.append(total)

        # If no signals, nothing to compare
        if not signal_durations:
            continue

        # Check consistency between active_period duration and signal durations
        if not all(d == signal_durations[0] for d in signal_durations):
            raise ValueError(
                "The different event signals have different total durations. "
                "They must all be equal."
            )
        if active_duration_td != signal_durations[0]:
            logger.warning(
                f"active_period duration ({active_duration_td}) differs from "
                f"signal total duration ({signal_durations[0]}). Adjusting active_period "
                f"duration to match the signals."
            )
            event['active_period']['duration'] = signal_durations[0]

    # Rule 9: SIMPLE signal payloads must be 0,1,2,3
    for event in message_payload.get('events', []):
        for signal in event.get('event_signals', []):
            if signal.get('signal_name') == 'SIMPLE':
                for interval in signal.get('intervals', []):
                    payload = interval.get('signal_payload')
                    if payload not in (0, 1, 2, 3):
                        raise ValueError(
                            f"SIMPLE signal payload must be 0,1,2,3, got {payload}"
                        )

    # Rule 14: current_value for non‑active SIMPLE events must be 0
    for event in message_payload.get('events', []):
        event_status = event.get('event_descriptor', {}).get('event_status')
        for signal in event.get('event_signals', []):
            if signal.get('signal_name') == 'SIMPLE' and event_status != 'ACTIVE':
                current = signal.get('current_value')
                if current is not None and current != 0:
                    logger.warning(
                        f"SIMPLE event not active: current_value {current} forced to 0."
                    )
                    signal['current_value'] = 0

    # Add XML namespaces to measurements in signals
    for event in message_payload.get('events', []):
        for signal in event.get('event_signals', []):
            measurement = signal.get('measurement')
            if measurement:
                name = measurement.get('name')
                if name in enums._MEASUREMENT_NAMESPACES:
                    measurement['ns'] = enums._MEASUREMENT_NAMESPACES[name]
                else:
                    raise ValueError(f"Unknown measurement name: '{name}'")

    # Validate/add response_required
    for event in message_payload.get('events', []):
        resp = event.get('response_required')
        if resp is None:
            event['response_required'] = 'always'
        elif resp not in ('never', 'always'):
            logger.warning(
                f"response_required value '{resp}' invalid. Changing to 'always'."
            )
            event['response_required'] = 'always'

    # Ensure event_descriptor.created_date_time is set
    for event in message_payload.get('events', []):
        desc = event.get('event_descriptor')
        if desc is None:
            event['event_descriptor'] = {}
            desc = event['event_descriptor']
        if not desc.get('created_date_time'):
            logger.warning(
                "Missing created_date_time in event_descriptor. Adding current UTC time."
            )
            desc['created_date_time'] = datetime.now(timezone.utc)

    # Synchronise targets and targets_by_type
    for event in message_payload.get('events', []):
        targets = event.get('targets')
        targets_by_type = event.get('targets_by_type')
        if targets and targets_by_type:
            computed_by_type = utils.group_targets_by_type(targets)
            if computed_by_type != targets_by_type:
                raise ValueError(
                    "Inconsistent 'targets' and 'targets_by_type' in event."
                )
        elif targets_by_type and not targets:
            event['targets'] = utils.ungroup_targets_by_type(targets_by_type)
        # else: nothing to do