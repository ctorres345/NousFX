"""One list of secret file patterns for the Read/Edit deny rules and the Gemini diff excludes (NFX-103).

Two roles:
- DENY: exact file names and extensions (plus the key-file prefixes `.env*` and `id_<type>*`), safe as Claude
  Code Read/Edit deny rules anywhere on disk. scripts/nousfx_deny.py loads this file from the repo at sync time
  and writes `Read(//**/<p>)` and `Edit(//**/<p>)` for each.
- EXCLUDE_ONLY: broad globs. As deny rules they would block source files such as `SecretsRepository.kt` or
  `PasswordField.swift`, so only the Gemini scripts use them. A pattern ending in `/**` is a directory.

The Gemini exclude set is DENY plus EXCLUDE_ONLY (gemini_common.py). Patterns are bare names matched in any
folder; no backslashes, so one text fits macOS and Windows. Standard library only, no imports: sync loads it
by path.
"""

DENY = (
    # environment files and SSH keys
    ".env*", "*.env", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    # certificates and private keys
    "*.pem", "*.key", "*.p12", "*.p8", "*.pfx",
    # Android signing (keystores and the Gradle file that names their passwords) and Firebase configs
    "*.jks", "*.keystore", "*.ks", "key.properties", "google-services.json", "GoogleService-Info.plist",
    # package registry, HTTP, database and container credentials
    ".npmrc", ".netrc", ".pypirc", ".pgpass", ".dockercfg", "kubeconfig", ".git-credentials",
    "application_default_credentials.json",
)

EXCLUDE_ONLY = (
    "*.env.*", "*password*", "*secret*", "*credential*", "*.tfvars", "*.mobileprovision",
    # Android builds need its sdk.dir, so Claude may read it; it can still hold signing passwords.
    "local.properties",
    ".env*/**", "*secret*/**", "*credential*/**",
)
