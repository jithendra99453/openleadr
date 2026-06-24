#!/bin/bash

set -e

# -------------------------------------------------------
# YOUR CLOUD SHELL INTERNAL IP
# -------------------------------------------------------

# Use the Cloud Shell internal IP
VTN_HOST="10.88.0.4:8443"

# Also create a SAN certificate to avoid hostname issues
# Create OpenSSL config file for SAN support
cat > openssl-san.cnf << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=IN
ST=AndhraPradesh
O=OpenADR-VTN
CN=${VTN_HOST}

[v3_req]
subjectAltName = @alt_names

[alt_names]
IP.1 = 10.88.0.4
DNS.1 = localhost
DNS.2 = cloudshell
EOF

# -------------------------------------------------------
# GENERATE CA KEY
# -------------------------------------------------------

echo "Generating CA private key..."

openssl genrsa -out dummy_ca.key 4096

# -------------------------------------------------------
# GENERATE CA CERTIFICATE
# -------------------------------------------------------

echo "Generating CA certificate..."

openssl req -x509 -new \
-nodes \
-key dummy_ca.key \
-sha256 \
-days 3650 \
-out dummy_ca.crt \
-subj "/C=IN/ST=AndhraPradesh/O=OpenADR-CA/CN=OpenADR-Root-CA"

# -------------------------------------------------------
# GENERATE VTN KEY
# -------------------------------------------------------

echo "Generating VTN private key..."

openssl genrsa -out dummy_vtn.key 2048

# -------------------------------------------------------
# GENERATE VTN CSR WITH SAN
# -------------------------------------------------------

echo "Generating VTN CSR with SAN..."

openssl req -new \
-key dummy_vtn.key \
-out dummy_vtn.csr \
-config openssl-san.cnf

# -------------------------------------------------------
# SIGN VTN CERTIFICATE
# -------------------------------------------------------

echo "Signing VTN certificate..."

openssl x509 -req \
-in dummy_vtn.csr \
-CA dummy_ca.crt \
-CAkey dummy_ca.key \
-CAcreateserial \
-out dummy_vtn.crt \
-days 3650 \
-sha256 \
-extfile openssl-san.cnf \
-extensions v3_req

# -------------------------------------------------------
# GENERATE VEN KEY
# -------------------------------------------------------

echo "Generating VEN private key..."

openssl genrsa -out dummy_ven.key 2048

# -------------------------------------------------------
# GENERATE VEN CSR
# -------------------------------------------------------

echo "Generating VEN CSR..."

openssl req -new \
-key dummy_ven.key \
-out dummy_ven.csr \
-subj "/C=IN/ST=AndhraPradesh/O=OpenADR-VEN/CN=TestVEN"

# -------------------------------------------------------
# SIGN VEN CERTIFICATE
# -------------------------------------------------------

echo "Signing VEN certificate..."

openssl x509 -req \
-in dummy_ven.csr \
-CA dummy_ca.crt \
-CAkey dummy_ca.key \
-CAcreateserial \
-out dummy_ven.crt \
-days 3650 \
-sha256

# -------------------------------------------------------
# CLEAN UP
# -------------------------------------------------------

rm -f dummy_vtn.csr dummy_ven.csr openssl-san.cnf

# -------------------------------------------------------
# VERIFY CERTIFICATES
# -------------------------------------------------------

echo ""
echo "======================================"
echo "Verifying certificates..."
echo "======================================"

echo ""
echo "VTN Certificate Info:"
openssl x509 -in dummy_vtn.crt -text -noout | grep -A2 "Subject:\|DNS:\|IP Address:"

echo ""
echo "VEN Certificate Info:"
openssl x509 -in dummy_ven.crt -text -noout | grep "Subject:"

echo ""
echo "======================================"
echo "Certificates Generated Successfully"
echo "======================================"
echo ""
echo "VTN Fingerprint:"
openssl x509 -in dummy_vtn.crt -noout -fingerprint | cut -d= -f2
echo ""
echo "VEN Fingerprint:"
openssl x509 -in dummy_ven.crt -noout -fingerprint | cut -d= -f2
echo ""
echo "======================================"