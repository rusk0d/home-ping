plugins {
    // AGP 9 compiles Kotlin itself; the separate Kotlin plugin is gone (#125)
    id("com.android.application") version "9.5.0" apply false
    id("com.google.gms.google-services") version "4.5.0" apply false
}