Separate Windows x64 applications, version 0.2.0-rc1.

- KE2Feed.exe: native NSE/BSE decoder, KE2 feed, independent indicative server and Swagger tester.
- WhiteGamma.exe: WhiteGamma interface only, using a separately running feed.
- GammaSimulator.exe: independent all-expiry Gamma simulator.
- Separate data folders, service ownership and control ports; stopping Gamma leaves feed running.
- Routed raw endpoint /ke2-source, source metadata verification and stale-quote rejection.
- Existing numerical WhiteGamma and backtest sources are retained.

Start KE2Feed.exe, WhiteGamma.exe and GammaSimulator.exe. Each owns different services.
See README.md for LAN configuration.

This is a prerelease candidate. Linux split-process integration and deterministic
transport checks passed. Archive/resource checks passed for all three Windows
EXEs. Full frozen Windows GUI, live multicast and real multi-PC acceptance is
pending; Application Control blocked a required pandas native module during
earlier Windows model testing. No Windows policy is bypassed or disabled.

No portfolios, positions, trades, marks or historical databases are included.
Dated authoritative master/reference seeds are bundled; configure daily BOD
sources for fresh data. Indicative's independent NSE CM stream remains local.
The earlier NSE packet-layout discrepancy is not fixed by this packaging.
