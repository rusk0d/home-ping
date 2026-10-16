package com.david.homeenv

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.widget.RemoteViews
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import com.david.homeenv.ui.WidgetGauge
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread
import com.david.homeenv.BuildConfig

class HomeEnvWidget : AppWidgetProvider() {

    companion object {
        private const val TAG = "HomeEnvWidget"
        private const val ACTION_AUTO_UPDATE = "com.david.homeenv.AUTO_UPDATE"
        private const val UPDATE_INTERVAL_MS = 2 * 60 * 1000L // 2 minutes
        private const val FETCH_TIMEOUT_S = 8L  // stay well inside goAsync's ~10 s budget
        private const val GAUGE_GAP_DP = 12     // layout: marginStart between the metric columns
        private const val CO2_GAUGE_HEIGHT_DP = 10f   // layout: w_co2_gauge height (thinner than the 16dp ones)
    }

    // Fetches queued by updateWidget during this broadcast; run in onReceive under goAsync().
    // (A new receiver instance is created for every broadcast, so this is per broadcast.)
    private val fetches = mutableListOf<() -> Unit>()

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

        // AUTO_UPDATE: our 2-minute alarm. MY_PACKAGE_REPLACED: the app was just updated,
        // which clears our alarms and resets the widget to its initial layout (no data,
        // no click handlers), so refresh it and restart the alarm chain right away.
        if (intent.action == ACTION_AUTO_UPDATE || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            val appWidgetManager = AppWidgetManager.getInstance(context)
            val componentName = ComponentName(context, HomeEnvWidget::class.java)
            val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

            if (appWidgetIds.isNotEmpty()) {
                onUpdate(context, appWidgetManager, appWidgetIds)
            }
        }

        // Keep the broadcast (and so the process) alive until the fetches are done;
        // otherwise Android may freeze or kill the app as soon as onReceive returns.
        if (fetches.isNotEmpty()) {
            val pending = goAsync()
            val queued = fetches.toList()
            fetches.clear()
            thread {
                try {
                    queued.forEach { it() }
                } finally {
                    pending.finish()
                }
            }
        }
    }

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle
    ) {
        // Resized: redraw the gauges at the new width
        updateWidget(context, appWidgetManager, appWidgetId)
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

        // Exact when allowed: always below Android 12, on 12+ with the exact-alarm
        // permission. Otherwise inexact. Both still fire while the phone is idle.
        val exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarmManager.canScheduleExactAlarms()
        try {
            if (exact) {
                alarmManager.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAtMillis, pendingIntent)
            } else {
                alarmManager.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAtMillis, pendingIntent)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Exact alarm failed, using an inexact one: $e")
            // Last resort (e.g. the exact-alarm permission was revoked in between)
            try {
                alarmManager.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAtMillis, pendingIntent)
            } catch (e2: Exception) {
                Log.w(TAG, "Couldn't schedule the widget update: $e2")
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
        // Refresh button = refresh widget
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

        // Tap anywhere else = open the app
        val openApp = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        fun RemoteViews.attachClicks() {
            setOnClickPendingIntent(R.id.widget_root, openApp)
            setOnClickPendingIntent(R.id.w_refresh, pendingIntent)
        }

        // Keep the last values on screen while fetching; only show that it's refreshing
        val refreshing = RemoteViews(context.packageName, R.layout.widget_homeenv)
        refreshing.setTextViewText(R.id.w_updated, "updating…")
        refreshing.attachClicks()
        manager.partiallyUpdateAppWidget(widgetId, refreshing)

        // Fetch data async (run by onReceive under goAsync)
        fetches += {
            val fresh = try {
                // CO2 comes from /api/air; fetched alongside so both fit in the time budget
                var air: JSONObject? = null
                val airFetch = thread { air = Api.getObject("/api/air", FETCH_TIMEOUT_S) }
                val latest = Api.getObject("/api/latest", FETCH_TIMEOUT_S)
                airFetch.join(FETCH_TIMEOUT_S * 1000)
                latest?.let { render(context, manager, widgetId, it, air) }
            } catch (_: Exception) {
                null // widgets must NEVER crash
            }

            try {
                if (fresh != null) {
                    fresh.attachClicks()
                    manager.updateAppWidget(widgetId, fresh)
                } else {
                    val views = offline(context)
                    views.attachClicks()
                    manager.partiallyUpdateAppWidget(widgetId, views)
                }
            } catch (_: Exception) {
            }
        }
    }

    /**
     * Full widget from the /api/latest object, plus CO2 from /api/air ([air] null = not
     * available; see contracts/api.md). Fields can be null.
     */
    private fun render(context: Context, manager: AppWidgetManager, widgetId: Int, json: JSONObject, air: JSONObject?): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_homeenv)
        val gaugeWidth = gaugeWidthPx(context, manager, widgetId)

        val temp = json.optNum("temp")
        val humidity = json.optNum("humidity")
        val occupied = json.optNum("occupied")?.let { it == 1.0 }   // null = no radar data
        val confidence = json.optNum("confidence")                  // %, null = no radar data
        val online = json.optBoolean("online")

        views.setTextViewText(R.id.w_temp_value, temp?.let { String.format(Locale.US, "%.1f", it) } ?: "--")
        setLevel(context, views, R.id.w_temp_level, temp?.let { Levels.temperature(it) })
        views.setImageViewBitmap(R.id.w_temp_gauge, WidgetGauge.render(context, Levels.TEMPERATURE, temp, gaugeWidth))

        views.setTextViewText(R.id.w_hum_value, humidity?.let { String.format(Locale.US, "%.0f", it) } ?: "--")
        setLevel(context, views, R.id.w_hum_level, humidity?.let { Levels.humidity(it) })
        views.setImageViewBitmap(R.id.w_hum_gauge, WidgetGauge.render(context, Levels.HUMIDITY, humidity, gaugeWidth))

        if (occupied == null) {
            views.setTextViewText(R.id.w_occ_value, "--")
            views.setTextViewText(R.id.w_occ_level, " ")
        } else {
            views.setTextViewText(R.id.w_occ_value, if (occupied) "Yes" else "No")
            val pct = confidence ?: 0.0
            views.setTextViewText(R.id.w_occ_level, String.format(Locale.US, "conf. %.0f%%", pct))
            // Grey text below 20 % (the faint bar colour is too pale for text)
            val color = if (pct < Levels.OCCUPANCY.stops[0]) R.color.muted else Levels.occupancy(pct)
            views.setTextColor(R.id.w_occ_level, Levels.color(context, color))
        }
        views.setImageViewBitmap(R.id.w_occ_gauge, WidgetGauge.render(context, Levels.OCCUPANCY, confidence, gaugeWidth))

        // CO2: one thin gauge as wide as the three above (their widths plus the two gaps)
        val co2 = air?.optNum("co2")
        views.setTextViewText(R.id.w_co2_value, co2?.let { String.format(Locale.US, "%.0f", it) } ?: "--")
        // Short level text ("Stuffy", not "Stuffy – open a window"): the row is narrow
        setLevel(context, views, R.id.w_co2_level, co2?.let { Levels.co2(it.toInt()).let { l -> l.copy(text = l.text.substringBefore(" –")) } })
        val co2Width = gaugeWidth * 3 + (2 * GAUGE_GAP_DP * context.resources.displayMetrics.density).toInt()
        views.setImageViewBitmap(R.id.w_co2_gauge, WidgetGauge.render(context, Levels.CO2, co2, co2Width, CO2_GAUGE_HEIGHT_DP))

        // Online = the sensor node reported within the backend's heartbeat timeout
        views.setInt(R.id.w_online_dot, "setColorFilter", Levels.color(context, if (online) R.color.good else R.color.bad))
        views.setTextViewText(R.id.w_online, if (online) "Sensors online" else "Sensors offline")
        views.setTextColor(R.id.w_online, Levels.color(context, if (online) R.color.muted else R.color.bad))
        views.setTextViewText(R.id.w_updated, "updated " + SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date()))
        return views
    }

    /** Server unreachable: keep the values, flag the status line. */
    private fun offline(context: Context): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_homeenv)
        views.setInt(R.id.w_online_dot, "setColorFilter", Levels.color(context, R.color.bad))
        views.setTextViewText(R.id.w_online, "Server unreachable")
        views.setTextColor(R.id.w_online, Levels.color(context, R.color.bad))
        views.setTextViewText(R.id.w_updated, "tried " + SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date()))
        return views
    }

    private fun setLevel(context: Context, views: RemoteViews, id: Int, level: Level?) {
        views.setTextViewText(id, level?.text ?: " ")
        if (level != null) views.setTextColor(id, Levels.color(context, level.color))
    }

    /** One gauge's width: a third of the widget minus its padding (14dp) and gaps (12dp). */
    private fun gaugeWidthPx(context: Context, manager: AppWidgetManager, widgetId: Int): Int {
        val options = manager.getAppWidgetOptions(widgetId)
        val landscape = context.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val widthDp = options.getInt(
            if (landscape) AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH else AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH
        ).takeIf { it > 0 } ?: 250
        val d = context.resources.displayMetrics.density
        return (((widthDp - 2 * 14 - 2 * GAUGE_GAP_DP) / 3f) * d).toInt()
    }
}
