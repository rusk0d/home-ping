package com.david.homeenv

import android.content.Context

/**
 * The home point for the location auto-arm of the occupancy alert (#239). Kept only on this
 * phone (its own preferences): the coordinates never go to the server or into the repo.
 * [manualOverride] (#240) is true after a manual flip of the switch, until the next
 * home/away transition.
 */
object HomeLocation {
    private const val PREFS = "home"

    // Metres. Small enough to mean "at home", big enough that the phone disarms before the
    // radar sees the arrival at the door (the arrival alert waits for movement after quiet)
    const val RADIUS_M = 250f

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The home point as (lat, lng), or null when none is set. */
    fun get(ctx: Context): Pair<Double, Double>? {
        val p = prefs(ctx)
        if (!p.contains("lat") || !p.contains("lng")) return null
        return p.getFloat("lat", 0f).toDouble() to p.getFloat("lng", 0f).toDouble()
    }

    fun set(ctx: Context, lat: Double, lng: Double) {
        prefs(ctx).edit().putFloat("lat", lat.toFloat()).putFloat("lng", lng.toFloat()).apply()
    }

    fun autoEnabled(ctx: Context): Boolean = prefs(ctx).getBoolean("auto", false)

    fun setAutoEnabled(ctx: Context, on: Boolean) {
        prefs(ctx).edit().putBoolean("auto", on).apply()
    }

    fun manualOverride(ctx: Context): Boolean = prefs(ctx).getBoolean("manual", false)

    fun setManualOverride(ctx: Context, on: Boolean) {
        prefs(ctx).edit().putBoolean("manual", on).apply()
    }
}
