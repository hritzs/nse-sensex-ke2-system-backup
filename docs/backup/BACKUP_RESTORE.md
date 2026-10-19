# Verified system backup and restoration

Source: the entire project, including decoder, KE2, legacy and Gamma simulators,
FastAPI tester, indicative-close source, launcher, tests and HLD/LLD.

## Create an offline backup

In Ubuntu WSL, from the project directory:

```bash
python3 scripts/backup_system.py --prepare-github
```

This does not restart services or change the original Git repository. Output
is a timestamped directory under `backups/system/`, excluded from the source
walk and original Git working tree. Artifacts have local private permissions.

| Artifact | Contents |
| --- | --- |
| source.tar.gz | Current source/config/docs and market masters, including uncommitted code |
| private-runtime.tar.gz | Consistent SQLite snapshots, runtime data, licenses, binaries, private configuration and old compressed source packages |
| git-history.bundle | Original Git refs/history for offline recovery; never uploaded by this workflow |
| native-dependencies.tar.gz | Available local uWebSockets/uSockets source under /opt/uWebSockets |
| MANIFEST.json | Every captured source/runtime path, size, SHA-256, dependencies and cloud preparation status |
| SHA256SUMS | Archive and manifest hashes |
| VERIFIED.txt | Archive/SQLite verification completed |
| github-source/ | Isolated sanitized Git repository prepared for the selected backup remote |

SQLite files are read with the SQLite backup API and quick_check, including
committed WAL contents. Raw WAL/SHM files are omitted. This is a per-database
consistent snapshot, not an atomic cross-service business transaction.
Files that change size/mtime during copying abort the backup for retry.

Virtual environments, node_modules, build directories (except explicit decoder
binaries), logs, caches and Windows Zone.Identifier files are omitted.
Restore Python environments using recorded freezes and requirement files.
The independent indicative binary is included privately. Its local CM master
is included; the maintenance Windows share is not contacted.

Recognized credentials, private keys, licenses, env files, runtime data and
original Git history are kept only offline. Recognizable tokens in small
source files are quarantined to the private archive. This automatic scan is
not a guarantee that every possible custom secret format is detectable.
Do not publish the private-runtime archive or original-history bundle.

## Verify and make a second offline copy

```bash
cd backups/system/ACTUAL_BACKUP_DIRECTORY
sha256sum -c SHA256SUMS
```

Copy the archives, manifest, checksums and verification file to a separate disk,
or to the Windows user backup directory. The prepared github-source working
tree is not needed for offline restore. A second directory on the same
physical disk protects against working-copy mistakes but not disk failure.
For disaster recovery keep another verified copy on removable/offsite storage.

The implementation session also creates a verified Windows copy under
`C:\Users\Administrator\Backups\NSE-KE2\<snapshot>` when the copy succeeds.
Use the actual path reported in the session; this document alone does not
prove a copy or upload occurred.

## GitHub backup

Requested repository: `hritzs/nse-sensex-ke2-system-backup`.
The original `hritzs/nse-fo-low-latency-decoder` remote is untouched.

```bash
python3 scripts/publish_system_backup.py backups/system/ACTUAL_BACKUP_DIRECTORY
```

Creation needs authenticated GitHub API access as hritzs: a locally configured
`gh auth login` session or `GH_TOKEN`/`GITHUB_TOKEN` environment variable.
Never paste tokens into source files or chat. Existing SSH authentication
can push after the repository exists, but cannot create it.

The helper checks the account and requested visibility before pushing, pushes
the prepared snapshot without force, verifies the remote commit and visibility
again, and writes `GITHUB_UPLOAD_RECEIPT.json` plus its hash. A matching receipt
is evidence of completed upload. MANIFEST's original preparation status is
immutable and remains prepared_not_uploaded; the separate receipt records
the later publication. Without a receipt, GitHub backup is pending.

Only sanitized current source is pushed, with files larger than 50 MiB stored
through Git LFS. Git LFS availability/storage permissions and quota must
succeed. The original repo's historical secrets and private runtime databases
are not sent to the new repository.

## Restore source and runtime to a NEW directory

Verify hashes first. Do not extract over an active service directory.

```bash
mkdir -p /home/administrator/restores/nse-ke2
tar -xzf /actual/backup/source.tar.gz -C /home/administrator/restores/nse-ke2
tar -xzf /actual/backup/private-runtime.tar.gz -C /home/administrator/restores/nse-ke2
cp -a /home/administrator/restores/nse-ke2/runtime/. /home/administrator/restores/nse-ke2/source/
cd /home/administrator/restores/nse-ke2/source
```

Inspect MANIFEST and dependencies. Recreate Python environments; rebuild C++
with the recorded compiler and native dependency sources. Binaries are a
rollback aid for compatible hosts, not portable executables. License files
and required local exchange masters must exist before startup. Check
multicast interfaces/routes and authoritative master freshness.

Original Git history can be recovered separately:

```bash
git clone /actual/backup/git-history.bundle /home/administrator/restores/original-history
```

The source archive is the current uncommitted snapshot; the historical bundle
alone does not contain those uncommitted changes. For GitHub recovery use
`git clone` followed by `git lfs pull`, then restore private runtime separately.

## Operational validation and rollback

1. Read architecture, startup and Gamma documents.
2. Run build and fixture tests before starting a service.
3. Use `bash start_system.sh --check --with-api` to inspect prerequisites.
4. Do not point a restored simulator at the live database for a test.
5. Start restored services only on deliberately chosen available ports or
   under the approved project restart procedure.
6. Verify actual fresh exchange quotes; successful sockets are insufficient.

The Gamma simulator uses its own DB. Changing model/reference hashes within
the same trade date requires a new GS_SIM_DB; new-day input updates are allowed. The original simulator and database remain a rollback path.
No automatic restore, service replacement or destructive cleanup is performed
by the backup script.


## Explicit public publication

The user subsequently requested a public repository. Select public visibility
explicitly when preparing/publishing; the default remains private:

```bash
python3 scripts/backup_system.py --prepare-github --visibility public
python3 scripts/publish_system_backup.py backups/system/ACTUAL_BACKUP_DIRECTORY --visibility public
```

The public repository contains current source, model LUTs, master exports,
tests and documentation. Live databases, credentials, licenses, runtime files,
compiled binaries and original Git history remain only in offline archives.
This publishes a source snapshot; it does not activate pending simulator or
decoder changes. Deployment/status notes from earlier steps are historical.

## Subsequent snapshots and deployed status

The public backup already exists at https://github.com/hritzs/nse-sensex-ke2-system-backup.
Subsequent prepared snapshots fetch its current main as their parent and publish
with a normal fast-forward push. No force push or original-repository mutation
is used. A concurrent remote update causes the push to fail for review.
See ../verification/DEPLOYMENT.md for the measured deployment status; publishing
a source snapshot and starting services are separate actions.
