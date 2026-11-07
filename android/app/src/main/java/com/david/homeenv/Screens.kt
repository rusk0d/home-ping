package com.david.homeenv

import android.content.Context
import android.content.Intent

/** Which screen an alert is about (#321), by its event type (`events.type`, contracts/notifications.md). */
object Screens {

    fun forEventType(ctx: Context, type: String): Intent {
        // a reading over time: History at its chart (#329); a baseline alert (any metric) at the top
        historyMetric(type)?.let { return HistoryActivity.open(ctx, it) }
        val screen = when {
            // a reading over time: the charts
            listOf("HUMIDITY_", "TEMP_", "PRESSURE_", "BASELINE_").any { type.startsWith(it) } -> HistoryActivity::class.java
            // a node, one of its sensors or its radar
            listOf("NODE_", "SENSOR_", "RADAR_").any { type.startsWith(it) } -> DevicesActivity::class.java
            // air, windows, fridge, occupancy, server watchdog: their cards on Home
            else -> MainActivity::class.java
        }
        return Intent(ctx, screen).apply {
            // Home is already open under the notification screens: go back to it, not a second one
            if (screen == MainActivity::class.java) flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
    }

    private fun historyMetric(type: String): HistoryActivity.Metric? = when {
        type.startsWith("TEMP_") -> HistoryActivity.Metric.TEMPERATURE
        type.startsWith("HUMIDITY_") -> HistoryActivity.Metric.HUMIDITY
        type.startsWith("PRESSURE_") -> HistoryActivity.Metric.PRESSURE
        else -> null
    }
}
