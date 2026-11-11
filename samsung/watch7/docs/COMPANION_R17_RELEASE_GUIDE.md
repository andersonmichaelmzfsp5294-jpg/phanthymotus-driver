# Watch7 r17 exact cross-repository source lock

This manifest **pins the 17-card source set to exact commits**, not floating branches.

- Driver code: `andersonmichaelmzfsp5294-jpg/phanthymotus-driver@666d48552cb36b6526e8090ff5fd9a52c24344f8`
- Companion Git (separate local repo): `73d04d964bbf2bdca6ddfa49fcbd4e6bbab4ac64`
- Companion annotated tag: `watch7-r17-source-20261111`
- Companion source count: 52
- Provenance file after archive: `../archive/watch7-r17-source-20261111/ARCHIVE_PROVENANCE.json`
- Companion source ZIP SHA-256: `d52f67d4995d244cc2b31b0c212e6ca9f786cac3e7ee4882419df87a3da91749`
- Offline Git bundle SHA-256: `adedbd392461f867743933c7a9d715b988b6c6aabe57627e010bf1d22cd02c6a`

Source of truth is the complete Android project `heart-rate-wear-test/`:
`app/src/`, `app/build.gradle.kts`, `app/gradle.lockfile`,
Gradle wrapper, build and audit scripts. Research variant requires a
separately licensed Samsung SDK AAR. No APK, AAR, private data or
credentials are included in the source bundle.

To recreate the pair, check out the **Driver exact SHA** above and
restore the Android local Git bundle to the **Companion exact SHA**.
Use `python -B build_s4.py --variant standard --no-samsung-sdk`
or provide the SDK AAR and build `--variant research`.
Standard/Research offline builds passed on the original Windows machine;
a clean new system still needs Gradle/Android/JDK dependencies.

**Remote limitation:** An independent GitHub Companion repository has
not yet been created. The personal Fork contains the cross-lock and
prior source snapshots, while the **complete Android Git history is
currently in the local bundle only**. Do not label the Companion as
cloud-backed until a private remote is created and its tag/commit verified.

**Acceptance:** Driver 80 local tests passed; the new advanced Watch7
sensor data has not all completed real Watch7 → Driver → Agent Core
end-to-end verification. Company PR #500 was not changed.
