package com.david.homeenv

import android.Manifest
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.firebase.Firebase
import com.google.firebase.FirebaseApp
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

        askForExactAlarmsOnce()

        // Push notifications (off in the preview build, which has no Firebase config)
        if (BuildConfig.PUSH_ENABLED) setupPush()
    }

    /**
     * The widget's 2-minute alarm is exact only with this permission (Android 12+;
     * sideloaded builds usually get USE_EXACT_ALARM anyway). Open the settings screen
     * at most once, and only when a widget is placed: it used to open on every launch.
     */
    private fun askForExactAlarmsOnce() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val alarmManager = getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
        if (alarmManager.canScheduleExactAlarms()) return
        val widgets = AppWidgetManager.getInstance(this)
            .getAppWidgetIds(ComponentName(this, HomeEnvWidget::class.java))
        if (widgets.isEmpty()) return
        val prefs = getSharedPreferences("ui", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked_exact_alarms", false)) return
        prefs.edit().putBoolean("asked_exact_alarms", true).apply()
        try {
            startActivity(android.content.Intent(android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM))
        } catch (e: Exception) {
            Log.w(TAG, "Can't open the exact-alarm settings: $e")
        }
    }

    private fun setupPush() {
        if (FirebaseApp.getApps(this).isEmpty()) {
            FirebaseApp.initializeApp(this)
        }

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
