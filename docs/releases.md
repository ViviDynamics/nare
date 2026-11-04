# Withdrawn releases

A published release is never deleted or moved: see
[the release policy](contract.md#releases). This page is the record for the
times that did not hold. Every version listed here once had a GitHub release, a
tag and a wheel, and no longer has any of them. A version that is not listed
here is either published or was never minted.

## 2026.9.0 to 2026.10.32

Withdrawn when `main`'s history was rewritten. The rewrite changed tests,
documentation and benchmark fixtures. It did not change what a release ships: for every version below, `src/`,
`pyproject.toml`, `uv.lock` and the `Dockerfile` are byte for byte the same as
at the commit in the second column, which is on `main` today.

When they went: the deletion of the `2026.10.32` tag is recorded at
2026-10-30 01:47 UTC, three minutes before `2026.10.33` was published. GitHub
kept no record of when the others were deleted; all of them were gone by
2026-11-03, when #140 recorded it.

- **Replacement.** `2026.10.33` replaces every version below: it is the first
  surviving release, and its history contains each one's code. `2026.10.32`
  and `2026.10.33` ship identical package files; only the version differs.
- **Reproducing a result** recorded under a withdrawn version: install from the
  commit in the second column,
  `pip install "git+https://github.com/ViviDynamics/nare@<commit>"`. The code
  is the same; the version it reports is a development version derived from
  that commit, not the withdrawn number.
- **Images.** The container images for `2026.9.7` to `2026.10.32` are still
  published at `ghcr.io/vividynamics/nare:<version>`, so a caller that pinned
  one of those still pulls the bytes it pinned. `2026.9.0` to `2026.9.6`
  predate the image.

| Version | Same shipped code at | Image | Replacement |
| --- | --- | --- | --- |
| `2026.9.0` | `612d2ef6a7b2` | no | `2026.10.33` |
| `2026.9.1` | `67db4f365e2c` | no | `2026.10.33` |
| `2026.9.2` | `b9a952c2e558` | no | `2026.10.33` |
| `2026.9.3` | `6c42bdbf08a5` | no | `2026.10.33` |
| `2026.9.4` | `89d95f1d1d84` | no | `2026.10.33` |
| `2026.9.5` | `79a0b37876c4` | no | `2026.10.33` |
| `2026.9.6` | `244f0e9f690a` | no | `2026.10.33` |
| `2026.9.7` | `9ab5fa0197e1` | yes | `2026.10.33` |
| `2026.9.8` | `23763f858d03` | yes | `2026.10.33` |
| `2026.9.9` | `7e1864a4b0a4` | yes | `2026.10.33` |
| `2026.9.10` | `756dc54188f8` | yes | `2026.10.33` |
| `2026.9.11` | `0c0bbda5c3f4` | yes | `2026.10.33` |
| `2026.9.12` | `5b3f06ed3c18` | yes | `2026.10.33` |
| `2026.9.13` | `43a144aa3b52` | yes | `2026.10.33` |
| `2026.9.14` | `dcd10776cb49` | yes | `2026.10.33` |
| `2026.9.15` | `93485363f195` | yes | `2026.10.33` |
| `2026.9.16` | `8db2e4c00a4b` | yes | `2026.10.33` |
| `2026.9.17` | `264455651a09` | yes | `2026.10.33` |
| `2026.9.18` | `4c24ab24dedf` | yes | `2026.10.33` |
| `2026.10.0` | `838e6ef7cb31` | yes | `2026.10.33` |
| `2026.10.1` | `7bdb29f8d38a` | yes | `2026.10.33` |
| `2026.10.2` | `5ba5ab79887b` | yes | `2026.10.33` |
| `2026.10.3` | `35853f637d04` | yes | `2026.10.33` |
| `2026.10.4` | `0e27b9bba953` | yes | `2026.10.33` |
| `2026.10.5` | `36eda6036d82` | yes | `2026.10.33` |
| `2026.10.6` | `7caa356ac67d` | yes | `2026.10.33` |
| `2026.10.7` | `ebac18183f7c` | yes | `2026.10.33` |
| `2026.10.8` | `d9651dda5d63` | yes | `2026.10.33` |
| `2026.10.9` | `e1423bd17cf4` | yes | `2026.10.33` |
| `2026.10.10` | `a69bd968ed2c` | yes | `2026.10.33` |
| `2026.10.11` | `cddbde5e0acc` | yes | `2026.10.33` |
| `2026.10.12` | `df57f7b6b1b0` | yes | `2026.10.33` |
| `2026.10.13` | `36ac8bf0bbda` | yes | `2026.10.33` |
| `2026.10.14` | `7ed7eb90969c` | yes | `2026.10.33` |
| `2026.10.15` | `e2fa22d3b626` | yes | `2026.10.33` |
| `2026.10.16` | `98976ffbe990` | yes | `2026.10.33` |
| `2026.10.17` | `2e8530c2799e` | yes | `2026.10.33` |
| `2026.10.18` | `cde55d993c34` | yes | `2026.10.33` |
| `2026.10.19` | `75dbd9a4154f` | yes | `2026.10.33` |
| `2026.10.20` | `e59d42166a94` | yes | `2026.10.33` |
| `2026.10.21` | `b67e8ccaab7e` | yes | `2026.10.33` |
| `2026.10.22` | `96c69473de7e` | yes | `2026.10.33` |
| `2026.10.23` | `4af3e2f4190b` | yes | `2026.10.33` |
| `2026.10.24` | `a298d63f8c84` | yes | `2026.10.33` |
| `2026.10.25` | `e2c371b857b9` | yes | `2026.10.33` |
| `2026.10.26` | `38c10784c4db` | yes | `2026.10.33` |
| `2026.10.27` | `26755a043954` | yes | `2026.10.33` |
| `2026.10.28` | `fd4dcaec1c70` | yes | `2026.10.33` |
| `2026.10.29` | `384e33c3f025` | yes | `2026.10.33` |
| `2026.10.30` | `e780a154878f` | yes | `2026.10.33` |
| `2026.10.31` | `be00818c969e` | yes | `2026.10.33` |
| `2026.10.32` | `be00818c969e` | yes | `2026.10.33` |
