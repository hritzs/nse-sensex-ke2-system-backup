# Shared expiry refresh fix, 28 October 2026

The authoritative GreekTokens.csv on the configured Windows share had today's
export, while shared/GreekTokens.csv was still the 19 October snapshot.
The local BSE master was still from August. Restarting loaded those same old
snapshots. The optional --refresh-inputs path copied only NSE and did not
refresh BSE. Each service held its master in memory until restart.

run_all.sh now requests --refresh-masters by default. The PowerShell importer
copies and verifies both masters; -MastersOnly excludes daily/model inputs.
The installer validates NSE near-term coverage and the combined KE2 identity
map, requires unexpired SENSEX futures, stages both files, retains rollback
copies and restores the first file if replacing the second fails.
The restart lock covers the refresh and subsequent coordinated restart.

--check remains read-only. --no-refresh-masters explicitly permits local
offline startup. --refresh-inputs also refreshes daily/calendar/LUT inputs
and remains an explicit operation subject to model binding.

The source maps are loaded on startup. This does not enable unsafe in-place
replacement of maps used by C++ packet workers. After an external CSV/share
update, rerun run_all.sh to update all consumers together. Explicit API
subscription expiries are preserved; clients rediscover and resubscribe.

Validation:
- Four launcher tests passed, including default refresh and read-only checks.
- Seven operations tests passed, including invalid-pair rejection, successful
  pair installation, unchanged refresh, and rollback on second-file failure.
- Live Gamma expiries are 3, 9, 17, 23 November and 1 December 2026.
- Every one of 224 indicative symbols matched its nearest current future.
- KE2 metadata matched the local master for NIFTY 1 December and SENSEX
  26 November; actual NIFTY, SENSEX and RELIANCE futures streamed afterward.
- All five Gamma expiry ladders were FRESH and its reference fingerprint
  remained unchanged. Existing database/history was retained.

Measured evidence: expiry_refresh_20261028.json.
Previous masters: backups/master_refresh/20261028-103729-685077/.
Source and simulator DB checkpoint:
backups/expiry_refresh_before_changes/20261028-045046/.

Rollback requires restoring BOTH chosen master snapshots and using
run_all.sh --no-refresh-masters so the restore is not overwritten from the
share. This retains the current simulator DB; no strategy or PnL migration
was performed. Run the relevant checks before restarting a restored version.
