plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.heartrateweartest"
    compileSdk = 36

    defaultConfig {
        // Existing research package stays stable for its on-watch private data.
        applicationId = "com.example.heartrateweartest"
        minSdk = 30
        targetSdk = 36
        versionCode = 2
        versionName = "0.2.0"
    }

    flavorDimensions += "sdk"
    productFlavors {
        create("standard") {
            dimension = "sdk"
            // A new installation: never replace the existing research app.
            applicationId = "com.phanthymotus.watch7"
            versionNameSuffix = "-standard"
            buildConfigField("boolean", "HAS_SAMSUNG_SDK", "false")
        }
        create("research") {
            dimension = "sdk"
            // Preserve legacy experimental application ID and its private recordings.
            versionNameSuffix = "-research"
            buildConfigField("boolean", "HAS_SAMSUNG_SDK", "true")
        }
    }

    buildFeatures {
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.health:health-services-client:1.1.0")
    implementation("com.google.guava:guava:33.4.8-android")

    // Research-only Samsung AAR supplied locally, never redistributed.
    // Standard builds must not read or resolve this binary dependency.
    val samsungAar = providers.gradleProperty("samsungSdkAar")
        .orElse(providers.environmentVariable("SAMSUNG_SDK_AAR"))
        .orNull
        ?.let { file(it) }
        ?: rootProject.file("../research/watch-capability-probe/app/libs/samsung-health-sensor-api.aar")
    add("researchImplementation", files(samsungAar))
}
