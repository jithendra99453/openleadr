from dataclasses import dataclass, field, asdict, is_dataclass
from typing import List, Dict, Optional, Union
from datetime import datetime, timezone, timedelta
from openleadr import utils
from openleadr import enums


@dataclass
class AggregatedPNode:
    node: str


@dataclass
class EndDeviceAsset:
    mrid: str


@dataclass
class MeterAsset:
    mrid: str


@dataclass
class PNode:
    node: str


@dataclass
class FeatureCollection:
    id: str
    location: dict


@dataclass
class ServiceArea:
    feature_collection: FeatureCollection


@dataclass
class ServiceDeliveryPoint:
    node: str


@dataclass
class ServiceLocation:
    node: str


@dataclass
class TransportInterface:
    point_of_receipt: str
    point_of_delivery: str


@dataclass
class Target:
    aggregated_p_node: Optional[AggregatedPNode] = None
    end_device_asset: Optional[EndDeviceAsset] = None
    meter_asset: Optional[MeterAsset] = None
    p_node: Optional[PNode] = None
    service_area: Optional[ServiceArea] = None
    service_delivery_point: Optional[ServiceDeliveryPoint] = None
    service_location: Optional[ServiceLocation] = None
    transport_interface: Optional[TransportInterface] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    resource_id: Optional[str] = None
    ven_id: Optional[str] = None
    party_id: Optional[str] = None

    def __repr__(self):
        targets = {key: value for key, value in asdict(self).items() if value is not None}
        targets_str = ", ".join(f"{key}={value}" for key, value in targets.items())
        return f"Target({targets_str})"


@dataclass
class EventDescriptor:
    event_id: str
    modification_number: int
    market_context: str
    event_status: str

    created_date_time: Optional[datetime] = None
    modification_date_time: Optional[datetime] = None
    priority: int = 0
    test_event: bool = False
    vtn_comment: Optional[str] = None

    def __post_init__(self):
        if self.modification_date_time is None:
            self.modification_date_time = datetime.now(timezone.utc)
        if self.created_date_time is None:
            self.created_date_time = datetime.now(timezone.utc)
        if self.modification_number is None:
            self.modification_number = 0


@dataclass
class ActivePeriod:
    dtstart: datetime
    duration: timedelta
    tolerance: Optional[dict] = None
    notification_period: Optional[dict] = None
    ramp_up_period: Optional[dict] = None
    recovery_period: Optional[dict] = None


@dataclass
class Interval:
    dtstart: datetime
    duration: timedelta
    signal_payload: float
    uid: Optional[int] = None


@dataclass
class SamplingRate:
    min_period: Optional[timedelta] = None
    max_period: Optional[timedelta] = None
    on_change: bool = False


@dataclass
class PowerAttributes:
    hertz: int = 50
    voltage: int = 230
    ac: bool = True


@dataclass
class Measurement:
    name: str
    description: str
    unit: str
    acceptable_units: List[str] = field(repr=False, default_factory=list)
    scale: Optional[str] = None
    power_attributes: Optional[PowerAttributes] = None
    pulse_factor: Optional[int] = None
    ns: Optional[str] = None  # XML namespace, only set for known measurement names

    def __post_init__(self):
        # Known measurement names get a namespace; unknown become 'customUnit' with no namespace
        if self.name not in enums._MEASUREMENT_NAMESPACES:
            if self.name != 'customUnit':
                # Auto-correct to customUnit (with warning in preflight, but here just set)
                self.name = 'customUnit'
            self.ns = None  # custom measurements have no namespace
        else:
            self.ns = enums._MEASUREMENT_NAMESPACES[self.name]


@dataclass
class EventSignal:
    intervals: List[Interval]
    signal_name: str
    signal_type: str
    signal_id: str
    current_value: Optional[float] = None
    targets: Optional[List[Target]] = None
    targets_by_type: Optional[Dict] = None
    measurement: Optional[Measurement] = None

    def __post_init__(self):
        # Validate signal_type
        if self.signal_type not in enums.SIGNAL_TYPE.values:
            raise ValueError(
                f"The signal_type must be one of '{', '.join(enums.SIGNAL_TYPE.values)}', "
                f"you specified: '{self.signal_type}'."
            )
        # Validate signal_name
        if self.signal_name not in enums.SIGNAL_NAME.values and not self.signal_name.startswith('x-'):
            raise ValueError(
                f"The signal_name must be one of '{', '.join(enums.SIGNAL_NAME.values)}', "
                f"or it must begin with 'x-'. You specified: '{self.signal_name}'."
            )

        # Handle targets
        if self.targets is None and self.targets_by_type is None:
            return  # No targets – allowed for some signals (e.g., simple)
        elif self.targets_by_type is None:
            # Build targets_by_type from targets
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            self.targets_by_type = utils.group_targets_by_type(list_of_targets)
            # OpenADR 2.0b restricts EventSignal targets to a single type (typically endDeviceAsset)
            if len(self.targets_by_type) > 1:
                raise ValueError(
                    "In OpenADR, the EventSignal target may only be of one type (e.g., endDeviceAsset). "
                    f"You provided types: '{', '.join(self.targets_by_type.keys())}'."
                )
        elif self.targets is None:
            # Build targets from targets_by_type
            self.targets = [
                Target(**t) for t in utils.ungroup_targets_by_type(self.targets_by_type)
            ]
        else:
            # Both provided – check consistency
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            if utils.group_targets_by_type(list_of_targets) != self.targets_by_type:
                raise ValueError(
                    "You assigned both 'targets' and 'targets_by_type' in your event signal, "
                    "but the two were not consistent with each other. "
                    f"You supplied 'targets' = {self.targets} and "
                    f"'targets_by_type' = {self.targets_by_type}."
                )


@dataclass
class Event:
    event_descriptor: EventDescriptor
    event_signals: List[EventSignal]
    targets: Optional[List[Target]] = None
    targets_by_type: Optional[Dict] = None
    active_period: Optional[ActivePeriod] = None
    response_required: str = 'always'

    def __post_init__(self):
        # Compute active_period from intervals if not provided
        if self.active_period is None:
            # Gather all intervals from all signals
            all_intervals = []
            for signal in self.event_signals:
                all_intervals.extend(signal.intervals)
            # Determine overall start and end
            dtstart = min(
                i.dtstart if isinstance(i, Interval) else i['dtstart']
                for i in all_intervals
            )
            end = max(
                (i.dtstart + i.duration) if isinstance(i, Interval) else (i['dtstart'] + i['duration'])
                for i in all_intervals
            )
            duration = end - dtstart
            self.active_period = ActivePeriod(dtstart=dtstart, duration=duration)

        # Handle targets (similar to EventSignal but without the single-type restriction)
        if self.targets is None and self.targets_by_type is None:
            raise ValueError("You must supply either 'targets' or 'targets_by_type' for an Event.")
        elif self.targets_by_type is None:
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            self.targets_by_type = utils.group_targets_by_type(list_of_targets)
        elif self.targets is None:
            self.targets = [
                Target(**t) for t in utils.ungroup_targets_by_type(self.targets_by_type)
            ]
        else:
            # Both provided – check consistency
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            if utils.group_targets_by_type(list_of_targets) != self.targets_by_type:
                raise ValueError(
                    "You assigned both 'targets' and 'targets_by_type' in your event, "
                    "but the two were not consistent with each other. "
                    f"You supplied 'targets' = {self.targets} and "
                    f"'targets_by_type' = {self.targets_by_type}."
                )

        # Set the event status based on the active period
        self.event_descriptor.event_status = utils.determine_event_status(self.active_period)


@dataclass
class Response:
    response_code: int
    response_description: str
    request_id: str


@dataclass
class ReportDescription:
    r_id: str
    market_context: str
    reading_type: str
    report_subject: Target
    report_data_source: Target
    report_type: str
    sampling_rate: SamplingRate
    measurement: Optional[Measurement] = None


@dataclass
class ReportPayload:
    r_id: str
    value: float
    confidence: Optional[int] = None
    accuracy: Optional[int] = None


@dataclass
class ReportInterval:
    dtstart: datetime
    report_payload: ReportPayload
    duration: Optional[timedelta] = None


@dataclass
class Report:
    report_specifier_id: str
    report_name: str
    report_request_id: Optional[str] = None
    report_descriptions: Optional[List[ReportDescription]] = None
    created_date_time: Optional[datetime] = None
    dtstart: Optional[datetime] = None
    duration: Optional[timedelta] = None
    intervals: Optional[List[ReportInterval]] = None
    data_collection_mode: str = 'incremental'

    def __post_init__(self):
        if self.created_date_time is None:
            self.created_date_time = datetime.now(timezone.utc)
        if self.report_descriptions is None:
            self.report_descriptions = []


@dataclass
class SpecifierPayload:
    r_id: str
    reading_type: str
    measurement: Optional[Measurement] = None


@dataclass
class ReportSpecifier:
    report_specifier_id: str
    granularity: timedelta
    specifier_payloads: List[SpecifierPayload]
    report_interval: Optional[Interval] = None
    report_back_duration: Optional[timedelta] = None


@dataclass
class ReportRequest:
    report_request_id: str
    report_specifier: ReportSpecifier


@dataclass
class VavailabilityComponent:
    dtstart: datetime
    duration: timedelta


@dataclass
class Vavailability:
    components: List[VavailabilityComponent]


@dataclass
class Opt:
    opt_type: str
    opt_reason: str
    opt_id: Optional[str] = None
    created_date_time: Optional[datetime] = None
    event_id: Optional[str] = None
    modification_number: Optional[int] = None
    vavailability: Optional[Vavailability] = None
    targets: Optional[List[Target]] = None
    targets_by_type: Optional[Dict] = None
    market_context: Optional[str] = None
    signal_target_mrid: Optional[str] = None

    def __post_init__(self):
        # Validate opt_type
        if self.opt_type not in enums.OPT.values:
            raise ValueError(
                f"The opt_type must be one of '{', '.join(enums.OPT.values)}', "
                f"you specified: '{self.opt_type}'."
            )
        # Validate opt_reason (fixed error message)
        if self.opt_reason not in enums.OPT_REASON.values:
            raise ValueError(
                f"The opt_reason must be one of '{', '.join(enums.OPT_REASON.values)}', "
                f"you specified: '{self.opt_reason}'."  # was incorrectly using self.opt_type
            )
        # Validate signal_target_mrid if present
        if self.signal_target_mrid is not None:
            allowed = enums.SIGNAL_TARGET_MRID.values
            if self.signal_target_mrid not in allowed and not self.signal_target_mrid.startswith('x-'):
                raise ValueError(
                    f"The signal_target_mrid must be one of '{', '.join(allowed)}', "
                    f"or begin with 'x-'. You specified: '{self.signal_target_mrid}'."
                )
        # Exactly one of event_id or vavailability must be provided
        if self.event_id is None and self.vavailability is None:
            raise ValueError("You must supply either 'event_id' or 'vavailability'.")
        if self.event_id is not None and self.vavailability is not None:
            raise ValueError("You supplied both 'event_id' and 'vavailability'. Please supply either, but not both.")

        # Default timestamps and modification number
        if self.created_date_time is None:
            self.created_date_time = datetime.now(timezone.utc)
        if self.modification_number is None:
            self.modification_number = 0

        # Handle targets (similar to Event)
        if self.targets is None and self.targets_by_type is None:
            raise ValueError("You must supply either 'targets' or 'targets_by_type' for an Opt.")
        elif self.targets_by_type is None:
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            self.targets_by_type = utils.group_targets_by_type(list_of_targets)
        elif self.targets is None:
            self.targets = [
                Target(**t) for t in utils.ungroup_targets_by_type(self.targets_by_type)
            ]
        else:
            # Both provided – check consistency
            list_of_targets = [
                asdict(t) if is_dataclass(t) else t for t in self.targets
            ]
            if utils.group_targets_by_type(list_of_targets) != self.targets_by_type:
                raise ValueError(
                    "You assigned both 'targets' and 'targets_by_type' in your opt, "
                    "but the two were not consistent with each other. "
                    f"You supplied 'targets' = {self.targets} and "
                    f"'targets_by_type' = {self.targets_by_type}."
                )