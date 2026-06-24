
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, dsa, ec, ed25519, ed448
from jinja2 import Environment, PackageLoader, FileSystemLoader, ChoiceLoader
from lxml import etree
from lxml.etree import Element
from signxml import XMLSigner, XMLVerifier, methods
from signxml.algorithms import SignatureMethod
import xmltodict

from openleadr import errors, utils
from openleadr.preflight import preflight_message

logger = logging.getLogger('openleadr')

# -----------------------------------------------------------------------------
# Constants & global configuration
# -----------------------------------------------------------------------------

# Replay protection settings
REPLAY_PROTECT_MAX_TIME_DELTA = timedelta(seconds=5)
NONCE_CACHE = set()

# -----------------------------------------------------------------------------
# XML schema validation – configurable path
# -----------------------------------------------------------------------------
_schema_env = os.environ.get('OPENADR_SCHEMA_PATH')
if _schema_env:
    XML_SCHEMA_LOCATION = os.path.join(_schema_env, 'oadr_20b.xsd')
else:
    XML_SCHEMA_LOCATION = os.path.join(os.path.dirname(__file__), 'schema', 'oadr_20b.xsd')

try:
    with open(XML_SCHEMA_LOCATION) as f:
        XML_SCHEMA = etree.XMLSchema(etree.parse(f))
    XML_PARSER = etree.XMLParser(schema=XML_SCHEMA)
except Exception as e:
    logger.error(f"Failed to load OpenADR XML schema from {XML_SCHEMA_LOCATION}: {e}")
    XML_PARSER = etree.XMLParser()
    logger.warning("XML schema validation is DISABLED due to missing schema files.")

# -----------------------------------------------------------------------------
# XML template loading – PRIORITIZE LOCAL templates/ folder
# -----------------------------------------------------------------------------
# First, check for a local 'templates' folder next to this file
local_template_path = os.path.join(os.path.dirname(__file__), 'templates')
if os.path.exists(local_template_path):
    # Use local templates folder (FileSystemLoader)
    template_loader = FileSystemLoader(local_template_path)
    logger.info(f"Loading templates from local folder: {local_template_path}")
else:
    # Fall back to package loader
    template_loader = PackageLoader('openleadr', 'templates')
    logger.info("Loading templates from package: openleadr.templates")

TEMPLATES = Environment(loader=template_loader)
TEMPLATES.filters['datetimeformat'] = utils.datetimeformat
TEMPLATES.filters['timedeltaformat'] = utils.timedeltaformat
TEMPLATES.filters['booleanformat'] = utils.booleanformat
TEMPLATES.trim_blocks = True
TEMPLATES.lstrip_blocks = True

# -----------------------------------------------------------------------------
# XML signature verifier (shared instance)
# -----------------------------------------------------------------------------
VERIFIER = XMLVerifier()

# -----------------------------------------------------------------------------
# Namespace mapping for xmltodict
# -----------------------------------------------------------------------------
NAMESPACES = {
    'http://docs.oasis-open.org/ns/energyinterop/201110': None,
    'http://openadr.org/oadr-2.0b/2012/07': None,
    'urn:ietf:params:xml:ns:icalendar-2.0': None,
    'http://docs.oasis-open.org/ns/energyinterop/201110/payloads': None,
    'http://docs.oasis-open.org/ns/emix/2011/06': None,
    'urn:ietf:params:xml:ns:icalendar-2.0:stream': None,
    'http://docs.oasis-open.org/ns/emix/2011/06/power': None,
    'http://docs.oasis-open.org/ns/emix/2011/06/siscale': None,
    'http://www.w3.org/2000/09/xmldsig#': None,
    'http://openadr.org/oadr-2.0b/2012/07/xmldsig-properties': None
}


# -----------------------------------------------------------------------------
# Helper functions for key handling
# -----------------------------------------------------------------------------

def load_private_key(key_data, passphrase=None):
    """
    Load a private key from PEM or DER data.
    Returns a private key object.
    Raises ValueError if the key cannot be loaded.
    """
    passphrase_bytes = passphrase.encode() if passphrase else None
    try:
        return serialization.load_pem_private_key(key_data, passphrase_bytes)
    except ValueError:
        pass
    try:
        return serialization.load_der_private_key(key_data, passphrase_bytes)
    except ValueError:
        raise ValueError("Could not load private key: unsupported format or incorrect passphrase.")


def get_signature_algorithm_from_private_key(key_data, passphrase=None, default_algorithm="rsa-sha256"):
    """
    Derive a signature algorithm (fragment) from the private key type.
    """
    key = load_private_key(key_data, passphrase)
    if isinstance(key, rsa.RSAPrivateKey):
        return "rsa-sha256"
    elif isinstance(key, dsa.DSAPrivateKey):
        return "dsa-sha256"
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        return "ecdsa-sha256"
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        logger.warning("Ed25519 keys are not supported by XMLDSig. Falling back to default.")
    elif isinstance(key, ed448.Ed448PrivateKey):
        logger.warning("Ed448 keys are not supported by XMLDSig. Falling back to default.")
    else:
        logger.warning(f"Unknown private key type: {type(key)}. Using default algorithm.")
    return default_algorithm


# -----------------------------------------------------------------------------
# Replay protection helpers
# -----------------------------------------------------------------------------

def _create_replay_protect():
    """
    Create a ReplayProtect element for inclusion in the XML signature.
    """
    dsp_ns = "http://openadr.org/oadr-2.0b/2012/07/xmldsig-properties"
    el = Element(
        f"{{{dsp_ns}}}ReplayProtect",
        nsmap={'dsp': dsp_ns},
        attrib={'Id': 'replayProtectId'}
    )
    timestamp = Element(f"{{{dsp_ns}}}timestamp")
    timestamp.text = utils.datetimeformat(datetime.now(timezone.utc))
    nonce = Element(f"{{{dsp_ns}}}nonce")
    nonce.text = uuid4().hex
    el.append(timestamp)
    el.append(nonce)
    return el


def _update_nonce_cache(timestamp, nonce):
    """Add a (timestamp, nonce) pair to the cache and expire old entries."""
    NONCE_CACHE.add((timestamp, nonce))
    now = datetime.now(timezone.utc)
    for ts, n in list(NONCE_CACHE):
        if ts < now - REPLAY_PROTECT_MAX_TIME_DELTA:
            NONCE_CACHE.remove((ts, n))


def _verify_replay_protect(xml_tree):
    """
    Verify the ReplayProtect element inside the signature.
    """
    ns = "http://openadr.org/oadr-2.0b/2012/07/xmldsig-properties"
    try:
        timestamp_str = xml_tree.findtext(f".//{{{ns}}}timestamp")
        nonce = xml_tree.findtext(f".//{{{ns}}}nonce")
        if timestamp_str is None or nonce is None:
            raise ValueError
        timestamp = utils.parse_datetime(timestamp_str)
    except Exception:
        raise ValueError("Missing or malformed ReplayProtect element in message signature.")

    now = datetime.now(timezone.utc)
    if timestamp < now - REPLAY_PROTECT_MAX_TIME_DELTA:
        raise ValueError("Message timestamp is too old (outside allowed window).")
    if timestamp > now + REPLAY_PROTECT_MAX_TIME_DELTA:
        raise ValueError("Message timestamp is too far in the future.")

    if (timestamp, nonce) in NONCE_CACHE:
        raise ValueError("Duplicate (timestamp, nonce) detected – possible replay attack.")
    _update_nonce_cache(timestamp, nonce)


# -----------------------------------------------------------------------------
# Message parsing and creation
# -----------------------------------------------------------------------------

def parse_message(data):
    """
    Parse an incoming OpenADR XML message.
    Returns a tuple (message_type, message_payload).
    """
    try:
        if isinstance(data, bytes):
            logger.debug(f"Parsing message: {data.decode('utf-8')}")
        else:
            logger.debug(f"Parsing message: {data}")
    except UnicodeDecodeError:
        logger.warning(f"Could not decode incoming message as UTF-8: {data!r}")

    message_dict = xmltodict.parse(data, process_namespaces=True, namespaces=NAMESPACES)

    try:
        signed_object = message_dict['oadrPayload']['oadrSignedObject']
    except KeyError:
        signed_object = message_dict['oadrPayload']

    message_type, message_payload = signed_object.popitem()
    message_payload = utils.normalize_dict(message_payload)
    return message_type, message_payload


def create_message(message_type, cert=None, key=None, passphrase=None,
                   disable_signature=False, **message_payload):
    """
    Create an OpenADR message (optionally signed) as an XML string.
    """
    message_payload = preflight_message(message_type, message_payload)

    template = TEMPLATES.get_template(f'{message_type}.xml')
    signed_object = utils.flatten_xml(template.render(**message_payload))

    if cert and key and not disable_signature:
        tree = etree.fromstring(signed_object)
        signer = XMLSigner(
            method=methods.detached,
            c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
        )
        signer.namespaces['oadr'] = "http://openadr.org/oadr-2.0b/2012/07"
        signer.sign_alg = SignatureMethod.from_fragment(
            get_signature_algorithm_from_private_key(key, passphrase)
        )
        signature_tree = signer.sign(
            tree,
            key=key,
            cert=cert,
            passphrase=utils.ensure_bytes(passphrase),
            reference_uri="#oadrSignedObject",
            signature_properties=_create_replay_protect()
        )
        signature_xml = etree.tostring(signature_tree).decode('utf-8')
    else:
        signature_xml = None

    envelope = TEMPLATES.get_template('oadrPayload.xml')
    msg = envelope.render(
        template=message_type,
        signature=signature_xml,
        signed_object=signed_object
    )
    logger.debug(f"Created message: {msg}")
    return msg


# -----------------------------------------------------------------------------
# XML signature & schema validation
# -----------------------------------------------------------------------------

def validate_xml_schema(content):
    """
    Validate the XML against the OpenADR 2.0b schema.
    Returns the parsed XML tree.
    """
    if isinstance(content, str):
        content = content.encode('utf-8')
    return etree.fromstring(content, XML_PARSER)


def validate_xml_signature(xml_tree, cert_fingerprint=None):
    """
    Validate the XMLDSIG signature and the ReplayProtect element.
    """
    cert_pem = utils.extract_pem_cert(xml_tree)
    if cert_fingerprint:
        fingerprint = utils.certificate_fingerprint(cert_pem)
        if fingerprint != cert_fingerprint:
            raise errors.FingerprintMismatch(
                f"Certificate fingerprint mismatch. Expected: {cert_fingerprint}, got: {fingerprint}"
            )
    VERIFIER.verify(
        xml_tree,
        x509_cert=utils.ensure_bytes(cert_pem),
        expect_references=2
    )
    _verify_replay_protect(xml_tree)


def validate_xml_signature_none(xml_tree):
    """Assert that the message has no signature."""
    assert xml_tree.find('.//{http://www.w3.org/2000/09/xmldsig#}X509Certificate') is None


# -----------------------------------------------------------------------------
# Request authentication (VTN side)
# -----------------------------------------------------------------------------

async def authenticate_message(request, message_tree, message_payload,
                               fingerprint_lookup=None, ven_lookup=None,
                               verify_message_signature=True):
    """
    Authenticate an incoming request using TLS client certificate and XML signature.
    """
    if request.secure and 'ven_id' in message_payload:
        connection_fingerprint = utils.get_cert_fingerprint_from_request(request)
        if connection_fingerprint is None:
            raise errors.NotRegisteredOrAuthorizedError(
                "Your request must use a client‑side SSL certificate. No fingerprint found."
            )

        ven_id = message_payload['ven_id']
        expected_fingerprint = None

        try:
            if fingerprint_lookup:
                expected_fingerprint = await utils.await_if_required(fingerprint_lookup(ven_id))
            elif ven_lookup:
                ven_info = await utils.await_if_required(ven_lookup(ven_id))
                expected_fingerprint = ven_info.get('fingerprint') if ven_info else None
            else:
                raise errors.NotRegisteredOrAuthorizedError(
                    "No fingerprint or VEN lookup function provided."
                )
        except Exception:
            raise errors.NotRegisteredOrAuthorizedError(
                f"VEN ID {ven_id} is not known to this VTN."
            )

        if not expected_fingerprint:
            raise errors.NotRegisteredOrAuthorizedError(
                "No certificate fingerprint registered for this VEN."
            )

        if connection_fingerprint != expected_fingerprint:
            raise errors.NotRegisteredOrAuthorizedError(
                f"TLS certificate fingerprint {connection_fingerprint} does not match "
                f"the registered fingerprint {expected_fingerprint}."
            )

        if verify_message_signature:
            message_cert = utils.extract_pem_cert(message_tree)
            message_fingerprint = utils.certificate_fingerprint(message_cert)
            if message_fingerprint != expected_fingerprint:
                raise errors.NotRegisteredOrAuthorizedError(
                    f"XML signature certificate fingerprint {message_fingerprint} "
                    f"does not match the registered fingerprint {expected_fingerprint}."
                )
            try:
                validate_xml_signature(message_tree)
            except Exception as e:
                raise errors.NotRegisteredOrAuthorizedError(
                    f"Invalid XML signature: {str(e)}"
                )