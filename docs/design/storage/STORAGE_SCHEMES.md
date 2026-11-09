# Storage schemes

Part of the [storage design](README.md). Issue
[workspace#102](https://github.com/INTO-CPS-Association/workspace/issues/102).

A workspace keeps user files under `$PERSISTENT_DIR` (`/workspace`),
bind-mounted from the host (`./files/<user>`). The admin service can
back up named folders of it, called **assets**, to one of two storage
schemes. Each asset uses exactly one scheme.

## Assets

An asset is a folder `$PERSISTENT_DIR/assets/<name>`, declared in
`config.toml` as `[assets.<name>]`.

- **Names** match `^[a-z0-9]{1,63}$`: lowercase letters and digits
  only. A name becomes a folder, an S3 prefix and a git directory, so
  anything else is refused.
- **No name is special.** `private` and `common` are ordinary names.
  The workspace does not decide who else may read an asset; the
  storage server's policy does (see [threat model](THREAT_MODEL.md)).
- **Add and remove only.** Renaming would move data between folders,
  prefixes and repositories, so it is not offered. Removing an asset
  stops its backup; it never deletes the folder or the stored copy.

## Scheme A: git

Already implemented (#93). The asset folder is a git work tree; the
git directory lives apart, in `.local/share/workspace-admin/<name>`,
so the folder holds no `.git`.

Each round, per asset:

1. Refuse to run during an unfinished merge or with conflict markers.
2. Commit local changes as `workspace-admin`.
3. Pull the remote branch; on conflict keep the local side.
4. Push if ahead.

| Property | Value |
| :--- | :--- |
| Direction | Two-way: remote changes arrive in the folder |
| History | Full git history on the remote |
| Suits | Text, code, small files; collaboration through the remote |
| Poor fit | Large or binary files; history grows forever |
| Credentials | GitLab connection or a pasted token ([connect](CONNECT_BACKENDS.md)) |
| Hosts | Any HTTPS git server with a token; the deployment's GitLab with a connection |

## Scheme B: S3 object storage

New. The asset folder is mirrored one way to an S3 bucket:

```text
local:  $PERSISTENT_DIR/assets/<name>/...
remote: s3://<bucket>/<MAIN_USER>/<name>/...
```

Each round, per asset:

1. Refuse to run if the folder is missing or empty
   (`s3_empty_source`). An unmounted volume must not mirror as
   "delete everything".
2. Obtain credentials ([connect](CONNECT_BACKENDS.md)).
3. Check the bucket exists and has versioning enabled
   (`GetBucketVersioning`).
4. Run `rclone sync` with the admin's own folders excluded,
   `--s3-no-check-bucket` (the role may not create buckets), time
   limits, and `--max-delete` as a brake. Symbolic links are skipped
   (`--skip-links`): following them could upload files from outside
   the asset.

A round that would delete more than `S3_MAX_DELETE` objects (default
1000) stops with `s3_delete_limit` and uploads nothing more. The user
confirms the deletion once, from the page or with
`workspace-admin asset allow-delete <name>`, and the next round runs
without the limit.

| Property | Value |
| :--- | :--- |
| Direction | One-way: the folder is the truth; the bucket follows |
| History | Bucket versioning keeps every overwritten or deleted object as a noncurrent version |
| Retention | A lifecycle rule expires noncurrent versions after `NONCURRENT_VERSION_DAYS` (default 365, configurable) |
| Suits | Large and binary files, datasets, FMUs, models |
| Poor fit | Collaboration; nothing flows back into the folder |
| Restore | Operator copies a prefix as it was at a time, with `rclone copy --s3-version-at` |
| Servers | SeaweedFS in compose today; AWS S3 or other S3 services by configuration |

### Prefix layout

The prefix is derived, never configured: `<MAIN_USER>/<name>/`. A
user therefore cannot point a backup at another user's prefix by
editing a file, and the server-side policy enforces the same rule.

This relies on one invariant: **`MAIN_USER` equals the user's
username at the identity provider (GitLab or Keycloak) exactly.** The
storage pass from login-relay carries that username, the policy scopes
by it,
and the admin builds the prefix from `MAIN_USER`; the owner
check at connect time refuses a mismatch and names both values, so a
misconfigured deployment fails loudly, not silently.

One bucket with prefixes, not a bucket per user: cloud providers
limit and bill buckets, and prefix policies scope just as well.

## SeaweedFS as the S3 server

A single `weed server -s3` container on the internal `dtaas-users`
network, pinned to 4.40 or later. Everything it stores lives below
its data directory, `/data`:

| Component | Keeps | On disk |
| :--- | :--- | :--- |
| Volume server | File contents, packed into append-only volumes | `<id>.dat`, `<id>.idx` |
| Filer | Bucket and object names, metadata, version lists | Embedded LevelDB, `filerldb2/` |
| Master | Which volumes exist | Small metadata files |
| S3 gateway | Nothing; translates S3 calls to filer calls | None |

One persistent volume mounted at `/data` survives restarts and image
upgrades.

### Durability limit

A single SeaweedFS has no replication. On the same disk as
`./files/`, it protects against deletion, overwrite and mistakes, not
against losing that disk or host. Operators should put `/data` on a
different disk, run SeaweedFS on another host, or use a cloud store.

## Moving to a cloud S3 service

The admin speaks only standard protocols: the S3 API through
`rclone`, AWS STS `AssumeRoleWithWebIdentity`, and OIDC. Moving to,
for example, AWS S3 changes environment values, not code:

| Setting | SeaweedFS | AWS S3 |
| :--- | :--- | :--- |
| `S3_ENDPOINT` | `http://seaweedfs:8333` | `https://s3.eu-north-1.amazonaws.com` |
| `S3_PROVIDER` | `SeaweedFS` | `AWS` |
| `S3_STS_ENDPOINT` | `http://seaweedfs:8333` | `https://sts.eu-north-1.amazonaws.com` |
| `S3_ROLE_ARN` | `arn:seaweed:iam::role/WorkspaceBackup` | the IAM role ARN |

What changes outside the admin:

- Traffic leaves the host: the endpoint must be HTTPS.
- AWS fetches login-relay's signing keys itself: login-relay's JWKS
  and issuer must be public over HTTPS with a valid certificate.
- The per-user policy uses `${aws:PrincipalTag/username}` instead of
  `${jwt:preferred_username}`; login-relay adds the
  `https://aws.amazon.com/tags` claim with `principal_tags.username`
  to the storage pass. Details are part of the cloud guide (roadmap
  follow-ups).
- Providers without STS web identity (Backblaze B2, Wasabi, R2 and
  others) use the static-key connection instead.
- Cost and data location become operator decisions.

## Choosing a scheme

| Need | Choose |
| :--- | :--- |
| Share and edit the files with others through GitLab | git |
| Back up large or binary files | S3 |
| Point-in-time restore of a whole folder | S3 |
| Readable history of changes | git |
| No account on a git server | S3 |
