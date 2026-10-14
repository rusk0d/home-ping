plugins {
    id("com.android.application") version "8.5.1" apply false
    id("org.jetbrains.kotlin.android") version "1.9.10" apply false
}
buildscript {
    dependencies {
        classpath("com.google.gms:google-services:4.4.0") // add this if missing
    }
}