# System architecture baseline

Version/date: 16 October 2026. Exact files are identified by the source
manifest in the corresponding system backup.

- [HLD.md](HLD.md): system context, responsibilities, data flows, deployment,
  capability boundaries, independent indicative service and known risks.
- [LLD.md](LLD.md): source map, models, packet paths, algorithms, threading,
  interfaces, persistence, launcher and backup behavior.
- [ADR.md](ADR.md): decisions and tradeoffs.
- [Backup/restore](../backup/BACKUP_RESTORE.md): artifacts, verification,
  recovery and private GitHub publication.
- [Developer API](../../KE2_DEVELOPER_API.md): implemented KE2 contract.
- [Swagger tester](../../KE2_FASTAPI_TESTER.md): interactive HTTP test client.
- [Startup](../../KE2_STARTUP_AND_API.md): operator instructions.

Scope includes core C++ NSE/BSE decoding, legacy analytics, KE2 adapter,
stock/index consumers, existing NIFTY simulator, indicative-close server,
FastAPI tester, launcher, reference files and persistence boundaries.

Mermaid diagrams are stored as text in Markdown, so they remain versionable
and readable offline even when a renderer is unavailable. These documents
describe actual implementation and mark missing or unverified behavior.
They do not claim full original KE2 spec compliance or live-wire certification.

The Gamma extension and explicit project restart are documented in the final
sections of HLD/LLD and [Gamma operation/API](../../GAMMA_WEEKLY_SIMULATOR.md).
