
import asyncio
import hashlib
import logging
import os
import re
import ssl
import uuid
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from openleadr import enums, objects

logger = logging.getLogger('openleadr')

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
DATETIME_FORMAT_NO_MICROSECONDS = "%Y-%m-%dT%H:%M:%SZ"


def generate_id(*args, **kwargs):
    """
    Generate a string that can be used as an identifier in OpenADR messages.
    """
    return str(uuid.uuid4())


def flatten_xml(message):
    """
    Flatten the entire XML structure by removing all newlines and
    reducing multiple whitespace characters to a single space.
    """
    # Strip each line and remove completely empty lines
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    # Further clean each line and join into a single string
    cleaned_parts = []
    for line in lines:
        # Remove newlines (should already be gone)
        line = line.replace('\n', '')
        # Replace multiple spaces with a single space
        line = re.sub(r'\s+', ' ', line)
        cleaned_parts.append(line)
    return "".join(cleaned_parts)


def normalize_dict(ordered_dict):
    """
    Main conversion function for the output of xmltodict to the OpenLEADR
    representation of OpenADR contents.

    :param ordered_dict dict: The OrderedDict, dict or dataclass that you wish to convert.
    """
    if is_dataclass(ordered_dict):
        ordered_dict = asdict(ordered_dict)

    def normalize_key(key):
        if key.startswith('oadr'):
            key = key[4:]
        elif key.startswith('ei'):
            key = key[2:]
        # Don't normalize the measurement descriptions
        if key in enums._MEASUREMENT_NAMESPACES:
            return key
        key = re.sub(r'([a-z])([A-Z])', r'\1_\2', key)
        if '-' in key:
            key = key.replace('-', '_')
        return key.lower()

    d = {}
    for key, value in ordered_dict.items():
        # Interpret values from the dict
        if key.startswith("@"):
            continue
        key = normalize_key(key)

        if isinstance(value, (OrderedDict, dict)):
            d[key] = normalize_dict(value)

        elif isinstance(value, list):
            d[key] = []
            for item in value:
                if isinstance(item, (OrderedDict, dict)):
                    dict_item = normalize_dict(item)
                    d[key].append(dict_item)  # FIXED: removed double normalize_dict call
                else:
                    d[key].append(item)
        elif key in ("duration", "startafter", "max_period", "min_period"):
            d[key] = parse_duration(value)
        elif ("date_time" in key or key == "dtstart") and isinstance(value, str):
            d[key] = parse_datetime(value)
        elif value in ('true', 'false'):
            d[key] = parse_boolean(value)
        elif isinstance(value, str):
            if re.match(r'^-?\d+$', value):
                d[key] = int(value)
            elif re.match(r'^-?[\d.]+$', value):
                d[key] = float(value)
            else:
                d[key] = value
        else:
            d[key] = value

        # Do our best to make the dictionary structure as pythonic as possible
        if key.startswith("x_ei_"):
            d[key[5:]] = d.pop(key)
            key = key[5:]

        # Group all targets as a list of dicts under the key "target"
        if key == 'target':
            targets = d.pop(key)
            new_targets = []
            if targets:
                for ikey in targets:
                    if isinstance(targets[ikey], list):
                        new_targets.extend([{ikey: value} for value in targets[ikey]])
                    else:
                        new_targets.append({ikey: targets[ikey]})
            d[key + "s"] = new_targets
            key = key + "s"

            # Also add a targets_by_type element to this dict
            # to access the targets in a more convenient way.
            d['targets_by_type'] = group_targets_by_type(new_targets)

        # Group all reports as a list of dicts under the key "pending_reports"
        if key == "pending_reports":
            # If there are pending reports, turn them into a list of dicts,
            # each with a single 'report_request_id' key.
            if isinstance(d[key], dict) and 'report_request_id' in d[key]:

                # If there is only one report_request_id, make sure it is
                # turned into a list before further processing.
                if not isinstance(d[key]['report_request_id'], list):
                    d[key]['report_request_id'] = [d[key]['report_request_id']]

                # When collecting the report_request_ids, make sure even numeric
                # ids get turned into strings.
                d[key] = [{'report_request_id': str(rrid)}
                          for rrid in d[key]['report_request_id']
                          if d[key]['report_request_id'] is not None]

            # If there are no pending reports, make sure we get an empty list back
            # so any iteration can proceed as normal.
            elif d[key] is None:
                d[key] = []

        # Group all events as a list of dicts under the key "events"
        elif key == "event" and isinstance(d[key], list):
            events = d.pop("event")
            new_events = []
            for event in events:
                new_event = event['event']
                new_event['response_required'] = event['response_required']
                new_events.append(new_event)
            d["events"] = new_events

        # If there's only one event, also put it into a list
        elif key == "event" and isinstance(d[key], dict) and "event" in d[key]:
            oadr_event = d.pop('event')
            ei_event = oadr_event['event']
            ei_event['response_required'] = oadr_event['response_required']
            d['events'] = [ei_event]

        elif key in ("request_event", "created_event") and isinstance(d[key], dict):
            d = d[key]

        # Pluralize some lists
        elif key in ('report_request', 'report', 'specifier_payload'):
            if isinstance(d[key], list):
                d[key + 's'] = d.pop(key)
            else:
                d[key + 's'] = [d.pop(key)]

        elif key in ('report_description', 'event_signal'):
            descriptions = d.pop(key)
            if not isinstance(descriptions, list):
                descriptions = [descriptions]
            for description in descriptions:
                # We want to make the identification of the measurement universal
                for measurement in enums._MEASUREMENT_NAMESPACES:
                    if measurement in description:
                        name, item = measurement, description.pop(measurement)
                        break
                else:
                    break
                item['description'] = item.pop('item_description', None)
                item['unit'] = item.pop('item_units', None)
                if 'si_scale_code' in item:
                    item['scale'] = item.pop('si_scale_code')
                if 'pulse_factor' in item:
                    item['pulse_factor'] = item.pop('pulse_factor')
                description['measurement'] = {'name': name,
                                              **item}
            d[key + 's'] = descriptions

        # Promote the contents of the Qualified Event ID
        elif key == "qualified_event_id" and isinstance(d['qualified_event_id'], dict):
            qeid = d.pop('qualified_event_id')
            d['event_id'] = qeid['event_id']
            d['modification_number'] = qeid['modification_number']

        # Durations are encapsulated in their own object, remove this nesting
        elif isinstance(d[key], dict) and "duration" in d[key] and len(d[key]) == 1:
            d[key] = d[key]["duration"]

        # In general, remove all double nesting
        elif isinstance(d[key], dict) and key in d[key] and len(d[key]) == 1:
            d[key] = d[key][key]

        # In general, remove the double nesting of lists of items
        elif isinstance(d[key], dict) and key[:-1] in d[key] and len(d[key]) == 1:
            if isinstance(d[key][key[:-1]], list):
                d[key] = d[key][key[:-1]]
            else:
                d[key] = [d[key][key[:-1]]]

        # Payload values are wrapped in an object according to their type. We don't need that.
        elif key in ("signal_payload", "current_value"):
            if isinstance(d[key], dict):
                if 'payload_float' in d[key] and 'value' in d[key]['payload_float'] \
                        and d[key]['payload_float']['value'] is not None:
                    d[key] = float(d[key]['payload_float']['value'])
                elif 'payload_int' in d[key] and 'value' in d[key]['payload_int'] \
                        and d[key]['payload_int'] is not None:
                    d[key] = int(d[key]['payload_int']['value'])

        # Report payloads contain an r_id and a type-wrapped payload_float
        elif key == 'report_payload':
            if 'payload_float' in d[key] and 'value' in d[key]['payload_float']:
                v = d[key].pop('payload_float')
                d[key]['value'] = float(v['value'])
            elif 'payload_int' in d[key] and 'value' in d[key]['payload_int']:
                v = d[key].pop('payload_float')
                d[key]['value'] = int(v['value'])

        # All values other than 'false' must be interpreted as True for testEvent (rule 006)
        elif key == 'test_event' and not isinstance(d[key], bool):
            d[key] = True

        # Promote the 'text' item
        elif isinstance(d[key], dict) and "text" in d[key] and len(d[key]) == 1:
            if key == 'uid':
                d[key] = int(d[key]["text"])
            else:
                d[key] = d[key]["text"]

        # Promote a 'date-time' item
        elif isinstance(d[key], dict) and "date_time" in d[key] and len(d[key]) == 1:
            d[key] = d[key]["date_time"]

        # Promote 'properties' item, discard the unused 'components' item
        elif isinstance(d[key], dict) and "properties" in d[key] and len(d[key]) <= 2:
            d[key] = d[key]["properties"]

        # Remove all empty dicts
        elif isinstance(d[key], dict) and len(d[key]) == 0:
            d.pop(key)
    return d


def parse_datetime(value):
    """
    Parse an ISO8601 datetime into a datetime.datetime object.
    Supports microseconds (1 to 6 digits) and the trailing 'Z' for UTC.
    """
    # Improved regex: allows optional fractional seconds with 1-6 digits
    # groups: year, month, day, hour, minute, second, microsecond (optional)
    matches = re.match(
        r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z',
        value
    )
    if matches:
        year, month, day, hour, minute, second = (int(v) for v in matches.groups()[:6])
        micro = matches.group(7)
        if micro is None:
            micro = 0
        else:
            micro = int(micro.ljust(6, '0'))  # pad to 6 digits
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=timezone.utc)
    else:
        logger.warning(f"parse_datetime: {value} did not match expected format")
        return value


def parse_duration(value):
    """
    Parse an RFC5545 duration into a timedelta.
    Supports sign, weeks, days, hours, minutes, seconds.
    Years and months are converted (1 year = 365 days, 1 month = 30 days) with a warning.
    """
    if isinstance(value, timedelta):
        return value

    # Pattern: sign? P (years? months? days? T (hours? minutes? seconds?)?) or weeks
    # We capture sign, then either a combined set or just weeks.
    regex = r'^([+-])?P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?|(?:(\d+)W)$'
    matches = re.match(regex, value)
    if not matches:
        raise ValueError(f"The duration '{value}' did not match the RFC5545 format")

    # Extract groups; the last group is the week-only case
    sign = matches.group(1)
    years = int(matches.group(2) or 0)
    months = int(matches.group(3) or 0)
    days = int(matches.group(4) or 0)
    hours = int(matches.group(5) or 0)
    minutes = int(matches.group(6) or 0)
    seconds = int(matches.group(7) or 0)
    weeks = int(matches.group(8) or 0)

    if years != 0:
        logger.warning("Received a duration that specifies years, which is not a determinate duration. "
                       "It will be interpreted as 1 year = 365 days.")
        days += 365 * years
    if months != 0:
        logger.warning("Received a duration that specifies months, which is not a determinate duration "
                       "It will be interpreted as 1 month = 30 days.")
        days += 30 * months

    duration = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    if sign == '-':
        duration = -duration
    return duration


def parse_boolean(value):
    """Convert 'true'/'false' strings to boolean."""
    return value == 'true'


def datetimeformat(value, format=DATETIME_FORMAT):
    """
    Format a given datetime as a UTC ISO3339 string.
    """
    if not isinstance(value, datetime):
        return value
    return value.astimezone(timezone.utc).strftime(format)


def timedeltaformat(value):
    """
    Format a timedelta to an RFC5545 Duration string.
    """
    if not isinstance(value, timedelta):
        return value
    # Handle negative durations
    sign = ''
    if value.total_seconds() < 0:
        sign = '-'
        value = -value
    days = value.days
    hours, seconds = divmod(value.seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}D")
    if hours or minutes or seconds:
        parts.append("T")
        if hours:
            parts.append(f"{hours}H")
        if minutes:
            parts.append(f"{minutes}M")
        if seconds:
            parts.append(f"{seconds}S")
    if not parts:
        return "PT0S"
    return sign + "P" + "".join(parts)


def booleanformat(value):
    """
    Format a boolean value as 'true' or 'false'.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value in ("true", "false"):
        return value
    raise ValueError(f"A boolean value must be provided, not {value}.")


def ensure_bytes(obj):
    """
    Converts a utf-8 str object to bytes.
    """
    if obj is None:
        return obj
    if isinstance(obj, bytes):
        return obj
    if isinstance(obj, str):
        return obj.encode('utf-8')
    raise TypeError("Must be bytes or str")


def ensure_str(obj):
    """
    Converts bytes to a utf-8 string.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, bytes):
        return obj.decode('utf-8')
    raise TypeError("Must be bytes or str")


def certificate_fingerprint_from_der(der_bytes):
    """Compute SHA-256 fingerprint of DER certificate (last 10 bytes as colon‑separated hex)."""
    hash_ = hashlib.sha256(der_bytes).hexdigest()
    return ":".join([hash_[i:i+2].upper() for i in range(44, 64, 2)])


def certificate_fingerprint(certificate_str):
    """
    Calculate the fingerprint for the given PEM certificate.
    """
    der_bytes = ssl.PEM_cert_to_DER_cert(ensure_str(certificate_str))
    return certificate_fingerprint_from_der(der_bytes)


def certificate_domain(cert):
    """
    Extract the Common Name (CN) from a PEM certificate.
    The cert argument may be a file path or a PEM-encoded string.
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    # If cert is a file that exists, read it
    if os.path.exists(cert):
        with open(cert) as f:
            cert_pem = f.read()
    else:
        # Assume it's already a PEM string (maybe with or without newlines)
        cert_pem = ensure_str(cert)
        if not cert_pem.startswith("-----BEGIN CERTIFICATE-----"):
            raise ValueError("Certificate must be a valid file path or a PEM-encoded string.")

    # Load the certificate
    parsed = x509.load_pem_x509_certificate(cert_pem.encode('utf-8'), default_backend())
    cn_attributes = parsed.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
    return ", ".join(attr.value for attr in cn_attributes) if cn_attributes else ""


def extract_pem_cert(tree):
    """
    Extract an X509 certificate from an XML signature element and return a PEM-encoded string.
    """
    cert = tree.find('.//{http://www.w3.org/2000/09/xmldsig#}X509Certificate').text
    if not cert.endswith("\n"):
        cert += "\n"
    # Insert line breaks every 64 characters for standard PEM formatting
    cert_body = '\n'.join(cert[i:i+64] for i in range(0, len(cert), 64))
    return "-----BEGIN CERTIFICATE-----\n" + cert_body + "\n-----END CERTIFICATE-----\n"


def find_by(dict_or_list, key, value, *args):
    """
    Find a dict inside a dict or list by key=value properties.
    You can search for a nesting by separating the levels with a period (.).
    """
    search_params = [(key, value)]
    if args:
        search_params += [(args[i], args[i+1]) for i in range(0, len(args), 2)]

    if isinstance(dict_or_list, dict):
        dict_or_list = dict_or_list.values()

    for item in dict_or_list:
        for key, value in search_params:
            _item = item
            keys = key.split(".")
            for k in keys[:-1]:
                if not hasmember(_item, k):
                    break
                _item = getmember(_item, k)
            last_key = keys[-1]
            if isinstance(value, tuple):
                if not hasmember(_item, last_key) or getmember(_item, last_key) not in value:
                    break
            else:
                if not hasmember(_item, last_key) or getmember(_item, last_key) != value:
                    break
        else:
            return item
    return None


def group_by(list_, key, pop_key=False):
    """
    Return a dict that groups values from a list of dicts by a (possibly dotted) key.
    """
    grouped = {}
    key_path = key.split(".")
    for item in list_:
        value = item
        for k in key_path:
            value = value.get(k)
        grouped.setdefault(value, []).append(item)
    return grouped


def pop_by(list_, key, value, *args):
    """
    Pop the first item that satisfies the search params from the given list.
    """
    item = find_by(list_, key, value, *args)
    if item:
        idx = list_.index(item)
        return list_.pop(idx)
    return None


def cron_config(interval, randomize_seconds=False):
    """
    Returns a dict with cron settings for the given interval (timedelta).
    Suitable for APScheduler.
    """
    total_seconds = interval.total_seconds()
    if total_seconds < 60:                     # less than 1 minute
        second = f"*/{int(total_seconds)}"
        minute = "*"
        hour = "*"
    elif total_seconds < 3600:                 # less than 1 hour
        second = "0"
        minute = f"*/{int(total_seconds // 60)}"
        hour = "*"
    elif total_seconds < 86400:                # less than 24 hours
        second = "0"
        minute = "0"
        hour = f"*/{int(total_seconds // 3600)}"
    else:
        second = "0"
        minute = "0"
        hour = "0"
    cfg = {"second": second, "minute": minute, "hour": hour}
    if randomize_seconds:
        jitter = min(int(total_seconds / 10), 300)
        cfg['jitter'] = jitter
    return cfg


def get_cert_fingerprint_from_request(request):
    """Extract the peer certificate fingerprint from an aiohttp request's SSL object."""
    ssl_obj = request.transport.get_extra_info('ssl_object')
    if ssl_obj:
        der_bytes = ssl_obj.getpeercert(binary_form=True)
        if der_bytes:
            return certificate_fingerprint_from_der(der_bytes)
    return None


def group_targets_by_type(list_of_targets):
    """Convert a list of {'type': value} dicts into a dict of type -> list of values."""
    targets_by_type = {}
    for target in list_of_targets:
        for key, value in target.items():
            if value is None:
                continue
            targets_by_type.setdefault(key, []).append(value)
    return targets_by_type


def ungroup_targets_by_type(targets_by_type):
    """Reverse of group_targets_by_type."""
    ungrouped = []
    for target_type, targets in targets_by_type.items():
        if isinstance(targets, list):
            for t in targets:
                ungrouped.append({target_type: t})
        elif isinstance(targets, str):
            ungrouped.append({target_type: targets})
    return ungrouped


def validate_report_measurement_dict(measurement):
    """Validate a measurement dict according to OpenADR 2.0b rules."""
    from openleadr.enums import _ACCEPTABLE_UNITS, _MEASUREMENT_DESCRIPTIONS

    required = {'name', 'description', 'unit'}
    if not required.issubset(measurement):
        raise ValueError("The measurement dict must contain the following keys: "
                         "'name', 'description', 'unit'.")

    name = measurement['name']
    description = measurement['description']
    unit = measurement['unit']

    if name in _MEASUREMENT_DESCRIPTIONS:
        expected_desc = _MEASUREMENT_DESCRIPTIONS[name]
        if description != expected_desc:
            if description.lower() == expected_desc.lower():
                logger.warning(f"The description for measurement '{name}' had wrong case; "
                               f"corrected from '{description}' to '{expected_desc}'.")
                measurement['description'] = expected_desc
            else:
                raise ValueError(f"Description '{description}' does not match expected "
                                 f"'{expected_desc}' for measurement '{name}'.")
        if unit not in _ACCEPTABLE_UNITS[name]:
            allowed = "', '".join(_ACCEPTABLE_UNITS[name])
            raise ValueError(f"Unit '{unit}' not allowed for '{name}'. Allowed: '{allowed}'.")
    else:
        if name != 'customUnit':
            logger.warning(f"Unknown measurement name '{name}' – changed to 'customUnit'.")
            measurement['name'] = 'customUnit'

    if 'power' in name:
        if 'power_attributes' not in measurement:
            raise ValueError("Power‑related measurement must include 'power_attributes'.")
        pa = measurement['power_attributes']
        if not all(k in pa for k in ('voltage', 'ac', 'hertz')):
            raise ValueError("power_attributes must contain 'voltage' (int), 'ac' (bool), 'hertz' (int).")


def get_active_period_from_intervals(intervals, as_dict=True):
    """Compute the overall active period from a list of intervals."""
    if is_dataclass(intervals[0]):
        intervals = [asdict(i) for i in intervals]
    start = min(i['dtstart'] for i in intervals)
    end = max(i['dtstart'] + i['duration'] for i in intervals)
    duration = end - start
    if as_dict:
        return {'dtstart': start, 'duration': duration}
    else:
        from openleadr.objects import ActivePeriod
        return ActivePeriod(dtstart=start, duration=duration)


def determine_event_status(active_period):
    """Return 'far', 'near', 'active', or 'completed' based on current time."""
    now = datetime.now(timezone.utc)
    start = getmember(active_period, 'dtstart')
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
        setmember(active_period, 'dtstart', start)
    duration = getmember(active_period, 'duration')
    end = start + duration
    if now >= end and duration.total_seconds() > 0:
        return 'completed'
    if now >= start:
        return 'active'
    ramp_up = getmember(active_period, 'ramp_up_period', missing=None)
    if ramp_up is not None:
        ramp_start = start - ramp_up
        if now >= ramp_start:
            return 'near'
    return 'far'


def hasmember(obj, member):
    """Check if a dict or dataclass has the given member."""
    if is_dataclass(obj):
        return hasattr(obj, member)
    return member in obj


def getmember(obj, member, missing='_RAISE_'):
    """Get a member from a dict or dataclass. Supports dotted paths."""
    def _get_one(obj, name, missing):
        if is_dataclass(obj):
            if missing == '_RAISE_':
                return getattr(obj, name)
            return getattr(obj, name, missing)
        else:
            if missing == '_RAISE_':
                return obj[name]
            return obj.get(name, missing)

    for part in member.split('.'):
        obj = _get_one(obj, part, missing)
        if missing != '_RAISE_' and obj == missing:
            return missing
    return obj


def setmember(obj, member, value):
    """Set a member of a dict or dataclass. Supports dotted paths."""
    parts = member.split('.')
    if len(parts) > 1:
        parent = getmember(obj, '.'.join(parts[:-1]))
        final_member = parts[-1]
    else:
        parent = obj
        final_member = parts[0]

    if is_dataclass(parent):
        setattr(parent, final_member, value)
    else:
        parent[final_member] = value


def validate_report_request_tuples(list_of_report_requests, mode='full'):
    """
    Validate user-supplied report request tuples.
    mode='full' expects (r_id, callback, sampling_interval [, reporting_interval])
    mode='partial' expects (callback, sampling_interval [, reporting_interval])
    Invalid entries are replaced with None.
    """
    if not list_of_report_requests:
        return
    for report_requests in list_of_report_requests:
        if report_requests is None:
            continue
        for i, rrq in enumerate(report_requests):
            if rrq is None:
                continue

            # Expected length: 3 or 4 for full mode, 2 or 3 for partial
            expected_len = (3, 4) if mode == 'full' else (2, 3)
            if not isinstance(rrq, tuple):
                report_requests[i] = None
                logger.error(f"Report request {i} is not a tuple (got {type(rrq)}).")
                continue
            if len(rrq) not in expected_len:
                report_requests[i] = None
                logger.error(f"Report request tuple has wrong length {len(rrq)} (expected {expected_len}).")
                continue

            # Extract callback (first element after r_id if full mode)
            callback_idx = 1 if mode == 'full' else 0
            sampling_idx = 2 if mode == 'full' else 1
            reporting_idx = 3 if mode == 'full' else 2

            if not callable(rrq[callback_idx]):
                report_requests[i] = None
                logger.error(f"Callback at position {callback_idx} is not callable.")
                continue
            if not isinstance(rrq[sampling_idx], timedelta):
                report_requests[i] = None
                logger.error(f"Sampling interval is not a timedelta (got {type(rrq[sampling_idx])}).")
                continue
            if len(rrq) > sampling_idx + 1 and not isinstance(rrq[reporting_idx], timedelta):
                report_requests[i] = None
                logger.error(f"Reporting interval is not a timedelta (got {type(rrq[reporting_idx])}).")
                continue


async def await_if_required(result):
    """Await if result is a coroutine, otherwise return as‑is."""
    if asyncio.iscoroutine(result):
        return await result
    return result


async def gather_if_required(results):
    """If results contains coroutines, gather them; else return unchanged."""
    if results is None:
        return None
    if not results:
        return results
    if all(asyncio.iscoroutine(r) for r in results):
        return await asyncio.gather(*results)
    if any(asyncio.iscoroutine(r) for r in results):
        return [await await_if_required(r) for r in results]
    return results


def order_events(events, limit=None, offset=None):
    """
    Order events according to OpenADR rules:
    - active events first
    - higher priority (lower number, with 0 = infinity) first
    - earlier start first
    """
    if events is None:
        return None
    if isinstance(events, (objects.Event, dict)):
        events = [events]

    # Update event statuses and creation times
    now = datetime.now(timezone.utc)
    for ev in events:
        if getmember(ev, 'event_descriptor.event_status') != enums.EVENT_STATUS.CANCELLED:
            status = determine_event_status(getmember(ev, 'active_period'))
            if getmember(ev, 'event_descriptor.event_status') != status:
                setmember(ev, 'event_descriptor.event_status', status)
                setmember(ev, 'event_descriptor.created_date_time', now)

    if len(events) <= 1:
        return events

    def sort_key(event):
        # priority: 0 is lowest → map to huge number, else use priority
        prio = getmember(event, 'event_descriptor.priority', missing=float('inf'))
        if prio == 0:
            prio = float('inf')
        # active events come first (True > False in sort order when reversed? We'll sort by tuple)
        is_active = (getmember(event, 'event_descriptor.event_status') == 'active')
        start = getmember(event, 'active_period.dtstart')
        # active first (True = 1, False = 0) – we want active = 0 for sort order
        return (not is_active, prio, start)

    events.sort(key=sort_key)
    if limit is not None and offset is not None:
        return events[offset:offset+limit]
    return events


def increment_event_modification_number(event):
    """Increment the event's modification number and return the new value."""
    mod_num = getmember(event, 'event_descriptor.modification_number') + 1
    setmember(event, 'event_descriptor.modification_number', mod_num)
    return mod_num