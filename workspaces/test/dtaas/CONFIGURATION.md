# ⚙️ DTaaS Configuration

This document outlines the configuration needed for the docker compose files.
Not all parts of the configuration are required by the compose files.
Here is a mapping of the sections needed for the configuration files. All sections
assume that you are in the `workspaces/test/dtaas/` directory.

- `compose.yml`:
  - [Environment](#-environment)
  - [Usernames](#-usernames)
  - [User Directories](#-user-directories)
- `compose.traefik.yml`:
  - [Environment](#-environment)
  - [Usernames](#-usernames)
  - [User Directories](#-user-directories)
- `compose.traefik.secure.yml`:
  - [Environment](#-environment)
  - [Usernames](#-usernames)
  - [User Directories](#-user-directories)
  - [Domain](#-domain)
  - [Web Client](#️-dtaas-web-client-config)
  - [HTTP](#http-protocol)
  - [OAuth2](#-oauth2-configuration)
  - [Forward Auth](#-traefik-forward-auth-configuration)
- `compose.traefik.secure.tls.yml`:
  - [Environment](#-environment)
  - [Usernames](#-usernames)
  - [User Directories](#-user-directories)
  - [Domain](#-domain)
  - [Certificate](#-certificate)
  - [Web Client](#️-dtaas-web-client-config)
  - [OAuth2](#-oauth2-configuration)

## 🌍 Environment

The compose commands used in the setup guides sets the environment
with an environment file. An example of this file can be found at
[`config/.env.example`](./config/.env.example).

Create a copy of this example file without the example suffix:

```bash
cp config/.env.example config/.env
```

## 👥 Usernames

The usernames of the main users for the workspaces can be changed in
the [environment variable file](#-environment) `config/.env`.
Change the default values (`user1` and `user2`) to your desired usernames:

```bash
# Username Configuration
# These usernames will be used as path prefixes for user workspaces
# Example: http://localhost/user1, http://localhost/user2
USERNAME1=user1
USERNAME2=user2
```

**NOTE:** For `compose.traefik.secure.yml`, these usernames must match the
email prefixes configured in the Traefik Forward Auth `config/conf` whitelist.

## 📁 User Directories

The compose files need user directories in `files`.
Copy existing `user1` directory and paste as two new directories
with usernames selected for your case. These usernames are mentioned as
`USERNAME1` and `USERNAME2` in the docker compose files.

```bash
# create required files
cp -R files/user1 files/<USERNAME1>
cp -R files/user1 files/<USERNAME2>
# set file permissions for use inside the container
sudo chown -R 1000:100 files
```

## 🌐 Domain

Decide on whether you are testing locally or remotely.

### 🏠 Local testing

From now on whenever you see `<DOMAIN_NAME>` in this guide, replace it with `localhost`.

### ☁️ Remote testing

From now on whenever you see `<DOMAIN_NAME>` in this guide, replace it with
your remote machines domain name. (Ensure that you remote machine has
a domain name, and that it is accesible from the internet.)

Go to the [Environment file](./config/.env) and replace the current value of
the `SERVER_DNS` variable with your domain name:

```bash
# Server Configuration
# Replace with your domain name
SERVER_DNS=<DOMAIN_NAME>
```

## 📜 Certificate

Dependent on whether you are testing locally or remotely, the procedure for
setting up TLS certificates differs.

### 🏠 Local testing

Generate self-signed certificates using
[mkcert](https://github.com/FiloSottile/mkcert). The mkcert root CA must
be trusted by your browser so that `https://<DOMAIN_NAME>` works without
certificate warnings.

Ensure your `/etc/hosts` file maps your domain to `127.0.0.1` (this should
already be the case when `<DOMAIN_NAME>` is `localhost`):

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

### ☁️ Remote testing

Make sure that you have valid TLS certifcates on the machine and
that they are properly located. The `fullchain.pem` and `privkey.pem`
secrets should be located in the [`certs/`](./certs/) directory.

There are multiple ways to setup of TLS certificates. If you are hosting on
a webserver, then you can use Certbot from Let's Encrypt:

```bash
# Install certbot
sudo apt-get update
sudo apt-get install certbot

# Generate certificates
sudo certbot certonly --standalone -d <DOMAIN_NAME>

# Copy certificates to the project
sudo cp /etc/letsencrypt/live/<DOMAIN_NAME>/fullchain.pem ./certs/
sudo cp /etc/letsencrypt/live/<DOMAIN_NAME>/privkey.pem ./certs/
sudo chown $USER:$USER ./certs/*.pem
chmod 644 ./certs/fullchain.pem
chmod 600 ./certs/privkey.pem
```

## 🖥️ DTaaS Web Client Config

The DTaaS Web Client can be configured with a small javascript file,
an example of which can be found at
[`config/client.js.example`](./config/client.js.example).

Create a copy of this example file without the example suffix:

```bash
cp config/client.js.example config/client.js
```

Then replace all occurrences of `<your-domain>` with your domain name.

### HTTP protocol

If not using TLS, change all instances of `https` to `http` in the DTaaS Web
Client config, [`config/client.js`](./config/client.js).

## 🔑 OAuth2 Configuration

For OAuth2 authentication, the [environment variable file](#-environment)
`config/.env` needs updating.

1. Set the default admin credentials for Keycloak. You can leave the admin
   username as the default (`admin`), but you should change the password.
   If testing remote make sure to set a strong password:
   ```bash
   # Keycloak Admin Credentials
   KEYCLOAK_ADMIN=admin
   KEYCLOAK_ADMIN_PASSWORD=changeme
   ```

2. Generate a base 64, 32 byte random string:

   ```bash
   openssl rand -base64 32
   ```

   and update the environment file, [`config/.env`](./config/.env), with it:

   ```bash
   ...
   # Secret key for encrypting OAuth session data
   # Generate a random string (at least 16 characters)
   # Example: openssl rand -base64 32
   OAUTH_SECRET=<RANDOM_SECRET>
   ...
   ```

3. Add the usernames to the list of users with workspaces, substituting the
   defaults with your specific names, if needed:
   ```bash
   WORKSPACE_USERS=user1,user2
   ```

The environment variables `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID` and
`KEYCLOAK_CLIENT_SECRET` must be updated after starting the Docker compositions.


## 🚪 Traefik Forward Auth Configuration

The [`config/conf.example`](./config/conf.example) contains
example configuration for the forward-auth service.

Create a copy of this example file without the example suffix:

```bash
cp config/conf.example config/conf
```

Then update the configuration file with the usernames and emails of
the GitLab users that correspond to user 1 and 2 respectively.
(You must either have two seperate GitLab users, or skip the configuration of
one of the two users).

```txt
rule.user1_access.action=auth
rule.user1_access.rule=PathPrefix(`/<USERNAME_USER1>`)
rule.user1_access.whitelist = <EMAIL_USER1>

rule.user2_access.action=auth
rule.user2_access.rule=PathPrefix(`/<USERNAME_USER2>`)
rule.user2_access.whitelist = <EMAIL_USER2>
```

**NOTE:** Ensure that the usernames set in the
[Usernames configuration step](#-usernames) are the same as those set
in the Traefik Forward Auth configuration file.
