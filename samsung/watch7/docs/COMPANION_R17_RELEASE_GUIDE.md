# Watch7 r17 exact cross-repository source lock

This manifest **pins the 17-card source set to exact commits**, not floating branches.

- Driver code: `andersonmichaelmzfsp5294-jpg/phanthymotus-driver@666d48552cb36b6526e8090ff5fd9a52c24344f8`
- Companion Git (separate local repo): `41ebd0fd99271a0615b8dd16332c862d48beed76`
- Companion annotated tag: `watch7-r17-source-20261111`
- Companion source count: 52
- Provenance file after archive: `../archive/watch7-r17-source-20261111/ARCHIVE_PROVENANCE.json`
- Companion source ZIP SHA-256: `32c5473c4ddc80d21f1d9d0ee43c537fabee4040f34342a381101d08fc15a103`
- Offline Git bundle SHA-256: `30939d5a46105c3da6464d2b055001a790c1ad6cda9f26e3562633179022b580`

Source of truth is the complete Android project `heart-rate-wear-test/`:
`app/src/`, `app/build.gradle.kts`, `app/gradle.lockfile`,
Gradle wrapper, build and audit scripts. Research variant requires a
separately licensed Samsung SDK AAR locked to SHA-256
`893cd5d6564db0f304bf511a555c1d65ca6bccc8475fc979ff1d71d50680344c`.
No APK, AAR, private data or
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
