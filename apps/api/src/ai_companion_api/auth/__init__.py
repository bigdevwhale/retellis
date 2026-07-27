"""Authentication & deployment-mode layer.

Replaces the single-user ``X-User-Id`` self-assertion with a verified ``Principal``
resolved from a session cookie. One identity layer serves both deployment modes
(``self_hosted`` / ``hosted``); only the configuration (which ``AuthBackend``) and
the feature-flag surface differ. The mode→backend matrix is enforced at boot by
``bootstrap.validate_auth_config``.

Auth identity is fully decoupled from the BYOK vault: the cookie holds only an
opaque session token — never the master key, a provider key, or the vault
passphrase. The passphrase is entered in-browser and never sent to any auth
endpoint (see ``tests/test_vault_auth_separation.py``).
"""
