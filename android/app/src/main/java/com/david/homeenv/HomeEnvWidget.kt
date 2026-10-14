package com.david.homeenv

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.util.Base64
import android.util.SizeF
import android.widget.RemoteViews
import okhttp3.OkHttpClient
import okhttp3.Request
import com.david.homeenv.ui.Levels
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread
import com.david.homeenv.BuildConfig

class HomeEnvWidget : AppWidgetProvider() {

    companion object {
        private val DATA_URL = "${BuildConfig.API_BASE_URL}/api/latest_all"
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

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle
    ) {
        // Resized: pick the matching layout (needed below Android 12)
        updateWidget(context, appWidgetManager, appWidgetId)
    }

    /** What the widget shows; null fields render as "--". */
    private class WidgetData(
        val temp: Double?,
        val humidity: Double?,
        val co2: Int?,
        val co2Seen: Boolean,
        val occupied: Boolean?,
        val confidence: Int?,
        val online: Boolean?,
        val ts: Date?,
    )

    private fun updateWidget(
        context: Context,
        manager: AppWidgetManager,
        widgetId: Int
    ) {
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

        // Fetch data async; keep the current content until it arrives
        thread {
            try {
                val data = fetch() ?: return@thread
                manager.updateAppWidget(widgetId, buildViews(context, manager, widgetId, data, pendingIntent))
            } catch (_: Exception) {
                // swallow exceptions – widgets must NEVER crash
            }
        }
    }

    private fun fetch(): WidgetData? {
        val authHeader = "Basic " + Base64.encodeToString(AUTH.toByteArray(), Base64.NO_WRAP)
        val request = Request.Builder().url(DATA_URL).addHeader("Authorization", authHeader).build()
        val body = OkHttpClient().newCall(request).execute().use {
            if (it.isSuccessful) it.body?.string() else null
        } ?: return null
        val json = JSONObject(body)

        val main = json.optJSONObject("home_ping_esp32")
        val co2 = json.optJSONObject("co2_sensor_esp32")
        val combined = json.optJSONObject("combined")
        return WidgetData(
            temp = main?.num("temp"),
            humidity = main?.num("humidity"),
            co2 = co2?.num("co2")?.toInt(),
            co2Seen = co2 != null,
            occupied = combined?.let { it.optInt("occupied") == 1 },
            confidence = combined?.optInt("confidence"),
            online = main?.takeIf { it.has("online") }?.optBoolean("online"),
            ts = main?.optString("ts")?.let {
                try { SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(it) } catch (_: Exception) { null }
            },
        )
    }

    private fun buildViews(
        context: Context,
        manager: AppWidgetManager,
        widgetId: Int,
        data: WidgetData,
        click: PendingIntent
    ): RemoteViews {
        val small = render(context, R.layout.widget_small, data, large = false, click)
        val large = render(context, R.layout.widget_large, data, large = true, click)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            // Android 12+: the launcher picks the layout that fits the current size
            RemoteViews(mapOf(SizeF(180f, 40f) to small, SizeF(180f, 110f) to large))
        } else {
            val maxHeight = manager.getAppWidgetOptions(widgetId)
                .getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0)
            if (maxHeight >= 110) large else small
        }
    }

    private fun render(
        context: Context,
        layout: Int,
        d: WidgetData,
        large: Boolean,
        click: PendingIntent
    ): RemoteViews {
        val v = RemoteViews(context.packageName, layout)
        fun color(res: Int) = context.getColor(res)

        // Temperature / humidity: value + level (coloured)
        v.setTextViewText(R.id.w_temp_value, d.temp?.let { "%.1f".format(Locale.US, it) + if (large) "" else "°" } ?: "--")
        val tempLevel = d.temp?.let(Levels::temperature)
        v.setTextViewText(R.id.w_temp_level, tempLevel?.text ?: if (large) " " else "temp")
        v.setTextColor(R.id.w_temp_level, color(tempLevel?.color ?: R.color.muted))

        v.setTextViewText(R.id.w_hum_value, d.humidity?.let { "%.0f".format(Locale.US, it) + if (large) "" else "%" } ?: "--")
        val humLevel = d.humidity?.let(Levels::humidity)
        v.setTextViewText(R.id.w_hum_level, humLevel?.text ?: if (large) " " else "humidity")
        v.setTextColor(R.id.w_hum_level, color(humLevel?.color ?: R.color.muted))

        // CO2: value coloured by level; short level names so they fit
        val co2Level = d.co2?.let(Levels::co2)
        v.setTextViewText(R.id.w_co2_value, d.co2?.toString() ?: "--")
        v.setTextColor(R.id.w_co2_value, color(co2Level?.color ?: R.color.text))
        v.setTextViewText(R.id.w_co2_level, when {
            co2Level != null -> co2Level.text.substringBefore(" –") + if (large) "" else " · ppm"
            d.co2Seen -> "no reading"
            else -> if (large) " " else "CO₂ ppm"
        })
        v.setTextColor(R.id.w_co2_level, color(co2Level?.color ?: R.color.muted))

        // Occupancy dot + text, and the status line (confidence / age / offline)
        v.setInt(R.id.w_occ_dot, "setColorFilter", color(when (d.occupied) {
            true -> R.color.good
            false -> R.color.faint
            null -> R.color.faint
        }))
        val occText = when (d.occupied) { true -> "Occupied"; false -> "Vacant"; null -> "--" }
        val age = ageText(d.ts)
        if (large) {
            v.setTextViewText(R.id.w_occ_value, d.confidence?.let { "$occText · $it% confidence" } ?: occText)
            v.setTextViewText(R.id.w_status, if (d.online == false) "main node offline" else age)
        } else {
            v.setTextViewText(R.id.w_occ_value, occText)
            v.setTextViewText(R.id.w_status, when {
                d.online == false -> "offline"
                d.confidence != null -> "${d.confidence}% · $age"
                else -> age
            })
        }
        v.setTextColor(R.id.w_status, color(if (d.online == false) R.color.bad else R.color.muted))

        v.setOnClickPendingIntent(R.id.widget_root, click)
        return v
    }

    private fun ageText(ts: Date?): String {
        if (ts == null) return "--"
        val s = (System.currentTimeMillis() - ts.time) / 1000
        return when {
            s < 60 -> "just now"
            s < 3600 -> "${s / 60}m ago"
            else -> "${s / 3600}h ago"
        }
    }

    private fun JSONObject.num(key: String): Double? =
        if (has(key) && !isNull(key)) optDouble(key).takeIf { !it.isNaN() } else null
}
