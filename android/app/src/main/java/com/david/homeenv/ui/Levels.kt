package com.david.homeenv.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.core.content.ContextCompat
import com.david.homeenv.R
import kotlin.math.ln

/** A level label and its colour, e.g. "Comfortable" / good. */
data class Level(val text: String, @param:ColorRes val color: Int)

/**
 * A banded scale: the value range a gauge shows, the band stops, and the colour of each
 * band (one more colour than stops). Used by the chip thresholds below, the CO₂ card's
 * gauge and the widget gauges.
 */
class Scale(val min: Double, val max: Double, val stops: List<Double>, val colors: List<Int>) {   // colour resource IDs
    init { require(colors.size == stops.size + 1) }

    /** Position of [value] on the gauge, 0..1. */
    fun fraction(value: Double) = ((value - min) / (max - min)).coerceIn(0.0, 1.0)

    /** Where each band starts on the gauge, 0..1 (the first one at 0). */
    val bandStarts: List<Float> get() = listOf(0f) + stops.map { fraction(it).toFloat() }
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
