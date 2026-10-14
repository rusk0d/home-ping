package com.david.homeenv.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.core.content.ContextCompat
import com.david.homeenv.R

/** A level label and its colour, e.g. "Comfortable" / good. */
data class Level(val text: String, @ColorRes val color: Int)

/** Level thresholds, same as web/index.html (co2Level, humLevel, tempLevel). */
object Levels {
    fun co2(ppm: Int) = when {
        ppm < 800 -> Level("Fresh", R.color.good)
        ppm < 1200 -> Level("OK", R.color.ok)
        ppm < 2000 -> Level("Stuffy – open a window", R.color.poor)
        else -> Level("Poor – ventilate now", R.color.bad)
    }

    fun humidity(h: Double) = when {
        h < 30 -> Level("Very dry", R.color.poor)
        h < 40 -> Level("Dry", R.color.ok)
        h <= 60 -> Level("Comfortable", R.color.good)
        h <= 70 -> Level("Humid", R.color.ok)
        else -> Level("Very humid", R.color.poor)
    }

    fun temperature(t: Double) = when {
        t < 18 -> Level("Cold", R.color.accent_hum)
        t < 20 -> Level("Cool", R.color.ok)
        t <= 24 -> Level("Comfortable", R.color.good)
        t <= 27 -> Level("Warm", R.color.ok)
        else -> Level("Hot", R.color.bad)
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
        pct >= 60 -> R.color.good
        pct >= 20 -> R.color.ok
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
