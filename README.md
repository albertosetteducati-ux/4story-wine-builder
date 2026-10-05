# 4Story Wine compatibility build (Android x86_64)

This repository is a **compatibility experiment**, not an anti-cheat bypass.
It builds a Wine 11.6 Android x86_64 layer for Bannerlator/Winlator and leaves
4Story and nProtect GameGuard binaries untouched.

## Why this exists

The observed 4Story trace reaches nProtect GameGuard and Wine logs:

```
fixme:toolhelp:CreateToolhelp32Snapshot Unimplemented: heap list snapshot
```

Wine 11.6 accepts `TH32CS_SNAPHEAPLIST` only incompletely and leaves
`Heap32ListFirst` / `Heap32ListNext` incomplete. This build implements the
heap-list part of the documented Toolhelp contract by reading the target
process PEB and its real `ProcessHeaps` array. It does **not** return fake
success, hide processes/modules, modify GameGuard, or forge anti-cheat data.

It also layers the four Wine compatibility patches from
`Krzysztof977/mu-online-linux` v0.1.0, a project that reports the official
nProtect GameGuard path working under Wine 11.6 without disabling GameGuard.
Those upstream patches are downloaded by CI and SHA256-verified.

## Build

1. Create a new GitHub repository.
2. Upload this repository's contents (including `.github/workflows/`).
3. Open **Actions** → **Build 4Story GameGuard Wine for Android x86_64**.
4. Press **Run workflow**.
5. When the job finishes, download the artifact
   `4story-wine-11.6-gg-x86_64-v1`.

The artifact contains:

- `4story-wine-11.6-gg-x86_64-v1.wcp` — zstd WCP, preferred for Bannerlator.
- `4story-wine-11.6-gg-x86_64-v1.wcp.xz` — XZ fallback for Winlator variants.
- `SHA256SUMS`.

## Test configuration

Use a **new container**, not the working existing one.

- Wine: `11.6-4story-gg-x86_64-v1`
- Box64: start with the exact Box64 version that already gets your 4Story
  `TClient.exe` as far as the GameGuard error (in the current experiment,
  Box64 0.3.7).
- Box64 preset: the same stable/compatibility preset that reached GameGuard.
- Startup services: Normal.
- X11.
- Keep Wine debug + Box64 logs enabled for the first launch.

Install/run the official 4Story client normally. Do not copy or modify
GameGuard files.

## What success means

The first goal is deliberately narrow: the old log line

```
CreateToolhelp32Snapshot Unimplemented: heap list snapshot
```

should disappear. If GameGuard still stops, the **new first unimplemented API
or driver error** in the log becomes the next compatibility target. Do not
blindly add more patches.

`Heap32First` and `Heap32Next` are intentionally still Wine stubs in v1. They
will only be implemented if a trace shows 4Story's GameGuard actually calls
them.

## Sources / provenance

- Wine 11.6 source is downloaded from WineHQ and pinned by SHA256.
- The four GameGuard compatibility patches are fetched from
  `Krzysztof977/mu-online-linux` tag `v0.1.0` and each is pinned by SHA256.
- Android x86_64 build scripts/patches come from GameNative's public
  `proton-wine` Android build stack.
- No 4Story, GameGuard, `GameMon.des`, or GameGuard driver binaries are stored
  in this repository.

## Caveat

This is an **experimental first build**. The upstream GameGuard patches were
validated by their author against desktop Wine 11.6; this project additionally
ports that Wine source through GameNative's Android/Bionic build stack. The CI
may expose a source-patch conflict that needs rebasing. If that happens, keep
the full GitHub Actions log: the failing patch name and hunk are enough to
prepare v2 without guessing.
