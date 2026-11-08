package com.david.homeenv

import android.content.Context
import android.content.Intent
import com.david.homeenv.HistoryActivity.Metric

/**
 * Which screen an alert is about: by its event type (`events.type`) for a row in the
 * notification history (#321), by the push's `data.event` for a tap on the push itself (#354).
 * Both in contracts/notifications.md.
 */
object Screens {

    fun forEventType(ctx: Context, type: String): Intent {
        // a reading over time: History at its chart (#329); a baseline alert (any metric) at the top
        historyMetric(type)?.let { return HistoryActivity.open(ctx, it) }
        return when {
            type.startsWith("BASELINE_") -> HistoryActivity.open(ctx)
            // a node, one of its sensors, its radar, or the server (its card is on Devices, #325)
            listOf("NODE_", "SENSOR_", "RADAR_", "WATCHDOG_").any { type.startsWith(it) } ->
                Intent(ctx, DevicesActivity::class.java)
            // air, windows, occupancy: their cards on Home
            else -> home(ctx)
        }
    }

    /**
     * The screen for a push's `data.event` (#354), or null for Home. Not the same names as the
     * event types: a push carries e.g. "TEMP" or "WATCHDOG" (one notification slot per kind).
     */
    fun forPushEvent(ctx: Context, event: String?): Intent? = when (event) {
        "TEMP" -> HistoryActivity.open(ctx, Metric.TEMPERATURE)
        "HUMIDITY_HIGH", "HUMIDITY_LOW" -> HistoryActivity.open(ctx, Metric.HUMIDITY)
        "PRESSURE" -> HistoryActivity.open(ctx, Metric.PRESSURE)
        "FRIDGE_ALERT", "FRIDGE_DOOR" -> HistoryActivity.open(ctx, Metric.FRIDGE)
        "BASELINE" -> HistoryActivity.open(ctx)
        "NODE_STATUS", "SENSOR_STATUS", "WATCHDOG", "RADAR_DISCONNECTED" -> Intent(ctx, DevicesActivity::class.java)
        else -> null   // OCCUPIED, AIR and anything new: Home
    }

    /** Home: it may already be open under the other screens, so go back to it, not a second one. */
    fun home(ctx: Context): Intent = Intent(ctx, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)

    private fun historyMetric(type: String): Metric? = when {
        type.startsWith("TEMP_") -> Metric.TEMPERATURE
        type.startsWith("HUMIDITY_") -> Metric.HUMIDITY
        type.startsWith("PRESSURE_") -> Metric.PRESSURE
        type.startsWith("FRIDGE_") -> Metric.FRIDGE   // alert, recovery and door, #332
        else -> null
    }
}
