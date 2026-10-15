import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
	id("com.google.gms.google-services") 
}
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
android {
    namespace = "com.david.homeenv"
    compileSdk = 34

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
            manifestPlaceholders["appLabel"] = "homeenv preview"
            buildConfigField("boolean", "PUSH_ENABLED", "false")
        }
    }
    buildTypes.matching { it.name != "preview" }.configureEach {
        manifestPlaceholders["appLabel"] = "homeenv"
        buildConfigField("boolean", "PUSH_ENABLED", "true")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}


// google-services.json has no client for the preview package; skip it there
tasks.matching { it.name == "processPreviewGoogleServices" }.configureEach { enabled = false }

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.squareup.okhttp3:okhttp:4.11.0")
    implementation("com.google.firebase:firebase-messaging-ktx:23.2.0")
    implementation("androidx.work:work-runtime-ktx:2.9.0")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.cardview:cardview:1.0.0")
	implementation("androidx.viewpager2:viewpager2:1.0.0")
	implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
