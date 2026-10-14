package com.david.homeenv

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.SystemClock
import android.util.Base64
import android.widget.RemoteViews
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.util.Locale
import kotlin.concurrent.thread
import com.david.homeenv.BuildConfig

class HomeEnvWidget : AppWidgetProvider() {

    companion object {
        private val DATA_URL = "${BuildConfig.API_BASE_URL}/api/latest"
        private val AUTH = "${BuildConfig.API_USER}:${BuildConfig.API_PASS}"
        private const val ACTION_AUTO_UPDATE = "com.david.homeenv.AUTO_UPDATE"
        private const val UPDATE_INTERVAL_MS = 2 * 60 * 1000L // 2 minutes
    }

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray
    ) {
        appWidgetIds.forEach { widgetId ->
            updateWidget(context, appWidgetManager, widgetId)
        }

        // Always reschedule after update
        scheduleNextUpdate(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)

        if (intent.action == ACTION_AUTO_UPDATE) {
            val appWidgetManager = AppWidgetManager.getInstance(context)
            val componentName = ComponentName(context, HomeEnvWidget::class.java)
            val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

            if (appWidgetIds.isNotEmpty()) {
                onUpdate(context, appWidgetManager, appWidgetIds)
            }
        }
    }

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        scheduleNextUpdate(context)
    }

    override fun onDisabled(context: Context) {
        super.onDisabled(context)
        cancelUpdate(context)
    }

    private fun scheduleNextUpdate(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = Intent(context, HomeEnvWidget::class.java).apply {
            action = ACTION_AUTO_UPDATE
        }
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val triggerAtMillis = SystemClock.elapsedRealtime() + UPDATE_INTERVAL_MS

        try {
            when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
                    // Android 12+ - check if we can schedule exact alarms
                    if (alarmManager.canScheduleExactAlarms()) {
                        alarmManager.setExactAndAllowWhileIdle(
                            AlarmManager.ELAPSED_REALTIME_WAKEUP,
                            triggerAtMillis,
                            pendingIntent
                        )
                    } else {
                        // Fallback to inexact alarm
                        alarmManager.setAndAllowWhileIdle(
                            AlarmManager.ELAPSED_REALTIME_WAKEUP,
                            triggerAtMillis,
                            pendingIntent
                        )
                    }
                }
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M -> {
                    // Android 6-11
                    alarmManager.setExactAndAllowWhileIdle(
                        AlarmManager.ELAPSED_REALTIME_WAKEUP,
                        triggerAtMillis,
                        pendingIntent
                    )
                }
                else -> {
                    // Android 5 and below
                    alarmManager.setExact(
                        AlarmManager.ELAPSED_REALTIME_WAKEUP,
                        triggerAtMillis,
                        pendingIntent
                    )
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
            // Last resort - try setAndAllowWhileIdle
            try {
                alarmManager.setAndAllowWhileIdle(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    triggerAtMillis,
                    pendingIntent
                )
            } catch (e2: Exception) {
                e2.printStackTrace()
            }
        }
    }

    private fun cancelUpdate(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = Intent(context, HomeEnvWidget::class.java).apply {
            action = ACTION_AUTO_UPDATE
        }
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        alarmManager.cancel(pendingIntent)
    }

    private fun updateWidget(
        context: Context,
        manager: AppWidgetManager,
        widgetId: Int
    ) {
        val views = RemoteViews(context.packageName, R.layout.widget_homeenv)

        // SAFE placeholders (must never crash)
        views.setTextViewText(R.id.widget_temp, "-- °C")
        views.setTextViewText(R.id.widget_humidity, "-- %")
        views.setTextViewText(R.id.widget_occupancy, "No")
        views.setTextViewText(R.id.widget_confidence, "-- %")
        views.setTextViewText(R.id.widget_online, "Off")

        // Set default colors
        views.setInt(R.id.widget_occupancy_icon, "setColorFilter", Color.GRAY)
        views.setInt(R.id.widget_online_icon, "setColorFilter", Color.RED)

        // Click = refresh widget
        val intent = Intent(context, HomeEnvWidget::class.java).apply {
            action = AppWidgetManager.ACTION_APPWIDGET_UPDATE
            putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, intArrayOf(widgetId))
        }

        val pendingIntent = PendingIntent.getBroadcast(
            context,
            widgetId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        views.setOnClickPendingIntent(R.id.widget_root, pendingIntent)

        manager.updateAppWidget(widgetId, views)

        // Fetch data async
        thread {
            try {
                val client = OkHttpClient()

                val authHeader = "Basic " + Base64.encodeToString(
                    AUTH.toByteArray(),
                    Base64.NO_WRAP
                )

                val request = Request.Builder()
                    .url(DATA_URL)
                    .addHeader("Authorization", authHeader)
                    .build()

                val response = client.newCall(request).execute()
                if (!response.isSuccessful) return@thread

                val body = response.body?.string() ?: return@thread
                val json = JSONArray(body)

                val temp = json.optDouble(1)
                val humidity = json.optDouble(2)
                val occupancy = json.optInt(4)  // Index 4 = occupancy (0 or 1)
                val confidence = json.optDouble(5)  // Index 5 = confidence %
                val online = json.optBoolean(7)

                val updated = RemoteViews(context.packageName, R.layout.widget_homeenv)

                // Update all values
                updated.setTextViewText(
                    R.id.widget_temp,
                    String.format(Locale.US, "%.1f °C", temp)
                )

                updated.setTextViewText(
                    R.id.widget_humidity,
                    String.format(Locale.US, "%.0f %%", humidity)
                )

                // Occupancy detection (Yes/No)
                updated.setTextViewText(
                    R.id.widget_occupancy,
                    if (occupancy == 1) "Yes" else "No"
                )

                // Set occupancy icon color (green if occupied, gray if not)
                updated.setInt(
                    R.id.widget_occupancy_icon,
                    "setColorFilter",
                    if (occupancy == 1) Color.GREEN else Color.GRAY
                )

                // Confidence percentage
                updated.setTextViewText(
                    R.id.widget_confidence,
                    String.format(Locale.US, "%.0f %%", confidence)
                )

                updated.setTextViewText(
                    R.id.widget_online,
                    if (online) "On" else "Off"
                )

                // Set icon color based on online status
                updated.setInt(
                    R.id.widget_online_icon,
                    "setColorFilter",
                    if (online) Color.GREEN else Color.RED
                )

                // Re-attach click listener
                updated.setOnClickPendingIntent(R.id.widget_root, pendingIntent)

                manager.updateAppWidget(widgetId, updated)

            } catch (_: Exception) {
                // swallow exceptions – widgets must NEVER crash
            }
        }
    }
}