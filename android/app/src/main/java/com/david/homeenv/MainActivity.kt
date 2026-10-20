package com.david.homeenv

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.firebase.Firebase
import com.google.firebase.messaging.messaging
import com.david.homeenv.BuildConfig
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        findViewById<android.view.View>(R.id.notification_settings).setOnClickListener {
            startActivity(android.content.Intent(this, NotificationSettingsActivity::class.java))
        }

        // Push notifications (off in the preview build, which has no Firebase config)
        if (BuildConfig.PUSH_ENABLED) setupPush()
    }

    private fun setupPush() {
        // Firebase itself is initialised in HomeEnvApp
        // FID-based registration (#153; replaces the deprecated registration tokens). The
        // backend only sends to topic "env", so the app needs no token or ID of its own:
        // it registers and subscribes itself. Both are cheap when already done.
        Firebase.messaging.register()
            .addOnSuccessListener { Log.i(TAG, "FCM registration OK") }
            .addOnFailureListener { Log.w(TAG, "FCM registration failed: $it") }
        Firebase.messaging.subscribeToTopic("env")
            .addOnCompleteListener { task ->
                Log.i(TAG, "Subscribed to topic 'env': ${task.isSuccessful}")
            }

        // Android 13+: notifications are only shown with this permission
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            Log.i(TAG, "Notification permission granted: $granted")
        }
}
