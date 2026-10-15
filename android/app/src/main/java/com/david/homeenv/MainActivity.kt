package com.david.homeenv

import android.Manifest
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.david.homeenv.BuildConfig
class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

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
            e.printStackTrace()
        }
    }

    private fun setupPush() {
        if (FirebaseApp.getApps(this).isEmpty()) {
            FirebaseApp.initializeApp(this)
        }

        // Subscribe to topic for notifications
        FirebaseMessaging.getInstance().subscribeToTopic("env")
            .addOnCompleteListener { task ->
                println("Subscribed to topic 'env': ${task.isSuccessful}")
            }

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            when {
                ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED -> getAndRegisterToken()
                else -> requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        } else {
            getAndRegisterToken()
        }
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) getAndRegisterToken()
        }

    private fun getAndRegisterToken() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                println("FCM token: $token")
                Thread { PushRegistration.register(applicationContext, token) }.start()
            }
            .addOnFailureListener {
                it.printStackTrace()
            }
    }
}
