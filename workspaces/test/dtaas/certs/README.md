# TLS Certificates Directory

This directory should contain your TLS certificates for HTTPS support.

## Required Files

Place your certificate files in this directory with the following structure:

```text
test/dtaas/certs/
├── fullchain.pem  # Full certificate chain
└── privkey.pem    # Private key
```

## Certificate Generation

### For Production

Obtain certificates from a Certificate Authority (CA) like:

- [Let's Encrypt](https://letsencrypt.org/) (Free, automated)
- [ZeroSSL](https://zerossl.com/) (Free)
- Commercial CA (Paid)

Using Let's Encrypt with Certbot:

```bash
sudo apt-get update
sudo apt-get install certbot

sudo certbot certonly --standalone -d <DOMAIN_NAME>

sudo cp /etc/letsencrypt/live/<DOMAIN_NAME>/fullchain.pem ./certs/
sudo cp /etc/letsencrypt/live/<DOMAIN_NAME>/privkey.pem ./certs/
sudo chown $USER:$USER ./certs/*.pem
chmod 644 ./certs/fullchain.pem
chmod 600 ./certs/privkey.pem
```

### For Testing/Development

Generate self-signed certificates using
[mkcert](https://github.com/FiloSottile/mkcert). The mkcert root CA must
be trusted by your browser so that `https://<DOMAIN_NAME>` works without
certificate warnings.

Ensure your `/etc/hosts` file maps your domain to `127.0.0.1`:

```bash
echo "127.0.0.1  <DOMAIN_NAME>" | sudo tee -a /etc/hosts
```

From within the `test/dtaas/certs/` folder, replacing `<DOMAIN_NAME>` with
your domain:

```bash
wget https://github.com/FiloSottile/mkcert/releases/download/v1.4.4/mkcert-v1.4.4-linux-amd64
chmod 774 mkcert-v1.4.4-linux-amd64
sudo mv mkcert-v1.4.4-linux-amd64 /usr/local/bin/mkcert
mkcert -install
mkcert -cert-file fullchain.pem -key-file privkey.pem \
  "<DOMAIN_NAME>" "*.<DOMAIN_NAME>" "localhost" "127.0.0.1" "::1"
cp ~/.local/share/mkcert/rootCA.pem rootCA.crt
```

No further steps are needed. Traefik picks up the certificates via the
`dynamic/tls.yml` volume mount. The login-relay and Oathkeeper communicate
with Keycloak over the internal Docker network (plain HTTP), so they do not
need to trust the self-signed cert.

## Security Notes

⚠️ **Important Security Practices:**

- Never commit actual certificate files to version control
- Keep private keys secure and restrict file permissions
- Use strong encryption (RSA 2048+ bits or ECC)
- Rotate certificates before expiration
- For production, always use certificates from trusted CAs

## File Permissions

From within `test/dtaas/certs/`, set appropriate permissions for certificate files:

```bash
chmod 644 fullchain.pem
chmod 600 privkey.pem
```
