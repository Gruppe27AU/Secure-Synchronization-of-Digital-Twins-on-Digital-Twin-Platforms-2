"""Set required environment variables before main.py is imported.

Force-set (not setdefault) so tests always run with known values regardless
of whatever production env vars the container has already exported.
"""
import os

os.environ["KEYCLOAK_CLIENT_SECRET"] = "test-secret"
os.environ["SERVER_DNS"] = "test.example.com"
os.environ["WORKSPACE_USERS"] = "user1,user2"
os.environ["KEYCLOAK_REALM"] = "dtaas"
os.environ["KEYCLOAK_CLIENT_ID"] = "dtaas-workspace"
