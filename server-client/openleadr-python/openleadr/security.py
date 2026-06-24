#!/usr/bin/env python3
import os
import ssl
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Certificate Directories & Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(os.path.dirname(BASE_DIR), "certificates")

CA_CERT = os.path.join(CERT_DIR, "dummy_ca.crt")
VTN_CERT = os.path.join(CERT_DIR, "dummy_vtn.crt")
VTN_KEY = os.path.join(CERT_DIR, "dummy_vtn.key")
VEN_CERT = os.path.join(CERT_DIR, "dummy_ven.crt")
VEN_KEY = os.path.join(CERT_DIR, "dummy_ven.key")

# Global Config
MTLS_ENABLED = True
REQUIRE_SIGNATURES = False
VERIFY_VTN_CERT = os.environ.get("OPENLEADR_VERIFY_VTN_CERT", "").lower() in {"1", "true", "yes"}

def get_server_ssl_context() -> Optional[ssl.SSLContext]:
    """Create a VTN server-side SSLContext for mTLS."""
    if not MTLS_ENABLED:
        return None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_verify_locations(cafile=CA_CERT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(certfile=VTN_CERT, keyfile=VTN_KEY)
        return context
    except Exception as e:
        logger.error(f"Failed to create Server SSL Context: {e}")
        return None

def get_client_ssl_context() -> Optional[ssl.SSLContext]:
    """Create a VEN client-side SSLContext for connecting to the VTN."""
    if not MTLS_ENABLED:
        return None
    try:
        if VERIFY_VTN_CERT:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.load_verify_locations(cafile=CA_CERT)
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        context.load_cert_chain(certfile=VEN_CERT, keyfile=VEN_KEY)
        # The dummy certificates are often used across localhost/IP test hosts.
        context.check_hostname = False
        return context
    except Exception as e:
        logger.error(f"Failed to create Client SSL Context: {e}")
        return None

def get_vtn_client_ssl_context() -> Optional[ssl.SSLContext]:
    """Create a VTN client-side SSLContext for connecting to a VEN push endpoint."""
    if not MTLS_ENABLED:
        return None
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.load_verify_locations(cafile=CA_CERT)
        context.load_cert_chain(certfile=VTN_CERT, keyfile=VTN_KEY)
        context.check_hostname = False
        return context
    except Exception as e:
        logger.error(f"Failed to create VTN Client SSL Context: {e}")
        return None

def get_client_server_ssl_context() -> Optional[ssl.SSLContext]:
    """Create a VEN server-side SSLContext for the local push listener."""
    if not MTLS_ENABLED:
        return None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=VEN_CERT, keyfile=VEN_KEY)
        return context
    except Exception as e:
        logger.error(f"Failed to create Client Server SSL Context: {e}")
        return None

def sign_xml_string(xml_str: str, is_server: bool = True) -> str:
    """Sign an XML string using the appropriate certificate and key"""
    try:
        from lxml import etree
        from signxml import XMLSigner

        # Strip XML declaration if present, as signxml / lxml handles it
        xml_clean = xml_str.strip()
        if xml_clean.startswith("<?xml"):
            # Find the end of the xml tag
            end_idx = xml_clean.find("?>")
            if end_idx != -1:
                xml_clean = xml_clean[end_idx+2:].strip()
                
        root = etree.fromstring(xml_clean.encode('utf-8'))
        
        key_path = VTN_KEY if is_server else VEN_KEY
        cert_path = VTN_CERT if is_server else VEN_CERT
        
        with open(key_path, 'r') as kf, open(cert_path, 'r') as cf:
            key = kf.read()
            cert = cf.read()
            
        signed_root = XMLSigner().sign(root, key=key, cert=cert)
        signed_xml = etree.tostring(signed_root, encoding='utf-8', xml_declaration=True).decode('utf-8')
        return signed_xml
    except Exception as e:
        logger.error(f"Error signing XML: {e}")
        return xml_str

def verify_xml_string(xml_str: str) -> bool:
    """Verify the digital signature of an XML string against the CA certificate"""
    if not REQUIRE_SIGNATURES:
        return True
    try:
        from lxml import etree
        from signxml import XMLVerifier

        xml_clean = xml_str.strip()
        if xml_clean.startswith("<?xml"):
            end_idx = xml_clean.find("?>")
            if end_idx != -1:
                xml_clean = xml_clean[end_idx+2:].strip()
                
        root = etree.fromstring(xml_clean.encode('utf-8'))
        
        # Check if ds:Signature element exists.
        ns = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
        sig = root.find('.//ds:Signature', namespaces=ns)
        if sig is None:
            logger.warning("XML signature element missing from payload")
            return False
            
        XMLVerifier().verify(root, ca_pem_file=CA_CERT)
        return True
    except Exception as e:
        logger.error(f"XML signature verification failed: {e}")
        return False
