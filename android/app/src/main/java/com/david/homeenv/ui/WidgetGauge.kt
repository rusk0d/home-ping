package com.david.homeenv.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas

/**
 * Gauge bars for the home-screen widget, drawn into a Bitmap because widgets
 * (RemoteViews) can't host custom Views. Same drawing as the CO₂ card ([GaugeBar]),
 * without the scale; scales and band stops come from [Levels].
 */
object WidgetGauge {

    /** Draws [scale] [widthPx] wide, with the needle at [value] (null = no needle, faded bands). */
    fun render(context: Context, scale: Scale, value: Double?, widthPx: Int): Bitmap {
        val gauge = GaugeBar(context)
        val d = context.resources.displayMetrics.density
        val w = widthPx.coerceAtLeast((40 * d).toInt())
        val bitmap = Bitmap.createBitmap(w, gauge.height.toInt(), Bitmap.Config.ARGB_8888)
        gauge.draw(Canvas(bitmap), scale, value, w.toFloat(), dimWhenEmpty = true)
        return bitmap
    }
}
