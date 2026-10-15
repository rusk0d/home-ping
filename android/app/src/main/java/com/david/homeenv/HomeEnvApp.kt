package com.david.homeenv

import android.app.Application
import com.google.firebase.FirebaseApp

class HomeEnvApp : Application() {

    override fun onCreate() {
        super.onCreate()
        // Initialize Firebase once for the entire app (not in the preview build)
        if (BuildConfig.PUSH_ENABLED) {
            FirebaseApp.initializeApp(this)
            // Channels must exist before FCM shows a message while the app is in the background
            MyFirebaseMessagingService.createChannels(this)
        }
    }
}
