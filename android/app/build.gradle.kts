import java.util.Properties

plugins {
    id("com.android.application")
    id("com.google.gms.google-services")
}
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
android {
    namespace = "com.david.homeenv"
    compileSdk = 37   // needed by the current AndroidX libraries and OkHttp; targetSdk stays (#125, #60)

    defaultConfig {
        applicationId = "com.david.homeenv"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        buildConfigField("String", "API_USER", "\"${localProps.getProperty("API_USER", "")}\"")
        buildConfigField("String", "API_PASS", "\"${localProps.getProperty("API_PASS", "")}\"")
        buildConfigField("String", "API_BASE_URL", "\"${localProps.getProperty("API_BASE_URL", "https://www.example.com").trimEnd('/')}\"")
    }

    buildTypes {
        // Side-by-side test build (./gradlew assemblePreview): own package ID and
        // launcher name, so installing it doesn't replace the regular app.
        // No Firebase entry exists for this package, so push is disabled in it.
        create("preview") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".preview"
            versionNameSuffix = "-preview"
            matchingFallbacks += "debug"
            manifestPlaceholders["appLabel"] = "Home Ping preview"
            buildConfigField("boolean", "PUSH_ENABLED", "false")
        }
    }
    buildTypes.matching { it.name != "preview" }.configureEach {
        manifestPlaceholders["appLabel"] = "Home Ping"
        buildConfigField("boolean", "PUSH_ENABLED", "true")
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}


// google-services.json has no client for the preview package; skip it there
tasks.matching { it.name == "processPreviewGoogleServices" }.configureEach { enabled = false }

dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("com.squareup.okhttp3:okhttp:5.5.0")
    // Firebase versions come from the BoM; the -ktx artifacts are discontinued (#125)
    implementation(platform("com.google.firebase:firebase-bom:35.0.2"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("com.google.android.material:material:1.14.0")
    // Geofence (home/away for the occupancy alert, #239) and the current location for "set home"
    implementation("com.google.android.gms:play-services-location:21.3.0")
    // Retries a location arm/disarm until it reaches the server (#239): the post on leaving
    // home often happens just as the phone drops WiFi
    implementation("androidx.work:work-runtime-ktx:2.10.1")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.2.0")
    // Coroutines tied to a screen's lifecycle instead of raw threads (#125)
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
}
