package com.david.homeenv.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.core.content.ContextCompat
import com.david.homeenv.R
import org.json.JSONObject
import java.util.Locale
import kotlin.math.ln

/** A level label and its colour, e.g. "Comfortable" / good. */
data class Level(val text: String, @param:ColorRes val color: Int)

/**
 * A banded scale: the value range a gauge shows, the band stops, and the colour of each
 * band (one more colour than stops). Used by the chip thresholds below, the CO₂ and fridge
 * cards' gauges and the widget gauges.
 */
class Scale(val min: Double, val max: Double, val stops: List<Double>, val colors: List<Int>) {   // colour resource IDs
    init { require(colors.size == stops.size + 1) }

    /** Position of [value] on the gauge, 0..1. */
    fun fraction(value: Double) = ((value - min) / (max - min)).coerceIn(0.0, 1.0)

    /** Where each band starts on the gauge, 0..1 (the first one at 0). */
    val bandStarts: List<Float> get() = listOf(0f) + stops.map { fraction(it).toFloat() }
}

/**
 * The fridge node's alert limits (#417): `limits` on /api/latest_all's fridge entry, which the
 * node publishes itself (contracts/mqtt.md), so the gauge, the history bands and the card's
 * texts follow a flash without an app update. [DEFAULT] until the backend serves them.
 */
data class FridgeLimits(val cold: Double, val warm: Double, val hot: Double, val minutes: Int, val longMinutes: Int) {
    /**
     * The fridge gauge and history bands (#326): too cold below [cold], fine up to [warm],
     * warming up to [hot], then too warm. −2…10 °C, wider if a limit is near an end. The chip
     * comes from the node's `alert` ([Levels.fridge]), which also needs [minutes] past a limit.
     */
    val scale: Scale
        get() = Scale(minOf(-2.0, cold - 1), maxOf(10.0, hot + 2), listOf(cold, warm, hot),
            listOf(R.color.accent_hum, R.color.good, R.color.poor, R.color.bad))

    /** The card's status line for the node's [alert], e.g. "above 7 °C for 30+ min". */
    fun status(alert: String): String = when (alert) {
        "" -> "normal"
        "cold" -> "below ${num(cold)} °C for $minutes+ min"
        "warm_low" -> "above ${num(warm)} °C for $minutes+ min"
        "warm_critical" -> "above ${num(hot)} °C for $minutes+ min"
        "warm_critical_duration" -> "above ${num(warm)} °C for ${num(longMinutes / 60.0)}+ h"
        else -> alert
    }

    companion object {
        val DEFAULT = FridgeLimits(0.0, 7.0, 8.0, 30, 120)

        /** The newest limits Home has seen, for the history screen's bands. */
        @Volatile var current: FridgeLimits = DEFAULT

        /** From the API's `limits`; null when missing or not a sane set (cold < warm ≤ hot). */
        fun from(o: JSONObject?): FridgeLimits? {
            if (o == null) return null
            fun d(key: String) = if (o.isNull(key)) null else o.optDouble(key).takeIf { it.isFinite() }
            val cold = d("cold") ?: return null
            val warm = d("warm") ?: return null
            val hot = d("hot") ?: return null
            val minutes = d("minutes")?.toInt() ?: return null
            val long = d("long_minutes")?.toInt() ?: return null
            if (!(cold < warm && warm <= hot && minutes > 0 && long >= minutes)) return null
            return FridgeLimits(cold, warm, hot, minutes, long)
        }

        /** 7.0 -> "7", -0.5 -> "-0.5". */
        fun num(v: Double): String = if (v % 1.0 == 0.0) v.toLong().toString() else "%.1f".format(Locale.US, v)
    }
}

/**
 * Level thresholds. The bands are defined in contracts/api.md ("Level bands"); change them
 * there first, then here, in web/index.html (co2Level, humLevel, tempLevel) and, for CO₂,
 * in the backend's /api/air `band`.
 */
object Levels {
    // Gauge ranges, band stops and band colours. The chips use the same stops.
    val TEMPERATURE = Scale(14.0, 30.0, listOf(18.0, 20.0, 24.0, 27.0),
        listOf(R.color.accent_hum, R.color.ok, R.color.good, R.color.ok, R.color.bad))
    val HUMIDITY = Scale(0.0, 100.0, listOf(30.0, 40.0, 60.0, 70.0),
        listOf(R.color.poor, R.color.ok, R.color.good, R.color.ok, R.color.poor))
    val CO2 = Scale(400.0, 3000.0, listOf(800.0, 1200.0, 2000.0),
        listOf(R.color.good, R.color.ok, R.color.poor, R.color.bad))
    // The fridge's gauge comes from the node's own limits: [FridgeLimits.scale] (#417)
    /** Occupancy confidence / hourly occupancy, % (see [occupancy]). */
    val OCCUPANCY = Scale(0.0, 100.0, listOf(20.0, 60.0),
        listOf(R.color.muted, R.color.ok, R.color.good))

    fun co2(ppm: Int): Level {
        val (fresh, ok, stuffy) = CO2.stops
        return when {
            ppm < fresh -> Level("Fresh", R.color.good)
            ppm < ok -> Level("OK", R.color.ok)
            ppm < stuffy -> Level("Stuffy – open a window", R.color.poor)
            else -> Level("Poor – ventilate now", R.color.bad)
        }
    }

    /**
     * CO₂ level from the `band` that /api/air returns (#127), so the server decides the
     * band. Null for an unknown or missing band: callers then fall back to [co2].
     */
    fun co2Band(band: String?): Level? = when (band) {
        "good" -> Level("Fresh", R.color.good)
        "fair" -> Level("OK", R.color.ok)
        "stuffy" -> Level("Stuffy – open a window", R.color.poor)
        "bad" -> Level("Poor – ventilate now", R.color.bad)
        else -> null
    }

    fun humidity(h: Double): Level {
        val (veryDry, dry, comfortable, humid) = HUMIDITY.stops
        return when {
            h < veryDry -> Level("Very dry", R.color.poor)
            h < dry -> Level("Dry", R.color.ok)
            h <= comfortable -> Level("Comfortable", R.color.good)
            h <= humid -> Level("Humid", R.color.ok)
            else -> Level("Very humid", R.color.poor)
        }
    }

    fun temperature(t: Double): Level {
        val (cold, cool, comfortable, warm) = TEMPERATURE.stops
        return when {
            t < cold -> Level("Cold", R.color.accent_hum)
            t < cool -> Level("Cool", R.color.ok)
            t <= comfortable -> Level("Comfortable", R.color.good)
            t <= warm -> Level("Warm", R.color.ok)
            else -> Level("Hot", R.color.bad)
        }
    }

    /** Fridge chip from the node's own `alert` state (#290, #296), not the room temperature scale. */
    fun fridge(alert: String): Level = when (alert) {
        "" -> Level("OK", R.color.good)
        "warm_low" -> Level("Warming", R.color.poor)
        "warm_critical", "warm_critical_duration" -> Level("Too warm", R.color.bad)
        "cold" -> Level("Too cold", R.color.poor)
        else -> Level("Alert", R.color.poor)
    }

    /** Humidity chip while /api/air reports `mould_risk` (#177; contracts/api.md). */
    val MOULD_RISK = Level("Mould risk", R.color.poor)

    /**
     * Dew point in °C from temperature (°C) and relative humidity (%), Magnus formula with
     * the Sonntag constants (contracts/api.md, "Dew point"; web: dewPoint). Null for a
     * humidity of 0 or less.
     */
    fun dewPoint(tempC: Double, rh: Double): Double? {
        if (rh <= 0) return null
        val g = ln(rh / 100) + 17.62 * tempC / (243.12 + tempC)
        return 243.12 * g / (17.62 - g)
    }

    /** 3 h pressure change in hPa → outlook (kept from the old Android screen). */
    fun pressureTrend(delta: Double) = when {
        delta <= -3.0 -> Level("Storm likely", R.color.bad)
        delta <= -1.0 -> Level("Falling", R.color.ok)
        delta < 1.0 -> Level("Steady", R.color.good)
        delta < 3.0 -> Level("Rising", R.color.good)
        else -> Level("Clearing fast", R.color.accent_hum)
    }

    /** Hourly occupancy % → bar colour (web: occColor). */
    @ColorRes
    fun occupancy(pct: Double) = when {
        pct >= OCCUPANCY.stops[1] -> R.color.good
        pct >= OCCUPANCY.stops[0] -> R.color.ok
        pct > 0 -> R.color.muted
        else -> R.color.faint
    }

    /** Style a TextView as a chip: tinted pill (18% of the colour) with coloured text. */
    fun applyChip(view: TextView, level: Level?) {
        if (level == null) {
            view.text = ""
            view.background = null
            return
        }
        val color = ContextCompat.getColor(view.context, level.color)
        view.text = level.text
        view.setTextColor(color)
        view.background = GradientDrawable().apply {
            cornerRadius = 999f
            setColor(withAlpha(color, 0.18f))
        }
    }

    fun withAlpha(color: Int, fraction: Float): Int =
        Color.argb((fraction * 255).toInt(), Color.red(color), Color.green(color), Color.blue(color))

    fun color(context: Context, @ColorRes res: Int) = ContextCompat.getColor(context, res)
}
